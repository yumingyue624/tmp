# perf 分支 `offline_inference.py` 请求链路与 DeepSeek MLA KV 布局

本文整理两个问题：

1. 当前 `perf` 分支原始 `examples/offline_inference.py` 从 vLLM 请求、token、KV block、UCMConnector、DramStore task 到 DramPool request 的完整转换关系。
2. DeepSeek-V2-Lite 日志中的 `27 层 × 每层 2 个 tensor` 到底是不是普通的 K/V，以及为什么需要分别保存。

## 一、先区分四种数量

分析日志时需要区分：

```text
token 数
  -> vLLM/UCM 逻辑 KV block 数
    -> DramStore IO entry 数
      -> DramPool 协议 request 数
```

它们不是一一对应的。特别是在本次 DeepSeek MLA 布局下，一个完整的逻辑 KV block 会展开成 54 个 DramStore IO entry。

## 二、当前原始 `offline_inference.py` 的请求

当前脚本的主要配置为：

```python
block_size = 128
max_tokens = 100
temperature = 0

print_output(llm, prompts, sampling_params, "first")
print_output(llm, prompts, sampling_params, "second")
```

对应代码位置：

- `examples/offline_inference.py:33`：`block_size=128`
- `examples/offline_inference.py:104`：`max_tokens=100`
- `examples/offline_inference.py:106-107`：同一 prompt 连续执行两次

当前原始脚本没有显式设置 `tensor_parallel_size`，所以没有外部参数覆盖时使用 vLLM 默认的 TP=1。

此前开发机用相同第一段 prompt 和 `DeepSeek-V2-Lite-Chat` 实测得到：

```text
输入 prompt：360 token
实际生成：80 token
```

80 小于 `max_tokens=100`，是模型提前结束。历史日志中的吞吐也能反算：

```text
72.55 token/s × 4.96 s ≈ 360 input token
16.12 token/s × 4.96 s ≈ 80 output token
```

精确 token 数取决于实际模型目录内的 tokenizer 和 chat template；如果模型版本发生变化，需要重新打印 `len(tokenizer(prompt).input_ids)` 验证。

## 三、360 token 如何变成 2 个 UCM block

`block_size=128`，因此输入被划分为：

```text
block 0：128 token，完整
block 1：128 token，完整
block 2：104 token，不完整
```

UCMConnector 的 `generate_hash()` 只为完整 block 生成 hash，不完整 block 会直接停止：

```python
if len(block_token_ids) < block_size:
    break
```

代码位置：`ucm/integration/vllm/ucm_connector.py:300-309`。

所以本次请求的可持久化 block 数为：

```text
floor(360 / 128) = 2
```

剩余 104 token 不会形成第三个外部缓存 block。开发机日志也记录了：

```text
total_blocks_num: 2
```

证据：

```text
D:\workspace\tmp\drampool_perf_0825\core_extracted\query_handle_perf_20260824\runs\run01\offline_inference.log:172
```

## 四、为什么 2 个逻辑 block 会展开成 108 个 DramStore entry

开发机实际 KV cache 布局日志为：

```text
base_ptrs: (27, 2)
tensor_size_lists: (27, 2)
```

即：

```text
27 个 attention layer
× 每层 2 个物理 tensor/内存区域
= 每个逻辑 block 有 54 个物理片段
```

UCMConnector 的 `extract_block_addrs()` 返回的非 layerwise 布局是：

```text
(num_blocks, num_layers, num_ptrs)
```

本次为：

```text
(2 blocks, 27 layers, 2 ptrs)
```

随后 reshape/normalize 成 DramStore task 的 IO entries：

```text
2 × 27 × 2 = 108 entries
```

因此日志中的数量关系是：

```text
LOOKUP entries=2
DUMP   entries=108
LOAD   entries=108
```

LOOKUP 只查询两个逻辑 block hash，所以是 2。DUMP/LOAD 必须传输这两个 block 在所有层、所有物理 tensor 中的数据，因此展开成 108 个 IO entry。

## 五、这两个 tensor 是普通的 K 和 V 吗

不是。此前把它简写成“每层 K、V 两个 tensor”不够准确；对于本次 DeepSeek-V2-Lite 的 MLA 布局，应当按下面理解：

```text
tensor 0：压缩 KV latent（维度约 512）
tensor 1：解耦的 RoPE key 分量（维度约 64）
```

日志中每层每 block 的 `tensor_size_list` 交替为：

```text
131072 bytes, 16384 bytes
```

本次 KV cache 使用 BF16，每个元素 2 字节，block 包含 128 token，因此：

```text
128 token × 512 × 2 bytes = 131072 bytes
128 token ×  64 × 2 bytes =  16384 bytes
```

这与日志完全一致。因此 `(27, 2)` 中的第二维，实际对应的是：

```text
[compressed KV latent, RoPE key component]
```

而不是传统 MHA 中简单的 `[K cache, V cache]`。

### 为什么 MLA 仍然要分成两个物理 tensor

MLA 会把可压缩的 K/V 信息编码进较小的 KV latent，以减少 KV cache 容量；但施加 RoPE 的 key 位置分量不能与这部分完全采用相同的吸收和重构路径，所以需要单独保留。

两部分的维度也不同：

```text
压缩 KV latent：512 维
RoPE key 分量：64 维
```

将它们分开可以让 attention kernel：

- 对压缩 latent 使用 MLA 的低秩重构/吸收计算路径；
- 对 64 维位置分量单独执行或使用 RoPE 相关计算；
- 使用不同的 shape、stride 和地址访问两块内存；
- 避免把较小的 RoPE 分量强行填充到较大的 latent 布局中。

