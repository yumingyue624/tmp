# DramStore >=500 us 阶段性能分析

分析日期：2026-08-25  
数据来源：`core_extracted/query_handle_perf_20260824/runs/*/offline_inference.log`，共 6 组运行日志。

## 1. 统计口径

- DramStore `request_done`：684 条
  - `SUCCESS`：622 条
  - `FAILED`：62 条
- DramStore `task_done`：622 条
  - `SUCCESS`：613 条
  - `FAILED`：9 条
- 时间戳字段（`*_ts_us`）不参与阶段耗时统计。
- 成功路径和失败超时路径分别统计。失败请求中的 `remote_wait_us=0` 可能只是没有观察到 reply，不能解释为没有等待。
- `request total_us`、`task requests_inflight_us` 和 `task total_us` 是汇总指标，会继承内部慢阶段，不能重复认定为独立瓶颈。

## 2. 成功路径中真正 >=500 us 的原始阶段

| 阶段 | >=1000 us 样本数 | 500-999 us 样本数 | 最大值 | 原因判断 |
|---|---:|---:|---:|---|
| `node_queue_us` | 1 | 0 | 82,898 us | NodeActor 收到请求后长时间未执行 `StartRequest()`。该样本紧跟一次请求超时和 fence recovery，可能是恢复后的事件循环调度停顿、线程被抢占，或恢复状态对调度产生影响；现有日志无法进一步区分 |
| `remote_wait_us` | 391 | 29 | 48,903 us | 从本地控制请求发送完成到 DramStore 观察到远端 reply 的整段等待，覆盖 DramPool 接收、TaskWorker 排队、HIXL Query/Transfer、回复发送和客户端轮询 |
| 其它原始阶段 | 0 | 0 | <=317 us | 当前日志中没有超过 500 us 的样本 |

因此，成功路径真正需要继续拆解的原始长尾只有：

1. `remote_wait_us`；
2. 一个孤立的 `node_queue_us=82,898 us`。

## 3. >=1000 us 的汇总/传播阶段

| 阶段 | 样本数 | 最大值 | 原因 |
|---|---:|---:|---|
| request `total_us` | 398 | 85,210 us | 汇总 `node_queue_us + remote_wait_us + 本地发送/处理`；最大样本由 82,898 us 的 NodeActor 排队造成 |
| task `requests_inflight_us` | 392 | 85,246 us | Task 等待最慢的子 request，继承 request 的 `remote_wait_us` 或 `node_queue_us` |
| task `total_us` | 393 | 85,278 us | 在 `requests_inflight_us` 基础上增加 task queue/route，仍是同一长尾的传播 |

这些字段很长，但不是新的独立瓶颈。

## 4. 500-999 us 的汇总阶段

| 阶段 | 样本数 | 最大值 | 原因 |
|---|---:|---:|---|
| request `remote_wait_us` | 29 | 999 us | 远端正常处理及回复等待，已接近 1 ms |
| request `total_us` | 36 | 985 us | 多个阶段相加，主要仍由 `remote_wait_us` 决定 |
| task `requests_inflight_us` | 42 | 971 us | 等待最慢子 request |
| task `total_us` | 48 | 999 us | `requests_inflight_us` 加 task queue/route |
| task `route_us` | 1 个失败样本 | 708 us | 将 3348 entries 拆为 27 个 request 的路由/分片；该 task 随后超时，不代表正常路径 |

## 5. `remote_wait_us` 详细分析

`remote_wait_us` 是当前 DramStore 最主要的正常长尾：

- `>=1000 us`：391 个；
- `500-999 us`：29 个；
- 合计 `>=500 us`：420 个；
- 最大值：48,903 us。

按 opcode 分布如下：

| opcode | 操作 | >=1000 us | 500-999 us |
|---:|---|---:|---:|
| 0 | Query/Lookup | 6 | 20 |
| 1 | Put/Save | 16 | 6 |
| 2 | Get/Load | 369 | 3 |

`>=1 ms` 的 `remote_wait_us` 中，Get/Load 占 369/391，约 94.4%。

### 5.1 最大样本

日志特征：

```text
component=dramstore event=request_done
task_id=94 request_id=94 opcode=2 entries=108 status=SUCCESS
node_queue_us=4
control_submit_us=2
control_transfer_us=10
remote_wait_us=48903
total_us=48949
```

证据文件：[stress_b32_run01/offline_inference.log](core_extracted/query_handle_perf_20260824/runs/stress_b32_run01/offline_inference.log)，搜索 `task_id=94 request_id=94 opcode=2`。

这个请求在本地 DramStore 的 `control_submit_us + control_transfer_us` 只有 12 us，48.9 ms 几乎全部发生在等待远端 reply 的区间。因此，它不能解释为“DramStore 调用 HIXL 异步提交接口本身卡了 48 ms”。

同一批相邻 Get 请求的 `remote_wait_us` 连续增长：

```text
request 86 约 48.1 ms
request 91 约 48.4 ms
request 92 约 48.6 ms
request 93 约 48.7 ms
request 94 约 48.9 ms
```

这更符合突发请求在远端排队、被串行消化的形态，而不像单个 Get 请求独立执行了 49 ms。

结合已保存的 DramPool/HIXL 日志，`remote_wait_us` 内可能同时包括：

