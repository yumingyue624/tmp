Mooncake调研

# 一、整体架构

Mooncake 采用了一种以 KVCache 为中心的分布式架构，旨在解决大模型推理中显存瓶颈和数据传输延迟的问题。其核心设计理念是将计算资源池化，并将控制面与数据面彻底解耦。

## 1.1 系统全景图与组件交互

如架构图所示，Mooncake 的整体系统主要由四个核心部分组成：全局调度器（Conductor）、Prefill 资源池、Decoding 资源池以及分布式存储与传输引擎（Mooncake Store & Transfer Engine）。

### 1.1.1 Prefill/Decode 集群分离：

PD分离设计允许针对两个阶段不同的计算和内存特征进行独立的资源分配和调度优化。
  ◦ Prefill Pool： 专注于处理高并发的首词生成（Prefill）阶段。其优化目标是最大化 KVCache 的重用率（max Cache Reuse），同时满足首字延迟（TTFT）的 SLO 要求。每个 Prefill Instance 内部包含 GPU/VRAM 和 CPU/DRAM/SSD，利用 Local Chunked Prefill Scheduler 管理显存内的 Paged KVCache。
  ◦ Decoding Pool： 专注于自回归解码阶段。其优化目标是最大化吞吐量（max Throughput），满足 token 生成时间（TBT）的 SLO。Decoding Instance 同样具备多级存储结构，通过 Local Scheduler 调度显存资源。

### 1.1.2 全局 Conductor 调度器：

Conductor根据全局视角下发调度指令，不直接参与数据搬运。它包含三个关键组件：
 ▪ Cache-aware Prefill Scheduler： 负责感知缓存状态的 Prefill 调度。
 ▪ KVCache Balance Scheduler： 负责在集群间平衡 KVCache 的分布。
 ▪ Load-balance Decoding Scheduler： 负责 Decoding 阶段的负载均衡。

### 1.1.3 多级 KV Store 与 Transfer Engine：

◦ Mooncake Store： 是一个跨越所有 Instance 的分布式 KVCache 池。它利用 CPU 的 DRAM 和 SSD 构建了缓存池。
◦ KVCache Transfer Engine： 位于架构中心，通过 RDMA 技术连接各个节点的 Distributed KVCache Pool。它负责在实例之间高效地传输 KVCache 数据。

## 1.2 控制流与数据流解耦设计

Mooncake 架构的控制面（Control Plane）与数据面（Data Plane）是解耦的

### 1.2.1控制面集中管控

◦ Conductor 维护全局的元数据视图。它知道哪个节点有空闲显存，哪个请求的 KVCache 存储在哪个 SSD 上，以及当前的负载情况。
◦ 所有的调度决策（如：将这个请求分配给哪个 Prefill 实例，或者将哪个 KVCache 从 SSD 加载到 GPU）都由 Conductor 集中计算并下发。
◦ 图中左侧的粗箭头代表了控制指令的下发，指向具体的 Scheduler 模块。

### 1.2.2 数据面分布式直传

◦ 实际的数据传输（KVCache 的读写和迁移）完全由右侧的 Instance 和中间的 Transfer Engine 执行，不经过 Conductor。
◦ 实例内传输： 数据在 GPU/VRAM（Paged KVCache）和 CPU/DRAM/SSD（Distributed KVCache Pool）之间通过 PCIe 进行上下行交换（图中双向箭头）。
◦ 实例间传输： 当需要跨节点获取数据时（例如 Decoding 实例需要 Prefill 产生的 KVCache），数据通过 RDMA 直接在网卡之间传输（图中带 RDMA 标签的箭头），绕过了 CPU 和中心节点，极大地降低了延迟和 CPU 开销。

# 二、请求调度策略

1 多项式回归 TTFT 预估模型
• 前缀匹配：根据 block_keys 匹配各 Prefill 节点，获取 prefill_len
• 多维预估：
  • 输入：请求长度 + prefill_len + 节点负载
  • 模型：离线数据拟合的多项式回归模型
  • 输出：TTFT = T_prefill + T_queue + T_transfer
