# DramPool HIXL QueryHandle 及长阶段性能分析

测试日期：2026-08-24  
测试机器：`10.218.3.25`（Ascend A3）  
隔离容器：`codex_drampool_perf`

## 1. 约束执行情况

- 开发机全程未访问公网。
- 模型、源码和依赖只使用 `.25` 已有内容或此前从 `.20` 经私网同步的内容。
- 未修改卡 IP、SuperPod ID、HCCL 拓扑或交付网络配置。
- 测试只修改隔离 UCM/HIXL checkout、隔离容器和日志目录。

## 2. 测试规模

共完成 6 轮：

```text
run01                              TP2，普通负载
stress_b8_run01                    TP2，batch=8
stress_b32_run01                   TP2，batch=32
stress_tp4_b8_run01                TP4，batch=8
stress_tp8_b4_run01                TP8，batch=4
stress_tp8_b4_run02_stage_trace    TP8，batch=4，增加队列和完成时间日志
```

汇总：

```text
DramPool 请求：622
SUCCESS：622
HIXL GetTransferStatus 分层样本：1727
Query worker queue_wait_us 最大值：2609 us
真正 HIXL backend Query 最大值：67 us
```

## 3. `data_tm_to_hixl_query_handle_us` 的准确语义

该字段从 `TransportManager::GetStatus` 入口计时，到 `HixlInstance` worker 中真正调用 `engine.GetTransferStatus` 前停止。它包含：

```text
TransportManager handle map 查找
  -> HixlTransport lifecycle/pending map 查找
  -> HixlInstance::Run 入队
  -> 等待串行 HIXL worker 执行前序任务
```

它不包含真正的 `engine.GetTransferStatus` 执行时间。因此字段名容易让人误以为是 HIXL Query API 自身很慢。

## 4. QueryHandle 长尾实测结论

普通 TP2 负载中：

```text
data_tm_to_hixl_query_handle_us 最大 13 us
```

提高并发后：

```text
TP2 batch=8：最大 133 us
TP2 batch=32：最大 141 us
TP4 batch=8：捕获 response query 1320 us
TP8 batch=4 第一轮：data query 2615、1649、1538 us
TP8 batch=4 第二轮：data query 1253、1201 us
```

最完整的 `2615 us` 样本：

```text
request_id=1
peer=127.0.0.1:4508
data_tm_to_hixl_query_handle_us=2615
HixlInstance queue_wait_us=2609
真正 backend GetTransferStatus=43 us
```

其前序 HIXL worker 任务是另一个新 peer 的 108 段 `TransferAsync`：

```text
slot_us=2440
HIXL CommEngine TransferAsync total_us=2691
worker execute_us=2705
```

状态查询在这个串行提交任务执行期间入队，因此等待了约 `2.6 ms`。另外两个 data query 长尾也分别被以下前序冷提交阻塞：

```text
data query 1649 us <- 前序 TransferAsync slot_us=1457，worker execute_us=1701
data query 1538 us <- 前序 TransferAsync slot_us=1291，worker execute_us=1544
```

结论：

> `data_tm_to_hixl_query_handle_us` 的偶发长尾不是 `GetTransferStatus`、host flag 读取或锁竞争本身，而是 `HixlInstance` 使用单一串行 worker；Query 状态任务被同一 worker 上新 peer 的冷 `TransferAsync` 阻塞。冷提交内部主要又是 `AcquireSharedSlot -> aclrtCreateContext/default stream` 的惰性初始化。

底层分解进一步排除了其它候选：

- `CommChannel::GetTransferStatus` 最大约十几微秒。
- ADXL Query 最大约几十微秒。
- `req2channel_mutex`、`device_launch_mu_` 等等待基本为 `0–1 us`。
- 所有 Query backend 样本最大仅 `67 us`。

## 5. 其它大于 1000 us 的阶段

六轮 622 个请求汇总：

| 阶段 | `>=1000 us` 样本数 | 最大值 | 原因 |
|---|---:|---:|---|
| `request_queue_us` | 325 | 17201 us | 单 TaskWorker 串行处理并发请求，FIFO 积压 |
| `taskworker_prepare_us` | 26 | 3540 us | 首次 peer 的 HIXL slot/context 惰性初始化及 HCCL 首调 |
| `poller_queue_us` | 183 | 5938 us | 单 CompletionPoller 正在扫描/查询已有 pending，不能及时从 completionQueue 补充 |
| `data_transfer_us` | 345 | 7995 us | 8 MB 传输完成时间，加完成状态被 poller 观察到之前的等待 |
| `response_submit_us` | 6 | 2115 us | 首次控制 peer 的 slot 初始化/worker 提交等待 |
| `response_transfer_us` | 263 | 8900 us | 完成标志传输及单 poller 轮询观察延迟 |
| `data_tm_to_hixl_query_handle_us` | 5 | 2615 us | HIXL 串行 worker 被冷 TransferAsync 阻塞 |
| `response_tm_to_hixl_query_handle_us` | 4 | 1389 us | 与 data query 相同的 worker 排队原因 |

`poller_queue_us` 的新日志记录到：

