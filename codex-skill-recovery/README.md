# Codex Skills 完整换机备份（2026-10-19）

这是由 Codex 整理的换机快照，存放于 `codex-skill-recovery`，覆盖用户目录及 D:\workspace 中扫描到的 skills，包括全局 Codex、OpenCode 和两个 UCM 目录。完整清单以 `manifest.json` 为准。`opencode-skill-recovery` 属于 OpenCode 的独立备份，已恢复到 Codex 修改前的状态。

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
