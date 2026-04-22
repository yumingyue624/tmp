Mooncake调研

# 一、整体架构

Mooncake 采用了一种以 KVCache 为中心的分布式架构，旨在解决大模型推理中显存瓶颈和数据传输延迟的问题。其核心设计理念是将计算资源池化，并将控制面与数据面彻底解耦。

1.1 系统全景图与组件交互

如架构图所示，Mooncake 的整体系统主要由四个核心部分组成：全局调度器（Conductor）、Prefill 资源池、Decoding 资源池以及分布式存储与传输引擎（Mooncake Store & Transfer Engine）。



1.1.1 Prefill/Decode 集群分离

PD分离设计允许针对两个阶段不同的计算和内存特征进行独立的资源分配和调度优化。
1）Prefill Pool：专注于处理高并发的首词生成（Prefill）阶段。其优化目标是最大化 KVCache 的重用率（max Cache Reuse），同时满足首字延迟（TTFT）的 SLO 要求。每个 Prefill Instance 内部包含 GPU/VRAM 和 CPU/DRAM/SSD，利用 Local Chunked Prefill Scheduler 管理显存内的 Paged KVCache。
2）Decoding Pool：专注于自回归解码阶段。其优化目标是最大化吞吐量（max Throughput），满足 token 生成时间（TBT）的 SLO。Decoding Instance 同样具备多级存储结构，通过 Local Scheduler 调度显存资源。

1.1.2 全局 Conductor 调度器

Conductor根据全局视角下发调度指令，不直接参与数据搬运。它包含三个关键组件：
1）Cache-aware Prefill Scheduler：负责感知缓存状态的 Prefill 调度。
2）KVCache Balance Scheduler：负责在集群间平衡 KVCache 的分布。
3）Load-balance Decoding Scheduler：负责 Decoding 阶段的负载均衡。

1.1.3 多级 KV Store 与 Transfer Engine

1）Mooncake Store：是一个跨越所有 Instance 的分布式 KVCache 池。它利用 CPU 的 DRAM 和 SSD 构建了缓存池。
2）KVCache Transfer Engine：位于架构中心，通过 RDMA 技术连接各个节点的 Distributed KVCache Pool。它负责在实例之间高效地传输 KVCache 数据。

1.2 控制流与数据流解耦设计

Mooncake 架构的控制面（Control Plane）与数据面（Data Plane）是解耦的

1.2.1 控制面集中管控

1）Conductor 维护全局的元数据视图。它知道哪个节点有空闲显存，哪个请求的 KVCache 存储在哪个 SSD 上，以及当前的负载情况。
2）所有的调度决策（如：将这个请求分配给哪个 Prefill 实例，或者将哪个 KVCache 从 SSD 加载到 GPU）都由 Conductor 集中计算并下发。
3）图中左侧的粗箭头代表了控制指令的下发，指向具体的 Scheduler 模块。

1.2.2 数据面分布式直传

1）实际的数据传输（KVCache 的读写和迁移）完全由右侧的 Instance 和中间的 Transfer Engine 执行，不经过 Conductor。
2）实例内传输：数据在 GPU/VRAM（Paged KVCache）和 CPU/DRAM/SSD（Distributed KVCache Pool）之间通过 PCIe 进行上下行交换（图中双向箭头）。
3）实例间传输：当需要跨节点获取数据时（例如 Decoding 实例需要 Prefill 产生的 KVCache），数据通过 RDMA 直接在网卡之间传输（图中带 RDMA 标签的箭头），绕过了 CPU 和中心节点，极大地降低了延迟和 CPU 开销。

# 二、请求调度策略

2.1 Master 元数据管理与分片架构

Mooncake Store 的 Master 服务采用分片架构来管理全局 KVCache 元数据，以支持高并发访问：

