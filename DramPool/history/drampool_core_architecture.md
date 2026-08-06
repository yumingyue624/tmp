# DramPool 核心架构设计

本文描述当前 DramPool 进程的核心架构草案。范围只覆盖 DramPool 内部主流程骨架，不覆盖 DramStore 侧 DHT、StoreDispatcher 分流、PosixStore 内部逻辑，也不展开 KVProtocol、BufferMgr、TransportMgr 的内部实现。

当前设计对应最新 v10 时序图：

- `D:\workspace\tmp\DramPool\drampool_single_thread_task_sequence_v10.excalidraw`
- `D:\workspace\tmp\DramPool\drampool_single_thread_load_sequence_v10.excalidraw`

## 1. 设计目标

DramPool 是每个节点一个独立进程，负责管理本节点 DRAM 池化 KVCache 数据和元数据。DramPool 接收来自多个 DramStore 进程的请求，完成 DUMP、LOAD、LOOKUP 三类操作。

核心目标：

- 将 `KvProtocol`、`BufferMgr`、`TransportMgr` 串成完整可运行流程。
- DramPool 本地元数据由 DramPool 进程统一管理。
- DUMP/LOAD 的数据搬运异步提交给 `TransportMgr`。
- CompletionPoller 统一轮询 transport handle。
- flagbuffer 按 request 粒度写回，一次 request 只写一次最终结果。
- GC 是独立线程，和正常请求路径通过 MetadataIndex 的锁边界隔离。

## 2. 总体线程模型

当前核心模型采用四类线程：

```text
Receiver
  -> request_queue
  -> TaskWorker
  -> new_handle_queue
  -> CompletionPoller

GCThread
  -> 独立扫描 MetadataIndex
```

### Receiver

Receiver 只负责请求入口：

- 从 TransportMgr 接收请求。
- 调用 KvProtocol 解包。
- 构造结构化 `RequestTask`。
- 将请求放入 `request_queue`。

Receiver 不做元数据修改，不做 BufferMgr 分配，不提交数据搬运任务。

当前假设 Receiver 是单生产者，因此：

```text
request_queue = SpscRingQueue<RequestTask>
producer = Receiver
consumer = TaskWorker
```

如果后续 Receiver 变成多个线程，`request_queue` 不能继续直接使用单条 SPSC，需要改为 MPSC，或者每个 Receiver 一条 SPSC。

### TaskWorker

TaskWorker 是当前正常请求路径唯一执行线程，负责：

- 从 `request_queue` 取 request。
- 访问 MetadataIndex。
- DUMP 时分配 BufferMgr 内存并创建 Entry。
- LOAD 时 lookup 并 pin Entry。
- 调用 TransportMgr 提交异步数据搬运任务。
- 将返回的 transport handle 放入 `new_handle_queue`。

当前核心前提：**全局只有一个 TaskWorker**。

这个前提简化了并发插入问题：不会出现多个 TaskWorker 同时插入同一个 key。但 GC 和 CompletionPoller 仍然会并发访问 Entry，所以 MetadataIndex 仍然需要严格锁边界。

### CompletionPoller

CompletionPoller 是独立轮询线程，负责：

- 从 `new_handle_queue` 取新 handle。
- 维护自己的本地 `pending_`。
- 每轮扫描 `pending_` 头部最多 64 个 handle。
- terminal handle 完成后，更新 Entry / RequestContext。
- request 全部子任务完成后，写一次 flagbuffer。

当前模型固定为：

```text
new_handle_queue:
  SpscRingQueue<InflightRecord>
  TaskWorker -> CompletionPoller

pending_:
  CompletionPoller 本地 deque
  容量 max_pending

CompletionPoller loop:
  DrainNewHandles(<=64)
  PollFirst64()
```

其中：

- `DrainNewHandles(<=64)`：从 SPSC 最多取 64 个新 handle，append 到 `pending_`。
- `PollFirst64()`：从 `pending_` 头部最多扫描 64 个 handle。
- `WAITING`：留在 `pending_` 原位置。
- `SUCCESS/FAILED/TIMEOUT`：从 `pending_` 删除，并执行 completion 处理。