```text
wait_us=3469 pending_size=3 pending_depth=64
wait_us=3270 pending_size=4 pending_depth=64
```

这说明长等待并不一定是 `pending_depth=64` 已满；即使 pending 很少，只要 poller 正在一轮 `PollPendingCompletions` 中被 Query/提交拖住，新的 completion record 也要等到下一次 `FillPendingWindow`。

## 6. 接近 1000 us 的阶段（800–999 us）

| 阶段 | 样本数 | 代表范围 | 分析 |
|---|---:|---:|---|
| `request_queue_us` | 9 | 814–973 us | TaskWorker FIFO 排队 |
| `poller_queue_us` | 23 | 812–973 us | 单 poller 扫描周期造成入场延迟 |
| `data_transfer_us` | 14 | 823–973 us | 传输完成及终态观察延迟 |
| `response_transfer_us` | 31 | 816–994 us | 小 response 仍受设备完成和 poller 周期影响 |
| `total_us` | 12 | 800–999 us | 上述阶段的组合结果 |

该区间没有 QueryHandle 样本。QueryHandle 在本环境呈明显双峰：通常小于约 `200 us`；如果正好撞上冷 TransferAsync，则直接上升到 `1.2–2.6 ms`。

## 7. 约 500 us 的阶段（400–799 us）

| 阶段 | 样本数 | 最大值/代表值 | 分析 |
|---|---:|---:|---|
| `request_queue_us` | 15 | 416–785 us | TaskWorker 的轻度积压 |
| `metadata_prepare_us` | 5 | 最大 612 us | 单请求循环处理 108 个 metadata 项；高并发下 CPU/metadata 锁调度放大 |
| `taskworker_prepare_us` | 18 | 408–708 us | 热 HIXL submit 加 metadata/线程调度开销 |
| `poller_queue_us` | 54 | 406–792 us | 一次 poller 扫描周期或已有 Query 阻塞 |
| `data_transfer_us` | 12 | 425–767 us | 热路径 8 MB 传输和完成观察 |
| `response_submit_us` | 12 | 442–610 us | HIXL worker 排队或提交波动 |
| `response_transfer_us` | 54 | 422–787 us | 小 response 的完成观察延迟 |
| `response_tm_to_hixl_execute_async_us` | 1 | 473 us | 提交任务进入 HIXL worker 前排队 |

`metadata_prepare_us` 的原因目前属于源码路径与样本关联推断：DUMP/LOAD/LOOKUP 都在一个 TaskWorker 中逐项执行 108 次 `StoreBegin`、`LoadBegin` 或 `Exist`。本轮没有继续拆成单项 metadata API 计时；如果要优化该阶段，下一步应对每个 metadata API 汇总 `sum/max/count`，而不是逐项打印日志。

## 8. 数据与 response 传输的底层证据

新增 `transfer_elapsed_us` 从 HIXL submit 时刻记录到 host completion flag 被观察为完成。

最后一轮中：

- 108 段、7,962,624 bytes 数据传输：`644–6254 us`
- 1 段、63 bytes response：`169–4426 us`
- Query 状态 API 本身仍只有几十微秒

因此 `data_transfer_us` / `response_transfer_us` 的长值主要是实际异步任务完成及被单 poller 观察到的时间，不是 Query API 在执行几十微秒以外额外做了同步传输。

## 9. 建议的优化顺序

1. 将 channel slot/context 初始化提前到 Connect 或显式 prewarm，先消除冷 TransferAsync 对 Query worker 的 `1–3 ms` 阻塞。
2. 为 `HixlInstance` worker 增加任务类型和优先级；完成状态 Query 可优先于尚未开始的普通提交，但无法抢占正在执行的冷提交。
3. 评估实现 HIXL batch GetTransferStatus；当前 `CommEngine::GetTransferStatus(args, results)` 返回 `UNSUPPORTED`，每个 handle 都会形成独立 worker 任务。
4. CompletionPoller 在长 scan 中按时间片或固定条数重新调用 `FillPendingWindow`，避免 pending 未满时 completionQueue 仍等待数毫秒。
5. TaskWorker 如需并行化，必须先验证 metadata 并发语义和 HIXL 单 worker 瓶颈，否则只会把排队从 TaskWorker 转移到 HIXL worker。
6. 对 metadata 的约 500 us 样本增加聚合计时，再决定是否优化 metadata 锁或批处理。

## 10. 证据目录

```text
/home/drampool/query_handle_perf_20260824/
```

关键文件：

```text
ANALYSIS.md
summary.json
request_stage_samples_all.tsv
query_samples_all.tsv
worker_task_samples_all.tsv
queue_threshold_samples.tsv
transfer_completion_samples.tsv
stage_bucket_evidence.tsv
evidence_ge_1000.log
evidence_near_1000.log
evidence_around_500.log
query_long_context.log
ucm_query_instrumentation.patch
hixl_query_instrumentation.patch
drampool_queue_instrumentation.patch
query_stress_workload.patch
runs/*/drampool_server.log
runs/*/offline_inference.log
```

其中 `query_long_context.log` 保存了每个 `queue_wait_us >= 400` Query 前后完整日志，可直接看到阻塞它的前序 HIXL worker 任务。