```cpp
// mooncake-store/include/master_service.h:841-854
static constexpr size_t kNumShards = 1024;  // 元数据分片数量

struct MetadataShard {
    mutable SharedMutex mutex;
    std::unordered_map<std::string, ObjectMetadata> metadata GUARDED_BY(mutex);
    std::unordered_set<std::string> processing_keys GUARDED_BY(mutex);
    std::unordered_map<std::string, const ReplicationTask> replication_tasks GUARDED_BY(mutex);
    std::unordered_map<std::string, const OffloadingTask> offloading_tasks GUARDED_BY(mutex);
};
std::array<MetadataShard, kNumShards> metadata_shards_;
```

1）分片路由：通过 `hash(key) % kNumShards` 将对象路由到对应分片，降低锁竞争
2）ObjectMetadata：每个对象维护 replica 列表、lease 超时时间、soft/hard pin 状态
3）Replica 管理：支持内存副本（MEMORY）和磁盘副本（DISK/LOCAL_DISK）两种类型

2.2 Lease 租约与并发控制

Master 通过 Lease 机制保证数据一致性，防止读写冲突：

```cpp
// mooncake-store/include/master_service.h:757-780
void GrantLease(const uint64_t ttl, const uint64_t soft_ttl) const {
    SpinLocker locker(&lock);
    std::chrono::system_clock::time_point now = std::chrono::system_clock::now();
    lease_timeout = std::max(lease_timeout, now + std::chrono::milliseconds(ttl));
    if (soft_pin_timeout) {
        soft_pin_timeout = std::max(*soft_pin_timeout,
                     now + std::chrono::milliseconds(soft_ttl));
    }
}

bool IsLeaseExpired() const {
    SpinLocker locker(&lock);
    return std::chrono::system_clock::now() >= lease_timeout;
}
```

1）Hard Lease：写入时授予，保证写入期间的排他访问
2）Soft Pin：VIP 对象的延长保护，可通过 `allow_evict_soft_pinned_objects_` 配置是否允许驱逐

2.3 内存驱逐策略（BatchEvict）

Master 侧的内存驱逐采用近似 LRU 策略，基于 lease 过期时间排序：

```cpp
// mooncake-store/src/master_service.cpp:3440-3529
void MasterService::BatchEvict(double evict_ratio_target, double evict_ratio_lowerbound) {
    // 两阶段驱逐策略
    // 第一阶段：驱逐无 soft pin 且 lease 过期的对象
    // 第二阶段：若 allow_evict_soft_pinned_objects_ 为 true，可驱逐 soft pin 对象

    // 随机选择起始分片，避免分片间驱逐不均衡
    size_t start_idx = rand() % kNumShards;

    // 驱逐条件：memory replica && completed && refcnt == 0
    auto can_evict_replicas = [](const ObjectMetadata& metadata) {
        return metadata.HasReplica([](const Replica& replica) {
            return replica.is_memory_replica() && replica.is_completed()
                   && replica.get_refcnt() == 0;
        });
    };
}
```

1）触发条件：`used_ratio > eviction_high_watermark_ratio_`（默认 0.95）或 `need_eviction_` 标志
2）驱逐目标：`eviction_ratio_`（默认 0.05，即驱逐 5%）
3）驱逐算法：使用 `std::nth_element` 按 lease 超时时间选择最早过期的对象，近似 LRU
4）Hard Pinned 对象永不驱逐：`IsHardPinned()` 返回 true 的对象跳过

2.4 磁盘后端驱逐策略

磁盘层支持多种后端配置，每种有不同的驱逐策略：

```cpp
// mooncake-store/include/storage_backend.h:174-178
enum class BucketEvictionPolicy {
    NONE,   // 无驱逐（默认）
    FIFO,   // 按创建顺序驱逐最旧的 bucket
    LRU,    // 按最近访问时间驱逐（last_access_ns_）
};

// mooncake-store/include/storage_backend.h:180-196
struct BucketBackendConfig {
    int64_t bucket_size_limit = 256 * kMB;   // 单个 bucket 最大 256MB
    int64_t bucket_keys_limit = 500;          // 单个 bucket 最多 500 个 key
    BucketEvictionPolicy eviction_policy = BucketEvictionPolicy::NONE;
    int64_t max_total_size = 0;               // 0 = 无限制
};
```

BucketStorageBackend 的两阶段驱逐：

