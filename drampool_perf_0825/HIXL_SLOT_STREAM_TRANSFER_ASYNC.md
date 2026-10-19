# HIXL Slot、ACL Stream、Warmup 与 TransferAsync 执行流程

本文根据本地保存的 HIXL 插桩源码和 2026-08-24 实测日志整理，目标是说明：

- `TransferSlotPool`、`SlotHandle`、`CommChannel::active_slot_` 和 `aclrtStream` 分别是什么。
- slot 第一次初始化为什么慢，warmup 后为什么变快。
- `TransferAsync` 从 CPU 收到调用到异步 handle 返回经历了哪些阶段。
- `HcclBatchGet/Put` 返回、数据传输完成和 completion flag 之间的关系。
- 增加并发是否能解决 `TransferAsync` 提交耗时。

## 1. 对象关系

### 1.1 TransferSlotPool

每个 `AdxlInnerEngine` 创建一个设备级 `TransferSlotPool`：

```cpp
constexpr size_t kMaxStreams = 512;
slot_pool_ = llm::MakeUnique<TransferSlotPool>(device_id, kMaxStreams);
```

它负责为这个 HIXL Engine 所绑定的 Device 管理可复用的 ACL 执行资源，当前上限是 512 套。

这个 `512` 是 slot/stream 资源池容量，不是 DramStore 单 request 的 key 数量限制，也不是允许同时执行 512 个 DramPool request 的承诺。实际并发还会受到 HIXL worker、`CommChannel` 锁、HCCL Runtime 和硬件资源限制。

### 1.2 SlotHandle

`SlotHandle` 不是 KV cache slot，也不是请求数据缓冲区。它是一套 ACL 异步执行资源的句柄。

根据当前 `comm_channel.cc` 中实际访问的成员，可以确认它至少包含：

```cpp
struct SlotHandle {
    aclrtContext ctx;       // ACL Runtime Context
    aclrtStream stream;     // 在该 Context 下使用的异步任务队列
    void *dev_const_one;    // Device 上保存 uint64_t(1) 的内存
    // TransferSlotPool 的其他内部管理字段没有包含在本地备份源码中
};
```

三个成员的用途：

| 成员 | 用途 |
|---|---|
| `ctx` | 让调用线程进入正确的 Device Runtime 上下文 |
| `stream` | 顺序承载 HCCL Get/Put 和 completion flag memcpy |
| `dev_const_one` | 在 stream 尾部异步复制到 Host，表示此前任务已经完成 |

### 1.3 CommChannel::active_slot_

每个 `CommChannel` 保存：

```cpp
std::shared_ptr<SlotHandle> active_slot_;
```

第一次提交时从 pool 获取 slot：

```text
CommChannel::AcquireSharedSlot
  -> TransferSlotPool::Acquire
       -> TransferSlotPool::InitSlotLocked
```

只要 `active_slot_` 仍然存在，后续请求直接复用：

```cpp
if (active_slot_ != nullptr) {
    slot_out = active_slot_;
    return SUCCESS;
}
```

因此典型关系是：

```text
一个 HIXL AdxlInnerEngine / Device
└─ TransferSlotPool（上限 512）
   ├─ Slot A：Context A + Stream A + dev_const_one A
   │    └─ CommChannel(peer A)::active_slot_
   ├─ Slot B：Context B + Stream B + dev_const_one B
   │    └─ CommChannel(peer B)::active_slot_
   └─ ...
```

同一个 `CommChannel` 的 inflight 请求共享 slot、Context 和 stream，但每个请求拥有独立的 `host_flag`：

```text
CommChannel::active_slot_
├─ ctx                         共享
├─ stream                      共享
├─ dev_const_one               共享，只读
├─ AsyncRecord(request A)
│    └─ host_flag_A            独立
└─ AsyncRecord(request B)
     └─ host_flag_B            独立
```

`shared_ptr` 让每个 inflight `AsyncRecord` 持有 slot 引用，避免请求尚未完成时 Context/stream 被回收到 pool。

## 2. ACL Stream 是什么

