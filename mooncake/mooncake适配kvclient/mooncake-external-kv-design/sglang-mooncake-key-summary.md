# SGLang → Mooncake

版本：sglang `5b0be031e7`。

## key 格式

```text
Mooncake key
│
├─ ① 公共前缀（按顺序拼接；各部分可缺省）
│  ├─ extra_backend_tag：自定义标签
│  ├─ model_name：把 / 替换为 -
│  ├─ DCP size > 1 时，再加拓扑标签
│  │  └─ tp{TP规模}_dcp{DCP规模}_page{逻辑页大小}_pp{PP规模}_cp{CP规模}
│  └─ 用 _ 连接；全部没有时，不加前缀及其分隔符
│
├─ ② 内容指纹 H
│  ├─ 当前页 token + 前一页/父节点 hash → SHA256 链
│  ├─ 链起点：extra_key、cache_salt 均未设置 → 不加 namespace seed
│  └─ 链起点：任一设置 → 用两者生成 namespace seed
│     （二者影响 hash，不作为明文字段加入 key）
│
└─ ③ 对象 key：分三条路径
   │
   ├─ A. 普通 v1 KVCache 路径
   │  ├─ MHA：K、V 分成两个对象
   │  │  ├─ PP size = 1 → B_{t}_k、B_{t}_v
   │  │  ├─ PP size > 1 → B_{t}_{p}_k、B_{t}_{p}_v
   │  │  └─ 开启 split heads
   │  │     ├─ factor = tp_lcm_size / tp_size
   │  │     ├─ 目标编号 r = t×factor … t×factor+factor-1
   │  │     └─ 每个 r 各生成一组 K/V；用 r 替代上面的 t
   │  └─ MLA：每页一个对象，类型名为 k
   │     ├─ DCP size = 1
   │     │  ├─ PP size = 1 → B__k（两个下划线）
   │     │  └─ PP size > 1 → B_{p}_k
   │     └─ DCP size > 1
   │        ├─ PP size = 1 → B_dcp{d}_cp{c}_k
   │        └─ PP size > 1 → B_dcp{d}_cp{c}_{p}_k
   │
   ├─ B. 混合模型 v2：普通 host pool
   │  │  M = 上述 MHA 并行后缀；A = 上述 MLA 并行后缀
   │  │  （空 A 仍保留前后下划线）
   │  ├─ KV → B_{A}_k
   │  ├─ Mamba
   │  │  ├─ temporal_state_elem_size > 0 → B_{M}_temporal
   │  │  ├─ temporal_state_elem_size = 0 → 不生成 temporal 对象
   │  │  └─ 每个 conv buffer → B_{M}_conv_{i}，i 从 0 开始
   │  ├─ Draft（按 draft 自己的 pool 类型判断）
   │  │  ├─ MLA host pool → B_{A}_draft_k
   │  │  └─ 其他 → B_{M}_draft_k、B_{M}_draft_v
   │  ├─ Draft SWA
   │  │  ├─ DeepSeekV4PagedHostPool 或 MLA host pool → B_{A}_draft_swa
   │  │  └─ MHA host pool → B_{M}_draft_swa_k、B_{M}_draft_swa_v
   │  ├─ SWA
   │  │  ├─ 非 MLA 后端且 pool 有 v_buffer → B_{M}_swa_k、B_{M}_swa_v
   │  │  └─ MLA 后端 → B_{A}_swa
   │  ├─ 单对象 side pool → B_{A}_{pool_name}
   │  │  └─ pool_name 完整列表见下一节
   │  └─ 未注册 pool、未覆盖的 pool/type 组合 → 报错，无有效 key
   │
   └─ C. v2 的 DevicePoolEntry（Direct linker 使用；优先于 B 的 pool 分类）
      ├─ 并行后缀 R
      │  ├─ rank_replicated = false → tp{t}_cp{c}_pp{p}
      │  └─ rank_replicated = true  → cp{c}_pp{p}
      │     （CP、PP 为 0 时也保留；覆盖普通 M/A 后缀）
      └─ 追加对象名：最终 key = B_{R}_{对象名}
         ├─ KV pool：对象名为 k → B_{R}_k
         └─ 其他已注册 DevicePoolEntry：对象名为 pool_name → B_{R}_{pool_name}
            KV pool → …_cp0_pp0_k
            Indexer pool → …_cp0_pp0_indexer
```