```cpp
// mooncake-store/include/storage_backend.h:907-932
// Phase 1: PrepareEviction - 在独占锁下选择并移除 bucket，不删除文件
PendingEviction PrepareEviction(int64_t required_size);

// Phase 2: FinalizeEviction - 等待 in-flight reads 完成后删除文件
void FinalizeEviction(const PendingEviction& pending);
```

1）LRU 索引：`lru_index_` 为 `set<pair<last_access_ns_, bucket_id>>`，惰性维护
2）安全删除：通过 `BucketReadGuard`（RAII）跟踪 in-flight reads，确保无并发读后再删除

2.5 本地热缓存（LocalHotCache）

Client 侧的本地热缓存用于缓存高频访问的 KVCache：

```cpp
// mooncake-store/include/local_hot_cache.h:37-158
class LocalHotCache {
    // LRU 队列 + key 到 iterator 的映射
    std::list<HotMemBlock*> lru_queue_ GUARDED_BY(lru_mutex_);
    std::unordered_map<std::string, std::list<HotMemBlock*>::iterator> key_to_lru_it_;

    // 延迟 LRU touch 优化
    void drainDeferredTouches();  // 批量将 accessed 块移到队首
};
```

1）异步 Put：`LocalHotCacheHandler` 通过线程池异步执行 `PutHotTask`，避免阻塞主路径
2）频率准入：通过 `CountMinSketch` 实现频率过滤，只有访问次数 >= `admission_threshold_`（默认 2）的 key 才进入热缓存
3）内存分配：支持标准内存和 memfd 共享内存（`use_shm=true` 用于跨进程共享）

2.6 副本迁移任务（Copy/Move/Drain）

Master 支持三种数据迁移任务：

| 任务类型              | 行为                | 源码位置                       |
| ----------------- | ----------------- | -------------------------- |
| CopyStart/CopyEnd | 复制副本到目标节点，源副本保留   | `master_service.h:327-336` |
| MoveStart/MoveEnd | 移动副本到目标节点，源副本删除   | `master_service.h:351-359` |
| CreateDrainJob    | 优雅撤离一个或多个 segment | `master_service.h:474`     |

```cpp
// mooncake-store/include/master_service.h:1249-1267
struct DrainJob {
    UUID id;
    JobType type{JobType::DRAIN};
    JobStatus status{JobStatus::CREATED};
    uint64_t succeeded_units{0};
    uint64_t failed_units{0};
    uint64_t migrated_bytes{0};
    std::unordered_map<UUID, ActiveDrainTask, boost::hash<UUID>> active_tasks;
    std::unordered_set<std::string> completed_unit_keys;
    std::unordered_map<std::string, uint32_t> retry_counts;  // 每 unit 最多重试 3 次
};
static constexpr uint32_t kMaxDrainUnitRetries = 3;
```

# 三、Transfer Engine：高性能数据传输

3.1 全局配置与拓扑感知

Transfer Engine 通过全局配置管理 RDMA/网络参数：

```cpp
// mooncake-transfer-engine/include/config.h:33-75
struct GlobalConfig {
    size_t num_cq_per_ctx = 1;
    uint64_t max_mr_size = 0x10000000000;  // 最大内存注册区域 64GB
    size_t max_cqe = 4096;
    int max_ep_per_ctx = 65536;
    size_t num_qp_per_ep = 2;
    size_t max_sge = 4;
    size_t max_wr = 256;
    size_t max_inline = 64;
    ibv_mtu mtu_length = IBV_MTU_4096;
    size_t slice_size = 65536;              // 切片大小 64KB
    int retry_cnt = 9;
    size_t fragment_limit = 16384;
    EndpointStoreType endpoint_store_type = EndpointStoreType::SIEVE;  // 默认 SIEVE
};
```

拓扑发现流程（`topology.cpp:473-492`）：

```cpp
int Topology::discover(const std::vector<std::string> &filter) {
    auto all_hca = listInfiniBandDevices(filter);  // 枚举 IB 设备
    for (auto &ent : discoverCpuTopology(all_hca))  // CPU NUMA 拓扑
        matrix_[ent.name] = ent;
    for (auto &ent : discoverCudaTopology(all_hca))  // GPU PCIe 拓扑
        matrix_[ent.name] = ent;
    return resolve();
}
```

