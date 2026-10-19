# DramPool / DramStore HIXL 异步提交性能分析交接文档

更新时间：2026-08-24

目的：供一个没有此前上下文的新 Codex session 直接接手后续诊断、优化和复测。

## 1. 最高优先级约束

以下要求适用于所有 `10.218.3.*` 开发机，必须严格遵守：

1. **开发机禁止使用任何公网带宽。** 不得执行访问公网的 `git fetch/pull/clone`、`pip install`、`apt/yum`、`curl/wget`、`docker pull`，也不得访问 Hugging Face。
2. 允许调整软件和 Docker 配置，但修改应限制在隔离容器和隔离工作目录。
3. **禁止修改卡 IP、SuperPod ID、HCCL 或交付网络配置。**
4. 缺失的源码、模型、Python 包或构建产物，只能从 `10.218.3.20` 通过内网复制，或使用开发机已有文件。
5. 跳板机可以访问公网，但不能借跳板机让开发机产生公网流量。
6. 不要修改或清理同事在 `.20` 上的 `/home/drampool`；它只作为只读内网来源。
7. 每次测试前检查 NPU 占用，只使用空闲卡；测试结束后确认没有遗留 NPU 进程。

连接路径：

```bash
ssh -J jump@100.122.44.47 root@10.218.3.25
```

密码未写入共享机器上的文档，由用户在会话中提供。

## 2. 当前机器与隔离环境

- 目标开发机：`10.218.3.25`
- 硬件：Ascend A3
- 隔离容器：`codex_drampool_perf`
- 镜像：`quay.io/ascend/vllm-ascend:v0.23.0rc1-a3`
- 容器使用 privileged、host network、256 GiB shm、restart policy `unless-stopped`
- `/home/drampool` 指向 `/home/codex_drampool_perf`
- 本次 E2E 只使用逻辑设备 `0,1`
- 最后一次检查时所有 NPU 均无运行进程

主要目录：

```text
/home/drampool/nt/unified-cache-management
/home/drampool/nt/hixl
/home/drampool/nt/cann
/home/drampool/models/DeepSeek-V2-Lite-Chat
/home/drampool/hixl_async_perf_logs_20260824
```

源码、模型和缺失的 `wrapt` Python 包均通过 `.20 -> .25` 私网复制，没有使用公网。

## 3. 源码基线与当前修改

### UCM

```text
branch: dram_pool
HEAD: 8c97c5427346d7334af3bfdad3b8c54f14faba80
```

隔离 UCM checkout 中保留了同事的未提交配置修改：

```text
examples/drampool.yaml
examples/offline_inference.py
examples/ucm_config_example.yaml
ucm/store/test/e2e/scripts/run_drampool_e2e.sh
```

为降低高频日志扰动，在隔离 checkout 中对以下两个日志提交执行了 `git revert --no-commit`：

```text
8c97c542  # TransferAsync 高频指标
8b805670  # GetStatus 高频指标
```

相应三个 transport 文件现在是 staged 状态：

```text
ucm/transport/p2p/src/core/transport_manager.cpp
ucm/transport/p2p/src/protocols/hixl/hixl_instance.cpp
ucm/transport/p2p/src/protocols/hixl/hixl_transport.cpp
```

这些都只是隔离实验修改，没有 commit 或 push。**不要执行 `git reset --hard`，不要覆盖同事的配置改动。**

### HIXL

```text
version: v9.1.0
HEAD: c12f9a56ab66299f62adb7f1f3f34d92e725e856
```

当前修改文件：

```text
src/hixl/cs/hixl_cs_client.cc
src/hixl/engine/comm_engine.cc
src/llm_datadist/adxl/adxl_inner_engine.cc
src/llm_datadist/adxl/comm_channel.cc
```

`hixl_cs_client.cc` 是第一阶段试探埋点，当前 `enable_cs=false` 不会走到它。真正命中的埋点在另外三个文件中，产生六层事件：

```text
event=comm_engine_async
event=adxl_inner_async
event=comm_channel_async
event=issue_async_host_flag
event=hccl_batch
event=issue_hccl_batch
```

插桩库：

```text
/home/drampool/nt/cann/aarch64-linux/lib64/libcann_hixl.so
SHA256: bd11e18fbc31c7d5012c4fd565a93778272eaa2eb377bc09bc1b4325cf9b4d3d
```

原始库备份：

```text
/home/codex_drampool_perf/results/libcann_hixl.baseline.so
```

## 4. 已确认的实际调用路径

当前 `enable_cs=false`，实际路径为：

```text
HixlImpl
  -> CommEngine::TransferAsync
  -> AdxlInnerEngine::TransferAsync
  -> CommChannel::TransferAsync
  -> IssueAsyncBatchWithHostFlag
  -> IssueHcclBatch
  -> HcclBatchGet / HcclBatchPut
  -> aclrtMemcpyAsync(completion flag)
```

当前业务不经过 `HixlCSClient` 的 Host NBI/fence 路径。这个结论已通过实际进程 `/proc/<pid>/maps` 和命中日志确认。

## 5. 已完成验证及数据

两轮 E2E 都重启 DramPool/DramStore，使 peer/channel 冷启动可重复出现。

针对 108 段、7,962,624 bytes 数据请求：

| 指标 | 首次 peer，4 个样本 | 复用 peer，8 个样本 |
|---|---:|---:|
| DramPool `taskworker_prepare_us` 中位数 | 2311.5 us | 396.5 us |
| Transport `submit_us` 中位数 | 2172 us | 262 us |
| HIXL `CommEngine` total 中位数 | 2133 us | 233 us |
| `AcquireSharedSlot` 中位数 | 1453.5 us | 1 us |
| `HcclBatchGet/Put` 中位数 | 588.5 us | 187 us |

