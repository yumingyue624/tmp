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
