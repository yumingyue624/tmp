# KV Connector 与缓存 Key 机制分析笔记

## 1. 总体结论

vLLM-Ascend 中存在**两条完全独立的 KV 缓存路径**，二者的 key 体系互不相通，**不存在任何转换关系**：

| | 路径一：AscendStoreConnector | 路径二：UCMConnector |
|---|---|---|
| key 形态 | 结构化字符串（PoolKey） | 16 字节二进制 BlockId |
| 层号编码 | 拼在字符串末尾 `@layer_id` | 独立的分片索引 shard index |
| 后端 | Mooncake / Memcache / Yuanrong（统一 `put(keys, addrs, sizes)` 接口） | DramStore → DramPool |
| UCM 关系 | **没有 UCM 后端** | 直接对接 UCM 实现 |

架构图：

```
vLLM 调度器 / 模型执行器
│  vLLM 标准接口
│  get_num_new_matched_tokens() / start_load_kv() / save_kv_layer() / wait_for_save() ...
│
├── AscendStoreConnector                ← 自己的缓存逻辑，生成 PoolKey 字符串
│       ↓ 后端接口 put(keys, addrs, sizes)
│       ├── MooncakeBackend
│       ├── MemcacheBackend
│       └── YuanrongBackend
│
└── UCMConnectorV1                      ← 自己的缓存逻辑，生成 16 字节 BlockId
        ↓ UCM 存储接口 lookup / dump / load
        DramStore
        ↓
        DramPool
```

## 2. 命名历史：MooncakeStoreConnector = AscendStoreConnector

`MooncakeStoreConnector` 是 `AscendStoreConnector` 的**老名字**。注册表
（`vllm-ascend/vllm_ascend/distributed/kv_transfer/__init__.py:33-43`）中两个名字指向同一个类：

```python
KVConnectorFactory.register_connector(
    "MooncakeConnectorStoreV1", "...ascend_store_connector", "AscendStoreConnector")
KVConnectorFactory.register_connector(
    "AscendStoreConnector", "...ascend_store_connector", "AscendStoreConnector")
```

## 3. 路径一：AscendStoreConnector（字符串 key，原样透传）

### 3.1 key 的生成

key 在 connector 侧构造为结构化字符串，前缀携带并行拓扑信息
（`vllm-ascend/.../kv_pool/ascend_store/config_data.py:46-52`）：

```python
def to_string(self):
    return (
        f"{self.key_metadata.model_name}"
        f"@pcp{self.key_metadata.pcp_rank}@dcp{self.key_metadata.dcp_rank}"
        f"@head_or_tp_rank:{self.key_metadata.head_or_tp_rank}"
        f"@pp_rank:{self.key_metadata.pp_rank}@{self.chunk_hash}"
    )
```

各字段含义：

- `model_name`：不同模型天然隔离
- `pcp/dcp rank`：pipeline-context / data-context parallel rank
- `head_or_tp_rank`：同一条 prompt 在不同 TP rank 上的 KV 分片用这个区分
- `chunk_hash`：块内容哈希，**前缀缓存复用的本体**
- 层号（layerwise 模式）：`LayerPoolKey.to_string()`（config_data.py:86-91）在此基础上追加 `@{layer_id}` 后缀

### 3.2 调用后端

`mooncake_backend.py:83-90`，映射到 C++ 侧
`RealClient::batch_put_from_multi_buffers`（Mooncake 项目 `real_client.h:260`）：

```python
def put(self, keys, addrs, sizes):
    try:
        # keys  → const std::vector<std::string>& keys
        # addrs → const std::vector<std::vector<void*>>& all_buffers（NPU 地址！）
        # sizes → const std::vector<std::vector<size_t>>& all_sizes
        # 默认 ReplicateConfig{}：单副本、无偏好
        res = self.store.batch_put_from_multi_buffers(keys, addrs, sizes)
        for value in res:           # list[int]，每个 key 一个返回码
            if value < 0:
                logger.error(...)   # 失败只打日志不抛——缓存语义，失败=下次重算
```

### 3.3 key 全链路原样透传

同一个字符串，**没有任何变换**（不做哈希、不加前缀、不改写）：

