# UCM Metrics 会议笔记对照解读

> 对应 develop @ b23adc9 的代码。把之前那次讲 metrics 的录音笔记逐段和代码对上，纠正口误，并回答笔记里留下的问题。
> 涉及文件都在 `ucm/` 下，行号以当前仓库为准。

## 0. 全局一张图

```
C++ 业务代码 ──┐
               ├─► libmetrics.so (UC::Metrics, 进程内单例)          ← 唯一数据源
Python 业务代码 ┘        ▲
   (ucmmetrics pybind)   │ update_stats() 打点
                         │
        ┌────────────────┴─────────────────────┐
        │        MetricsDispatcher (Python)     │  一次 drain, 两份 consumer 缓冲
        └────────┬──────────────────┬──────────┘
   consumer: vllm_connector   consumer: multiproc
        │                         │
  get_kv_connector_stats()   PrometheusStatsLogger
  (vLLM scheduler 拉取,       (prometheus_client 写
   ZMQ worker→scheduler 回传,  PROMETHEUS_MULTIPROC_DIR
   汇聚到唯一 API server)       下 per-pid mmap 文件,
        │                       /metrics 抓取时现场聚合)
        └────────► vLLM API server /metrics ◄──────┘
                         ▲
                         │ Prometheus 抓取
外部独立进程(如 YuanRong Worker): 定时写 resource log 文件
                         │
             YuanRongResourceReporter(UCM 侧后台线程, host 级 flock 选主)
                         └─► ucmmetrics.update_stats() 回流进上面的链路
```

两个 consumer 常量定义在 `ucm/metrics_config.py:36-37`：

```python
MULTIPROC_CONSUMER = "multiproc"          # 共享目录方式
VLLM_CONNECTOR_CONSUMER = "vllm_connector" # 接口反射方式
```

YAML 里 `consumers: {vllm_connector: true, multiproc: true/false}` 控制开关（`ucm/metrics_config.py:97-103`），默认配置只开 vllm_connector（`ucm/default_metrics_config.py:734`）。

---

## 1. "不管是 cpp 还是 python 都通过这一个 Metrics.so 打点" —— 对

构建定义在 `ucm/shared/metrics/CMakeLists.txt`：

- `:2` `add_library(metrics SHARED ...)` → **libmetrics.so**，C++ 域实现；
- `:9-10` `pybind11_add_module(ucmmetrics ...)` 并 `target_link_libraries(ucmmetrics PRIVATE metrics)`。

所以 Python `from ucm.shared.metrics import ucmmetrics` 调进来的就是同一个进程内的同一个 `Metrics::GetInstance()` 单例。C++ 侧打点示例（POSIX store）：

```cpp
// ucm/store/posix/cc/posix_store.cc:172
UC::Metrics::UpdateStats(NAME_TO_METRIC_ID("posix_lookup_query_blocks_total"), ...);
```

Python 侧打点示例（observability 消费前的任何一处）：

```python
# ucm/store/yuanrongstore/resource_reporter.py:216
ucmmetrics.update_stats(updates)   # dict 形式批量打点, pybind 绑定见 metrics.py.cc:37-38
```

"用的不是 metrics 公开的接口，相当于自己实现了一个 metrics 聚合库" —— 对。没有直接用 prometheus_client（它是纯 Python，C++ 无法共用其注册表和计数），也没有引入 C++ 版 prometheus，而是自研了一个薄的"线程本地缓冲 + 后台聚合"库，整个对外 API 只有 5 个函数（`ucm/shared/metrics/cpy/metrics.py.cc:33-53`）：`set_up / create_stats / update_stats(×2) / get_all_stats_and_clear`。笔记里"没这个需求（跨语言聚合）可以直接用 Prometheus 的东西"这个结论成立。

## 2. "每个线程写本地 buffer，双 buffer 切换，聚合时交换" —— 对，代码逐一能对上

### 2.1 thread-local buffer，注册进共享 list（"buffer 是自己的，list 是共享的"）

`ucm/shared/metrics/cc/domain/metrics.h:173-175`：

```cpp
std::list<std::shared_ptr<MetricBuffer>> buffers_;              // 共享 list
static thread_local std::shared_ptr<MetricBuffer> threadBuffer_; // 每线程一份
static thread_local bool isRegisteredThread_;
```