1）CPU 拓扑：扫描 `/sys/devices/system/node`，按 NUMA node 匹配 HCA
2）GPU 拓扑：通过 `cudaDeviceGetPCIBusId` 获取 GPU PCI 地址，计算与 HCA 的 PCIe 距离
3）设备选择：`selectDevice()` 优先选择 `preferred_hca`（同 NUMA/最小 PCIe 距离），失败时回退到 `avail_hca`

3.2 NUMA 亲和性与 GPU Direct RDMA

```cpp
// topology.cpp:303-338
static std::vector<TopologyEntry> discoverCpuTopology(
    const std::vector<InfinibandDevice> &all_hca) {
    // 扫描 NUMA node，将同 NUMA 的 HCA 标记为 preferred
    for (const auto &hca : all_hca) {
        if (hca.numa_node == node_id) {
            preferred_hca.push_back(hca.name);  // 同 NUMA 优先
        } else {
            avail_hca.push_back(hca.name);      // 跨 NUMA 备选
        }
    }
}

// topology.cpp:384-443
static std::vector<TopologyEntry> discoverCudaTopology(...) {
    // 先找同 NUMA 的 HCA，若无则找 PCIe 距离最近的
    const auto &candidate_preferred_hca =
        same_numa_hca.empty() ? all_hca : same_numa_hca;
    for (const auto &hca : candidate_preferred_hca) {
        int distance = getPciDistance(hca.pci_bus_id.c_str(), pci_bus_id);
        // 选择最小 PCIe 距离的 HCA 作为 preferred
    }
}
```

GPU Direct RDMA 的实现通过 `ibv_reg_mr` 注册 GPU 显存，使 RDMA NIC 可以直接读写 VRAM，绕过 CPU 内存拷贝。

3.3 SIEVE 端点池化技术

SIEVE 算法管理 RDMA QP 连接池，实现按需连接和高效驱逐：

```cpp
// mooncake-transfer-engine/include/transport/rdma_transport/endpoint_store.h:86-119
class SIEVEEndpointStore : public EndpointStore {
    // endpoint_map_ 存储 (endpoint, visited_flag) 对
    std::unordered_map<std::string, std::pair<std::shared_ptr<RdmaEndPoint>, std::atomic_bool>> endpoint_map_;
    std::list<std::string> fifo_list_;  // FIFO 链表
    std::optional<std::list<std::string>::iterator> hand_;  // SIEVE 手指针
    std::unordered_set<std::shared_ptr<RdmaEndPoint>> waiting_list_;  // 等待回收的 endpoint
};
```

核心操作（`endpoint_store.cpp:127-217`）：

| 操作                    | 行为                                                             |
| --------------------- | -------------------------------------------------------------- |
| `getEndpoint(key)`    | 查找 endpoint，若存在则将 `visited` 标记为 `true`                         |
| `insertEndpoint(key)` | 新 endpoint 插入队首，`visited=true`；若满则触发驱逐                         |
| `evictEndpoint()`     | 从 `hand_` 位置反向扫描：visited=true 的标记为 false 并跳过；visited=false 的驱逐 |

```cpp
// endpoint_store.cpp:191-217
void SIEVEEndpointStore::evictEndpoint() {
    auto o = hand_.has_value() ? hand_.value() : --fifo_list_.end();
    std::string victim;
    while (true) {
        victim = *o;
        if (endpoint_map_[victim].second.load(std::memory_order_relaxed)) {
            // visited=true: 标记为 false，跳过（快速淘汰机制）
            endpoint_map_[victim].second.store(false, std::memory_order_relaxed);
            o = (o == fifo_list_.begin() ? --fifo_list_.end() : std::prev(o));
        } else {
            break;  // visited=false: 驱逐此 endpoint
        }
    }
    // 更新 hand 指针，从 map 和 list 中移除 victim
    waiting_list_.insert(endpoint_map_[victim].first);
    endpoint_map_.erase(victim);
}
```

