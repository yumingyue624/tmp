# DramPool 核心容量估算

## 1. 基础假设

单节点可用于 DramPool 的内存：

```text
1.5 TB
```

单个 key 对应一个 block：

```text
9 MB / key
```

理论最大 key 数：

```text
1.5 TB / 9 MB ≈ 166,666 keys
```

如果按二进制单位粗算：

```text
1.5 TiB / 9 MiB ≈ 174,762 keys
```

工程估算建议按十进制保守值看：

```text
raw capacity ≈ 16 万 keys
```

## 2. 核心内存占用

### 2.1 KV 数据区

这是绝对大头：

```text
key_count * 9 MB
```

示例：

```text
140,000 keys -> 1,260,000 MB ≈ 1.26 TB
150,000 keys -> 1,350,000 MB ≈ 1.35 TB
160,000 keys -> 1,440,000 MB ≈ 1.44 TB
```

只要多放 1 万个 key，就需要：

```text
10,000 * 9 MB = 90 GB
```

因此容量主要由 value block 决定。

### 2.2 对齐和预留

如果 BufferMgr 按 9MB 精确分配，理论容量接近：

```text
16 万 keys
```

如果因为 hugepage / slot 对齐导致 9MB 实际占用 10MB：

```text
1.5 TB / 10 MB = 150,000 keys
```

所以需要重点确认：

```text
9MB block 实际分配大小是多少
```

建议先预留 10% 到 15% 给：

```text
metadata
index
queue
inflight record
TransportMgr / RDMA 相关结构
GC 辅助结构
系统内存和碎片
```

可用 key 数建议按：

```text
85% 可用: 约 136,000 keys
90% 可用: 约 144,000 keys
95% 可用: 约 152,000 keys
```

第一版建议用：

```text
140,000 ~ 150,000 keys
```

作为工程容量目标。

### 2.3 Entry + 三套索引

每个 key 需要：

```text
Entry 元数据
primary index
expire index
position index
```

粗略按：

```text
512 B ~ 1 KB / key
```

估算。

对于 150,000 keys：

```text
512 B/key -> 约 75 MB
1 KB/key  -> 约 150 MB
```

相比 1.5TB 数据区，这部分不是容量瓶颈。

## 3. Shard 数量估算

如果按 140,000 到 150,000 keys 作为有效容量：

```text
64 shards:
  约 2,200 ~ 2,350 keys/shard

128 shards:
  约 1,100 ~ 1,200 keys/shard

256 shards:
  约 550 ~ 600 keys/shard

512 shards:
  约 270 ~ 300 keys/shard
```

建议：

```text
shard_count = 128 或 256
```

原因：

```text
64 shards:
  每个 shard key 数偏多，单 shard 锁竞争和 GC 扫描压力更大。

128 / 256 shards:
  每个 shard 的 key 数适中，后续可以灵活映射到 executor thread。

512 shards:
  每个 shard key 数很少，管理结构和调度复杂度增加，第一版必要性不高。
```

## 4. Executor 线程建议

不建议：

```text
一个 shard 一个线程
```

原因是 key 总量只有 14 万到 15 万级别，metadata 操作不是最大瓶颈，线程数过多会带来调度和队列开销。

建议：

```text
shard_count = 128 或 256
executor_threads = 8 ~ 32
每个 executor thread 负责一组 shard
```

示例：

```text
128 shards, 16 executor threads
  -> 每个 executor 负责 8 个 shard

256 shards, 16 executor threads
  -> 每个 executor 负责 16 个 shard

256 shards, 32 executor threads
  -> 每个 executor 负责 8 个 shard
```

这种设计的好处是：

```text
shard 数决定元数据分片粒度
executor_threads 决定并行处理能力
两者解耦，后续可以调 executor 数量，不必重做 shard 结构
```

## 5. 推荐初始配置

第一版建议：

```text
有效容量目标:
  140,000 ~ 150,000 keys

shard_count:
  128 或 256

executor_threads:
  16 起步

每个 executor 负责:
  8 ~ 16 个 shard
```

最终需要确认：

```text
1. 9MB block 实际分配后是否仍是 9MB
2. 1.5TB 是否全部可用于 RDMA 注册和 DramPool 数据区
3. TransportMgr / RDMA 注册内存是否有额外上限
```
