# DramPool 流程设计

本章节基于最终时序图 `drampool_single_thread_task_sequence_v8.excalidraw`，描述 DUMP 请求在 DramPool 内部的主流程、异步完成路径以及相关锁边界。

## 组件职责

`DramPoolServer` 是 DramPool 进程的主流程骨架，负责模块初始化、请求接收、任务提交和线程生命周期管理。它内部主要串联以下模块：

- `KvProtocol`：负责解析 DramStore 发来的请求 buffer，生成 `DumpTaskContext`。
- `TaskFlow`：单线程任务处理逻辑，负责执行 DUMP task 的 reserve、分配、提交传输、注册 completion。
- `BufferMgr`：负责分配和释放 DramPool 本地 KVCache buffer。
- `MetadataIndex`：负责 `Entry` 生命周期和三套索引维护，包括 `primary_index`、`expire_index`、`position_index`。
- `TransportMgr`：负责提交单边写 KVCache 和单边写 flagbuffer 操作。
- `CompletionPoller`：独立线程，轮询 `TransportMgr` 返回的 async handle，并驱动 `Entry` 从 `RESERVED` 发布为 `READY`。

## DUMP 主流程

DUMP 请求分为同步接收阶段和异步执行阶段。

同步阶段只负责把请求接进 DramPool 并提交 task：

```text
DramStore -> DramPoolServer:
  发送 DUMP request

DramPoolServer -> KvProtocol:
  解析请求 buffer

KvProtocol -> DramPoolServer:
  返回 DumpTaskContext

DramPoolServer -> TaskFlow:
  submit task
```

这段完成后，DramPoolServer 的接收路径结束，后续由内部任务流推进。DramStore 侧的 send buffer 可以释放。

异步阶段由单线程 `TaskFlow` 执行：

```text
TaskFlow:
  dequeue DumpTaskContext

TaskFlow -> BufferMgr:
  分配 KVCache buffer

TaskFlow -> MetadataIndex:
  构造 Entry(state=RESERVED)
  插入 primary_index / expire_index / position_index

TaskFlow -> TransportMgr:
  提交单边写 KVCache 数据
  获取 async handle

TaskFlow -> TransportMgr:
  提交单边写 flagbuffer

TaskFlow -> CompletionPoller:
  Register InflightRecord(handle, key, generation)
```

`RESERVED` 的含义是“key 已经占位，但数据尚未被本地确认完成”。因此 `RESERVED` entry 不能被 lookup/load 命中。只有 `CompletionPoller` 确认 KVCache data handle 完成后，才能发布为 `READY`。

flagbuffer 写用于通知 DramStore，本地 `READY` 发布以 KVCache data handle 完成为准。TransportMgr 必须保证 KVCache data op 和 flagbuffer op 在同一个有序域内按顺序提交，通常是同一个 QP 或 provider 明确保证顺序的 ordered chain。这样 DramStore 看到 flag 时，data op 已经在传输层顺序上位于它之前。

## CompletionPoller

`CompletionPoller` 是独立线程，维护一个 pending queue。`TaskFlow` 新提交的 handle 会追加到队尾；如果 Poller 查询到队头 handle 还未完成，也会把它重新追加到队尾。

```cpp
struct InflightRecord {
    Key key;
    uint64_t generation;
    TransportHandle handle;
    BufferHandle buffer;
    uint64_t submit_ms;
};
```

轮询逻辑：

```text
while running:
  rec = pending_queue.pop_front()
  status = TransportMgr.QueryStatus(rec.handle)

  if status == WAITING:
      pending_queue.push_back(rec)
      continue

  if status == SUCCESS:
      MetadataIndex.MarkReady(rec.key, rec.generation)
      TransportMgr.ReleaseHandle(rec.handle)
      continue

  if status == FAILED or TIMEOUT:
      MetadataIndex.RemoveOrInvalidate(rec.key, rec.generation)
      BufferMgr.Free or quarantine(rec.buffer)
      TransportMgr.ReleaseHandle(rec.handle)
```

当前设计不做 backoff。只要 pending queue 非空，Poller 就持续轮询。pending queue 为空时可以等待新 handle 到来，或者采用轻量自旋；这不影响核心状态机。

## 锁与并发边界

虽然当前 `TaskFlow` 是单线程，`CompletionPoller` 也是单线程，但它们会并发访问两个共享结构：

```text
pending_queue
MetadataIndex
```

因此需要两个独立锁边界。

### Pending Queue 锁

pending queue 不是严格 SPSC 队列，因为存在两个写入方：