1）快速降级：新插入的 endpoint visited=true，首次访问不会被驱逐
2）手指针：`hand_` 记录上次驱逐位置，避免每次都从头扫描
3）等待列表：被驱逐的 endpoint 进入 `waiting_list_`，等待 outstanding slices 完成后在 `reclaimEndpoint()` 中清理

3.4 传输引擎初始化与拓扑加载

```cpp
// mooncake-transfer-engine/src/transfer_engine_impl.cpp:67-150
int TransferEngineImpl::init(...) {
    // 设置文件描述符上限
    setFilesLimit();
    // 解析本地地址，支持 P2P 握手和传统 RPC 两种模式
    auto [host_name, port] = parseHostNameWithPort(local_server_name);
    // 加载拓扑（从文件或自动发现）
    std::string topology_json = loadTopologyJsonFile(topology_path);
}
```

3.5 多传输协议支持

Transfer Engine 支持多种传输后端：

| 传输类型    | 目录                            | 适用场景                   |
| ------- | ----------------------------- | ---------------------- |
| RDMA    | `transport/rdma_transport/`   | 高性能 InfiniBand/RoCE 网络 |
| TCP     | `transport/tcp_transport/`    | 通用 TCP 网络              |
| NVMe-oF | `transport/nvmeof_transport/` | 远程 NVMe 存储             |
| NvLink  | `transport/nvlink_transport/` | 节点内 GPU 互联             |
| EFA     | `transport/efa_transport/`    | AWS EC2 EFA 网络         |
| HIP     | `transport/hip_transport/`    | AMD GPU                |
| CXL     | `transport/cxl_transport/`    | CXL 内存扩展               |
| Ascend  | `transport/ascend_transport/` | 华为昇腾 NPU               |
| Barex   | `transport/barex_transport/`  | 昆仑芯                    |

3.6 切片传输与分片机制

```cpp
// config.h:48,58
size_t slice_size = 65536;          // 默认切片 64KB
size_t fragment_limit = 16384;      // 分片上限 16K
```

大传输请求会被切分为多个 slice，每个 slice 独立提交到 RDMA Read/Write 队列，支持：
1）异步提交：通过 `TransferSubmitter` 异步提交传输请求
2）并发执行：多个 worker 线程并行处理不同 slice
3）错误重试：`retry_cnt = 9` 次重试

# 四、Mooncake Store：内存池与存储管理

4.1 Master-Client 架构

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   Client Node 1 │     │   Client Node 2 │     │   Client Node N │
│  (GPU+DRAM+SSD) │     │  (GPU+DRAM+SSD) │     │  (GPU+DRAM+SSD) │
└────────┬────────┘     └────────┬────────┘     └────────┬────────┘
         │                       │                       │
         └───────────────────────┼───────────────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │    Master Service       │
                    │  (元数据管理 + 调度)      │
                    │  - 1024 分片             │
                    │  - Lease 管理            │
                    │  - 驱逐策略              │
                    │  - 副本迁移              │
                    └─────────────────────────┘