线程**第一次打点**时走 `RegisterCurrentThread()`（`metrics.cc:129-135`）：拿写锁把自己的 threadBuffer_ push 进共享 `buffers_`。之后打点只写自己的 map，**零锁、零同步**——这就是笔记说的"这样不会有多线程同步的问题"。

### 2.2 双 buffer（"一个写、一个聚合，会切换，原子变量控制"）

`metrics.h:61-128` 的 `MetricBuffer`：

```cpp
InnerBuffer innerBufs_[2];        // :77  双缓冲
std::atomic<int> writeIdx_;       // :78  当前写哪一块
std::atomic<int> activeWriteIdx_; // :79  当前是否有写者（NO_ACTIVE_WRITER=-1）
```

- 写路径：`UpdateStats` → `WriteGuard`（RAII，`metrics.h:81-95`）→ `BeginWrite()` 用 CAS 式循环拿到当前 `writeIdx_` 并标记自己是活跃写者（`metrics.h:104-112`），析构时 `EndWrite()` 清掉标记。
- 聚合路径 `GetAllStatsAndClear()`（`metrics.cc:167-215`）：对每个线程 buffer：
  1. `SwitchBuffer()`：原子交换 `writeIdx_`，返回旧块号（`metrics.h:97-102`）——此后新写入落到另一块；
  2. `WaitNoActiveWriter(oldIdx)`：自旋 yield，等在旧块上的在途写者退出（`metrics.h:116-121`）——对应笔记"现在有没有人写"这个原子变量的第二个作用；
  3. 读旧块并合并，然后 `ClearReadBuffer()`（`metrics.cc:208`）。

### 2.3 CachedMetric static 缓存 ID（"string 哈希太重，用 static 只 hash 一次"）

`ucm/shared/metrics/cc/api/metrics_api.h:47-51`：

```cpp
#define NAME_TO_METRIC_ID(name)                          \
    []() -> ::UC::Metrics::CachedMetric& {               \
        static ::UC::Metrics::CachedMetric metric{name}; \
        return metric;                                   \
    }()
```

函数局部 `static` 在 C++11 后线程安全且只初始化一次，所以每个打点位置持有一个进程级 `CachedMetric{name, std::atomic<MetricId> id, seenEpoch}`（`metrics.h:50-59`）。解析逻辑 `ResolveCachedMetric`（`metrics.cc:116-127`）：

- id 已缓存 → 直接返回（一次 relaxed load，无哈希）；
- 未缓存 → 到 `nameToId_` map 查一次（注册发生在 `CreateStats`，`metrics.cc:62-71`），存进 atomic；
- `registerEpoch_`（`metrics.cc:71,121-122`）用来感知"查询之后又有人注册了新指标"，避免在 `CreateStats` 之前打点的线程把 INVALID 永久缓存住。

之后所有打点都以 `MetricId`（uint32）作为 `unordered_map<MetricId, double>` 的 key（`InnerBuffer`，`metrics.h:62-65`），正是笔记说的"后面只用 vector+ID 访问"。

### 2.4 "updateStats 对 counter/gauge/histogram 使用一样，只是聚合逻辑不同"

打点入口一个 `UpdateStats(id, value)`，按注册时的类型分派（`metrics.cc:141-156`）：

| 类型 | 线程内写入 | 跨线程聚合 (`GetAllStatsAndClear`) |
|---|---|---|
| counter | `+= value` | 累加 `totalCounter[*name] += value`（:186） |
| gauge | `= value`（覆盖） | **覆盖** `totalGauge[*name] = value`（:191） |
| histogram | `lower_bound` 找桶 +1、累加 sum（:149-152） | 按桶累加 + sum 累加（:194-207） |

笔记"gauge 只记录最新值，多个进程/线程都想打一个值时它决定记录哪个"——在本库内体现为**聚合时的后写覆盖**（跨线程遍历 list 时的顺序覆盖）；注意这个顺序是不确定的，同一聚合窗口内多线程打同名 gauge 时"最终留哪个"没有时序保证，gauge 应当只有唯一逻辑写者（比如 YuanRong reporter 的 leader 机制，见 §6）。