`aclrtStream` 是 Ascend ACL Runtime 提供的异步任务执行序列，不是 CPU 工作线程，也不能简单等同于一个固定的 Device 线程。

CPU 可以向 stream 提交：

- HCCL 通信操作。
- Device kernel。
- Host/Device 内存拷贝。
- Event 或其他 Runtime 任务。

同一条 stream 内存在顺序保证：后提交的任务不会越过前面任务完成。不同 stream 可以并行或交错执行，但是否真正并行取决于 Runtime、HCCL 和硬件资源。

HIXL 当前在同一个 slot stream 上提交：

```text
slot->stream
----------------------------------------------------------------->
HcclBatchGet/Put batch 1
        -> HcclBatchGet/Put batch 2（描述符超过 256 时）
                -> aclrtMemcpyAsync(Device 1 -> Host flag)
```

## 3. Slot 第一次初始化

本文后续不再笼统使用“非 warmup”描述所有慢请求，因为这里至少存在两个彼此独立的冷启动层次：

```text
第一层：slot/context/stream 冷
  -> AcquireSharedSlot / InitSlotLocked 慢

第二层：slot 已经存在，但 HCCL 提交路径冷
  -> HcclBatchGet/Put 首调比稳定态慢

第三层：slot 和 HCCL 都已 warm
  -> slot_us 接近 0，HcclBatchGet/Put 成为同步提交阶段的主要耗时
```

因此，“哪个阶段最慢”必须同时说明测量范围和冷启动层次。

当前调用链和已确认 API 为：

```text
CommChannel::AcquireSharedSlot
  -> TransferSlotPool::Acquire
       -> TransferSlotPool::InitSlotLocked
            -> aclrtCreateContext
            -> aclrtCtxGetCurrentDefaultStream
            -> aclrtSetStreamFailureMode
```

它至少完成：

1. 为指定 Device 创建 ACL Context。
2. 获取该 Context 的 default stream。
3. 设置 stream failure mode。
4. 准备 slot 关联的 completion 辅助资源，例如 Device 常量 `1`。

当前日志只测量了 `AcquireSharedSlot` 整体，没有继续进入 `InitSlotLocked` 分别包围上述 ACL API。因此可以确认“slot 初始化整体慢”，但不能从现有日志断言三个 ACL API 中具体哪一个最慢。

### 3.1 冷启动数据

实测第一次使用新 peer/channel 时：

| 场景 | `slot_us` | `HcclBatchGet/Put` | 结论 |
|---|---:|---:|---|
| 首次 1 descriptor | 1458 us | 288 us | slot 初始化占绝对多数 |
| 首次 108 descriptors | 1486 us | 605 us | slot 初始化仍是最大单项 |

所以，对于“新 peer/channel 第一次创建 slot”的完整 `CommChannel::TransferAsync`：

> 当前列出的冷 slot 样本中，最大的阶段是 `AcquireSharedSlot -> InitSlotLocked`，而不是 `HcclBatchGet/Put`。

这不表示所有被称为“非 warmup”的请求都必然由 slot 主导。如果 slot 已经存在、只是 HCCL 提交路径尚未稳定，那么 `slot_us` 已经接近 0，此时 `HcclBatchGet/Put` 会成为最大项。

### 3.2 Warmup 后

当 `CommChannel::active_slot_` 已经建立后，`AcquireSharedSlot()` 只复制一个 `shared_ptr`：

| 阶段 | warmup 后典型耗时 |
|---|---:|
| `mutex_wait_us` | 0-1 us（无竞争时） |
| `slot_us` | 0-3 us |
| `context_us` | 0-1 us |
| `host_flag_us` | 0-2 us |
| `record_us` | 0-1 us |

108 descriptors 的一个代表样本：

```text
HcclBatchGet/Put                 181 us
IssueHcclBatch total            215 us
TemporaryRtContext                1 us
aclrtMemcpyAsync 提交             7 us
IssueAsyncBatchWithHostFlag     225 us
CommChannel::TransferAsync      230 us
CommEngine::TransferAsync       238 us
TransportManager submit         269 us
```

