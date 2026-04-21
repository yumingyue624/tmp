Mooncake调研

# 一、整体架构

Mooncake 采用了一种以 KVCache 为中心的分布式架构，旨在解决大模型推理中显存瓶颈和数据传输延迟的问题。

## 1.1 系统全景图与组件交互

如架构图所示，Mooncake 的整体系统主要由四个核心部分组成：全局调度器（Conductor）、Prefill 资源池、Decoding 资源池以及分布式存储与传输引擎（Mooncake Store & Transfer Engine）。

### 1.1.1 Prefill/Decode 资源池

PD分离设计允许针对两个阶段不同的计算和内存特征进行独立的资源分配和调度优化。

1）Prefill Pool：处理高并发的首词生成（Prefill）阶段。其优化目标是最大化 KVCache 的重用率（max Cache Reuse），同时满足首字延迟（TTFT）的 SLO 要求。每个 Prefill Instance 内部包含 GPU/VRAM 和 CPU/DRAM/SSD，利用 Local Chunked Prefill Scheduler 管理显存内的 Paged KVCache。

2）Decoding Pool：解码阶段。其优化目标是最大化吞吐量（max Throughput），满足 token 生成时间（TBT）的 SLO。Decoding Instance 同样具备多级存储结构，通过 Local Scheduler 调度显存资源。

### 1.1.2 全局 Conductor 调度器

Conductor根据全局视角下发调度指令，不直接参与数据搬运。它包含三个关键组件：

1）Cache-aware Prefill Scheduler：负责为每个请求，选择一个能**最大化复用已缓存KVCache**且**负载最低**的Prefill节点。
2）KVCache Balance Scheduler：通过主动迁移热点KVCache来平衡KVCache在集群中的分布。
3）Load-balance Decoding Scheduler：根据各Decode节点的实时负载情况，选择一个负载最低的节点来处理该请求的解码工作。

### 1.1.3 多级 KV Store 与 Transfer Engine

1）Mooncake Store：是一个跨越所有 Instance 的分布式 KVCache 池。它利用 CPU 的 DRAM 和 SSD 构建了缓存池。
2）KVCache Transfer Engine：位于架构中心，通过 RDMA 连接各个节点的 Distributed KVCache Pool。它负责在实例之间高效地传输 KVCache 数据。

# 二、请求调度策略

调度器（Conductor）的核心目标是：**在严格满足延迟 SLO 的前提下，最大化 KVCache 复用率与集群整体吞吐**。请求调度分为四个阶段：

- 阶段 1：前缀匹配

将请求 Prompt 按固定 Block Size 切分并 Hash 生成 `block_keys`。再扫描全局元数据，找出持有最长公共前缀的实例节点 `best_instance`及其匹配长度 `best_len`。

- 阶段 2：prefill实例选择

遍历所有 Prefill 节点：

- 若 `best_len / instance.prefix_len > threshold`：说明当前节点缓存远少于最优节点，**远程拉取+计算** 比 **全量本地计算** 更快。此时预估传输耗时 `T_transfer`，并按 `best_len`计算剩余 Prefill 计算量（使用通过离线数据拟合的多项式回归模型来估计相应的执行时间）。
- 若比值未超阈值：说明当前节点缓存已足够，**就地计算** 可避免网络开销。此时 `T_transfer = 0`，按本地 `prefix_len`计算剩余 Prefill 计算量。

预估`TTFT = T_transfer(网络) + T_queue(排队) + T_prefill(计算)`。选择预估 TTFT 最小的节点p，并相应地更新该实例的缓存和队列时间。

- 阶段 3：请求拒绝

选定 `p` 后，同步选择负载最轻的 Decode 节点 `d`，并预估token生成间隔 `TBT`。若预估 `TTFT`或 `TBT`超出人工设定的 SLO 阈值，**直接拒绝请求**。避免在系统超载时盲目接纳请求导致雪崩，保障已接受请求的体验。

- 阶段 4：缓存调度

若最终选定的实例并非 `best_instance`，且缓存差距超阈值，Conductor会将缓存的位置和请求转发到目标实例，目标实例主动从缓存持有者处检索KVCache，并拉取缺失的 KVCache。

# 三、Transfer Engine

## 3.1 拓扑感知

在大规模 GPU 集群中，RDMA 传输性能高度依赖于数据路径是否经过 PCIe Switch 跨桥、NUMA 跨节点访问。一次跨 NUMA 的 RDMA 传输延迟可能是同 NUMA 内的 2-3 倍。因此，Transfer Engine 必须在初始化阶段构建完整的硬件拓扑地图，以便后续传输时选择最优的 RDMA 网卡。