## 3. "和 vllm 接口怎么对上：注册接口 + get 返回结构" —— 对

注册发生在启动时，两处出口各一套：

1. **注册进 C++ 库**：`setup_ucm_metrics()`（`ucm/metrics_config.py:164-187`）→ 对 YAML 里每个 definition 调 `ucmmetrics.create_stats(name, type, buckets)`。histogram 的桶必须**预先定好**（笔记原话），`NormalizeBuckets`（`metrics.cc:30-37`）自动补 `+Inf` 末桶。
2. **注册进 vLLM Prometheus 体系**：vLLM 启动时调 connector 的类方法 `build_prom_metrics()`（`ucm/integration/vllm/ucm_connector.py:2933-2955`），UCM 返回 `UCMPromMetrics`（`ucm/integration/vllm/metrics.py:190`），它在构造里把每个 definition 变成 vLLM 原生 Counter/Gauge/Histogram 对象，名字用 `vllm_connector_name`（前缀 `vllm_connector_prefix`，默认 `ucm:`，`metrics_config.py:203-206`），标签加 `worker_rank`（`metrics.py:213`）。**这就是"告诉 vLLM 有哪些指标"**。

拉取/回传：

```
worker 打点(C++ 库) ── ZMQ(scheduler↔worker 通道, vLLM 自带) ──► scheduler
scheduler 周期调 connector.get_kv_connector_stats()   (ucm_connector.py:2909)
  → dispatcher.drain_to_consumers() + get_stats_and_clear(VLLM_CONNECTOR_CONSUMER)  (:2914-2916)
  → UCMConnectorStats{counters_by_rank, gauges_by_rank, histograms_by_rank}  (integration/vllm/metrics.py:106-111)
多个 stats 由 vLLM 调 UCMConnectorStats.aggregate() 合并 (metrics.py:143-158)
  → 喂给 UCMPromMetrics.observe() → 更新 §3.2 注册的那些 Prom 对象 (metrics.py:223-296)
  → 唯一 API server 的 /metrics 暴露
```

即笔记说的"不需要自己开 HTTP server，vLLM 的 /metrics 一定会收"（"多暴露一个 metrics 部署时很麻烦，vllm 的 metrics 是一定会收的"——指复用 vLLM 抓取链路省去独立部署）。

## 4. 两种共享方式对比 & "不同指标能不能用目录方式"

| | vllm_connector（接口反射） | multiproc（共享目录） |
|---|---|---|
| 数据怎么跨进程 | 随 vLLM worker→scheduler 的 ZMQ 消息回传，scheduler 聚合 | 每进程往 `PROMETHEUS_MULTIPROC_DIR` 写自己的 `pid_<pid>.db` mmap 文件，抓取端扫目录现场聚合 |
| 谁来暴露 | vLLM API server `/metrics`（全部署只有一个） | 也挂在该 `/metrics` 下（vLLM 的 collector 开了 multiprocess 模式），前提是暴露进程能看到这个目录 |
| 跨机 DP | 天然支持：各节点的 scheduler 数据聚合到唯一 API server | **不行（笔记原话）**：其它节点 headless 起，没有 API server 进程去聚合本节点目录里的文件；目录也只是本地文件系统 |
| 逐指标开关 | 有：`vllm_connector_enabled` / `VLLM_EXCLUDED_METRICS`（`metrics_config.py:35,230-233`，dispatcher `:113-117`） | **没有**：`_register_metrics_by_type` 把 YAML 里所有指标全部注册（`observability.py:97-123`），开就全开 |
| 拉取节奏 | vLLM 控制 | 自己 10s 后台线程 `update_stats_loop`（`observability.py:93-95,198-208`） |
| 适用 | vLLM 部署内指标（当前默认路径） | "多个进程聚合**同名**指标"的场景，如各 worker 分别 inc 同一个 counter，抓取端求和 |

回答笔记里的问题——**"多个进程，如果是不同的指标，还能用目录的方式么？"**：