从 UCM/DramStore 的角度看，它不需要理解 tensor 的数学语义，只看到每层有两个独立的基地址和长度，所以每个逻辑 block 被展开成 `27 × 2 = 54` 个可传输 IO entry。

## 六、单个逻辑 block 的数据量

每层、每 block：

```text
131072 + 16384 = 147456 bytes
```

27 层合计：

```text
147456 × 27 = 3981312 bytes
```

所以一个逻辑 KV block 的数据量为：

```text
3981312 bytes
```

两个 block：

```text
3981312 × 2 = 7962624 bytes
```

这与 DramPool `request_done` 中的 `data_bytes=7962624` 完全一致。

注意：如果把 108 个 IO entry 平均来看，每个 entry 的平均值是 73728 字节，但真实 entry 并不等长，而是在 131072 和 16384 字节之间交替。此前使用 `7962624 / 108 = 73728` 只是平均值，不能理解为每个 entry 都是 73728 字节。

## 七、第一次 `llm.generate()`：外部缓存未命中

第一次执行时：

```text
360 input token
  -> 2 个完整逻辑 block
  -> Scheduler UCMConnector 查询这 2 个 block hash
  -> DramStore LOOKUP task：entries=2
  -> 单 DramPool node，形成 1 个 LOOKUP request
```

缓存未命中后，vLLM 计算 KV，Worker UCMConnector 保存完整 block：

```text
2 blocks × 27 layers × 2 tensors
  = 108 DramStore entries
  -> 1 个 DUMP task
  -> 1 个 DramPool DUMP request（batch_size=108）
```

因此第一次生成产生：

| 操作 | 逻辑 block | DramStore entries | DramPool request |
|---|---:|---:|---:|
| LOOKUP | 2 | 2 | 1 |
| DUMP | 2 | 108 | 1 |
| 合计 |  |  | 2 |

## 八、第二次 `llm.generate()`：外部缓存命中

第二次 prompt 完全相同：

```text
LOOKUP：2 个 block，external hit=2
LOAD：2 × 27 × 2 = 108 entries
```

当全部 block 命中时，当前 Connector 会让最后一个 token 重新计算：

```python
if num_total_hit_tokens == request.num_tokens:
    external_hit_tokens -= 1
```

代码位置：`ucm/integration/vllm/ucm_connector.py:520-522`。

默认 TP=1 时，第二次产生：

| 操作 | DramStore entries | DramPool request |
|---|---:|---:|
| LOOKUP | 2 | 1 |
| LOAD | 108 | 1 |
| 合计 |  | 2 |

## 九、当前原始脚本的完整 DramPool 请求序列

在默认 TP=1、一个 DramPool node、初始缓存为空时，理论序列为：

```text
第一次 llm.generate()
├─ opcode=0 LOOKUP：batch_size=2
└─ opcode=1 DUMP：batch_size=108

第二次 llm.generate()
├─ opcode=0 LOOKUP：batch_size=2
└─ opcode=2 LOAD：batch_size=108
```

因此正常约有 4 条 DramPool `request_done`：

```text
LOOKUP：2
DUMP：1
LOAD：1
```

如果外部环境把 TP 改成 2，MLA 的 DUMP 只由主 rank 提交，但命中后的 LOAD 可能由两个 worker 分别提交；此时同一段 prompt 连续运行两次可形成约 5 条 `request_done`。此前保存的压力 `run01` 还额外加入了第二段 prompt，所以共得到 10 条。

## 十、`kTargetBatchEntries=128/512` 在哪里生效

`kTargetBatchEntries` 限制的不是 token 数，也不是逻辑 KV block 数，而是路由到某个 DramPool node 后，一个协议 request 最多包含多少个 DramStore IO entry：

```text
DramStore task
  -> 按 entry key 路由到 node
  -> 每个 node 内按 batchLimit 拆分协议 request
```

代码位置：

- `ucm/store/dram/cc/task_manager.cc:227`：按 key 路由
- `ucm/store/dram/cc/task_manager.cc:228`：确定 `batchLimit`
- `ucm/store/dram/cc/task_manager.cc:233-240`：按上限拆 request

本次模型每个完整逻辑 block 展开为 54 entries，因此：

| 完整逻辑 block 数 | DramStore entries | 上限 128 | 上限 512 |
|---:|---:|---:|---:|
| 1 | 54 | 1 request | 1 request |
| 2 | 108 | 1 request | 1 request |
| 3 | 162 | 2 requests | 1 request |
| 9 | 486 | 4 requests | 1 request |
| 10 | 540 | 5 requests | 2 requests |

当前原始 prompt 是 360 token，只产生两个完整 block：

```text
2 × 54 = 108 entries
```

所以无论上限是 128 还是 512，都会形成一个 DUMP/LOAD request。这就是原始 `offline_inference.py` 对 `128 -> 512` 不敏感的直接原因。

## 十一、结论

1. 当前原始脚本每次只提交一个 vLLM 推理请求，同一 prompt 重复两次。
2. 开发机实测输入为 360 token，其中只有 2 个完整的 128-token block 会进入 UCM 外部缓存。
3. DeepSeek MLA 每个逻辑 block 在本次实现中包含 27 层、每层两个物理 tensor，所以展开成 54 个 DramStore entry。
4. 这两个 tensor 不是普通 K/V，而是约 512 维的压缩 KV latent 和约 64 维的 RoPE key 分量。
5. 两个逻辑 block 最终形成 108 entries，小于 128，因此将 batch 上限改为 512 不改变该脚本的 request 数。
6. 若要稳定测试 `128 -> 512`，至少应让单个 DUMP/LOAD task 包含 3 个完整逻辑 block；考虑 chat template 和尾部非完整块，prompt 最好明显超过 384 token，并用日志确认 `task_done entries > 128`。
