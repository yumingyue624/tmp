# Skills 完整换机备份（2026-10-19）

本次新增完整快照，覆盖用户目录及 D:\workspace 中扫描到的 skills，特别包含全局 OpenCode、Codex 和两个 UCM 目录。旧目录仍保留供查看，完整清单以 `manifest.json` 为准。

## 完整快照恢复

需要 Python 3.9+，无需第三方包。克隆本仓库并切到包含本次提交的 `drampool_docs` 分支，进入本目录，在 PowerShell 运行：

```powershell
python .\restore.py --verify-only
python .\restore.py --home C:\Users\新用户名 --workspace D:\workspace
python .\restore.py --home C:\Users\新用户名 --workspace D:\workspace --apply
```

第二条只预览，第三条实际恢复。home 默认当前用户目录，workspace 默认 D:\workspace。原项目名称保持不变，两个 UCM 目录分别恢复各自版本。请先检查预览路径。

- `skills-snapshot.zip`：包含各 skill 的说明及配套文件，按完整内容去重。
- `manifest.json`：每个原路径、对应对象、恢复相对路径和逐文件 SHA256。
- `SHA256SUMS`：压缩包校验值。
- `restore.py`：先校验全部文件，再预检全部目标；存在不同内容的已有文件即停止，不写任何文件。先备份并移开冲突文件即可重试。不会删除目标原有文件。
- `build_snapshot.py` 与 `sources.txt`：本机快照生成工具和扫描清单。重建需要原机器上的源目录仍然存在；换机恢复只需 restore.py、manifest.json、SHA256SUMS 与 ZIP。

去重比较整个 skill 目录的相对路径与原始字节；完全一致只存一份。同名不同内容、换行差异均保留，不推断新旧替代关系。每个原路径可恢复自己的版本。

默认恢复 `active`（用户及项目）类别。插件、扩展、内置和包缓存归为 `managed`，也已归档，默认不恢复；通常应在新机重新安装插件。确需恢复这些缓存可加 `--include-managed`。复制 skill 不会恢复 MCP 服务、账户授权或外部依赖。

Downloads 中独立的 SKILL.md 归为 `reference`，只归档该文件，需手动取用，不恢复整个下载目录。旧备份本身不重复扫描；skill 内排除 .git、node_modules、__pycache__、.pytest_cache。不收集 skill 目录外的应用认证配置、SSH 私钥等。skill 中的远程主机和绝对路径需按新机调整。

扫描范围为 C:\Users\Xuuuuuun 与 D:\workspace，使用 rg 隐藏文件扫描，不跟随目录链接；其他磁盘、不可访问目录不在覆盖范围。缓存中存在未启用的历史插件版本，归档不代表当前已启用。

## 历史备份说明

以下是原备份说明，其中数量和版本推荐只适用于旧目录；本次完整快照保留全部不同版本：

# opencode skills 恢复备份（2026-10-19 生成）

换机迁移用。打包时已去重，并排除 `.opencode\node_modules` 等无关内容，只含 skill 文件。

## 目录结构与恢复方法

| 目录 | 内容 | 恢复到 |
|---|---|---|
| `1-global\` | bailian-model, hixl-transport-test | `%USERPROFILE%\.config\opencode\skills\` |
| `2-ucm-project\` | ascend-container-test（2026-07-21 版，含 `assets\0001-Fix-...patch`）、clang-format、squash-to-base-commit | `D:\workspace\unified-cache-management<任意副本>\.opencode\skills\` |
| `3-hixl-project\` | api-doc-generator, cann-toolkit-installer, gitcode-issue, gitcode-pipeline, gitcode-pr, hixl-dev, hixl-pr-review, hixl-srs-doc-generator, hixl-troubleshoot, hixl-ut-generator | `D:\workspace\hixl\.agents\skills\` |
| `4-vllm-ascend-project\` | main2main, vllm-ascend-model-adapter, vllm-ascend-release | `D:\workspace\vllm-ascend\.agents\skills\` |
| `archive\` | ascend-container-test 旧版（2026-06-16），仅备查，一般无需恢复 | — |

恢复方式：把对应目录下的 skill 子目录原样复制到上表目标路径即可。UCM 若有多个工作副本都要用，各自复制一份相同内容。

## 去重说明

原机器共 6 处 skill 目录，去重后保留 4 类：

1. `unified-cache-management`、`unified-cache-management_0`、`unified-cache-management_1` 三个目录各有 3 个同名 skill：
   - **clang-format / squash-to-base-commit**：三处内容完全一致（仅换行符差异），只保留 `_0` 版（2026-07-10）。
   - **ascend-container-test**：`unified-cache-management` 与 `_1` 完全相同（2026-06-16 旧版）；`_0` 为 2026-07-21 新版，是合并了 ASU + DramPool 的超集，另含 A2 兼容补丁。正式保留 `_0` 新版，旧版移入 `archive\`。
2. `hixl`、`vllm-ascend` 的 skill 与其他目录无重复，原样保留。

## 注意事项

- `_0` 新版 ascend-container-test 中记录了远程主机 `110.138.0.3`（Ascend A2）、容器 `codex_drampool_cann851` / `ymy_ascend_test` 等环境信息，换机后注意网络连通性。
