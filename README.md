<div align="center">

# AstrBot 语言逻辑优化大师

[![version](https://img.shields.io/badge/version-v2.6.3-blue.svg)](https://github.com/Shtraiy/astrbot_plugin_filter)
[![AstrBot](https://img.shields.io/badge/AstrBot-%3E%3D4.16-orange.svg)](https://github.com/Soulter/AstrBot)
[![license](https://img.shields.io/badge/license-AGPL--3.0-green.svg)](./LICENSE)

**清理 AstrBot 输出中的内部痕迹，优化表达排版，并支持智能分段与列表图片渲染**
</div>

## Refactor architecture

The outbound path is split into four responsibilities:

- `outbound_pipeline.py` keeps the existing metadata, tool-trace, sensitive-content, Markdown, and style cleanup order.
- `reply_coordinator.py` owns reply locks, wake-up gates, user-priority invalidation, cooldowns, and completion callbacks.
- `message_dispatcher.py` is the single delayed follow-up sender and releases the owning reply session even when sending fails.
- `private_companion_adapter.py` contains the optional Private Companion marker detection and cancellation API lookup.

The cleanup stages are intentionally retained for compatibility. Private Companion remains optional: an unavailable adapter cannot block ordinary replies.

> **astrbot_plugin_filter** 是一个用于 AstrBot 的输出后处理插件，能够在消息发送前清理模型输出中的元数据、工具叙述和敏感信息，并提供 LLM 文风优化、智能分段、消息串行发送与列表图片渲染等能力。

## 📌 主要能力

- 清理 OneBot、MCP 等结构化元数据泄漏。
- 过滤系统路径、Shell 命令、内网 IP、API Key 等敏感信息。
- 检测到工具调用流程泄露时，直接替换为正常的用户可见提示。
- 删除工具调用过程中的内部叙述，并将工具函数名转换为更自然的中文描述。
- 使用规则或 LLM 优化 AI 味表达。
- 支持 LLM 智能分段，失败时自动降级到规则分段。
- 将常见 Markdown 语法转换为适合 QQ 展示的纯文本。
- 将分段结果按多条消息发送，并自动合并高度相似的重复段落。
- 同一群聊内按顺序发送不同用户的完整回复，避免消息交错。
- 可选：将编号列表渲染为图片发送。
- 防护群聊输入和输出内容，拦截配置词库命中及常见诱导绕过请求。
- 新群聊在一段时间或一定消息数内自动启用更严格的防护。

## 🔄 处理流程

```text
AstrBot 生成回复
        |
        v
垃圾符号清理 -> 用户称呼替换 -> 敏感信息过滤
        |
        v
工具叙述清理 -> 工具名脱敏 -> AI 味优化
        |
        v
LLM 分段/文风优化 -> 规则分段降级 -> 重复段落合并
        |
        v
Markdown 纯文本化 -> 按会话串行发送
```

## 🚀 安装

### 通过 AstrBot 插件市场

在 AstrBot 管理面板的插件市场中搜索“语言逻辑优化大师”，安装后重启 AstrBot。

### 手动安装

```bash
cd AstrBot/data/plugins
git clone https://github.com/Shtraiy/astrbot_plugin_filter.git
cd astrbot_plugin_filter
pip install -r requirements.txt
```

安装后重启 AstrBot，并在管理面板中打开插件配置。

## ⚙️ 配置

配置入口：`AstrBot 管理面板 -> 插件 -> 语言逻辑优化大师 -> 配置`

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | ---: | --- |
| `llm_provider_id` | provider | 空 | LLM 分段和文风优化使用的模型 |
| `enable_llm_style` | bool | `true` | 每条正常非空回复调用 LLM 润色，保留原意并适量删除八股文；需要配置 `llm_provider_id` |
| `llm_timeout_seconds` | float | `15.0` | 单次 LLM 润色/分段最多等待时间；超时自动回退，最大 60 秒 |
| `enable_llm_segment` | bool | `false` | 启用 LLM 语义分段 |
| `enable_de_ai_flavor` | bool | `true` | 启用规则去 AI 味 |
| `enable_image_render` | bool | `false` | 启用列表图片渲染 |
| `image_min_list_items` | int | `3` | 触发图片渲染的最少列表项数 |
| `image_font_size` | int | `22` | 图片字体大小 |
| `image_max_width` | int | `600` | 图片最大宽度 |
| `multi_message` | bool | `true` | 是否将分段结果逐条发送；单次回复最多发送 5 条消息，关闭后保留空行排版并合并为一条消息 |
| `delay_min` | float | `2.0` | 分段消息间隔下限，运行时限制在 2~5 秒 |
| `delay_max` | float | `5.0` | 分段消息间隔上限，运行时限制在 2~5 秒 |
| `gate_seconds` | float | `0.0` | 闸门时间：最后一条消息发送完成后，等待此时间再接受同一来源的新唤醒；`0` 表示发送完成后立即接受 |
| `gate_ttl_seconds` | float | `300.0` | 闸门最大存活时间：请求异常中断或回复从未完成时自动释放闸门，避免该来源永久无法唤醒；`0` 禁用 |
| `enable_content_guard` | bool | `true` | 在 LLM 请求前和消息发送前启用内容防护 |
| `content_guard_mode` | string | `balanced` | `balanced` 拦截明确风险，`strict` 更积极地拦截可疑诱导 |
| `content_guard_block_terms` | string | 空 | 每行或逗号分隔填写需要拦截的词/短语 |
| `onboarding_guard_minutes` | float | `30.0` | 新群聊严格防护的持续时间，单位为分钟 |
| `onboarding_guard_messages` | int | `20` | 新群聊严格防护覆盖的 LLM 请求次数 |

启用 LLM 功能时，需要先在 AstrBot 中配置可用的 LLM provider，并填写 `llm_provider_id`。LLM 不可用或输出不符合校验要求时，插件会自动使用规则处理。

## 🧰 环境依赖

- AstrBot：`>= 4.16, < 5`
- Python：`>= 3.10`
- 消息协议：OneBot v11 / v12

列表图片渲染需要 Pillow：

```bash
pip install Pillow
```

## ✅ 开发与验证

```bash
python -m pip install -r requirements-dev.txt
python -m py_compile main.py content_guard.py pipelines.py segmentation.py image_renderer.py outbound_pipeline.py reply_coordinator.py message_dispatcher.py private_companion_adapter.py
python -m pytest -q
```

## 📦 项目结构

```text
astrbot_plugin_filter/
├── main.py              # 插件入口与输出流程编排
├── content_guard.py     # 输入/输出内容防护与诱导检测
├── pipelines.py         # 文本清理、脱敏和去 AI 味
├── segmentation.py      # LLM/规则分段、重复检测和多消息发送
├── image_renderer.py    # 列表图片渲染
├── _conf_schema.json     # AstrBot 配置项定义
├── metadata.yaml         # 插件元数据
├── requirements.txt      # Python 依赖
├── requirements-dev.txt  # 本地测试依赖
├── tests/                # 测试代码
├── LICENSE               # AGPL-3.0 许可证
└── README.md
```

## ❓ 常见问题

### 安装后没有生效

确认 AstrBot 版本满足要求，重启 AstrBot，并在日志中检查插件是否成功加载。

### LLM 分段没有生效

确认已配置 `llm_provider_id`，并打开 `enable_llm_style`（默认开启）。插件会对每条正常非空回复尝试调用 LLM；调用失败、结果不合格或文本超出安全上限时自动降级，不影响普通规则处理。该功能会增加延迟和模型调用消耗。

### 群聊内容防护

内容防护在用户请求进入 LLM 前和机器人最终发送前各检查一次。词库配置支持每行一个词或短语，也支持逗号分隔；检测会忽略常见空格、标点、零宽字符和 Unicode 变形。命中高风险内容时，机器人不会复述原文，而是发送中性提示。词库应根据实际群规和运营场景维护，插件不会内置会变化的具体词表。

## 🤝 Private Companion 联动

2.6.3 起，本插件与 [Private Companion](https://github.com/menglimi/astrbot_plugin_private_companion) 协同处理主动消息：

- 普通用户消息拥有优先权，不会因同源主动请求处于在途而被丢弃。
- 识别到 Private Companion 的主动请求后，用户新消息会使旧请求失效，并尽力请求取消。
- 旧主动请求的过时回复会被丢弃，避免在用户消息后又发送。
- Private Companion 未安装、API 不可用或取消失败时，本插件会自动降级，不影响原有过滤功能。

## 📄 许可证

本项目采用 [GNU AGPL v3](./LICENSE) 许可证。

## 👤 作者

- Shtraiy
- 仓库：[astrbot_plugin_filter](https://github.com/Shtraiy/astrbot_plugin_filter)

## 📝 更新日志

### 2.6.3

- 修复同源冷却/门控导致真实用户消息被卡住或丢弃的问题。
- 增加 Private Companion 主动请求识别、取消适配和过时回复抑制。
- 增加用户优先、重复取消防护和相关回归测试。
