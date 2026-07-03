# DramPool LOAD 流程设计

本章节基于时序图 `drampool_single_thread_load_sequence_v4.excalidraw`，描述 LOAD 请求在 DramPool 内部的处理流程、锁边界以及与 DUMP 流程的差异。

## 组件职责

`DramPoolServer` 仍然作为 DramPool 进程的主流程骨架，负责接收请求、调用协议层解包并提交内部 task。LOAD 路径涉及的模块如下：

- `KvProtocol`：解析 DramStore 发来的 LOAD request，生成 `LoadTaskContext`。
- `TaskFlow`：单线程任务处理逻辑，负责执行 LOAD task。
- `MetadataIndex`：查询 `Entry` 和三套索引，校验 entry 是否存在、是否 `READY`、TTL 是否有效。
- `TransportMgr`：按顺序提交单边写 value 和单边写 flagbuffer。

LOAD 与 DUMP 的差异很明确：

- LOAD 不分配 DramPool 本地内存，所以 `BufferMgr` 不参与主路径。
- LOAD 不改变 entry 的发布状态，所以 `CompletionPoller` 不参与当前主路径。
- LOAD 只读取已经 `READY` 的 entry。`RESERVED` entry 代表 DUMP 还没完成，不能被 LOAD 命中。

## LOAD 主流程

LOAD 请求同样分为同步接收阶段和异步执行阶段。

同步阶段只做请求接收、协议解析和 task 提交：

```text
DramStore -> DramPoolServer:
  发送 LOAD request

DramPoolServer -> KvProtocol:
  解析请求 buffer

KvProtocol -> DramPoolServer:
  返回 LoadTaskContext

DramPoolServer -> TaskFlow:
  submit task
```

这段完成后，DramPoolServer 的接收路径结束，后续 LOAD 由内部 `TaskFlow` 单线程推进。

异步阶段由 `TaskFlow` 执行：

```text
TaskFlow:
  dequeue LoadTaskContext

TaskFlow -> MetadataIndex:
  lookup key
  校验 Entry state == READY
  校验 expire_at_ms > now_ms
  返回 local addr / len / status

TaskFlow -> TransportMgr:
  提交单边写 value 到 DramStore

TaskFlow -> TransportMgr:
  提交单边写 flagbuffer
```

DramStore 侧看到 flagbuffer 后，认为本次 LOAD 请求完成。TransportMgr 必须保证 value write 在 flagbuffer write 之前提交到同一个有序域，不能让 DramStore 先看到完成 flag 再收到 value。

## 命中与未命中

LOAD 对 `Entry` 的可见性判断只接受 `READY`：

```text
primary_index 中不存在:
  MISS

Entry.state == RESERVED:
  MISS 或 BUSY，具体返回码由 KV 协议定义

Entry.expire_at_ms <= now_ms:
  EXPIRED / MISS

Entry.state == READY && Entry 未过期:
  HIT
```

当前建议把 `RESERVED` 当成不可见状态处理，避免读到还没有完成 RDMA 写入的数据。协议如果需要区分 `MISS` 和 `BUSY`，可以在 `KvProtocol` 的 response status 中扩展；MetadataIndex 内部只需要返回明确的 lookup 结果。

## MetadataIndex 锁边界

MetadataIndex 仍然采用 shard 化设计。每个 shard 内维护三套索引：

```cpp
struct MetadataShard {
    std::shared_mutex mu;

    PrimaryIndex primary_index;
    ExpireIndex expire_index;
    PositionIndex position_index;
};
```

LOAD 只需要查询 `primary_index`，但仍然要和 DUMP reserve、CompletionPoller mark ready、GC evict 互斥。

建议提供一个面向 LOAD 的接口：

```cpp
struct LoadLookupResult {
    LookupStatus status;
    void* local_addr;
    uint32_t value_len;
    uint64_t generation;
};

LoadLookupResult MetadataIndex::LookupForLoad(const Key& key, uint64_t now_ms);
```

`LookupForLoad` 内部持有对应 shard 的锁，完成以下逻辑：

```text
1. 查 primary_index
2. 校验 Entry.state == READY
3. 校验 expire_at_ms
4. 读取 local addr / len / generation
5. 更新 last_access_ms
6. 返回 LoadLookupResult
```

`last_access_ms` 只是访问时间统计，可用于后续淘汰策略或调试观测。更新它不能隐式延长 TTL，也就是说 LOAD 命中后不能自动修改 `expire_at_ms`。如果后续协议明确支持 refresh，再单独增加 refresh 语义。

第一版可以在 `LookupForLoad` 内直接持有 shard 写锁，因为更新 `last_access_ms` 需要写 entry：