- 目录方式解决的是"**同一指标名**被多个进程各自写，读的时候按 pid 文件聚合（counter/histogram 求和、gauge 按 `multiprocess_mode` 处理）"。不同指标之间互不影响，都能用目录方式——只要它在 YAML 里有 definition，注册就是全量的。
- 反过来不成立的用法是：想用目录方式做"每个指标只属于某个进程、彼此不聚合"——没必要，目录机制的语义就是求和/合并。
- gauge 的多进程合并语义要在 YAML 里显式声明 `multiprocess_mode`（只对 gauge 生效，`metrics_config.py:144-148`）。默认配置里资源类 gauge 全部用 `livemostrecent`（`default_metrics_config.py:354-421`），保证读到"最近存活进程写的值"而不是把所有 rank 的用量加起来。
- 缺点正如笔记总结：**只有一个聚合服务器（API server），且只能聚合它看得见的目录**。跨机就要另想办法（YuanRong 模式，见 §5-6）。

另有一个 fork 陷阱：目录方式的指标对象是进程启动时注册的，若 worker 是从已注册进程 fork 出来的（offline 脚本场景，前面聊过），子进程会带着继承的计数用新 pid 再写一份文件 → 双算。`vllm serve` 默认 spawn 掩盖了它。

## 5. "元戎模式"：外部进程写文件，UCM 读进来再走统一出口

背景（笔记）：元戎是"UCM 内的 store 客户端 + 每 host 一个独立进程负责实际内存申请/读写"两层结构，UCM 与它的数据通信走 `yuanrong_host:yuanrong_port`（HTTP/RPC，`resource_reporter.py:270` 只是拼 endpoint 标识）。独立进程的打点进不了 vLLM 进程内的 C++ 库，所以：

> "没有暴露 http 接口，写到一个文本文件里，UCM 开线程定时读，再走我需要的出口暴露"

### 5.1 写方（元戎 Worker，独立进程，不在本仓库）

定时往 `yuanrong_resource_log_path` 追加一行 JSON：`{"event":"resource_snapshot","version":"v0","time":...,"metrics":{"oc_hit_num":{...},"shared_memory":{...},"spill_hard_disk":{...}}}`，字段解析见 `parse_yuanrong_resource_snapshot()`（`resource_reporter.py:41-84`）。

### 5.2 读方（UCM 进程内后台线程）

`YuanRongResourceReporter`（`resource_reporter.py:120-259`）：

- **启动时机**：pipeline 组装 Stack 了 YuanRong store 时启动（`ucm/store/pipeline/connector.py:327-340`）；且只有 `device_id < 0` 的进程启动（`resource_reporter.py:267`）——即 **scheduler/core 进程启动，每张卡的 worker 进程不启动**。
- **host 级选主**（笔记"每个 host 加了把锁，多个 scheduler 只有一个抢锁来读"）：对 `/dev/shm/ucm_yuanrong_metrics_<sha256(endpoint|logpath)前24位>.lock` 执行 `fcntl.flock(LOCK_EX | LOCK_NB)`（`:133-136,189-194`）。抢到的进程起循环线程，没抢到的线程直接退出（`:160-161`），leader 状态通过 gauge `yuanrong_resource_log_reporter_leader=1`（`:81`）暴露——只有 leader 上报这个 1，看板上能判断 leader 有没有活。
- **循环**：每 `yuanrong_resource_metrics_interval_sec`（默认 15s，`:274`）执行 `_collect_once()`（`:211-217`）：
  1. 从文件尾部倒着读**最后一条完整行**（分块回退 + 丢弃可能被截断的尾行，`:219-240`）；
  2. 解析出 counters（4 个命中数）+ gauges（DRAM/SSD used/capacity/ratio、快照时间戳）（`:26-31,55-84`）；
  3. **counter 差值化**：元戎文件里的 hit_num 是累计值，UCM 的出口语义是增量——把上次快照存进 `/dev/shm/...json` 状态文件（原子写，`:255-259`），本次 `counter_deltas()` 求差（`:87-99`，值回退视为重置取新值）；
  4. `ucmmetrics.update_stats(gauges | deltas)` 打进本进程 C++ 库（`:216`）；读文件失败打 `yuanrong_resource_log_read_errors_total`（`:164,172-174`）。

