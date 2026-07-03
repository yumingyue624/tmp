# DramPool LOOKUP 流程设计

本章节基于时序图 `drampool_single_thread_lookup_sequence_v2.excalidraw`，描述 LOOKUP 请求在 DramPool 内部的处理流程、锁边界以及与 DUMP/LOAD 的差异。

## 组件职责

`DramPoolServer` 仍然负责请求接收、协议解包和 task 提交。LOOKUP 路径涉及的模块如下：

- `KvProtocol`：解析 DramStore 发来的 LOOKUP request，生成 `LookupTaskContext`。
- `TaskFlow`：单线程任务处理逻辑，负责执行 LOOKUP task。
- `MetadataIndex`：查询 `Entry`，校验 `READY` 状态和 TTL，计算 prefix hit count。
- `TransportMgr`：把 LOOKUP 结果写回 DramStore 的 flagbuffer。

LOOKUP 与 DUMP/LOAD 的差异是：

- LOOKUP 不搬 value 数据。
- LOOKUP 不分配 DramPool 本地 KVCache，所以 `BufferMgr` 不参与。
- LOOKUP 不等待 data write completion，也不改变 entry 状态，所以 `CompletionPoller` 不参与。
- LOOKUP 只查元数据，并把 prefix 命中结果写回 flagbuffer。

## LOOKUP 主流程

LOOKUP 请求同样分为同步接收阶段和异步执行阶段。

同步阶段：

```text
DramStore -> DramPoolServer:
  发送 LOOKUP request

DramPoolServer -> KvProtocol:
  解析请求 buffer

KvProtocol -> DramPoolServer:
  返回 LookupTaskContext

DramPoolServer -> TaskFlow:
  submit task
```

这段完成后，DramPoolServer 的接收路径结束，后续由内部 `TaskFlow` 单线程执行。

异步阶段：

```text
TaskFlow:
  dequeue LookupTaskContext

TaskFlow -> MetadataIndex:
  lookup keys
  校验 Entry state == READY
  校验 expire_at_ms > now_ms
  计算 prefix hit count

MetadataIndex -> TaskFlow:
  返回 prefix hit count

TaskFlow -> TransportMgr:
  写回 flagbuffer(idx)

TransportMgr -> TaskFlow:
  返回 submit status
```

时序图里的 `idx` 是协议 flagbuffer 中的字段名。为了避免语义歧义，DramPool 内部建议统一叫 `prefix_hit_count` 或 `hit_count`，最后写回协议字段时再赋值给 `idx`。

```text
flagbuffer.idx = prefix_hit_count
```

这样 `0` 表示 prefix 命中 0 个，不会和“命中第 0 个位置”混淆。

## Prefix Hit Count 语义

LOOKUP 的输入通常是一组按 DramStore/Scheduler 语义排列的 key。DramPool 按请求顺序检查，从第一个 key 开始连续命中多少个，就返回多少。

```text
keys = [k0, k1, k2, k3]

k0 HIT
k1 HIT
k2 MISS
k3 HIT

prefix_hit_count = 2
```

即使 `k3` 命中，也不能计入 prefix，因为 prefix 在 `k2` 处已经断开。

单个 key 的命中条件和 LOAD 一致：

```text
Entry 存在
Entry.state == READY
Entry.expire_at_ms > now_ms
```

以下情况都会终止 prefix：

```text
primary_index 中不存在
Entry.state == RESERVED
Entry 已过期
Entry 被 GC 删除
```

`RESERVED` 代表 DUMP 还没被 CompletionPoller 发布为 `READY`，LOOKUP 不能把它算作命中。

## MetadataIndex 接口

建议给 LOOKUP 单独提供接口，不要把 prefix 逻辑散在 TaskFlow 里：

```cpp
struct LookupPrefixResult {
    uint32_t prefix_hit_count;
    LookupStatus status;
};

LookupPrefixResult MetadataIndex::LookupPrefix(
    std::span<const Key> keys,
    uint64_t now_ms);
```

内部逻辑：

```text
count = 0

for key in keys:
  result = LookupOneReady(key, now_ms)
  if result != HIT:
      break

  update last_access_ms
  count++

return count
```

`last_access_ms` 可以在 LOOKUP 命中时更新，用于后续淘汰策略和观测。但它不能隐式延长 TTL，也就是不能修改 `expire_at_ms`。TTL 只由 DUMP 写入时的过期时间或后续明确的 refresh 协议决定。