| 路径 | SGLang 配置 |
|---|---|
| HBM → CPU → Mooncake（A：v1、B：v2 host pool） | `--enable-hierarchical-cache --hicache-storage-backend mooncake` |
| HBM → Mooncake → KVClient（C：Direct linker） | `--enable-unified-cache-external-linker --unified-cache-external-linker-backend mooncake` |



rank_replicated

表示这组缓存是否在同一个 TP 组的不同 rank 上保存了等价副本。由代码根据实际缓存池类型设置。

```
rank_replicated=false → key带tp编号：各 TP rank 保存不同分片
TP0 → 数据 A → key 必须有 tp0
TP1 → 数据 B → key 必须有 tp1

rank_replicated=true  → key不带tp编号：各 TP rank 保存等价数据。
TP0 → 数据 A ┐
TP1 → 数据 A ┴→ 共用一个不带 TP 编号的 key
```

例如 DeepSeek-V3.2 的 DSA 构建分支设置：

```
return DevicePoolGroup(
    entries, num_layers, page_size, rank_replicated=True
)
```

所以这条路径生成的是：

```
…_cp0_pp0_k
…_cp0_pp0_indexer
```

**不会生成 `…_tp0_…`、`…_tp1_…` 两套 key**

复制模式下只有 TP rank 0 负责卸载，其他 TP rank 跳过实际写入。

```
self.offload_owner = not rank_replicated or tp_rank == 0
```

## sglang 调用 mooncake 流程
### put
SGLANG整理好各 page 的所有层 buffer 地址后，最终进入 D:/workspace/sglang/python/sglang/srt/mem_cache/storage/mooncake_store/mooncake_store.py:1499：

```
if self._uses_multi_buffer(buffer_ptrs):
    config = config or self._replicate_config_cls()
    return self.store.batch_put_from_multi_buffers(
        key_strs, buffer_ptrs, buffer_sizes, config
    )
```

### get

调用的mooncake接口：
```
std::vector<int> batch_get_into_multi_buffer_ranges(
    const std::vector<std::string>& keys,
    const std::vector<std::vector<void*>>& all_buffers,
    const std::vector<std::vector<size_t>>& all_sizes,
    const std::vector<std::vector<size_t>>& all_src_offsets // 二维列表，一次可以读取多个对象，每个对象内的多个范围
) override;
```

sglang流程：
1. 先建立 get session
(D:/workspace/sglang/python/sglang/srt/mem_cache/storage/mooncake_store/mooncake_direct_linker.py:329)

```
for keys, _ in batches.values():
    result = self.storage.store.batch_get_session_start(keys) # 查询对象，检查 lease，选择可用副本，把查询结果保存在 `get_sessions_`
```

2. 逐层读取 
(D:/workspace/sglang/python/sglang/srt/mem_cache/storage/mooncake_store/mooncake_direct_linker.py:338)：

```
for layer in range(self.num_layers):
    for name, (keys, locations) in batches.items():
        meta = self.pools[name].get_prepared_layer_range_meta(
            locations, layer
        )
        if meta is None:
            continue

        ptrs, sizes, offsets = meta

        result = self.storage.store.batch_get_into_multi_buffer_ranges(
            keys,     # 对象 key
            ptrs,     # 当前层的目标 HBM 地址
            sizes,    # 读取长度
            offsets,  # 对象内源偏移
        )

        # 随后检查读取结果……

    self.layer_done_counter.complete(counter_index, layer) # 当前层完成后，标记该层就绪
```

3. 最终在清理阶段调用：

```
self.storage.store.batch_get_session_end(keys)
```

## 常见模型kv对象大小
page_size=128、BF16、CP=1
| 模型与配置 | 当前 stage 层数 | 一个 KV 对象的理论大小 |
|---|---:|---:|
| Qwen3-8B，TP1/PP1 | 36 | 18 MiB |
| Qwen3-8B，TP4/PP1 | 36 | 4\.5 MiB |
| Qwen3-32B，TP4/PP1 | 64 | 8 MiB |
| Qwen3-32B，TP8/PP2 | 32 | **2 MiB** |
| Qwen3-235B，TP8/PP2 | 47 | **2\.9375 MiB** |