warmup 后 slot 不再是瓶颈，CPU 同步提交时间主要落在 `HcclBatchGet/Put` API 内。

## 4. Warmup 到底 warm 了什么

这里的 warmup 不是把业务数据预先传一遍这么简单，它至少消除了以下首次成本：

1. 新 peer/channel 第一次从 `TransferSlotPool` 获取资源。
2. `aclrtCreateContext` 等 slot 惰性初始化。
3. default stream 获取和 failure mode 设置。
4. HCCL Get/Put 首次调用涉及的 Runtime/HCCL 内部初始化和代码路径预热。
5. 部分内存分配、页映射、动态加载或内部缓存的首次成本。

当前日志能够直接证明第 1-4 项存在首次/复用差异；第 5 项属于常见 Runtime 行为，但尚未用当前日志逐项拆分。

推荐将 warmup 定义为“每个实际会使用的 HIXL Engine、peer/channel 和传输方向至少完成一次代表性请求”，而不是只对进程进行一次全局空调用。

## 5. TransferAsync 完整步骤

```text
CommChannel::TransferAsync
  1. 获取 device_launch_mu_
  2. 检查 CommChannel/HCCL comm 是否可用
  3. 计算 request id、字节数、descriptor 数量
  4. AcquireSharedSlot
  5. AcquireHostFlag
  6. 写入 req_2_async_record_
  7. IssueAsyncBatchWithHostFlag
       7.1 TemporaryRtContext(slot->ctx)
       7.2 IssueHcclBatch
            7.2.1 TransferOpDesc -> HcclOneSideOpDesc
            7.2.2 每 256 descriptors 执行一次 Flush
            7.2.3 READ -> HcclBatchGet
                  其他 -> HcclBatchPut
       7.3 aclrtMemcpyAsync(Device 1 -> Host flag, same stream)
  8. 返回异步 TransferReq/handle
```

### 5.1 获取 device_launch_mu_

```cpp
std::lock_guard<std::mutex> launch_lock(device_launch_mu_);
```

它串行保护当前 `CommChannel` 的 Device 提交路径。`GetTransferStatus()` 也获取同一把锁。

因此，同一 `CommChannel` 上增加调用线程不会让多个 `TransferAsync` 同时进入提交临界区；竞争时反而会增加 `mutex_wait_us`，并可能让 Query 等待 TransferAsync 退出。

### 5.2 AcquireSharedSlot

- 首次：从 pool 获取并初始化 Context/stream，约 1.3-1.5 ms。
- warmup 后：复用 `active_slot_`，约 0-3 us。

### 5.3 AcquireHostFlag

每个 request 单独获得 Host completion flag。它在提交前处于未完成状态，Device 在 stream 执行到队尾时把它写成 `1`。

### 5.4 注册 AsyncRecord

在 `req_2_async_record_` 保存：

```text
request id
slot shared_ptr
host_flag
transfer_start
transfer_bytes
op_desc_count
```

注册发生在 Device 提交之前，确保提交成功后异步资源具有完整生命周期；提交失败则由失败清理路径统一回收。

### 5.5 TemporaryRtContext

```cpp
hixl::TemporaryRtContext ctx_guard(slot->ctx);
```

它让当前 CPU 调用线程进入 slot 对应的 ACL Context，作用域结束时恢复原 Context。warmup 后该步骤通常只有 0-1 us，它不会重复创建 Context。

### 5.6 IssueHcclBatch

HIXL 将每个 `TransferOpDesc` 转成：

```cpp
HcclOneSideOpDesc {
    localAddr,
    remoteAddr,
    count,
    HCCL_DATA_TYPE_UINT8
};
```

当前 `kMaxOpDescNum = 256`，所以 512 descriptors 会拆为两个 `HcclBatchGet/Put(desc_num=256)`，最后再追加一个 completion flag。

`BufferedTransfer::Put()` 当前还为每个 descriptor 执行一条 `LLMLOGI("Batch transfer sync ...")`。这部分属于 HCCL API 外围成本；如果生产日志级别实际输出这些日志，会随 descriptor 数量增加，应当单独评估或关闭逐 descriptor INFO 日志。