```

Master 配置（`master_config.h`）：

```cpp
double eviction_ratio = 0.1;                    // 驱逐比例 10%
double eviction_high_watermark_ratio = 1.0;      // 高水位 100%
uint64_t default_kv_lease_ttl;                   // 默认 lease TTL
bool allow_evict_soft_pinned_objects_ = false;   // 是否允许驱逐 soft pin
```

4.2 Segment 管理与内存注册

```cpp
// mooncake-store/include/segment.h:19-25
enum class SegmentStatus {
    UNDEFINED = 0,  // 未初始化
    OK,             // 正常，可分配
    DRAINING,       // 撤离中，只读不写
    DRAINED,        // 已撤离，等待卸载
    UNMOUNTING,     // 卸载中
};
```

Segment 生命周期：
1）MountSegment：Client 注册本地内存到 Master，Master 创建对应的 BufferAllocator
2）ReMountSegment：Client 重连或 Ping 超时时重新注册，支持幂等
3）UnmountSegment：两阶段卸载 - PrepareUnmount（删除 allocator）→ CommitUnmount（清理元数据）

4.3 双分配器架构

Mooncake Store 支持两种内存分配器：

4.3.1 CacheLib 分配器（CachelibBufferAllocator）

```cpp
// mooncake-store/include/allocator.h:140-184
class CachelibBufferAllocator : public BufferAllocatorBase {
    std::unique_ptr<facebook::cachelib::MemoryAllocator> memory_allocator_;
    facebook::cachelib::PoolId pool_id_;
    // 对齐要求：基地址至少 8 字节对齐，推荐 4MB 对齐
};
```

1）基于 Facebook CacheLib 的 slab 分配策略
2）适合不规则大小的 KVCache 分配
3）`getLargestFreeRegion()` 返回 `kAllocatorUnknownFreeSpace`（无法精确计算最大空闲）

4.3.2 Offset 分配器（OffsetBufferAllocator）

```cpp
// mooncake-store/include/allocator.h:191-234
class OffsetBufferAllocator : public BufferAllocatorBase {
    std::shared_ptr<offset_allocator::OffsetAllocator> offset_allocator_;
};

// mooncake-store/include/offset_allocator/offset_allocator.hpp:23-27
static constexpr uint32 NUM_TOP_BINS = 32;
static constexpr uint32 BINS_PER_LEAF = 8;
static constexpr uint32 NUM_LEAF_BINS = NUM_TOP_BINS * BINS_PER_LEAF;  // 256 bins
```

1）基于 offset 的连续内存分配，支持精确的 `getLargestFreeRegion()` 查询
2）256 个 Bin：32 个顶层 bin × 8 个子 bin，按大小分级管理
3）无需后台 Compaction，通过 Bin 机制减少外部碎片
4）适合固定大小或已知大小的 KVCache 分配

4.4 AllocatedBuffer RAII 管理

```cpp
// mooncake-store/include/allocator.h:25-89
class AllocatedBuffer {
    std::weak_ptr<BufferAllocatorBase> allocator_;
    void* buffer_ptr_{nullptr};
    std::size_t size_{0};
    std::optional<offset_allocator::OffsetAllocationHandle> offset_handle_;

    // 析构时自动释放
    ~AllocatedBuffer();
};
```

1）RAII 机制：`AllocatedBuffer` 析构时自动调用 allocator 的 `deallocate`
2）Transfer Engine 集成：通过 `get_descriptor()` 序列化为 `Descriptor`，包含 `buffer_address_`、`protocol_`、`transport_endpoint_`，供远程 RDMA 访问
3）Offset 分配器特有：`offset_handle_` 持有 `OffsetAllocationHandle`，析构时自动释放 offset 空间

4.5 分层存储后端

```cpp
// mooncake-store/include/storage_backend.h:158
enum class StorageBackendType { kFilePerKey, kBucket, kOffsetAllocator };
```

| 后端类型            | 特点                     | 适用场景            |
| --------------- | ---------------------- | --------------- |
| FilePerKey      | 每个 key 一个文件            | 简单场景，易于调试       |
| Bucket          | 多个 key 打包到一个 bucket 文件 | 减少文件数，提高 I/O 效率 |
| OffsetAllocator | 单文件 + offset 分配        | 最高性能，类似 LSM 结构  |

BucketBackend 核心结构：

```cpp
// storage_backend.h:974-987
mutable SharedMutex mutex_;
std::unordered_map<std::string, StorageObjectMetadata> object_bucket_map_;  // key -> bucket
std::map<int64_t, std::shared_ptr<BucketMetadata>> buckets_;               // bucket_id -> metadata
std::set<std::pair<int64_t, int64_t>> lru_index_;                          // LRU 索引
```

OffsetAllocatorStorageBackend 分片设计：

```cpp
// storage_backend.h:1160-1176
static constexpr size_t kNumShards = 1024;  // 1024 个分片
struct MetadataShard {
    mutable SharedMutex mutex;
    std::unordered_map<std::string, ObjectEntry> map;
};
std::array<MetadataShard, kNumShards> shards_;