一旦进了 C++ 库，后面就是普通指标：**vllm_connector 出口自动带上它**（ZMQ 回传 → 唯一 API server），"其它节点的元戎指标通过 vllm 聚合机制一起暴露"（笔记原话）就是这么实现的——每个节点的 scheduler 读不到别的节点的元戎文件，但每个节点把自己的 vllm_connector stats 通过 vLLM 的通道汇到唯一的 API server。

### 5.3 笔记里另外两个提法的定位

- "给个文本文件路径，定时写文本文件"作为**方案本身**还有一种走法：Prometheus node_exporter 的 textfile collector 直接读文本，完全不经过 UCM——笔记说元戎"没暴露 http、改 Prometheus"接近这个思路，但**本仓库实现的是 §5.2 的"UCM 读文件再走统一出口"**，没有用 textfile collector。区别在于后者把外部指标并入了 UCM 的名字空间、labels（model_name/worker_id 或 worker_rank）和两条 consumer 出口。

## 6. HBMist（笔记听记成 "hidmist"）与内存池（DramPool）

- 笔记里读不准的词应是 **HBMist**（华为 HBM 内存池化产品）。仓库代码里没有 `hbmist` 字样（全库 grep 无结果），所以这条是会议背景，不是代码事实。它对应笔记"内存池就是加强版的 cache store"——DramPool 一类内存池 store 在 UCM 视角就是一个新 store。
- "hidmist 的 host 指标走 scheduler 和 worker 之间的消息队列回传" —— 即走 §3 的 vllm_connector 路（ZMQ 回传），不是目录方式。
- **"drampool 是不是可以像元戎这样，写个文件，所有都走 UCM 这一套？"** —— 从架构上完全可行，且和 DramPool 进程模型吻合（每节点一个独立 DramPool 进程，`drampool.md`）：
  1. DramPool 进程把资源/状态指标周期性写成本地 JSON 快照（模仿 `resource_snapshot` 格式，版本字段留好）；
  2. UCM 侧仿照 `YuanRongResourceReporter` 写一个 DramPool reporter：`/dev/shm` flock 选主（防多 DP 重复读）、tail 最后完整行、counter 差值化、`ucmmetrics.update_stats()` 回流；
  3. 指标走默认 vllm_connector 出口聚合到唯一 API server，跨节点不需要任何新通道。
  （注意：`drampool.md` 是 DramPool 设计权威文档，这条属于扩展方向，落地前需要补进设计文档。）
- 每个 store 用自己的名字前缀（`cache_*`、`posix_*`、`mooncake_*`、`yuanrong_*`），不同 connector 打不同 name，都写在同一份配置里；"没打过 metrics 的不会展示"——C++ 库里没人 `UpdateStats` 的指标，`GetAllStatsAndClear()` 快照里就没有这个 key，出口自然不输出（dispatcher `:61-62` 空快照直接返回）。

## 7. 两个配置文件必须一起改

| 文件 | 角色 |
|---|---|
| `examples/metrics/metrics_configs.yaml` | 配置**示例** + 用户可指定 `metrics_config_path` 用来自定义（`metrics_config.py:54-76` 加载 YAML） |
| `ucm/default_metrics_config.py` | 内置默认配置（**Python dict，结构和 YAML 完全一致**，`get_default_metrics_config()`） |

生效逻辑 `load_launch_metrics_config()`（`metrics_config.py:79-90`）：启动配置 `enable_metrics`（默认 True，`:93-94`）开着时，给了 `metrics_config_path` 就读 YAML，否则用默认 Python dict。所以：**不指定 YAML 就用 Python 默认值，两边是同一份清单的两套载体，加/删指标必须同步改两处**。

YAML 能删指标的真实原因（笔记"yaml 存在的原因，有些占时间的不打了可以删掉"）：打点代码是编译进 so 的删不掉，但**从配置里删掉 definition 后 `CreateStats` 不会注册它**，C++ 库里 `ResolveMetricId` 查不到 → `UpdateStats` 直接 return（`metrics.cc:87-89`），这条指标就完全消失，连开销都省了。