```text
TaskFlow:
  新 handle push_back

CompletionPoller:
  WAITING handle push_back
```

建议先使用 `std::mutex + std::deque<InflightRecord>`：

```cpp
class CompletionPoller {
private:
    std::mutex pending_mu_;
    std::deque<InflightRecord> pending_;
};
```

队列锁只保护 `pop_front` 和 `push_back`，不能覆盖 `TransportMgr.QueryStatus()`：

```text
lock pending_mu
  pop_front
unlock

QueryStatus(handle)

lock pending_mu
  if WAITING: push_back
unlock
```

不建议第一版实现 lock-free queue 或 CAS tail。`std::deque` 本身不是线程安全容器，只做 tail CAS 不能保证正确性；同时当前队列锁持有时间很短，不应成为主要瓶颈。

### MetadataIndex 锁

MetadataIndex 负责 Entry 和三套索引，建议按 shard 切分：

```cpp
struct MetadataShard {
    std::shared_mutex mu;

    PrimaryIndex primary_index;
    ExpireIndex expire_index;
    PositionIndex position_index;
};
```

所有会修改 Entry 或索引的操作必须持有对应 shard 的写锁：

```text
Reserve Entry
Insert primary / expire / position
MarkReady
Remove / timeout cleanup
GC evict
```

lookup 这类只读路径可以持有读锁。load 如果要异步使用 buffer，需要在锁内增加 `io_refcnt` 或 pin 住 buffer，再释放锁后提交 RDMA。

### Entry 状态与 generation

Entry 状态保持简单：

```cpp
enum class EntryState : uint8_t {
    RESERVED = 0,
    READY = 1,
};
```

`generation` 用于防 stale completion / ABA-like 场景：

```text
1. 老 Entry 因超时被删除，buffer 被隔离或释放。
2. 同 key 新 DUMP 请求到来，创建新 Entry，generation 增加。
3. 老 handle 的 completion 晚到。
4. CompletionPoller 必须通过 generation mismatch 识别它是旧 completion，不能把新 Entry 置 READY。
```

`MarkReady` 必须同时校验 key、generation 和当前状态：

```cpp
Status MetadataIndex::MarkReady(const Key& key, uint64_t generation)
{
    auto& shard = ShardFor(key);
    std::unique_lock lock(shard.mu);

    auto it = shard.primary_index.find(key);
    if (it == shard.primary_index.end()) {
        return Status::StaleCompletion;
    }

    Entry& entry = *it->second;
    if (entry.generation != generation) {
        return Status::StaleCompletion;
    }

    if (entry.state != EntryState::RESERVED) {
        return Status::InvalidState;
    }

    entry.state = EntryState::READY;
    return Status::Ok;
}
```

## 异常处理

### Submit 失败

如果 BufferMgr 分配成功，但 MetadataIndex reserve 或 TransportMgr submit 失败，需要回滚：

```text
删除 primary / expire / position
释放或隔离 buffer
写回失败状态到 flagbuffer
```

三套索引的插入和删除必须在同一个 shard 写锁内完成。

### QueryStatus 失败

如果 `QueryStatus(handle)` 返回终态失败，可以移除对应 `Entry`：

```text
MetadataIndex.Remove(key, generation)
BufferMgr.Free(buffer)
TransportMgr.ReleaseHandle(handle)
```

如果失败语义不能保证底层 DMA 已停止，不能立即复用 buffer，应放入 quarantine，等待 TransportMgr 给出 terminal completion 或 abort guarantee 后再释放。

### Timeout

Timeout 需要区别对待：

- 如果 TransportMgr 明确保证 timeout 后操作已终止，不会再写本地 buffer，可以删除元数据并释放 buffer。
- 如果 timeout 只是“本轮等待超时”，但 RDMA 可能仍在飞，应删除或失效元数据，同时将 buffer 隔离，避免被复用后被迟到 DMA 覆盖。

迟到 completion 到来时，CompletionPoller 会根据 key/generation 判断为 stale completion 并忽略。

## 可见性语义

DUMP 的可见性以 Entry 状态为准：

```text
RESERVED:
  key 已占位，防止重复 DUMP。
  lookup/load 不命中。

READY:
  KVCache data 已经完成，lookup/load 可命中。
```

flagbuffer 的作用是通知 DramStore 本次 DUMP 请求完成；它不直接决定 DramPool 本地 lookup/load 的可见性。DramPool 本地可见性由 `CompletionPoller -> MetadataIndex.MarkReady` 发布。