```text
unique_lock(shard.mu)
  find entry
  validate READY / TTL
  update last_access_ms
  copy addr / len / generation
unlock
```

如果后续 LOAD QPS 较高，可以把 `last_access_ms` 改为原子字段，或者把访问时间更新降采样，从而让普通 LOAD 命中走读锁。

## Entry 生命周期约束

LOAD 主路径不通过 CompletionPoller 改 entry 状态，但它仍然会把 entry 的本地 buffer 地址交给 TransportMgr 做单边写。这里有一个关键约束：

```text
只要 value write 可能还在使用 entry->buffer，
GC 就不能释放或复用这个 buffer。
```

因此实现时需要二选一：

```text
方案 A:
  TransportMgr 的 LOAD value write 接口在返回前已经保证本次写完成。
  TaskFlow 返回后，GC 可以正常淘汰未被锁保护的 READY entry。

方案 B:
  TransportMgr 的 LOAD value write 是真正异步提交。
  MetadataIndex 需要在提交前 pin entry，例如 io_refcnt++。
  写完成后再 unpin，例如 io_refcnt--。
```

当前时序图采用的是“CompletionPoller 不参与 LOAD”的主路径，因此更适合方案 A，或者由 TransportMgr 在 LOAD 接口内部完成同步等待。否则如果 LOAD 也是纯异步 submit，必须补充一个 LOAD completion 释放 pin 的路径；这个路径不一定要改变 entry 状态，但必须解决 GC 与在飞 RDMA 的 buffer 生命周期问题。

推荐 `Entry` 保留一个轻量引用计数，给 GC 和后续异步 LOAD 扩展使用：

```cpp
struct Entry {
    Key key;
    void* local_addr;
    uint32_t value_len;
    uint64_t abs_pos;
    uint64_t expire_at_ms;
    uint64_t last_access_ms;
    uint32_t io_refcnt;
    EntryState state;
    uint64_t generation;
};
```

当前如果采用方案 A，`io_refcnt` 可以暂时不走复杂 completion 流程，但 GC 淘汰逻辑需要预留判断：

```text
只淘汰 state == READY 且 io_refcnt == 0 的 entry
```

## Transport 顺序语义

LOAD 对 DramStore 的完成通知依赖 flagbuffer：

```text
value write -> flagbuffer write
```

TransportMgr 需要提供明确的有序提交语义：

- value write 和 flagbuffer write 必须位于同一个有序队列、同一个 QP，或者 provider 明确保证两者顺序。
- flagbuffer 只能在 value write 之后提交。
- DramStore 看到 flagbuffer 后，可以读取本次 LOAD 的 value buffer。

如果 value write 提交失败，不能继续提交成功 flag；应写失败 status，或者让协议层按超时/错误处理。如果 flagbuffer write 提交失败，DramStore 可能无法收到完成通知，需要记录错误并按 TransportMgr 的错误语义处理连接或 task。

## 与 GC 的关系

GC 是独立线程，不通过 TaskFlow 消息驱动。因此 LOAD 和 GC 的并发边界必须通过 MetadataIndex shard lock 和 entry pin 保证。

GC 淘汰一个 entry 的基本流程应是：

```text
1. 选择候选 victim
2. 获取 victim 所在 shard 写锁
3. 重新校验 key / generation / state / TTL / io_refcnt
4. 从 primary_index / expire_index / position_index 中原子删除
5. 释放或隔离 buffer
```

LOAD 查询 entry 时，不能在未受保护的情况下长期持有裸指针。如果 LOAD 的 TransportMgr 调用不是同步完成，就必须在锁内 pin entry，释放锁后再提交 RDMA，完成后 unpin。

## 失败处理

LOAD 不改变 DramPool 内部数据内容，因此失败处理主要影响本次 response，不影响 entry 本身。

```text
lookup miss:
  不提交 value write
  写 flagbuffer，status = MISS / NOT_FOUND

entry expired:
  不提交 value write
  写 flagbuffer，status = EXPIRED / MISS
  是否立即删除过期 entry 交给 GC 或后续 cleanup 路径

value write submit failed:
  不提交成功 flag
  写失败 status 或返回 Transport error

flagbuffer write failed:
  记录错误
  DramStore 侧可能按请求超时处理
```

LOAD 命中后更新 `last_access_ms` 是允许的，但不能更新 `expire_at_ms`。过期时间只由写入时的 TTL 或后续明确的 refresh 协议决定。

## 可见性语义

LOAD 的可见性规则可以简化为：

```text
只有 READY 且未过期的 Entry 可以被 LOAD 命中。
```

`RESERVED` entry 对 LOAD 不可见。DUMP 的完成发布由 CompletionPoller 完成；LOAD 本身不发布新数据，也不改变 `RESERVED -> READY` 状态。