新增一个指标的完整改动面（笔记"就是改这俩配置文件 + updateStats 打点的地方"）：
1. 两处配置各加一条 definition（name/type/documentation，histogram 加 buckets，gauge 按需 `multiprocess_mode`）；
2. 业务代码打点：C++ `UC::Metrics::UpdateStats(NAME_TO_METRIC_ID("..."), v)` 或 Python `ucmmetrics.update_stats({...}, v)`；
3. 不想走 vllm 出口的加 `vllm_connector_enabled: false`（或进 `VLLM_EXCLUDED_METRICS`）。

## 8. "一个大时间拆三段，整体靠 Grafana query" —— 对

代码只负责"耗时操作前后记时间、按阶段各打各的"：

```cpp
// ucm/store/posix/cc/trans_queue.cc:102  队列等待段
UC::Metrics::UpdateStats(NAME_TO_METRIC_ID("posix_load_queue_wait_duration_ms"), ...);
// ucm/store/cache/cc/trans_manager.h:69-78  总时长+带宽一起打
UC::Metrics::UpdateStats(NAME_TO_METRIC_ID("cache_load_duration_ms"), costMs);
UC::Metrics::UpdateStats(NAME_TO_METRIC_ID("cache_load_bandwidth_gbps"), bwGbps);
```

每个阶段是一个独立 histogram（桶在配置里定好），"整体 breakdown、占比、分位数"全部是 PromQL 查询层的事（`histogram_quantile`、`rate()` 相除），不在代码里做算术。NPU 侧时间短不好统计的操作，用"前后记时间点"或干脆只靠剪出来的几个段。

## 9. "数据量可控 / 默认开着没性能影响" —— 对

- 每个进程出口大小 = Σ(counter 1 个 double + gauge 1 个 double + histogram 桶数个 uint64)，指标数由配置决定（默认清单百量级），回传和 ZMQ 消息体积可控（笔记原话）。
- 打点路径 = atomic load 缓存 id + 写自己线程的 map（无锁、无系统调用、无内存分配之外的开销）；聚合是后台线程按 `log_interval`/vLLM 拉取节奏做的，摊薄后开销可忽略。所以 `enable_metrics` 默认 True（`metrics_config.py:93-94`）。

---

## 附：笔记逐条勘误/确认表

| 笔记说法 | 结论 | 代码位置 |
|---|---|---|
| cpp/python 共用一个 metrics lib，自研而非公开接口 | 确认 | `shared/metrics/CMakeLists.txt:2,9-10` |
| 线程本地 buffer，首次打点注册进共享 list | 确认 | `metrics.cc:129-135` |
| 双 buffer 切换 + 原子变量控制写哪块/有没有人写 | 确认 | `metrics.h:77-79,97-121` |
| static 缓存 string→id，只 hash 一次 | 确认（另有 epoch 失效机制） | `metrics_api.h:47-51`、`metrics.cc:116-127` |
| 两种共享方式：vllm 接口 / 共享目录 | 确认 | `observability.py` vs `ucm_connector.py:2909` |
| 目录方式只有 API server 一个聚合点，跨机 headless 节点聚合不了 | 确认 | `observability.py:72-76` |
| 目前默认走 scheduler 反查（接口反射） | 确认 | `default_metrics_config.py:734` |
| gauge 记录最新值、多写者取哪个不确定 | 确认，跨进程要靠选主 | `metrics.cc:191`、`resource_reporter.py:177-197` |
| 元戎 host 级锁选一个 reader，每 host 传一份，靠 vllm 聚合 | 确认 | `resource_reporter.py:189-194` |
| 元戎数据通信走 yuanrong_host/port，读的是文件 | 确认（endpoint 仅作 identity，快照走文件） | `resource_reporter.py:270` |
| "hidmist" | 应为 **HBMist**（内存池），仓库无此字样，属会议背景 | — |
| DramPool 可复刻元戎"写文件 + UCM 读"模式 | 方向可行，需补进 drampool.md | §6.2 |
| yaml 和 default_metrics_config.py 是两套要同步改 | 确认 | `metrics_config.py:79-90` |
| 从 yaml 删指标 = 不注册 = 不打 = 不展示 | 确认 | `metrics.cc:87-89` |
| 一次大时间拆段，聚合在 Grafana query | 确认 | `trans_manager.h:69-78` 等 |
| 数据量可控、默认开启无性能影响 | 确认 | `metrics_config.py:93-94` |