### 5.7 HcclBatchGet/Put 返回的含义

HIXL 把它当作 stream 异步提交接口使用。API 返回表示对应的 HCCL 工作已经成功提交/接受，不表示远端数据已经传输完成。

可以把它概念化为：

1. 校验和解析 descriptors。
2. 构造 HCCL/AICPU/Runtime 所需任务。
3. 将任务下发到 `slot->stream` 对应的执行路径。
4. 返回 CPU 调用线程。

但是当前插桩只测了整个 `HcclBatchGet/Put` API：

```text
hccl_begin -> HcclBatchGet/Put(...) -> hccl_done
```

所以现阶段只能确认“时间发生在 HCCL API 内”，不能用已有日志精确区分上述第 1、2、3 步各占多少。

基于 108 descriptors warm 样本，descriptor 转换及外围代码约为：

```text
IssueHcclBatch total 215 us - HCCL API 181 us = 34 us
```

因此大头确实在 HCCL API 内部，而不是 HIXL 的 descriptor vector 转换。但“HCCL API 内部究竟是校验、任务构造、AICPU unfold，还是 Runtime 队列/资源等待”需要 HCCL/ACL profiler 或更底层插桩才能定论。根据这种批量 API 的行为，任务构造和 Runtime 下发比纯参数校验更可疑，但这目前是推断，不是日志已证实结论。

### 5.8 提交 completion flag

```cpp
aclrtMemcpyAsync(
    host_flag,
    sizeof(uint64_t),
    slot->dev_const_one,
    sizeof(uint64_t),
    ACL_MEMCPY_DEVICE_TO_HOST,
    slot->stream);
```

它不是复制业务数据，而是在同一条 stream 的尾部追加一个 8 字节完成标记：

```text
HCCL batch 1 -> HCCL batch 2 -> ... -> Device(1) 拷贝到 host_flag
```

`flag_memcpy_us` 只测量 `aclrtMemcpyAsync` 的 CPU API 提交耗时，一般约 5-15 us；它不包含等待 HCCL 完成以及 Device 实际执行 D2H memcpy 的时间。

### 5.9 TransferAsync 返回

返回仅表示：

> HCCL 操作和 completion flag 操作已经成功提交。

随后 `CompletionPoller -> GetTransferStatus()` 在 CPU 上读取 `host_flag`：

```text
host_flag != 1 -> WAITING
host_flag == 1 -> COMPLETED，并释放 AsyncRecord/host flag/slot 引用
```

## 6. 不同冷启动层次究竟哪里最慢

需要区分两个范围。

### 6.1 新 peer/channel 第一次创建 slot：看整个 CommChannel::TransferAsync

当前冷 slot 样本的最大单项是：

```text
AcquireSharedSlot -> TransferSlotPool::InitSlotLocked
```

证据是首次 108 descriptors：

```text
slot_us       = 1486 us
hccl_us       =  605 us
issue_us      =  652 us
TransferAsync = 2147 us
```

因此，不能把“包含 slot 首次创建”的整次冷调用主要原因概括为 `HcclBatchGet/Put`。

### 6.2 Slot 已存在或只看 IssueAsyncBatchWithHostFlag 内部

`IssueAsyncBatchWithHostFlag()` 的入口位于 `AcquireSharedSlot()` 之后，所以它的计时范围本来就不包含 slot 初始化。进入这个函数以后，最大项是：

```text
IssueHcclBatch -> HcclBatchGet/Put
```

无论冷、暖样本，`TemporaryRtContext` 和 `aclrtMemcpyAsync` 的 CPU 提交耗时通常远小于 HCCL API。

这正是“HCCL Batch Get/Put 最慢”这个结论成立的准确范围：

> 在 slot 已经取得之后的异步 issue 阶段，或者在 slot 已 warm 的整次 TransferAsync 中，主要同步耗时是 `HcclBatchGet/Put`。

## 7. 增加并发能否解决

### 7.1 不能降低单次请求的 HCCL API 耗时

