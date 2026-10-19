---
name: bailian-model
description: Add Alibaba Bailian (DashScope) models to OpenCode, including Qwen and third-party models like GLM, DeepSeek, Kimi
license: MIT
compatibility: opencode
metadata:
  audience: developers
  workflow: configuration
---

## 百炼模型添加指南

### API Key 配置

统一使用 API Key: `sk-aea0c6f7f9554e8e80eff8a4d1397eee`

此 Key 已配置在以下 provider:
- `alibaba-cn` (内置 provider)
- `zai-coding-plan`
- `zai`
- `bailian-coding-plan` (自定义)

### 端点说明

| 端点 | URL | 用途 |
|------|-----|------|
| OpenAI 兼容 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | 千问及第三方模型（GLM、DeepSeek 等） |
| Anthropic 兼容 | `https://coding.dashscope.aliyuncs.com/apps/anthropic/v1` | 仅部分模型支持 |

### 添加新模型步骤

#### 方法一：添加到内置 alibaba-cn provider（推荐）

对于百炼支持的模型（包括第三方），直接添加到 `alibaba-cn` provider:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "alibaba-cn": {
      "models": {
        "模型ID": {
          "name": "显示名称",
          "modalities": {
            "input": ["text"],
            "output": ["text"]
          },
          "options": {
            "thinking": {
              "type": "enabled",
              "budgetTokens": 8192
            }
          },
          "limit": {
            "context": 1000000,
            "output": 65536
          }
        }
      }
    }
  }
}
```

#### 方法二：创建独立 provider

如需使用不同端点或配置:

```json
{
  "provider": {
    "my-bailian": {
      "npm": "@ai-sdk/openai",
      "name": "My Bailian",
      "options": {
        "baseURL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "apiKey": "sk-aea0c6f7f9554e8e80eff8a4d1397eee"
      },
      "models": { ... }
    }
  }
}
```

### 常见问题

#### 1. "invalid_api_key" 错误

**原因**: 使用了错误的 API Key 或端点不匹配

**解决**: 
- 确保使用统一的 API Key
- 第三方模型（GLM、DeepSeek、Kimi 等）必须走 OpenAI 兼容端点
- 不要混用 `coding.dashscope.aliyuncs.com` 和 `dashscope.aliyuncs.com`

#### 2. 模型 ID 错误

**解决**: 在百炼控制台确认模型 ID，常见模型:
- `glm-5`, `glm-5.2`
- `deepseek-v3`, `deepseek-r1`
- `kimi-k2.5`
- `qwen3.6-plus`, `qwen3.7-max`

#### 3. 之前失败的原因

`glm-5.2` 添加失败是因为:
1. 错误地放在 `bailian-coding-plan` provider 下（Anthropic 兼容端点）
2. 使用了已回收的旧 API Key (`sk-d7f0...`)
3. 该端点不支持 `glm-5.2` 模型

正确做法: 放到 `alibaba-cn` provider，走 OpenAI 兼容端点。

### 验证模型可用

用 curl 测试:

```powershell
$body = @{ model = "模型ID"; messages = @(@{ role = "user"; content = "hi" }); max_tokens = 10 } | ConvertTo-Json -Depth 3
Invoke-WebRequest -Uri "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions" -Method Post -ContentType "application/json; charset=utf-8" -Headers @{ "Authorization" = "Bearer sk-aea0c6f7f9554e8e80eff8a4d1397eee" } -Body ([System.Text.Encoding]::UTF8.GetBytes($body)) -UseBasicParsing
```

### 当前配置

- 配置文件: `~/.config/opencode/opencode.json`
- 认证文件: `~/.local/share/opencode/auth.json`
- 账户文件: `~/.local/share/opencode/account.json`
