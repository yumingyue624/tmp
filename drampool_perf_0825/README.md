# DramPool 性能测试备份（2026-08-25）

## 可直接使用的备份

- `drampool_perf_0825_core.tar.gz`：从 10.218.3.25 拉取并完成远端/本地 SHA-256 对比的核心复现包。
- `core_extracted/`：核心包的展开版本，共 222 个文件，包含两套性能证据、全部测试结果、补丁、离线依赖和 E2E 脚本。
- `metadata/`：容器/镜像配置、主机目录清单、UCM/HIXL Git 状态及完整 tracked diff。
- `local_workspace_artifacts/`：此前已经同步到本地工作区的日志、分析文档、补丁和临时证据。

核心包 SHA-256：

`8b84e74d83a4b56cd6f4b698bad90b6a5d9bc86054418cc598b0a93d4f6dfe1d`

## 关键入口

- `core_extracted/hixl_async_perf_logs_20260824/HANDOFF.md`
- `core_extracted/query_handle_perf_20260824/ANALYSIS.md`
- `core_extracted/query_handle_perf_20260824/evidence_ge_1000.log`
- `core_extracted/query_handle_perf_20260824/evidence_near_1000.log`
- `core_extracted/query_handle_perf_20260824/evidence_around_500.log`
- `core_extracted/nt/unified-cache-management/ucm/store/test/e2e/scripts/run_drampool_e2e.sh`
- `metadata/ucm_git_diff.patch`
- `metadata/hixl_git_diff.patch`

## 开发机清理结果

- 已删除容器 `codex_drampool_perf`。
- 已删除由 `/root/va023.tar` 加载的镜像 `quay.io/ascend/vllm-ascend:v0.23.0rc1-a3`（镜像 ID `611962...`）。
- 已删除新增链接 `/home/drampool` 和目录 `/home/codex_drampool_perf`。
- 已删除备份过程中生成的 `/root/drampool_perf_0825_*.tar.gz` 临时包。
- 按用户后续要求，已删除原始 `/root/va023.tar`。
- 未改动卡 IP、SuperPod ID、HCCL 或网络配置；未在开发机使用公网带宽。

## 不完整归档说明

`drampool_perf_0825_repro.partial_60MiB.tar.gz` 是跳板链路异常前收到的完整源码/构建归档前 60MiB，SHA-256 未完成，不能作为完整归档使用。保留它仅用于尽可能抢救核心包之外的前缀文件。