# LMCache key
```python
@dataclass(slots=True)
class CacheEngineKey:
    model_name: str
    world_size: int
    worker_id: int
    chunk_hash: int
    dtype: torch.dtype
    request_configs: Optional[dict] = field(default_factory=dict)
```

序列化形式：

```text
model_name@world_size@worker_id@chunk_hash@dtype
```

layerwise 模式会再加 `layer_id`，`LayerCacheEngineKey` ：

```text
model_name@world_size@worker_id@chunk_hash@dtype@layer_id
```

所以 LMCache 的 cache 身份由这些维度共同决定：

| 维度 | 是否参与 key | 说明 |
|---|---|---|
| model | 是 | `model_name` |
| TP/PP/PCP | 间接参与 | 编码在 `world_size` + `worker_id` |
| layer | 仅 layerwise 时 | `LayerCacheEngineKey.layer_id` |
| chunk/prefix | 是 | `chunk_hash` |
| dtype | 是 | KV cache 数据类型 |
| request tags | 是 | `request_configs`，比如 LoRA、多模态等 |

TP / PP 处理：

vLLM 的 `world_size` 定义在：

```python
self.world_size = (
    self.pipeline_parallel_size
    * self.tensor_parallel_size
    * self.prefill_context_parallel_size
)

if self.distributed_executor_backend == "external_launcher":
    self.world_size *= self.data_parallel_size
```

所以：

```text
world_size ≈ TP × PP × PCP
external_launcher 模式下再乘 DP

worker_id = 当前 worker 的全局 rank
```

- LMCache 不会把不同 TP/PP rank 的 KV 合并成同一个 cache entry。

## 单对象 side pool：完整名称

普通 host pool 路径 B 对下列每个名字都生成 `B_{A}_{pool_name}`：

```text
indexer
draft_indexer
deepseek_v4_c1
deepseek_v4_c1_indexer
deepseek_v4_c1_indexer_scale
deepseek_v4_c2
deepseek_v4_c2_indexer
deepseek_v4_c2_indexer_scale
deepseek_v4_c4
deepseek_v4_c4_rope
deepseek_v4_c4_indexer
deepseek_v4_c4_indexer_scale
deepseek_v4_c128
deepseek_v4_c128_rope
deepseek_v4_c4_state
deepseek_v4_c4_indexer_state
deepseek_v4_c128_state
```

`auxiliary_swa` 虽然存在于 PoolName 枚举，但普通 host pool 的此函数没有对应分支，不能因此推断它能生成 key。DevicePoolEntry 则走通用的 pool_name 拼接规则。

## 最终示例：直接看字符串

除 DCP 示例外，假设 `B = test_Qwen-Qwen3-8B_abc123`。这些是分支示例，不代表同一模型同时使用所有分支。