## 锁边界

MetadataIndex 仍然按 shard 组织：

```cpp
struct MetadataShard {
    std::shared_mutex mu;

    PrimaryIndex primary_index;
    ExpireIndex expire_index;
    PositionIndex position_index;
};
```

LOOKUP 只需要查 `primary_index`，但仍然要和以下操作并发互斥：

- DUMP reserve 插入三套索引。
- CompletionPoller 把 entry 从 `RESERVED` 置为 `READY`。
- GC 删除 entry 并释放 buffer。
- Timeout/failure cleanup 删除 `RESERVED` entry。

第一版可以用简单可靠的写锁实现，因为 LOOKUP 命中会更新 `last_access_ms`：

```text
for key in keys:
  shard = ShardFor(key)

  unique_lock(shard.mu)
    find primary_index
    validate READY / TTL
    update last_access_ms
  unlock

  if not hit:
    break
```

这样每次只持有一个 shard 锁，不会出现跨 shard 加锁顺序问题。

如果后续 LOOKUP QPS 很高，可以优化为：

```text
shared_lock 查询 READY / TTL
last_access_ms 使用 atomic 更新
```

或者对 `last_access_ms` 做采样更新，例如每 N 次命中或每隔一段时间更新一次。

## 批量一致性

LOOKUP 的 prefix 计算默认是逐 key 的元数据快照，不要求整个 key 数组在同一个全局锁下完成。

这样设计的原因是：

- key 可能分布在多个 shard。
- 如果为了批量一致性同时锁多个 shard，需要排序加锁，代码复杂度更高。
- LOOKUP 只返回当前时刻的可见命中情况，不搬运 value，不改变 entry 生命周期。

因此第一版建议不要为了 LOOKUP 加全局锁。只要单个 key 的状态判断在 shard 锁内完成，就能保证不会读到半插入、半删除或 `RESERVED` 未完成状态。

如果未来协议要求“整批 key 在同一元数据版本下计算 prefix”，再引入全局 epoch 或按 shard id 排序后批量加锁。

## Transport 写回

LOOKUP 只需要写回 flagbuffer：

```text
flagbuffer.idx = prefix_hit_count
```

这里没有 value write，因此不需要像 LOAD 那样保证 “value write -> flagbuffer write” 的传输顺序。LOOKUP 的完成语义就是 flagbuffer 写回。

TransportMgr 返回 submit status 后，TaskFlow 可以结束本次 LOOKUP task：

```text
submit success:
  请求已交给 TransportMgr

submit failed:
  记录错误
  DramStore 侧可能按超时处理
```

如果后续 TransportMgr 对 flagbuffer 写也提供 async handle，LOOKUP 可以选择仍然不进入 CompletionPoller，因为它不需要改变 DramPool 本地元数据状态。只有在需要统计可靠完成、连接错误恢复或重试时，才需要为 LOOKUP 增加独立的 completion 处理。

## 与 GC 的关系

LOOKUP 不使用 entry 的 value buffer，因此它不需要像 LOAD 那样 pin buffer。GC 可以在 LOOKUP 两次 key 查询之间删除其他 entry；LOOKUP 只要保证当前 key 的判断在 shard 锁内完成即可。

GC 删除 entry 时必须持有对应 shard 写锁，并从三套索引中原子删除：

```text
primary_index
expire_index
position_index
```

LOOKUP 持有同一个 shard 锁查询 `primary_index`，所以不会看到索引删除到一半的状态。

## 失败处理

LOOKUP 失败主要分为两类：

```text
协议解析失败:
  同步阶段直接返回错误或丢弃请求，按 KvProtocol / TransportMgr 约定处理。

flagbuffer 写回失败:
  DramPool 本地元数据不需要回滚。
  记录错误，DramStore 侧按超时或连接错误处理。
```

LOOKUP miss、expired、reserved 都不是系统错误，只会影响 `prefix_hit_count`：

```text
prefix_hit_count = 已连续命中的 key 数量
```

## 可见性语义

LOOKUP 的可见性规则是：

```text
只有 READY 且未过期的 Entry 可以计入 prefix_hit_count。
```

`RESERVED` entry 对 LOOKUP 不可见。DUMP 完成发布仍然由 CompletionPoller 完成；LOOKUP 本身不发布新数据，也不修改 `RESERVED -> READY` 状态。