```
put(keys, addrs, sizes)
  → pybind: MooncakeDistributedStore.batch_put_from_multi_buffers
      （mooncake-integration/store/store_py.cpp:3208，keys 即 std::vector<std::string>）
  → RealClient::batch_put_from_multi_buffers → BatchWriteFromMultiBuffers
      （mooncake-store/src/real_client.cpp:266，同一份 keys 向量）
  → Client::BatchPut（client_service.cpp:3580）
  → Client::Put(const ObjectKey& key, ...)（client_service.cpp:1959）
      ObjectKey = std::string（include/types.h:191）
```

`Client::Put` 拿到后原样使用：直接传给 `master_client_.PutStart(key, ...)`，并用于
checksum、日志、本地磁盘文件名等。

## 4. 路径二：UCMConnector（16 字节 BlockId）

**UCM 接入不走 AscendStore 的后端接口**。vllm-ascend 的 `UCMConnectorV1`
（`vllm-ascend/vllm_ascend/distributed/kv_transfer/kv_pool/ucm_connector.py:3`）直接导入
UCM 的实现并转交调用，既没经过 `MooncakeBackend.put()`，也没把 `PoolKey.to_string()`
的字符串传入 UCM：

```python
from ucm.integration.vllm.ucm_connector import UCMConnector

self._ucm_engine = UCMConnector(vllm_config, role)
self._ucm_engine.save_kv_layer(layer_name, kv_layer, attn_metadata, **kwargs)
```

### 4.1 BlockId 的生成

`RequestHasher`（UCM 项目 `ucm/integration/vllm/request_hasher.py:107`）：

```python
hashlib.md5(meta_bytes + input_bytes).digest()
```

要点：

- 返回的是 **16 字节二进制摘要**，不是 32 字符的 MD5 十六进制字符串
- 块标识**链式生成**：输入包含前一个块的哈希 + 当前块 token + 额外的请求语义
- 命名空间包含模型、TP 配置、数据类型、rank、DCP 规模、布局等信息
- 不解析 PoolKey 中的 `@pcp`、`@dcp` 等字段，两套 key 体系互不相干

C++ 侧定义（UCM 项目 `ucm/store/detail/type/types.h:33`）：

```cpp
using BlockId = std::array<std::byte, 16>;
```

### 4.2 层号通过独立的分片索引传入

存储接口接收的三要素：**块标识（16 字节 BlockId）+ 分片索引（shard.index）+ 数据地址（shard.addrs）**。
逐层模式下，层号作为分片索引传入，而不是在字符串末尾追加 `@layer_id`。

### 4.3 DramStore 合成分片信息

DramStore 为每个张量计算：

```
shardId = 分片索引 × 每个分片的张量数量 + 张量序号
```

随后 `StorageKey()`（UCM 项目 `ucm/store/dram/cc/node_actor.cc:38`）：

- `shardId == 0`：保留原始 BlockId
- `shardId != 0`：将 shardId 混入原始 BlockId 的**前 4 字节**
- 最终 key 仍为 16 字节

这样同一个 token 块的不同层、不同张量可在 DramPool 中分别存储。
**DramPool 只处理最终的二进制 key**，不再解析模型名、rank 或层号。

### 4.4 整体路径

```
UCMConnector
  token 块 + 命名空间信息
        ↓ 哈希（md5 digest，链式）
  16 字节 BlockId + 层/分片索引
        ↓ DramStore 展开张量并混入 shardId（前 4 字节）
  16 字节存储 key
        ↓
  DramPool
```

## 5. 复习要点

1. **选 connector = 选 key 体系**：不是所有 connector 都接收同一种字符串
2. AscendStore 路径：PoolKey 字符串全链路**原样透传**到 Mooncake `ObjectKey`（就是 `std::string`）
3. UCM 路径：16 字节二进制 BlockId，层号走 shard index，DramStore 混入 shardId 后进 DramPool
4. Mooncake `put` 部分失败只打日志不抛异常（缓存语义：失败 = 下次重算）
5. 若实际部署选的是 KVPool connector，需核对它的后端适配层；不能直接套用 UCMConnector 的转换过程