- RequestReceiver 或 TaskWorker FIFO 排队；
- 单 TaskWorker 串行处理；
- QueryHandle 受同一 HixlInstance 上其它 TransferAsync 等操作影响；
- HIXL 状态轮询或 reply 观察延迟；
- DramPool reply 发送及 DramStore reply polling。

但现有日志没有贯通 `DramStore peer + DramPool worker + HixlInstance local_index` 的统一 trace ID，因此不能把最大 48.9 ms 样本精确拆成上述每个子阶段各占多少时间。

## 6. `node_queue_us=82,898 us` 详细分析

最大 NodeActor 排队样本：

```text
component=dramstore event=request_done
task_id=66 request_id=92 opcode=1 entries=108 status=SUCCESS
node_queue_us=82898
reply_slot_wait_us=5
encode_us=4
control_submit_us=3
control_transfer_us=41
remote_wait_us=2209
total_us=85210
```

证据文件：[stress_b32_run01/offline_inference.log](core_extracted/query_handle_perf_20260824/runs/stress_b32_run01/offline_inference.log)，搜索 `task_id=66 request_id=92 opcode=1`。

该样本之前紧邻以下恢复日志：

```text
DramStore request timeout triggered node recovery
timed_out_requests=26 affected_requests=26 pending_requests=0

DramStore node recovery fence completed
active_requests=26
```

代码中，`node_queue_us` 是请求进入 NodeActor pending 队列到 `StartRequest()` 的间隔。NodeActor 只有处于 `ACTIVE`，并且 `activeRequests_.size() < maxInflightRequests` 时才会从 pending 队列派发请求。

相关代码：[node_actor.cc](core_extracted/nt/unified-cache-management/ucm/store/dram/cc/node_actor.cc)，参见 `DispatchPendingRequests()`、`StartRequest()` 和 `Advance()`。

因此，该样本能确定的是：请求已经进入 NodeActor，但约 82.9 ms 后才开始执行本地发送流程。它不是 HIXL 异步提交慢，因为随后的 `control_submit_us=3 us`、`control_transfer_us=41 us` 都很短。

该异常只有 1 个样本。现有日志不足以在以下原因中做唯一选择：

- fence recovery 后 NodeActor 状态或调度恢复延迟；
- NodeActor event loop 没有及时被唤醒；
- NodeActor 线程被操作系统抢占或发生 CPU starvation；
- 恢复后的 active request 清理/调度对下一请求产生影响。

## 7. 失败超时路径

失败超时不能与成功路径性能混算：

| 失败项 | 数量 | 最大值 | 原因 |
|---|---:|---:|---|
| request `total_us` | 62 | 3,203,055 us | 请求约 3 秒未收到 reply，触发 timeout，随后等待 fence recovery 完成 |
| task `requests_inflight_us` | 9 | 3,203,078 us | Task 等待超时 request 完成恢复 |
| task `total_us` | 9 | 3,203,193 us | 同上 |

其中一组典型过程为：

1. 26 个 request 发送后约 3 秒没有收到 reply；
2. NodeActor 输出 `request timeout triggered node recovery`；
3. 26 个已暴露请求进入 `WAITING_FENCE`；
4. fence recovery 约再耗费 189 ms；
5. request 最终以 `status_code=-50010` 完成，总耗时约 3.189 秒。

这些失败记录的 `reply_observed_ts_us=0`，所以派生出来的 `remote_wait_us=0` 只是“没有可用于计算结束点的 reply 时间戳”，并不表示没有远端等待。

证据文件仍为：[stress_b32_run01/offline_inference.log](core_extracted/query_handle_perf_20260824/runs/stress_b32_run01/offline_inference.log)，搜索 `request timeout triggered node recovery` 和 `status_code=-50010`。

## 8. 未超过 500 us 的 DramStore 本地阶段

| 阶段 | 成功路径最大值 |
|---|---:|
| `control_submit_us` | 9 us |
| `control_transfer_us` | 317 us |
| `reply_slot_wait_us` | 50 us |
| `encode_us` | 18 us |
| `reply_process_us` | 2 us |
| task `queue_us` | 160 us |
| task `route_us` | 79 us |

这组数据不支持“DramStore 本地异步提交接口是 >=500 us 正常长尾来源”的判断。

## 9. 结论与下一步日志建议

结论：

1. 正常路径的主要长尾是 `remote_wait_us`，且主要来自 opcode=2 Get/Load；问题范围位于 DramPool/HIXL/回复链路，而不是 DramStore 本地 submit 调用。
2. `node_queue_us=82,898 us` 是一次超时 fence recovery 之后的孤立 NodeActor 调度停顿。
3. request/task 的 `total_us` 和 `requests_inflight_us` 是上述长尾的传播，不能重复视为独立瓶颈。
4. 62 个失败 request 和 9 个失败 task 属于约 3 秒 timeout 加 fence recovery，需要作为可靠性问题单独分析。

下一轮建议给整条链路补统一字段：

```text
trace_id
dramstore_pid
dramstore_peer_id
request_id
drampool_receiver_id
taskworker_id
hixl_instance_local_index
hixl_device_id
```

并记录以下时间点：

```text
DramStore transmit completed
DramPool request observed
TaskWorker dequeued
QueryHandle begin/end
TransferAsync begin/end
GetTransferStatus begin/end
DramPool reply submitted/completed
DramStore reply observed
```

这样才能把 DramStore 的 `remote_wait_us` 精确拆分到 RequestReceiver、TaskWorker queue、QueryHandle、TransferAsync/poll 和 reply 路径。