这里 64 是每轮 drain/poll budget，不是 pending 容量。`pending_` 容量应由 `max_pending` 或 `max_inflight_handles` 控制。

### GCThread

GC 是独立线程，后续由其他同事实现。DramPool 核心架构只预留 MetadataIndex 删除接口和锁边界。

GC 只能删除满足条件的 Entry：

```text
state == READY
refcnt == 0
io_refcnt == 0
expired 或被淘汰策略选中
```

GC 不允许删除：

- `RESERVED` Entry。
- 正在被 LOAD pin 的 Entry。
- 正在被 DUMP/LOAD transport handle 引用的 Entry。

## 3. 核心模块

### DramPoolServer

`DramPoolServer` 是进程主骨架，负责：

- 解析配置。
- 初始化 KvProtocol、BufferMgr、TransportMgr、MetadataIndex。
- 启动 Receiver、TaskWorker、CompletionPoller、GCThread。
- 处理进程 shutdown。

### KvProtocol

负责协议编解码：

- Receiver 调用 KvProtocol 解包 request。
- TaskWorker 或 CompletionPoller 根据最终结果构造 flagbuffer 写回内容。

KvProtocol 不持有元数据，不参与 Entry 生命周期管理。

### BufferMgr

负责 DramPool 本地 KVCache 内存：

- DUMP miss key 时分配 buffer。
- DUMP 失败或回滚时释放 buffer。
- GC 删除 Entry 时释放 buffer。

LOAD 不分配新 buffer，只通过 Entry 定位已有 buffer。

### MetadataIndex

负责 key 到 Entry 的索引和淘汰辅助索引。当前按 shard 拆分：

```text
MetadataIndex
  Shard[0]
    primary: key -> Entry
    expire_index
    position_index
    rwlock

  Shard[1]
    ...
```

当前不维护全局最早过期节点。GC 按 shard 扫描或按 shard 内策略淘汰。

### TransportMgr

负责所有传输操作：

- 请求接收。
- DUMP 数据搬运。
- LOAD 数据搬运。
- flagbuffer 写回。
- handle 状态查询。

DramPool 不关心 TransportMgr 内部使用 TCP、RDMA send/recv 还是 RDMA read/write。DramPool 只依赖抽象语义：

```text
SubmitDataOp(...) -> TransportHandle
QueryStatus(handle) -> WAITING / SUCCESS / FAILED / TIMEOUT
WriteFlagBuffer(...)
```

## 4. 核心数据结构

### RequestTask

`RequestTask` 是 Receiver 解包后投递给 TaskWorker 的结构化请求。

包含：

- request id。
- op type：DUMP / LOAD / LOOKUP。
- 原始 key 列表和每个 key 的协议参数。
- flagbuffer 目标地址或句柄。
- 请求级上下文 `RequestContext`。

Receiver 只传结构化 request，不透传可复用 receive buffer 指针。

### RequestContext

`RequestContext` 是 request 级 fan-in 状态，用于保证 flagbuffer 只写一次。

核心字段：

```text
request_id
op_type
flagbuffer target
remaining
failed
first_error
result fields
```

DUMP/LOAD 的每个 key 子任务 terminal 后都会更新 `remaining`。当 `remaining == 0` 时，由触发归零的 completion 路径写一次 flagbuffer。

如果某个子任务失败：

- `failed = true`。
- 后续尚未提交的子任务执行前检查 failed 标记，可以跳过。
- 已经提交给 TransportMgr 的 handle 必须继续 drain 到 terminal，不能丢。

### InflightRecord

`InflightRecord` 是 TaskWorker 提交 TransportMgr 后传给 CompletionPoller 的对象。

DUMP 典型内容：

```text
op = DUMP
key
handle
request_ctx
buffer handle
entry ref
```

LOAD 典型内容：

```text
op = LOAD
key
handle
request_ctx
entry ref
```

当前 v10 图不强调 `generation`。这个设计成立的前提是：inflight Entry 不会在 handle terminal 前被删除并允许同 key 重新插入。若后续 timeout 路径允许删除 inflight Entry，则 `InflightRecord` 必须带 `generation`，否则会出现 stale completion 错改新 Entry 的问题。

### Entry

Entry 是 DramPool 元数据核心对象。

建议字段：