// 快速分片路由：hash & (kNumShards-1) 替代取模
inline size_t ShardForKey(const std::string& key) const {
    return std::hash<std::string>{}(key) & (kNumShards - 1);
}
```

4.6 写回策略与 Offload 机制

```cpp
// storage_backend.h:250-256
virtual tl::expected<int64_t, ErrorCode> BatchOffload(
    const std::unordered_map<std::string, std::vector<Slice>>& batch_object,
    std::function<ErrorCode(...)> complete_handler,
    std::function<void(const std::vector<std::string>& evicted_keys)> eviction_handler
) = 0;
```

Offload 流程：
1）Heartbeat 收集：`OffloadObjectHeartbeat()` 收集未 offload 的对象列表
2）分桶：`AllocateOffloadingBuckets()` 按 bucket 限制（256MB/500 keys）分组
3）写入：`BatchOffload()` 批量写入 bucket 文件
4）通知：`NotifyOffloadSuccess()` 通知 Master 更新元数据
5）驱逐回调：`eviction_handler` 通知 Master 清理被驱逐的 key

4.7 热点保护机制

```cpp
// master_service.h:621-626
const bool hard_pinned{false};           // 不可变，创建时设置
mutable std::optional<std::chrono::system_clock::time_point> soft_pin_timeout;  // 可选 soft pin

// master_service.h:783-793
bool IsSoftPinned() const {
    SpinLocker locker(&lock);
    return soft_pin_timeout && std::chrono::system_clock::now() < *soft_pin_timeout;
}
bool IsHardPinned() const { return hard_pinned; }
```

1）Hard Pin：创建时设置，`hard_pinned=true` 的对象永远不会被驱逐
2）Soft Pin：有 TTL 的保护，过期后变为可驱逐；通过 `GrantLease(ttl, soft_ttl)` 同时设置 hard lease 和 soft pin
3）VIP 对象：启用 soft pin 的对象会计入 `soft_pin_key_count` 指标

4.8 Client 端数据路径

```cpp
// mooncake-store/include/client_service.h:60-744
class Client {
    std::shared_ptr<TransferEngine> transfer_engine_;
    MasterClient master_client_;
    std::shared_ptr<LocalHotCache> hot_cache_;
    std::unique_ptr<CountMinSketch> admission_sketch_;
    uint8_t admission_threshold_ = 2;
};
```

Get 操作路径：
1）`Query(key)` → Master 获取 replica 列表和 lease 超时
2）`RedirectToHotCache(key, replica)` → 检查本地热缓存
3）`TransferRead(replica, slices)` → 通过 Transfer Engine 读取数据
4）`ProcessSlicesAsync(key, slices, replica)` → 异步更新热缓存

Put 操作路径：
1）`PutStart(key, slice_length, config)` → Master 分配 replica 缓冲区
2）`TransferWrite(replica, slices)` → 通过 Transfer Engine 写入数据
3）`PutEnd(key, replica_type)` → Master 标记 replica 完成

4.9 高可用（HA）支持

```cpp
// client_service.h:667-672
std::unique_ptr<ha::LeaderCoordinator> leader_coordinator_;
std::optional<ha::MasterView> current_master_view_;
std::thread leader_monitor_thread_;
```

1）LeaderCoordinator：协调 Master 集群的 Leader 选举
2）LeaderMonitor：监控 Leader 状态，自动切换
3）SwitchLeader：在 Leader 切换时更新连接，保证服务连续性

4.10 快照与恢复

```cpp
// master_service.h:1173-1190
bool enable_snapshot_restore_ = false;
bool enable_snapshot_ = false;
uint64_t snapshot_interval_seconds_ = DEFAULT_SNAPSHOT_INTERVAL_SEC;
uint32_t snapshot_retention_count_ = DEFAULT_SNAPSHOT_RETENTION_COUNT;
```

1）定期快照：后台线程定期序列化元数据到持久存储
2）子进程快照：fork 子进程执行序列化，避免阻塞主服务
3）快照恢复：启动时从最新快照恢复状态，支持 etcd oplog 增量回放
4）快照保留：可配置保留数量，自动清理旧快照