| 场景 | 最终 key 示例 |
|---|---|
| MHA，TP rank=3，PP size=1，K | `test_Qwen-Qwen3-8B_abc123_3_k` |
| MHA，TP rank=3，PP rank=1，PP size>1，K | `test_Qwen-Qwen3-8B_abc123_3_1_k` |
| 上述 MHA 的 V | `test_Qwen-Qwen3-8B_abc123_3_1_v` |
| split heads：TP size=4，LCM=8，本地 TP rank=1，PP size=1 | `test_Qwen-Qwen3-8B_abc123_2_k`、`…_2_v`、`…_3_k`、`…_3_v` |
| MLA，PP size=1，DCP size=1 | `test_Qwen-Qwen3-8B_abc123__k` |
| MLA，PP rank=1，PP size>1，DCP size=1 | `test_Qwen-Qwen3-8B_abc123_1_k` |
| MLA，DCP size=2/rank=1，TP size=8，页大小64，PP size=2/rank=1，CP size=1/rank=0 | `test_Qwen-Qwen3-8B_tp8_dcp2_page64_pp2_cp1_abc123_dcp1_cp0_1_k` |
| Mamba temporal，M=3_1 | `test_Qwen-Qwen3-8B_abc123_3_1_temporal` |
| Mamba 第0个 conv，M=3_1 | `test_Qwen-Qwen3-8B_abc123_3_1_conv_0` |
| MLA Draft，A=1 | `test_Qwen-Qwen3-8B_abc123_1_draft_k` |
| MHA Draft，M=3_1 | `test_Qwen-Qwen3-8B_abc123_3_1_draft_k`、`…_3_1_draft_v` |
| MLA Draft SWA，A=1 | `test_Qwen-Qwen3-8B_abc123_1_draft_swa` |
| MHA Draft SWA，M=3_1 | `test_Qwen-Qwen3-8B_abc123_3_1_draft_swa_k`、`…_3_1_draft_swa_v` |
| MLA SWA，A=1 | `test_Qwen-Qwen3-8B_abc123_1_swa` |
| MHA SWA，M=3_1 | `test_Qwen-Qwen3-8B_abc123_3_1_swa_k`、`…_3_1_swa_v` |
| Indexer，A=1 | `test_Qwen-Qwen3-8B_abc123_1_indexer` |
| DeepSeek V4 side pool，A=1 | `test_Qwen-Qwen3-8B_abc123_1_deepseek_v4_c4_indexer_scale` |
| Direct linker，非复制，TP=3/CP=0/PP=1，KV pool | `test_Qwen-Qwen3-8B_abc123_tp3_cp0_pp1_k` |
| Direct linker，复制模式，CP=0/PP=1，KV pool | `test_Qwen-Qwen3-8B_abc123_cp0_pp1_k` |
| Direct linker，非复制，Mamba DevicePoolEntry | `test_Qwen-Qwen3-8B_abc123_tp3_cp0_pp1_mamba` |

## 其他入口：与上面的自动生成 key 区分

```text
其他 Mooncake 入口
├─ 原样透传 API
│  └─ set / batch_set / get / batch_get / exists 等底层接口使用调用方给定 key
│     不自动保证它符合上述 page key 格式
├─ 初始化预热
│  └─ sglang_mooncake_store_warmup_key{uuid4.hex}
└─ MooncakeEmbeddingStore（多模态 embedding，不是 KVCache page）
   └─ emb_{mm_hash}
```

另有可选的 group ID：`sglang-hicache:{已加前缀的逻辑page key}`。它是关联多个对象的元数据，不是替代上述对象 key 的另一种格式。tenant_id 通过 Mooncake setup 单独传入，不在这里作为明文字段拼接。

## 对聚合实现的含义

- 不能固定按下划线位置取 TP/PP：前缀、pool 名本身也可能带下划线。
- 普通 MHA/MLA 的 rank 是裸数字，Direct linker 使用带名字的 rank；两者需要分别适配。
- 这些 key 拼接分支没有独立 layer 字段；Direct linker 可以通过同一对象内的字节范围逐层读。
- key 的拼接规则由路径和配置决定；hash 内容随 token、上下文和 namespace 变化。

## 源码索引（相对 D:\workspace\sglang）

| 内容 | 文件与行号 |
|---|---|
| 前缀 | `python/sglang/srt/mem_cache/storage/mooncake_store/mooncake_store.py:440` |
| MHA/MLA/DCP/split 后缀 | 同文件 `:614` |
| 预热 | 同文件 `:705` |
| 加前缀、group ID | 同文件 `:766`、`:774` |
| 完整 v2 pool 分类 | 同文件 `:792` |
| v1 split/MHA/MLA 拼接 | 同文件 `:1127`、`:1164`、`:1177` |
| 原样透传接口 | 同文件 `:1322` 起 |
| Direct linker 后缀及覆盖 | `python/sglang/srt/mem_cache/storage/mooncake_store/mooncake_direct_linker.py:37`、`:105` |
| namespace / page hash | `python/sglang/srt/mem_cache/utils.py:116` 起 |
| PoolName 全部枚举 | `python/sglang/srt/mem_cache/hicache_storage.py:84` |
| embedding key | `python/sglang/srt/mem_cache/storage/mooncake_store/mooncake_embedding_store.py:50` |