```text
key
buffer handle / local addr / length
expire_at_ms
last_access_ms
abs_pos
class_id
state: RESERVED / READY
refcnt
io_refcnt
generation
```

字段语义：

- `RESERVED`：DUMP 已占位，数据尚未完成，不允许 LOOKUP/LOAD 命中。
- `READY`：数据完整，可以 LOOKUP/LOAD 命中。
- `refcnt`：LOAD pin 或其他读侧引用计数，防止 GC 删除。
- `io_refcnt`：transport inflight 引用计数，防止 GC 删除正在被 RDMA 使用的 Entry。
- `generation`：防 stale completion，是否进入 InflightRecord 取决于 timeout/删除语义。

## 5. 锁模型

### 锁职责

每个 Metadata shard 有一把 rwlock：

```text
shard.rwlock 保护：
  primary index
  expire_index
  position_index
  Entry 对象生命周期
```

每个 Entry 有一把轻量锁：

```text
entry.spinlock 保护：
  state
  refcnt
  io_refcnt
  last_access_ms
  generation 校验相关状态
```

固定锁顺序：

```text
先 shard.rwlock
再 entry.spinlock
```

任何路径不得反向加锁。

### TaskWorker 与 MetadataIndex

TaskWorker 是唯一插入线程，所以不会出现多个 TaskWorker 并发插入同 key。

DUMP 流程里：

1. 持 shard 读锁检查 key 是否存在。
2. 不存在的 key 进入待写列表。
3. 分配 buffer。
4. 持 shard 写锁插入 Entry 和三套索引。

虽然当前只有一个 TaskWorker，写锁插入时仍建议做 debug/assert 检查，防止未来多 worker 或异常路径破坏不变量。

### CompletionPoller 与 MetadataIndex

CompletionPoller 只处理 terminal handle：

- DUMP SUCCESS：Entry `RESERVED -> READY`。
- DUMP FAILED/TIMEOUT：删除 Entry，释放 buffer，记录 request failed。
- LOAD SUCCESS/FAILED：Entry 保持 READY，释放 pin。

CompletionPoller 查找和修改 Entry 时必须持有 shard 读锁或写锁，且不能在释放 shard lock 后继续使用 Entry 指针。

DUMP SUCCESS 需要修改 `state`，可采用：

```text
shard 读锁 + entry spinlock
```

前提是只改 Entry 内部字段，不动三套索引。

DUMP FAILED/TIMEOUT 如果要删除 Entry，必须持 shard 写锁。

### GC 与 MetadataIndex

GC 删除 Entry 必须持 shard 写锁。这样可以保证：

- CompletionPoller 持 shard 读锁时，GC 不能删除 Entry。
- TaskWorker 插入 Entry 时，GC 不能并发破坏三套索引。

GC 删除前必须检查：

```text
state == READY
refcnt == 0
io_refcnt == 0
```

## 6. DUMP 主流程

DUMP 处理目标：将 DramStore 侧 value 数据搬运到 DramPool 本地 buffer，成功后发布 Entry 为 READY，request 完成后写一次 flagbuffer。

### 同步入口

```text
DramStore -> Receiver:
  DUMP request

Receiver -> KvProtocol:
  解包

Receiver -> request_queue:
  RequestTask(op=DUMP)
```

Receiver 投递完成后，同步入口结束。

### TaskWorker

TaskWorker 从 request_queue 取出 DUMP request。

对 request 内 key 逐个处理：

1. 持 shard 读锁查询 key 是否存在。
2. 已存在：本 key 记录 duplicate/skip。
3. 不存在：加入待写任务。
4. 对待写 key 分配 BufferMgr 内存。
5. 构造 Entry，`state = RESERVED`。
6. 持 shard 写锁插入 primary / expire_index / position_index。
7. 调用 TransportMgr 提交数据搬运任务。
8. 收到 handle 后构造 InflightRecord。
9. 将 InflightRecord 放入 `new_handle_queue`。

如果 request 中任意 key 在提交前失败：

- 标记 `request_ctx.failed = true`。
- 后续未提交 key 执行前检查 failed，直接 skip。
- 已提交 handle 继续由 CompletionPoller drain。

### CompletionPoller

CompletionPoller 每轮：