增加线程不会让一次 `HcclBatchGet/Put` 自身从 181 us 自动变短。它最多利用其他线程/stream 做重叠，提高系统吞吐量；单请求提交延迟仍然存在。

### 7.2 同一个 CommChannel 增加并发通常无效

同一个 `CommChannel`：

- 共享 `active_slot_->stream`。
- `TransferAsync()` 和 `GetTransferStatus()` 共享 `device_launch_mu_`。
- stream 内任务按提交顺序执行。

所以增加 TaskWorker/调用线程只会在 mutex 前排队：

```text
线程 A：TransferAsync ----持有锁---->
线程 B：TransferAsync --等待-------->
Poller ：Query         --等待-------->
```

这不能解决单 channel 延迟，反而可能增加 `mutex_wait_us` 和 Query 抖动。

### 7.3 不同 CommChannel/slot/stream 可能提高吞吐量

如果请求能够分散到不同：

- `CommChannel`
- `active_slot_`
- ACL stream
- HIXL worker 或提交 shard

那么 HCCL/Device 有机会重叠处理。但仍需满足：

1. 上层不能继续只有一个 HIXL worker 串行执行所有调用。
2. 锁不能是 Engine 级全局锁。
3. HCCL Runtime 支持这些 stream 的并行提交/执行。
4. Device、链路、AICPU 和 DMA 没有已经饱和。

这类并发优化目标是吞吐量和队头阻塞，不是直接消除 slot 冷初始化或降低单次 HCCL API 延迟。

### 7.4 冷 slot 应优先采用预热，不是增加业务并发

更直接的方案是：

1. 服务启动后提前建立实际需要的 peer/channel。
2. 为每个 channel 提前 `AcquireSharedSlot`。
3. 每个传输方向提交一次小型或代表性 HCCL 请求。
4. warmup 完成后再接收正式流量。

用正式业务并发触发惰性初始化，会把 1.3-1.5 ms 首次成本暴露给用户请求；多个 channel 同时冷启动还可能竞争 ACL/HCCL 内部全局资源。

### 7.5 HCCL 提交阶段优先考虑的优化

按当前证据，建议优先级为：

1. 用 ACL/HCCL profiler 或更底层打点拆分 `HcclBatchGet/Put` 内部时间。
2. 移除或降低 `BufferedTransfer::Put()` 中逐 descriptor 的 INFO 日志。
3. 在 HCCL 接口允许范围内增大有效 batch，摊薄每次 API 固定提交成本。
4. 检查 256 descriptor 拆批是否符合当前 HCCL 版本上限；不要未经验证直接突破接口限制。
5. 将不同 channel 分片到多个提交 worker，避免一个 HIXL worker 串行阻塞所有 channel。
6. 保持同一 channel 内有序串行，避免为了并发破坏 completion flag 与 stream 的顺序语义。

## 8. 最终结论

1. `SlotHandle` 是 Context、stream 和 completion 辅助资源的组合，不是 KV 数据 slot。
2. 新 peer/channel 第一次创建 slot 的完整 `TransferAsync` 中，当前冷 slot 样本的最大项是 slot 惰性初始化，实测约 1.3-1.5 ms。
3. slot 已存在后，或者只观察 `IssueAsyncBatchWithHostFlag` 的内部范围，CPU 同步提交耗时主要在 `HcclBatchGet/Put`；完全 warm 的 108 descriptors 代表值约 181 us。
4. 当前日志无法继续拆分 HCCL API 内部校验、任务构造、AICPU/Runtime 下发各自占比；认为任务构造/下发更可疑属于合理推断，但尚未证实。
5. 同一 `CommChannel` 增加调用并发无法解决问题，因为共享锁和 stream；它还可能扩大 Query 等待。
6. 不同 channel 使用不同 slot/stream 并配合多提交 worker，可能提高总吞吐量，但不能直接降低单次 HCCL API 延迟。
7. slot 冷启动应通过显式 channel/slot warmup 消除；HCCL 提交成本应通过 profiler、减少逐 descriptor 日志、合理 batching 和跨 channel 分片分别处理。
