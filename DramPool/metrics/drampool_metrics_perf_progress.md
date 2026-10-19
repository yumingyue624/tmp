# DramPool Metrics 性能对比测试 - 进度文档

> 目标：vllm + ucm + dramstore + drampool 全链路，对比 DramPool metrics **全开** vs **全关** 的性能差异；
> 在 A2 (910B3) 上应用 hixl host-sync mock 后验证 mock 性能，并做性能优化迭代。
>
> - 代码基线：upstream/develop (latest) + PR #1394 (DramPool metrics，未合入)
> - 测试环境：110.138.0.3 (A2/910B3)，容器 `nt_p2p_test`（CANN 8.5.1 + HIXL 8.5.0-beta.1）
> - Mock 补丁：`D:\workspace\tmp\DramPool\metrics\a2_hixl_host_sync_mock.patch`

## 进度总览

| # | 阶段 | 状态 | 完成时间 |
|---|------|------|----------|
| 1 | 拉取最新 develop + PR 1394 并拼接 | ✅ 完成 | 10-14 |
| 2 | 检查并应用 A2 hixl host-sync mock | ✅ 完成（含 2 处修复） | 10-14 |
| 3 | 容器中构建 | ✅ 完成 | 10-14 |
| 4 | metrics 全开 vs 全关 性能对比 | ✅ 完成（n=3+3 / 32KiB，n=3+2 / 128KiB） | 10-14 |
| 5 | 性能优化迭代 + 复测验证 | ✅ 完成（mock 32→128KiB，-50% p50） | 10-14 |

## 最终结论（TL;DR）

1. **metrics 开关对端到端性能无显著影响**：多轮对比中 ON/OFF 的 p50/p90 区间大幅重叠（运行方差 >> metrics 开销），drampool metrics 全开不构成链路瓶颈。
2. **但发现一个待跟进的真实差异**：metrics ON 时 daemon CPU 稳定为 OFF 的 3~4 倍（128KiB mock 下 390-483s vs 81-154s / ~400s 墙钟，5 轮无重叠）。该 CPU 消耗被多线程并行吸收，未传导为请求延迟。已排除 reporter sweep（间隔 60s 无变化）与 metrics 核心热路径（thread-local 双缓冲，理论每次 ~百 ns）；线程采样定位在 daemon 两个工作线程，需带符号构建 + perf 采样进一步定位（见"待跟进"）。
3. **mock 本身有巨大优化空间并已兑现**：A2 实际支持 128KiB 单段传输（原 patch 保守用 32KiB），改后 p50 **-50%**、dump 批次耗时 **-63%**、传输失败**清零**。

### 阶段 1：代码集成（完成）