```text
DrainNewHandles(<=64)
PollFirst64()
```

DUMP handle terminal 后：

- SUCCESS：
  - 从 `pending_` 删除。
  - 持对应 shard 锁找到 Entry。
  - 将 Entry 从 `RESERVED` 改为 `READY`。
  - 更新 request_ctx。

- FAILED/TIMEOUT：
  - 从 `pending_` 删除。
  - 记录 request failed。
  - 删除 Entry，释放 buffer。
  - 更新 request_ctx。

当 request_ctx 收集完全部子任务：

- 全部成功：写一次 flagbuffer OK。
- 有失败：写一次 flagbuffer FAILED。

## 7. LOAD 主流程

LOAD 处理目标：从 DramPool 已有 READY Entry 中读取 value，写回 DramStore 侧目标 buffer。LOAD 不创建新 Entry，不改变数据内容。

### 同步入口

```text
DramStore -> Receiver:
  LOAD request

Receiver -> KvProtocol:
  解包

Receiver -> request_queue:
  RequestTask(op=LOAD)
```

### TaskWorker

TaskWorker 逐 key 执行 LookupAndPin：

1. 按 key 找到 metadata shard。
2. 持 shard 读锁查 primary。
3. 检查 Entry 是否存在、`state == READY`、未过期。
4. 持 entry spinlock 增加 `refcnt`。
5. 对命中的 key 生成待传输任务。
6. 调用 TransportMgr 提交 value 写回任务。
7. 构造 InflightRecord，放入 `new_handle_queue`。

MISS / RESERVED / EXPIRED：

- 记录本 key 失败。
- 不提交 transport handle。
- 更新 request_ctx。

### CompletionPoller

LOAD handle terminal 后：

- SUCCESS：
  - 从 `pending_` 删除。
  - 持 shard 读锁 + entry spinlock。
  - `refcnt--`。
  - Entry 保持 READY。
  - 更新 request_ctx。

- FAILED/TIMEOUT：
  - 从 `pending_` 删除。
  - `refcnt--`。
  - Entry 保持 READY。
  - 记录 request failed。
  - 更新 request_ctx。

request_ctx 完成后：

- 全部成功：写一次 flagbuffer OK。
- 有失败：写一次 flagbuffer FAILED。

## 8. LOOKUP 主流程

LOOKUP 不进入 CompletionPoller，除非后续要求 flagbuffer 写也必须跟踪 completion。

当前建议：

1. Receiver 解包 LOOKUP request。
2. TaskWorker 按 request 内 key 原始顺序检查。
3. 每个 key 按 shard 读锁查询 Entry。
4. 只统计连续 prefix 命中数。
5. 遇到 MISS / RESERVED / EXPIRED 即停止。
6. 写一次 flagbuffer，返回 `prefix_hit_count`。

LOOKUP 的 prefix 语义要求保持 request 内 key 顺序，因此不建议按 shard 分组并乱序处理。

## 9. 队列与背压

### request_queue

```text
Receiver -> TaskWorker
SpscRingQueue<RequestTask>
```

如果满：

- Receiver 应返回 RESOURCE_BUSY 或做上层背压。
- 不应丢 request。

### new_handle_queue

```text
TaskWorker -> CompletionPoller
SpscRingQueue<InflightRecord>
```

关键要求：TransportMgr submit 成功后，handle 必须被 CompletionPoller 接管。

因此：

- `new_handle_queue` 满时不能丢 handle。
- 要么 `Push` 阻塞等待空间。
- 要么 submit 前先确认容量。
- 要么失败时必须有明确 abort/rollback 语义。

### pending_

```text
CompletionPoller 私有 deque
capacity = max_pending
```

`pending_` 满说明 CompletionPoller 消费速度跟不上，应该触发背压。因为只有 CompletionPoller 访问，`pending_` 不需要锁。

每轮只扫头部 64 个是当前确认策略。该策略的风险是 head-of-line blocking：如果头部 64 个长期 WAITING，后面的 handle 即使已完成也不会被处理。因此必须配套 timeout，确保头部 handle 最终 terminal。

## 10. 核心待确认问题

以下问题会影响核心架构，需要优先确认。

### 1. Receiver 是否永远单线程

