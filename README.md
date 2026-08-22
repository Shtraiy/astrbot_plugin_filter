<div align="center">

# AstrBot 回复优化大师

[![version](https://img.shields.io/badge/version-v2.13.4-blue.svg)](https://github.com/Shtraiy/astrbot_plugin_filter)
[![AstrBot](https://img.shields.io/badge/AstrBot-%3E%3D4.16-orange.svg)](https://github.com/Soulter/AstrBot)
[![license](https://img.shields.io/badge/license-AGPL--3.0-green.svg)](./LICENSE)

**清理 AstrBot 输出中的内部痕迹，优化表达排版，并支持智能分段与列表图片渲染**
</div>

## Refactor architecture

The outbound path is split into three responsibilities:

- `outbound_pipeline.py` keeps the existing metadata, tool-trace, sensitive-content, Markdown, and style cleanup order.
- `reply_coordinator.py` owns reply locks, wake-up gates, cooldowns, and completion callbacks.
- `message_dispatcher.py` is the single delayed follow-up sender and releases the owning reply session even when sending fails.

The cleanup stages are intentionally retained for compatibility.

> **astrbot_plugin_filter** 是一个用于 AstrBot 的输出后处理插件，能够在消息发送前清理模型输出中的元数据、工具叙述和敏感信息，并提供 LLM 文风优化、智能分段、消息串行发送与列表图片渲染等能力。

## 📌 主要能力

- 清理 OneBot、MCP 等结构化元数据泄漏。
- 过滤系统路径、Shell 命令、内网 IP、API Key 等敏感信息。
- 检测到工具调用流程泄露时，直接替换为正常的用户可见提示。
- 删除工具调用过程中的内部叙述，并将工具函数名转换为更自然的中文描述。
- 使用规则或 LLM 优化 AI 味表达。
- 支持 LLM 智能分段，失败时自动降级到规则分段；编号/项目符号/中文序数等特殊格式直接机械分段，不调用 LLM。
- 收到唤醒后开启短窗口（默认 6 秒），把同一用户继续发送的分段消息（含图片/文件）合并成一次回复，避免"表情包"和"可爱的表情包"被拆成两次割裂的回复。
- 自动剔除 LLM 上下文中机器人自己发送的图片/文件，并移除其它插件注入的上一轮自发表情包描述（它会拼进用户消息，模型可能误以为用户刚发了表情包），避免纯文字消息也被表情包话题带偏。
- 将常见 Markdown 语法转换为适合 QQ 展示的纯文本。
- 将分段结果按多条消息发送，并合并重复段落及工具流程叙述中高度相似的段落。
- 同一群聊内按顺序发送不同用户的完整回复，避免消息交错。
- 可选：将编号列表渲染为图片发送。
- 防护群聊输入和输出内容，拦截配置词库命中及常见诱导绕过请求。
- 新群聊在一段时间或一定消息数内自动启用更严格的防护。

## 🔄 处理流程

```text
收到唤醒
        |
        v
分段消息合并窗口（同一用户短窗口内合并，可选）
        |
        v
AstrBot 生成回复
        |
        v
垃圾符号清理 -> 用户称呼替换 -> 敏感信息过滤
        |
        v
工具叙述清理 -> 工具名脱敏 -> AI 味优化
        |
        v
特殊格式机械分段 -> LLM 文风优化 -> LLM 分段 -> 规则分段降级 -> 重复段落合并
        |
        v
Markdown 纯文本化 -> 按会话串行发送
```

## 🚀 安装

### 通过 AstrBot 插件市场

在 AstrBot 管理面板的插件市场中搜索“回复优化大师”，安装后重启 AstrBot。

### 手动安装

```bash
cd AstrBot/data/plugins
git clone https://github.com/Shtraiy/astrbot_plugin_filter.git
cd astrbot_plugin_filter
pip install -r requirements.txt
```

安装后重启 AstrBot，并在管理面板中打开插件配置。

## ⚙️ 配置

配置入口：`AstrBot 管理面板 -> 插件 -> 回复优化大师 -> 配置`

管理面板只显示常用配置；其余为高级配置（使用默认值即可），如需调整请直接编辑
`AstrBot/data/config/astrbot_plugin_filter_config.json`。

### 常用配置（管理面板显示）

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | ---: | --- |
| `llm_provider_id` | provider | 空 | LLM 分段和文风优化使用的模型 |
| `enable_llm_style` | bool | `true` | 每条正常非空回复调用 LLM 润色，保留原意并适量删除八股文；需要配置 `llm_provider_id` |
| `enable_llm_segment` | bool | `false` | 启用 LLM 语义分段 |
| `enable_message_merge` | bool | `true` | 启用分段消息合并窗口：把同一用户短窗口内的分段消息合并成一次回复 |
| `enable_content_guard` | bool | `true` | 在 LLM 请求前和消息发送前启用内容防护 |
| `content_guard_mode` | string | `balanced` | `balanced` 拦截明确风险，`strict` 更积极地拦截可疑诱导 |
| `content_guard_block_terms` | string | 空 | 每行或逗号分隔填写需要拦截的词/短语 |

### 高级配置（面板隐藏，默认值即可）

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | ---: | --- |
| `llm_timeout_seconds` | float | `15.0` | 单次 LLM 润色/分段最多等待时间；超时自动回退，最大 60 秒 |
| `segment_min_chars` | int | `80` | 触发分段的最少字符数；回复低于该值不尝试分段，最小 20 |
| `enable_de_ai_flavor` | bool | `true` | 启用规则去 AI 味 |
| `enable_image_render` | bool | `false` | 启用列表图片渲染 |
| `image_min_list_items` | int | `3` | 触发图片渲染的最少列表项数 |
| `image_font_size` | int | `22` | 图片字体大小 |
| `image_max_width` | int | `600` | 图片最大宽度 |
| `multi_message` | bool | `true` | 是否将分段结果逐条发送；单次回复最多发送 5 条消息，关闭后保留空行排版并合并为一条消息 |
| `delay_min` | float | `2.0` | 分段消息间隔下限，运行时限制在 2~5 秒 |
| `delay_max` | float | `5.0` | 分段消息间隔上限，运行时限制在 2~5 秒 |
| `gate_seconds` | float | `0.0` | 闸门时间：最后一条消息发送完成后，等待此时间再接受新的唤醒；`0` 表示发送完成后立即接受 |
| `gate_ttl_seconds` | float | `300.0` | 闸门最大存活时间：请求异常中断或回复从未完成时自动释放闸门，避免该来源永久无法唤醒；`0` 禁用 |
| `wakeup_interval_min` | float | `1.0` | 全局唤醒间隔下限；运行时不会低于 1 秒 |
| `wakeup_interval_max` | float | `2.0` | 全局唤醒间隔上限；默认在 1～2 秒之间随机等待 |
| `queue_full_notice` | string | `队列繁忙，请稍后再试。` | 全局唤醒队列满、丢弃最新消息时发送的提示；同一轮丢弃只提示一次，留空则不发送 |
| `merge_window_seconds` | float | `6.0` | 合并窗口时长；从第一条唤醒消息起等待后续分段，运行时限制在 1~30 秒 |
| `merge_max_messages` | int | `5` | 单个窗口最多合并的消息条数；`0` 不限制 |
| `merge_max_chars` | int | `2000` | 合并文本最大字符数；`0` 不限制 |
| `merge_ignore_prefixes` | string | `/,!` | 不参与合并的消息前缀，逗号分隔 |
| `merge_include_media` | bool | `true` | 是否把窗口内同用户发送的图片/文件一并合并进同一次回复 |
| `merge_continuation_ttl` | float | `120.0` | 规划期收到的无唤醒词补充暂存时长（秒）；该用户下一次唤醒时并入，`0` 关闭 |
| `merge_task_cancel` | bool | `false` | 规划中收到同用户新消息时尝试真正取消旧请求任务；依赖 AstrBot 4.25+，旧版本保持关闭 |
| `protect_user_media_focus` | bool | `true` | 请求上下文清洗总开关：每次请求剔除机器人自己历史消息里的图片/文件，并移除自发表情包描述 |
| `strip_self_media_from_context` | bool | `true` | 每次请求都从历史 assistant 消息中剔除机器人自己发送的图片/文件组件 |
| `strip_recent_self_meme_context` | bool | `true` | 每次请求都移除 `<recent_sent_meme>` 等上一轮自发表情包描述注入块；若需要保留以便用户纯文字追问“刚才的表情”，可关闭 |
| `guard_own_media_attribution` | bool | `true` | 用户消息为纯文字时，注入图片归属提示：用户本轮未发图、历史中的图片属于各自发送者，避免模型把机器人自己的表情包当成用户发的 |
| `onboarding_guard_minutes` | float | `30.0` | 新群聊严格防护的持续时间，单位为分钟 |
| `onboarding_guard_messages` | int | `20` | 新群聊严格防护覆盖的 LLM 请求次数 |

启用 LLM 功能时，需要先在 AstrBot 中配置可用的 LLM provider，并填写 `llm_provider_id`。LLM 不可用或输出不符合校验要求时，插件会自动使用规则处理。

唤醒调度采用全局 FIFO：同一时间只处理一条唤醒，当前回复之外最多保留 3 条等待中的唤醒；队列满时丢弃最新唤醒，并通过 `queue_full_notice` 提示对应会话（留空则静默丢弃）。当前完整回复结束后，下一条唤醒至少等待 1 秒才会进入 LLM，实际间隔默认随机为 1～2 秒。`gate_seconds` 如果设置得更大，则使用两者中的较大值。

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
python -m py_compile main.py content_guard.py pipelines.py segmentation.py image_renderer.py outbound_pipeline.py reply_coordinator.py message_dispatcher.py merge_window.py request_cleaner.py
python -m pytest -q
```

## 📦 项目结构

```text
astrbot_plugin_filter/
├── main.py              # 插件入口与输出流程编排
├── content_guard.py     # 输入/输出内容防护与诱导检测
├── pipelines.py         # 文本清理、脱敏和去 AI 味
├── segmentation.py      # LLM/规则分段、重复检测和多消息发送
├── outbound_pipeline.py # 输出净化管线：清洗/脱敏/去 AI 味/分段/Markdown
├── reply_coordinator.py # 全局唤醒队列、回复锁与完成回调
├── message_dispatcher.py # 后续分段消息的延迟发送
├── merge_window.py      # 分段消息合并窗口：同用户短窗口内合并后续消息
├── request_cleaner.py   # LLM 请求上下文清洗：用户带图时剔除机器人自身图片/表情包描述
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

确认已配置 `llm_provider_id`，并打开 `enable_llm_segment`（默认关闭）。回复长度需要超过 `segment_min_chars`（默认 80 字符）才会尝试分段。若同时开启 `enable_llm_style`，插件会先润色再对结果分段；LLM 输出改动非空白内容、超时或失败时自动降级到规则分段。该功能会增加延迟和模型调用消耗。

### 分段消息合并没有生效

合并窗口默认开启（`enable_message_merge`）。它只对有人类发送者（sender）的唤醒消息生效，按"会话 + 用户 ID"隔离；窗口期内同一用户的补充**无论带不带唤醒词**都会合并（带唤醒词的后续消息会被停掉自身回复、并入首条），图片/文件在 `merge_include_media`（默认开启）下也会一并合并，引用/转发等其它类型以及以 `merge_ignore_prefixes`（默认 `/`、`!`）开头的消息不参与合并。窗口关闭后、回复生成期间，同用户再 @ 会终止旧规划并合并重生成；不带唤醒词的补充会暂存 `merge_continuation_ttl` 秒，在该用户下一次唤醒时并入。每条消息都会因窗口等待最多 `merge_window_seconds` 秒（默认 6 秒）的延迟。

### 机器人把自己发的表情包当成识图对象

配合表情包插件（如 meme_manager）发送表情包后，机器人自己发过的图片会留在会话历史里，多模态模型在后续每次请求中都能看到；同时 meme_manager 还会把上一轮自发表情包的描述（`<recent_sent_meme>`）追加进**用户消息**里，模型可能误以为用户刚发送了表情包，从而回复“你又发这种可爱的表情包”，即使你只发了文字。`protect_user_media_focus`（默认开启）会在**每次请求**中剔除这两类内容：历史 assistant 消息里的图片/文件，以及 `<recent_sent_meme>` 描述块。若你确实需要保留描述块以便纯文字追问“刚才的表情”，可关闭 `strip_recent_self_meme_context`。

### 群聊内容防护

内容防护在用户请求进入 LLM 前和机器人最终发送前各检查一次。词库配置支持每行一个词或短语，也支持逗号分隔；检测会忽略常见空格、标点、零宽字符和 Unicode 变形。命中高风险内容时，机器人不会复述原文，而是发送中性提示。词库应根据实际群规和运营场景维护，插件不会内置会变化的具体词表。

## ⚠️ 已知限制

- **流式输出**：AstrBot 在流式输出（`streaming_response`）过程中不会触发发送前钩子，文本会边生成边显示，插件的清洗/分段无法作用于中间过程；需要完整生效时，建议在 AstrBot provider 设置中关闭流式输出。
- **旧版 AstrBot**：4.16 早期版本在丢弃唤醒后仍会短暂占用会话锁并构建 agent（新版已修复），建议升级到较新 4.x。
- **与内置功能叠加**：AstrBot 自带的"分段回复"会在本插件之后再次处理第一条消息，建议两者只开启一个；第一条分段会带 @/引用，插件直发的后续分段不带，属于预期行为。
- **合并窗口的终止规划**：窗口关闭后若 bot 已开始生成回复，同用户新消息会终止旧回复并携带合并文本重新生成；在 AstrBot 4.16 上旧请求会继续跑完（token 已消耗、新回复需等会话锁释放），开启 `merge_task_cancel`（需 4.25+）才能真正中断加速。已开始的工具调用或流式输出无法回退。
- **上下文清洗为尽力而为**：`protect_user_media_focus` 依赖 `req.contexts`/`req.extra_user_content_parts` 结构（`extra_user_content_parts` 需 AstrBot 4.24.2+），无法识别的上下文形态会被跳过；若模型仍引用机器人历史消息中的图片，可尝试升级 AstrBot。

## 📄 许可证

本项目采用 [GNU AGPL v3](./LICENSE) 许可证。

## 👤 作者

- Shtraiy
- 仓库：[astrbot_plugin_filter](https://github.com/Shtraiy/astrbot_plugin_filter)

## 📝 更新日志

### 2.13.4

- 归属提示改为对所有纯文字用户消息生效：不再只匹配“我发了什么/这上面有字吗”等特定问法，而是每条纯文字消息都注入“用户本轮未发送图片、历史图片属于各自发送者、记忆/历史中声称用户发过图的内容可能是机器人自己的误判”的提示，全面防止机器人把自己的表情包归为用户发送。

### 2.13.3

- 加固机器人自身历史媒体的剔除：支持对象型上下文条目和嵌套 Message 结构，进一步覆盖“一边发表情包一边识别自己表情包”的场景；新增诊断日志，若检测到 assistant 历史媒体却无法剔除会输出告警与上下文结构，便于定位。

### 2.13.2

- 修复纯文字询问图片文字（如“这上面有字吗”）时 bot 用自己历史表情包的内容作答的问题：用户本轮未发图却询问图片文字时，注入提示“用户本轮为纯文字消息，不要根据机器人自己历史发送的表情包回答”，并请用户确认/发送图片。

### 2.13.1

- 修复机器人分不清“自己发的表情包”和“用户发的表情包”的问题：用户询问“我发了什么表情包/我发过图吗”时，在请求中注入一条消息归属提示（assistant 消息属于机器人自己、user 消息才是用户发送的），避免模型把机器人自己的表情包描述成用户发送的。新增高级配置 `guard_own_media_attribution`（默认开启）。

### 2.13.0

- 精简插件配置面板：只保留常用配置（LLM 模型、文风/分段开关、消息合并开关、内容防护相关）；其余 27 项标记为 `invisible`，管理面板不再显示，继续使用默认值，可通过 `AstrBot/data/config/astrbot_plugin_filter_config.json` 直接修改。

### 2.12.2

- 修复纯文字请求被误判为“用户发送了表情包”的问题：`strip_recent_self_meme_context` 改为每次请求都移除 `<recent_sent_meme>` 描述块（该块会拼进用户消息，模型会误以为用户刚发了表情包）；不再要求用户本轮带图。需要保留该描述块的用户可关闭此项。

### 2.12.1

- 修复纯文字消息被历史表情包话题带偏的问题：`strip_self_media_from_context` 改为每次请求都从历史 assistant 消息中剔除机器人自己发送的图片/文件，不再要求用户本轮带图；用户带图时才额外剔除 `<recent_sent_meme>` 描述块，纯文字追问“刚才的表情”时仍保留该描述。

### 2.12.0

- 修复机器人把自己发送的表情包纳入识图范围的问题：用户本轮发送图片/文件时，自动剔除历史上下文中机器人自己发送的图片/文件，并移除 meme_manager 等插件注入的上一轮自发表情包描述（`<recent_sent_meme>`），让识图目标锁定在用户自己的图片上。
- 新增配置：`protect_user_media_focus`（总开关，默认开启）、`strip_self_media_from_context`、`strip_recent_self_meme_context`。

### 2.11.1

- 修复：消息合并监听改用 `filter.event_message_type(filter.EventMessageType.ALL)`，兼容 AstrBot 4.16+（`filter.on_message` 在该系列版本中不存在，会导致插件加载失败）。

### 2.11.0

- 更名：插件显示名由“语言逻辑优化大师”改为“回复优化大师”，更贴合清洗、分段、合并、安全拦截等回复优化能力。

### 2.10.0

- 规划期补充不丢失：窗口关闭后、回复生成期间收到的同用户无唤醒词补充会暂存并在下一次唤醒时并入（新增 `merge_continuation_ttl`，默认 120 秒，`0` 关闭）。
- 合并规则明确为：窗口期内补充带不带唤醒词都合并；规划中再 @ 终止旧规划合并重生成。

### 2.9.0

- 合并窗口支持图片/文件：窗口内同一用户发送的图片和文件会随文本一并进入同一次 LLM 请求（新增 `merge_include_media`，默认开启）；引用/转发等其它类型仍不参与合并。

### 2.8.0

- 新增分段消息合并窗口：收到人类唤醒后默认等待 6 秒，把同一用户继续发送的纯文本合并为一次 LLM 请求；窗口关闭后若已开始规划，会终止旧回复并携带合并文本重新生成。
- 合并按"会话 + 用户 ID"隔离，群聊不同用户不互相合并；图片/文件/引用/转发及命令前缀消息不参与合并。
- 新增配置：`enable_message_merge`、`merge_window_seconds`、`merge_max_messages`、`merge_max_chars`、`merge_ignore_prefixes`、`merge_task_cancel`。

### 2.7.0

- 移除与 Private Companion 的联动：不再识别主动请求标记，也不再做用户优先抢占与主动取消；回复调度统一由全局 FIFO 队列管理。
- 修复智能分段：新增 `segment_min_chars`（默认 80），回复长度超过该值即尝试分段；LLM 文风成功后不再跳过分段，长文本会先润色再分段。
- 新增特殊格式机械分段：编号列表、项目符号列表、中文序数（第一步/第二点）等结构化内容直接按条目拆分，不调用 LLM，秒级完成。
- 唤醒队列满时不再静默丢弃：新增 `queue_full_notice` 配置（默认发送"队列繁忙，请稍后再试。"），同一轮丢弃只提示一次，留空可关闭。
- 机械分段遇到"标题："前缀时自动合并进第一条列表项，不再单独发送。
- 修复多消息分段去重误删正常段落：顺序编号、共享结构的列表项不再被误判为重复。
- 修复后续分段消息重复调用 LLM 润色导致的延迟与内容漂移；后续段落只做安全清洗，不再二次润色。
- 修复 `gate_seconds` 在空回复/拦截等无发送路径上仍被应用的问题，并限制超时取消事件 id 的内存增长。
- 修复敏感信息过滤误删"环境变量/配置文件/数据库连接"等正常叙述的问题。
- 清理重构后遗留的死代码。

### 2.6.3

- 修复同源冷却/门控导致真实用户消息被卡住或丢弃的问题。
- 增加 Private Companion 主动请求识别、取消适配和过时回复抑制。
- 增加用户优先、重复取消防护和相关回归测试。