• 贪心分配：路由至预估 TTFT 最短的实例
• 拒绝机制：若预估 TTFT > SLO 阈值，直接拒绝，保护集群稳定性

2 基于启发式的自动热点迁移
• 代价权衡：对比 目标节点 Prefill 时间 vs 远程拉取 KV 传输延迟
• 主动检索：若拉取更优，Conductor 转发缓存位置，目标实例主动从持有者处 Pull KVCache
• 就地计算：若远程收益低于动态阈值（如 best_len < local_prefix * threshold），跳过拉取，避免网络拥塞

# Transfer Engine：高性能数据传输

对应论文：Transfer Engine (Section 3), HiCache Integration
逻辑位置：数据的搬运工，解释数据如何高效移动
3.1 拓扑感知与零拷贝传输
• 拓扑矩阵广播：节点生成并广播拓扑矩阵，感知全局网络结构
• NUMA 亲和性：自动选择同 NUMA 节点的 NIC，避免跨 Socket 访问
• GPU Direct RDMA：通过本地 PCIe Switch 直访 GPU，绕过 CPU 内存拷贝
3.2 SIEVE 端点池化技术
• 按需连接：Endpoint 初始为 UNCONNECTED，首次请求时触发握手
• SIEVE 算法：基于 NSDI'24 的缓存驱逐算法管理 RDMA QP 连接池
• 效益：降低握手开销，最大化长连接复用与网卡带宽
3.3 PD 分离 Layer-wise 异步流水线
• 计算 - 传输重叠：Layer N Prefill 完成后立即触发 KVCache 传输
• 隐藏延迟：Layer N+1 的数据加载与 Layer N 的计算并行，不阻塞主流程
• 双线程模型：Prefetch Thread 查询状态 + IO Thread 执行传输

# Mooncake Store：内存池与存储管理

对应论文：MOONCAKE Store Design (Section 4)
逻辑位置：系统的底座，解释数据存在哪里、如何管理
2.1 内存池架构与全局命名空间
• Master 集中管控：维护 Object/Block 到物理缓冲区的映射，统一分配策略
• Managed Buffer 分布式存储：各节点注册本地内存，支持远程 RDMA 暴露
• KVCache 池化：逻辑地址统一，物理位置透明，打破设备孤岛
2.2 RDMA 注册与介质支持
• 零拷贝注册：通过 ibv_reg_mr 注册连续内存区域，供 RDMA NIC 直接读写
• 双介质支持：
  • CPU Pinned Memory：主机内存锁定，通用场景
  • GPU VRAM：GPUDirect RDMA 直通，GPU 显存零拷贝
• 容量策略：支持运行时动态配置（无硬编码默认值，受限于物理硬件与 max_mr_size）
2.3 分层存储与驱逐策略
透明分级链路：VRAM/HBM → DRAM → SSD/NVMe → 共享存储
• 内存驱逐机制：
  • BatchEvict()：水位触发（默认 >95% 触发，驱逐 5%）
  • LRU 策略：优先驱逐冷数据，保护正在访问的 Block
• 磁盘/后端驱逐：支持 FIFO / LRU 策略（基于 StorageBackend 配置）
• 热点保护 (Pin 机制)：
  • soft_pin / hard_pin 保护关键 KVCache 不被驱逐
• 写回策略 (Write-back Policies)：
  • write_through（立即写回）、write_back（驱逐时写回）等策略平衡 I/O 与命中率
2.4 内存生命周期管理
• 预分配 / 按需扩缩容：MountSegment / UnmountSegment 实现动态注册与卸载
• 碎片管理：基于 OffsetAllocator 的 Bin 机制（256 Bin），减少外部碎片，无需后台 Compaction
• 异步回收：RAII 机制（AllocatedBuffer）自动释放，配合 Transfer Engine 异步解注册