首次数据 peer 的 `taskworker_prepare_us`：

```text
2296, 2327, 2380, 2064 us
```

复用后的数据 peer：

```text
302, 449, 300, 644, 357, 479, 436, 284 us
```

分层结果：

- mutex 等待基本 `0-1 us`，不是锁竞争。
- context 切换、connect、channel 查找通常为个位到几十微秒。
- host flag、record、描述符转换不是主要瓶颈。
- 首次 peer 的 `AcquireSharedSlot` 为 `1.3-1.5 ms`，复用后为 `0-3 us`。
- `HcclBatchGet/Put` 首次也有数百微秒的预热开销。

## 6. 根因结论

`taskworker_prepare_us` 偶发长发生在同步返回的异步提交接口内部，但最大部分不是等待数据传输完成，而是首次访问每个 peer/channel 时的 slot 惰性初始化：

```text
CommChannel::AcquireSharedSlot
  -> TransferSlotPool::Acquire
  -> TransferSlotPool::InitSlotLocked
      -> aclrtCreateContext
      -> aclrtCtxGetCurrentDefaultStream
      -> aclrtSetStreamFailureMode
```

首次调用创建 ACL context 和 default stream；随后 `CommChannel::active_slot_` 被复用，所以 slot 部分从约 `1.45 ms` 降至约 `1 us`。`HcclBatchGet/Put` 也有首调预热，但小于 slot 初始化。

目前测量到的是 `AcquireSharedSlot` 整体耗时，尚未拆分 `InitSlotLocked` 中三个 ACL API，不能断言其中具体哪一个占比最大。

## 7. 测试完整性与脚本陷阱

每轮均为：

```text
DramPool request_done: 10
DramPool SUCCESS:      10
HIXL TransferAsync:    16
HIXL_PERF records:     96
Python Traceback:      0
script.rc:             0
```

原 `run_drampool_e2e.sh` 的 `cleanup` trap 固定 `exit 0`，可能掩盖中间失败。因此后续不能只看 shell 返回码，必须同时检查请求数、`status=SUCCESS`、Python Traceback、HIXL 事件数和退出后的 NPU 进程。

## 8. 日志和产物

统一目录：

```text
/home/drampool/hixl_async_perf_logs_20260824/
```

关键文件：

```text
analysis.md
HANDOFF.md
SHA256SUMS
build.log
hixl_transfer_summary_all.tsv
request_summary_all.tsv
run1/drampool_server.log
run1/offline_inference.log
run1/hixl_transfer_summary.tsv
run1/request_summary.tsv
run2/drampool_server.log
run2/offline_inference.log
run2/hixl_transfer_summary.tsv
run2/request_summary.tsv
```

完整性校验：

```bash
cd /home/drampool/hixl_async_perf_logs_20260824
sha256sum -c SHA256SUMS
```

## 9. 新 session 接手检查

登录后先执行只读检查：

```bash
readlink -f /home/drampool
docker ps -a --filter name=codex_drampool_perf
npu-smi info

docker exec codex_drampool_perf bash -lc '
  cd /home/drampool/nt/unified-cache-management && git status --short
  cd /home/drampool/nt/hixl && git status --short
  sha256sum /home/drampool/nt/cann/aarch64-linux/lib64/libcann_hixl.so
  cd /home/drampool/hixl_async_perf_logs_20260824 && sha256sum -c SHA256SUMS
'
```

如果卡被其他同事占用，不要抢占或 kill 对方进程，应选择经用户允许的空闲卡或暂停测试。

运行前必须设置：

```bash
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export HF_DATASETS_OFFLINE=1
export http_proxy=
export https_proxy=
export HTTP_PROXY=
export HTTPS_PROXY=
export ALL_PROXY=
export all_proxy=
export no_proxy='*'
export NO_PROXY='*'
export ASCEND_RT_VISIBLE_DEVICES=0,1
```

只有设备 0、1 仍为空闲时才能继续使用。

## 10. 推荐后续工作

1. 在 `TransferSlotPool::InitSlotLocked` 内分别测量 `aclrtCreateContext`、`aclrtCtxGetCurrentDefaultStream`、`aclrtSetStreamFailureMode`。
2. 做一个实验性 eager-slot 版本：在 channel 建连后创建 slot，比较冷请求、启动时间、context 数量和多 peer 资源占用。
3. 对比“业务请求前预热一次 peer/channel”的方案，并设计失败处理及 peer 生命周期。
4. 给正式 HIXL 指标增加环境变量开关或采样，避免永久无条件 `fprintf(stderr)`。
5. 修复 E2E 脚本 cleanup 固定 `exit 0`，增加请求成功数断言。
6. 正式合入前从干净基线制作最小 patch，不要直接提交当前混合的试探埋点和 staged 日志回退。

优化验收至少要求：两轮以上冷启动、10/10 请求成功、冷路径明显下降、热路径无回归、多 peer 不耗尽 slot/context、退出后无 NPU/context 残留。

## 11. 禁止事项

- 不得在 `.25` 上访问 GitHub、PyPI、Docker Hub、Hugging Face 或其他公网。
- 不得修改卡 IP、SuperPod ID、网卡或 HCCL 拓扑配置。
- 不得在隔离 UCM/HIXL checkout 上执行未经检查的 `git reset --hard`。
- 不得删除 `/home/drampool/hixl_async_perf_logs_20260824`。
- 不得把 A2/CANN 8.5.1 的 Host async 兼容补丁用于这台 A3/CANN 9.x 机器。
- 不得只凭脚本返回 0 判断测试成功。