拓扑发现流程：每个服务器扫描底层硬件拓扑（NUMA 节点、PCIe 总线），构建计算设备与 RDMA 网卡的亲和性矩阵。

```cpp
int Topology::discover(const std::vector<std::string> &filter) {
    matrix_.clear();
    // ★ 1. 获取所有RDMA NIC设备
    auto all_hca = listInfiniBandDevices(filter);
    // ★ 2. 生成CPU拓扑（基于NUMA亲和性）
    for (auto &ent : discoverCpuTopology(all_hca)) {
        matrix_[ent.name] = ent;  // "cpu:0", "cpu:1" ...
    }
    // ★ 3. 生成CUDA拓扑（基于PCIe距离）
#if defined(USE_CUDA) || defined(USE_MUSA) || ...
    for (auto &ent : discoverCudaTopology(all_hca)) {
        matrix_[ent.name] = ent;  // "cuda:0", "cuda:1" ...
    }
#endif
    return resolve();
}
```

每个服务器生成一个拓扑矩阵并在集群中广播它。该矩阵将网络接口卡（NIC）分为“首选”和“次要”列表。在正常情况下，选择首选列表中的NIC进行传输，从而仅通过本地NUMA或GPU Direct RDMA通过本地PCIe交换机促进RDMA操作。在发生故障时，可以使用来自两个列表的NIC。

```cpp
int Topology::selectDevice(const std::string storage_type, int retry_count) {
    // storage_type = "cpu:0", "cuda:1" 等存储位置标识
    if (resolved_matrix_.count(storage_type) == 0) 
        return ERR_DEVICE_NOT_FOUND;
    
    auto &entry = resolved_matrix_[storage_type]; //从拓扑表中获取该存储位置的网卡信息
    
    if (retry_count == 0) {
        // ★ 首次选择：从preferred随机选一个，随机负载均衡，避免所有请求都打到同一个网卡，造成负载不均衡
        int rand_value = SimpleRandom::Get().next();
        if (!entry.preferred_hca.empty()) // 正常情况，返回rand_value这个随机数映射的首选网卡
            return entry.preferred_hca[rand_value % entry.preferred_hca.size()];
        else // 极端情况，首选网卡是空的，返回rand_value这个随机数映射的候选网卡
            return entry.avail_hca[rand_value % entry.avail_hca.size()];
    } else {
        // ★ 重试（上次选的设备失败了，需要换个设备试一下）：遍历preferred → avail
        //  retry_count超过总数时，用 % 循环回前面的设备
        size_t index = (retry_count - 1) %
                       (entry.preferred_hca.size() + entry.avail_hca.size()); 
        if (index < entry.preferred_hca.size())
            return entry.preferred_hca[index];  // 索引在 preferred 范围内，直接返回 preferred[index]
        else {
            index -= entry.preferred_hca.size(); // 索引超出 preferred 范围，需要从 avail 中选取
            return entry.avail_hca[index];      // avail
        }
    }
}
```

# 3.2 SIEVE 端点池化技术

针对大规模并发连接导致的 QP 资源耗尽问题，Transfer Engine 引入 SIEVE 缓存淘汰算法管理 RDMA Endpoints。相比传统 LRU/FIFO，SIEVE 利用原子访问标志位和单向扫描机制，在极低锁竞争下实现高命中率，并通过延迟回收队列（waiting_list）确保正在进行的 RDMA 传输安全完成，有效平衡了资源利用率与连接稳定性。

## 3.3 PD 分离 Layer-wise 异步流水线

Layer-wise（逐层） 是 Mooncake 在 KVCache 生命周期管理中采用的细粒度流水线策略。

- 传统做法：Prefill 阶段需等待模型所有 N 层全部计算完成后，再统一打包传输 KVCache。
- 痛点：
  1. 显存峰值极高（需同时驻留全部层的 KVCache）
  2. 计算与传输串行执行，GPU 网卡闲置，硬件利用率低
  3. Decode 节点必须等全部 Prefill 结束才能拿到 KVCache，拉长首 Token 延迟（TTFT）
     二、Layer-wise 工作原理（流水线执行流）
     Mooncake 打破“整体计算→整体传输”的串行模式，改为以模型层（Layer）为调度粒度的异步流水线：
1. 计算第 i 层 → GPU 完成该层 Prefill 计算，生成对应的 KVCache Block
2. 立即触发传输/卸载 → 该层 KVCache 立刻启动异步流程：
   - 通过 KVCache Transfer Engine 发给 Decode Instance，或
   - Dump 至 CPU/DRAM 进行 Offload
3. GPU 不等待 → 传输/卸载在后台异步进行的同时，GPU 立即开始计算第 i+1 层
4. 循环推进，形成 计算下一层 ↔ 传输上一层 的完全重叠流水线