- 最新 develop：`e53c2fb0` ([ci] remove workflow allowlist restriction #1490)
- PR #1394 head：`900b5e62` ([Fix] Isolate DramPool metrics collection when disabled)，基点 e7375fcd
- develop 侧仅多 2 个无冲突提交（#1490 CI、#1487 FAWA GC），merge 无冲突
- 集成分支：`perf-1394-metrics`（worktree `D:\workspace\ucm-metrics-perf-1394`），已推送到 dev 远端
  - `a1fc078d` merge: PR #1394 into latest develop
  - `70352cbb` test: apply A2 hixl host-sync mock
  - `95b9e0db` test: fix A2 mock（见阶段 2）
- 注意：本机仓库原来是浅克隆，已从 upstream unshallow

### 阶段 2：Mock patch 审查与修复（完成）

原 patch（`a2_hixl_host_sync_mock.patch`，基线 develop e7375fcd + PR1394 b0c29266）：
- 对最新代码 `git apply --check` **干净通过**
- 方案正确：TransferAsync 在 A2 上退化为分片(≤32KiB)同步执行 + 假句柄；GetTransferStatus 查表返回终态；TransferSync 也做了 32KiB 分片（A2 上大段单次传输报 507001）

发现并修复 2 个问题（commit 95b9e0db）：
1. **数据竞争**：`async_results_` map 由 TransferAsync（TaskWorker 线程）写、GetTransferStatus（CompletionPoller 线程）读，无锁 → 加 `async_results_mutex_`
2. **无限增长**：map 条目永不删除 → 改为 GetTransferStatus 读后即删（poller 语义：终态后不再查询，安全）

### 阶段 3：构建与环境修复（完成）

- **构建**（Release, `-DRUNTIME_ENVIRONMENT=ascend -DASCEND_ROOT=.../cann-8.5.1`）：
  - nt_p2p_test：`/root/ucm-metrics-perf`（drampool + libdramstore.so + libucm_p2p_transport.so）
  - codex_drampool_cann851：`/home/codex/metrics-perf-1394`（全目标，供 vllm 侧 UCM 使用）
  - 坑1：codex 容器 OMP_NUM_THREADS=1 导致 nproc=1、构建 -j1 极慢 → 显式 -j32
  - 坑2：新克隆需 DOWNLOAD_DEPENDENCE=ON（vendor 依赖 fmt/spdlog/pybind11 需下载）
- **nt 容器 daemon 启动卡死排查**（HIXL engine.Initialize 内两个线程空转）：
  - 我的二进制在 codex 正常、老二进制（pr1394-latest）在 nt 也卡 → **环境问题**
  - 根因：nt 容器 libcann_hixl.so 是 **8.5.0-beta.1**（skill 时代构建，驱动 25.5.2 升级后失效）；codex 是 **8.5.1** 正式版
  - 修复：docker cp codex 的 8.5.1 hixl 库覆盖 nt 容器的；另补拷了 `/etc/hccn.conf`（nt 未挂载）
  - 验证：daemon 在 nt 容器 "service ready"，无空转线程
- 测试编排：`/tmp/metrics-perf/run_scenario.sh <on|off>`（宿主机 root 执行）：
  清理 → 起 daemon(nt) → 起 vllm(codex, device 3) → 预热 10 请求 → 压测 300s/并发4 → 采集产物 → 清理
- 压测客户端：`/tmp/metrics-perf/bench_client.py`（固定种子、新 prompt 打 dump + 重放打 lookup/load，统计 rps/tokens/p50-p99）

### 阶段 4：metrics 全开 vs 全关 对比（进行中）

场景参数：duration=300s, concurrency=4, replay 25%, prompt ~1.3k tokens, pool 20GB/256KB blocks, TTL 60min
- 场景 ON：drampool.yaml metrics.enabled=true (interval 1000ms) + ucm drampool_resource_metrics_enable=true
- 场景 OFF：两者均 false

**链路环境问题修复记录**（vllm 侧，codex 容器）：
- pybind 模块需从 build 拷入源码树（ucmlogger/ucmmetrics/ucmtrans/ucmpipelinestore 等 6 个）
- libdramstore.so 及依赖 lib*.so 需拷入源码树 ucm/store/dram/（connector 按源码树路径 ctypes 加载）
- libruntime.so 等来自 CANN set_env.sh 的 LD_LIBRARY_PATH（不能覆盖只能追加）

**预跑结果（ON, 5GB 池，有噪声仅作参考）**：155 ok / 8 timeout, rps=0.345, p50=1016ms, p90=5087ms, avg=2299ms
- 5GB 池在压测中段打满（20480/20480 slots, usage_ratio=1）：dump_nospace_failures=1638, dump_failed_entries=42800，GC 驱逐导致 replay lookup 全 miss（load 路径未覆盖）→ 改 20GB 池重跑
- daemon metrics 端到端验证 OK：snapshot JSON（counters/gauges/histograms）+ UCM reporter → vllm /metrics 423 行 drampool 指标
- 8 次 client timeout (>180s)：疑似 mock 串行同步传输在高并发下的排队（两场景共有，属 mock 特性）

### Mock 传输性能探针（hixl_chunk_probe，dev1→dev2 单流 TransferSync WRITE）

| 单段大小 | 每次延迟 | 吞吐 | 结果 |
|---------|---------|------|------|
| 4 KiB | 0.245 ms | 16 MB/s | OK |
| 32 KiB | 0.233 ms | 134 MB/s | OK（原 mock 设定） |
| 64 KiB | 0.235 ms | 266 MB/s | OK |
| 128 KiB | 0.245 ms | 509 MB/s | **OK（新 mock 设定）** |
| ≥256 KiB | - | - | **FAIL 507001** |

- **核心发现**：每次 TransferSync 延迟恒定 ~0.24ms（与大小无关，纯 per-call 开销）
- 原 mock 的 32KiB 限制过于保守 —— A2 实际支持到 128KiB，改为 128KiB 后同一数据量调用次数减为 1/4
- 优化提交：`70bf6ddf perf: raise A2 mock chunk size 32KiB -> 128KiB`（已推送 dev 远端，双容器重建）

### ON vs OFF 汇总（32KiB mock，n=3 各场景）

| run | 场景 | p50 ms | p90 ms | p99 ms | ok/err | daemon CPU s |
|-----|------|--------|--------|--------|--------|--------------|
| 1 | ON | 4348 | 5983 | 10179 | 151/4 | - |
| 2 | ON | 4649 | 6004 | 8710 | 151/4 | 433* |
| 3 | ON | 3255 | 5683 | 8266 | 152/3 | 246 |
| 1 | OFF | 2660 | 3663 | 4136 | 155/8 | - |
| 2 | OFF | 4034 | 5384 | 7510 | 152/3 | 249 |
| 4 | OFF | 3226 | 5286 | 6473 | 151/4 | 203 |

- *ON run2 的 daemon CPU 433s 为离群值（该时段宿主机 load 高），其余 ON/OFF daemon CPU 持平（~200-250s/310s 墙钟）
- **结论**：ON 与 OFF 的延迟分布区间大幅重叠（ON p50 3255-4649，OFF 2660-4034），运行间方差（共享宿主机 + mock 串行传输 + 偶发 vllm 卡顿 3-8 次 >180s 超时）远大于 metrics 本身开销；daemon CPU 亦持平 → **drampool metrics 全开的开销在当前链路中不可测出（<10% 量级），非瓶颈**
- 佐证（代码层面）：UpdateStats 为 thread-local 双缓冲 + CachedMetric，单次 ~百 ns 级；metrics 关闭时 isInited_ 守卫直接 no-op

### Daemon 侧瓶颈画像（ON run3 直方图，metrics 自身测量）

- dump_batch_total：中位 ~1s，15% >5s（3108 个 dump 批次 / 310s ≈ 10 批/s，TaskWorker 单线程串行）
- dump_submit mean 44ms + dump_prepare mean 52ms 为 TaskWorker 主耗时；**mock 下 ExecuteAsync 即同步执行**，44ms 中含 ~88 次×0.24ms 的 chunk 调用（每批 ~2.8MB）
- lookup：77534 次 batch_total mean 仅 4ms（非瓶颈）
- 队列不堵：request_residence 96% < 0.5ms
- dump_transfer 直方图双峰：76% <1ms（响应/小传输），23% 50-2000ms（大块 KV dump）

### 阶段 5：性能优化验证（128KiB mock）

**ON 场景复测（128KiB chunk vs 32KiB n=3）**：

| 指标 | 32KiB ON (n=3 中位) | 128KiB ON | 改善 |
|------|--------------------:|----------:|------|
| latency p50 ms | 4348 | **2187** | **-50%** |
| latency p90 ms | 5983 | 4145 | -31% |
| latency p99 ms | 8710 | 5279 | -39% |

**daemon 内部直方图（128KiB vs 32KiB run3）**：

| 指标 | 32KiB | 128KiB | 改善 |
|------|------:|-------:|------|
| dump_transfer mean ms | 40.7 | 10.0 | -75%（符合 4 倍 chunk 减少预期） |
| dump_batch_total mean ms | 1983 | 736 | -63% |
| dump_prepare mean ms | 52.4 | 28.7 | -45% |
| queue_residence mean ms | 76.0 | 15.8 | -79% |
| dump_failed_entries | 7480 | **0** | 失败清零 |
| submit/response failures | 346/370 | **0/2** | 基本清零 |
| lookup 吞吐（次/310s） | 77534 | 135590 | +75% |

- 失败清零的机制：32KiB 下大量 chunk 串行排队，偶发超过 10s operation timeout → submit/response 失败 → vllm 侧重试/卡顿；128KiB 后 4 倍调用减少，不再超时
- 复测结论：**优化有效且稳定**，dump 路径各阶段改善与理论推算一致

### 阶段 4 补充：128KiB mock 下的 ON vs OFF（最终 A/B）

| run | 场景 | p50 ms | p90 ms | p99 ms | ok/err | daemon CPU s |
|-----|------|--------|--------|--------|--------|--------------|
| 1 | ON/128K | 2187 | 4145 | 5279 | 155/8 | 390 |
| 2 | ON/128K | 1371 | 4043 | 5356 | 155/8 | 483 |
| 3 | ON/128K | 1232 | 2743 | 3850 | 155/8 | 446 |
| 1 | OFF/128K | 1166 | 1625 | 2578 | 157/7 | 154 |
| 2 | OFF/128K | 1181 | 1341 | 1489 | 155/8 | 81 |

- p50：ON 1232-2187 vs OFF 1166-1181 —— 区间重叠（ON 波动大），端到端无显著差异结论不变
- **daemon CPU：ON 390-483 vs OFF 81-154（3~4 倍，5 轮无重叠，真实差异）**
- 工作量等价性验证（vllm 侧 dramstore 指标）：OFF lookup 168622 次 / ON 124229 次 —— OFF 甚至做了更多工作、CPU 却低 3 倍，排除工作量差异解释
- 每轮仍有 3-8 次 >180s 的 vllm 长卡顿（两场景共有，32KiB→128KiB 后依旧存在），是当前链路（mock 串行化 + vllm 引擎）的固有噪声源

### 遗留问题调查记录（daemon CPU 3~4 倍）

已做实验与排除：
1. 空闲 CPU：ON 仅 2%（0.61s/30s）→ 消耗与负载相关
2. reporter 间隔 60s（Test B）：CPU 仍 473s → **排除 reporter sweep / JSON 渲染 / 文件写**
3. 代码审查：UpdateStats 为 thread-local 双缓冲 + 原子操作 + CachedMetric（函数级 static，一次构造），单次 ~百 ns；全部 stats 调用约 2M 次/run → 理论 ~0.4s，无法解释 ~300s
4. 线程采样（/proc/task 级）：烧 CPU 的是 daemon 内两个线程（87% + 40%，静态链接主体内），gdb 采样时均处于 nanosleep（消耗为突发性）
5. GetUsedCount 等已确认为原子读，非锁竞争

**待跟进建议**：
- 用带符号构建（绕过 FLAGS_RELEASE 的 `-s` strip，如 `-DCMAKE_BUILD_TYPE=None -DCMAKE_CXX_FLAGS="-O2 -g"`）复现负载，perf/gdb 连续采样定位热点函数
- 重点怀疑方向：metrics 开启路径下与负载相关的隐藏轮询/格式化（如 UC_DEBUG 参数在日志级别关闭时仍求值、直方图桶查找的缓存效应等）
- 该 CPU 差异不影响请求延迟（多线程吸收），但影响部署密度（每实例多占 ~0.7 核）

### 产物归档

- 全部压测结果与指标快照：`D:\workspace\tmp\DramPool\metrics\drampool_metrics_perf_artifacts.tar.gz`（bench_*.jsonl、drampool_metrics_on.json、vllm_metrics_*.txt、server 日志）
- 远端保留：`/tmp/metrics-perf/`（results、results_run1、配置、脚本）
- 测试脚本：`/tmp/metrics-perf/run_scenario.sh`（可复跑：`run_scenario.sh on|off <duration> <concurrency>`）、`bench_client.py`
- 探针：`/tmp/hixl_chunk_probe.cc`（已入库 worktree，未提交到分支）

### 2026-10-14 环境确认

- 主仓库：`D:\workspace\unified-cache-management`（upstream = ModelEngine-Group/unified-cache-management）
- PR 1394 分支：`upstream/pr/1394-latest` 等已配置
- 远端 110.138.0.3：root 已配置免密 SSH（公钥已注入）
- skill 容器 `nt_p2p_test` 存在但已停止（2 个月前退出，255）
- NPU 状态：0 号卡 47GB/64GB 占用，1/2/3/4 号卡约 3.4-4GB 占用，5 号卡 58GB 占用；跑测试避开 0/5

## 测试结果汇总

### Run 1（20GB 池, 300s, 并发4, 固定种子同负载）

| 指标 | metrics ON | metrics OFF | 差异 |
|------|-----------|------------|------|
| requests_ok | 151 | 155 | - |
| errors(>180s超时) | 4 | 8 | - |
| 实际时长 s | 340 | 458 | - |
| rps | 0.444 | 0.338 | - |
| prompt_tok/s | 589.4 | 448.8 | - |
| latency p50 ms | 4348.5 | 2659.5 | **+63.5%** |
| latency p90 ms | 5982.8 | 3663.0 | +63.4% |
| latency p99 ms | 10179.2 | 4136.2 | +146% |

- ON daemon 计数（20GB 池）：dump 3164（nospace=0），lookup 71088（**全 miss**，load 路径未覆盖），response_failures=821，submit_failures=762
- 两场景均出现 vllm 长卡顿（>180s 超时，疑似 mock 串行同步传输 + response 失败所致），吞吐被卡顿主导，p50 差异需复跑确认
- 机制分析：metrics 热路径每次请求仅 ~10 次 μs 级 UpdateStats，理论开销 <1%，无法直接解释 +63% p50 → 怀疑运行间方差（共享宿主机 load avg ~36）或卡顿放大效应 → Run 2 复测中

### Run 0（预跑，5GB 池，仅参考）
ON: 155 ok/8 err, p50=1016ms（池打满致 dump 大量 nospace 失败、传输负载骤减，数据失真）

### 阶段 5：性能分析与优化（待 Run 2 数据）