当前 `request_queue` 依赖单 Receiver 单 TaskWorker。如果 Receiver 改成多线程，SPSC 不再适用。

需要确认：

- TCP demo 阶段是否单 epoll receiver。
- RDMA send/recv 阶段是否单 receive poller。
- 是否可能多个 receiver 同时投递 request。

### 2. TaskWorker 是否长期保持单线程

当前很多简化都依赖“只有一个 TaskWorker”：

- 不存在多 TaskWorker 并发插入同 key。
- request_queue 可以是单消费者。
- 元数据插入路径更简单。

如果后续要多个 TaskWorker，需要重新设计：

- request 分发策略。
- key/shard affinity。
- 多 worker 下的 duplicate double-check。
- CompletionPoller 和多个 worker 的 completion apply 边界。

### 3. flagbuffer 写是否需要 completion

当前设计中，CompletionPoller 在 request_ctx 完成后提交一次 flagbuffer 写。

需要确认：

- flagbuffer 写是否 fire-and-forget。
- flagbuffer 写失败如何处理。
- flagbuffer 写是否也返回 handle，是否需要二级 completion。

### 4. Transport timeout 的语义

需要确认 `QueryStatus(handle) == TIMEOUT` 后：

- TransportMgr 是否保证不会再发生 late completion。
- 是否支持 abort。
- 是否允许释放 buffer。

如果 timeout 后仍可能 late completion，则必须保留 Entry generation，并让 InflightRecord 携带 generation。

### 5. InflightRecord 是否必须带 generation

当前图里不带 generation。成立前提：

- GC 不删除 inflight Entry。
- timeout 不删除 inflight Entry，或删除后不允许同 key 重新插入直到 late completion 被处理。

如果不能满足，需要加：

```text
InflightRecord.generation
Entry.generation
completion 时校验 generation
```

### 6. GC 和 TTL 语义

需要确认：

- `expire_at_ms` 从 request 携带，还是由 DramPool 根据统一 TTL 计算。
- LOOKUP/LOAD 是否刷新 TTL。
- GC 是按 expire 优先，还是也支持容量淘汰。
- GC 每次扫描 shard 的策略和预算。

### 7. DUMP duplicate 语义

当前建议：

- TaskWorker 读锁查到 key 已存在，则本次 key 记 duplicate/skip。
- 即使 GC 随后删除该 key，也不改变本次 request 结果。

需要确认业务是否接受这个语义。

### 8. request 内部分失败语义

当前 request 级 flagbuffer 只写一次。

需要确认：

- 任意 key 失败，整个 request 是否 FAILED。
- 是否需要 per-key status。
- 已成功的 DUMP key 是否要在 request 失败时统一 rollback，避免部分 READY。

当前建议 DUMP request 失败时回滚本 request 创建的 Entry。

### 9. pending_ 头部 64 扫描策略

当前按领导要求固定：

```text
每轮从 pending_ 头部最多扫 64 个
WAITING 留原地
terminal erase
```

需要确认是否接受 head-of-line blocking 风险。若不同连接乱序完成明显，后续可能需要改成 cursor round-robin。

### 10. BufferMgr 分配失败后的回滚边界

需要确认 DUMP request 中部分 key 已经插入 RESERVED 后，后续 key 分配失败时：

- 是否立即标记 request failed。
- 是否回滚已插入 Entry。
- 是否继续提交已准备好的 key。

当前建议：任意失败后 request failed，未提交 key skip，已提交 key drain 后统一失败处理。

## 11. 当前推荐结论

第一版核心架构建议固定为：

```text
单 Receiver
单 TaskWorker
单 CompletionPoller
独立 GCThread

request_queue:
  SpscRingQueue<RequestTask>

new_handle_queue:
  SpscRingQueue<InflightRecord>

pending_:
  CompletionPoller 本地 std::deque<InflightRecord>
  max_pending 可配置

CompletionPoller:
  while running:
    DrainNewHandles(<=64)
    PollFirst64()
```

这个方案和 CacheStore 的三阶段思想一致，但不依赖 CacheStore 的 FIFO 最后 shard 完成语义。DramPool 使用 request_ctx 做 request 级 fan-in，保证 flagbuffer 每 request 只写一次。
