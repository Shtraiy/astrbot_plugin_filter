# 重构设计：会话合并窗口 + 自回复标记（v3.0.0）

> 日期：2026-08-22
> 状态：待用户审阅
> 范围：`astrbot_plugin_filter` 全量重构（破坏性变更，版本升至 3.0.0）

## 1. 背景与目标

用户已改用 AstrBot 官方分段回复，本插件原有的输出后处理链路（LLM/规则分段、多消息逐条发送、文风优化、去 AI 味、Markdown 转纯文本、元数据/敏感信息清洗、列表图片渲染）不再需要。

重构后插件聚焦两项核心能力：

1. **会话合并窗口**：同一用户分段消息（纯文本、图片/文件）合并为一次 LLM 请求；规划中收到同用户新消息时终止旧规划、合并重生成。按会话独立，不同会话可并行回复。
2. **bot 自回复标记**：让 bot 明确知道自己最近 N 分钟内发过什么（文本 + 图片/文件），保留 assistant 历史媒体进入上下文并注入客观归属标记，解决"bot 把自己发的表情包识别成用户发的"这一系列问题。

同时与 livingmemory 无冲突联动：窗口建立后、LLM 请求前自动读取窗口之前的记忆（livingmemory 召回以合并后的完整文本为查询词）；窗口结束后按"一段对话 = 一条用户消息 + 一条助手消息"录入 livingmemory 会话库。无需修改 livingmemory 任何配置或代码。

## 2. 范围：删除 / 保留 / 新增

### 2.1 删除

| 文件 | 删除的功能 |
|---|---|
| `segmentation.py` | LLM 智能分段、规则/机械分段、段落合并、分段相关工具 |
| `message_dispatcher.py` | 多消息逐条发送、延迟发送调度 |
| `pipelines.py` | 出站清洗管道（元数据/工具痕迹/敏感信息/Markdown/风格） |
| `outbound_pipeline.py` | 出站文本管道封装 |
| `image_renderer.py` | 列表图片渲染 |
| `main.py` 中的出站装饰逻辑 | `on_decorating_result` 中除"丢弃被终止事件结果"外的全部处理 |

删除的配置项（19 项）：
`llm_provider_id`、`enable_llm_style`、`llm_timeout_seconds`、`enable_llm_segment`、`segment_min_chars`、`enable_de_ai_flavor`、`enable_image_render`、`image_min_list_items`、`image_font_size`、`image_max_width`、`multi_message`、`delay_min`、`delay_max`、`gate_seconds`、`gate_ttl_seconds`、`wakeup_interval_min`、`wakeup_interval_max`、`queue_full_notice`、`merge_continuation_ttl`。

删除的调度概念：全局 FIFO 唤醒队列、排队上限、闸门（gate）、冷却（cooldown）、唤醒随机间隔、"规划期补充暂存等下次唤醒"（`continuation` 阶段）。

删除的测试文件：`test_segmentation.py`、`test_message_dispatcher.py`、`test_pipelines.py`、`test_outbound_pipeline.py`、`test_image_renderer.py`、`test_llm_timeout.py`、`test_console_text.py`、`test_dedupe.py`、`test_single_message.py`、`test_wakeup_queue.py`、`test_cooldown.py`、`test_reply_lock_unittest.py`（按最终保留能力裁剪）。

### 2.2 保留

| 文件 | 保留的功能 |
|---|---|
| `content_guard.py` | 群聊内容防护（词库 + 诱导绕过检测 + 新群聊严格模式） |
| `merge_window.py` | 合并窗口状态机（重构：窗口期/规划期两阶段，去掉 continuation） |
| `reply_coordinator.py` | 会话级准入、supersede（终止旧规划）、取消事件跟踪、丢弃迟到结果 |
| `request_cleaner.py` | 重构为 `self_reply_marker.py`（见 2.3），语义从"剔除"改为"标记" |
| `main.py` | 钩子编排（大幅瘦身） |

保留的配置项：`enable_message_merge`、`merge_window_seconds`、`merge_max_messages`、`merge_max_chars`、`merge_ignore_prefixes`、`merge_include_media`、`merge_task_cancel`、`strip_recent_self_meme_context`、`guard_own_media_attribution`、`enable_content_guard`、`content_guard_mode`、`content_guard_block_terms`、`onboarding_guard_minutes`、`onboarding_guard_messages`。

### 2.3 新增

| 文件 | 职责 |
|---|---|
| `self_reply_marker.py` | bot 自回复标记（由 `request_cleaner.py` 演化）：记录最近自回复、注入 `<self_reply_mark>` 归属标记、移除 `<recent_sent_meme>`、保留纯文字归属提示 |
| `merge_guards.py` | LLM 侧守卫钩子：终止被 supersede 的旧事件，防止下游插件（重点：livingmemory）记录脏数据 |

## 3. 目标架构

### 3.1 模块依赖

```
main.py（瘦身：钩子编排）
 ├─ merge_window.py      MergeWindowManager（窗口/规划状态机）
 ├─ reply_coordinator.py ReplyCoordinator（会话准入 + supersede + 取消跟踪）
 ├─ self_reply_marker.py SelfReplyMarker（自回复标记 + 归属提示）
 ├─ merge_guards.py      MergeGuards（on_llm_request / on_llm_response 守卫）
 └─ content_guard.py     内容防护（不变）
```

### 3.2 MergeWindowManager（`merge_window.py` 重构）

状态：`{key: _MergeState}`，`key = (unified_msg_origin, sender_id)`，按用户隔离。

阶段：
- `window`：首个唤醒被 sleep 阻塞，等待同用户分段。
- `planning`：窗口关闭、回复生成中；同用户新消息触发 supersede + 合并重生成。

接口（保留并调整）：
- `user_key(event)`、`join_text(earlier, later)`（去前导 @ 提及与空白）
- `start_window(event, pipeline_task)`：开启窗口，仅当无既有状态时成功
- `is_window_open(event)`：窗口期判定
- `capture(event)`：**仅窗口期**追加同用户非唤醒分段（文本 + 图片/文件）
- `merge_wake(event)`：窗口期同用户带唤醒词消息并入缓冲（事件随后 stop）
- `finalize_window(event)`：窗口结束写回 `event.message_str`，进入 planning
- `take_planning(event)`：**规划期合并**——消费 planning 状态，返回 `(old_event, pending_text, media, pipeline_task)`
- `rearm_planning(event, merged_text, pipeline_task)`：**新增**——规划期合并后为当前事件重建 planning 状态，使后续补充可持续递归合并
- `promote_planning(event)`：**新增**——规划期收到同用户**无唤醒词**补充时，校验可合并后置 `event.is_at_or_wake_command = True`，让事件继续走 LLM 管道（群聊场景；私聊天然唤醒不需要）
- `attach_media(event, media)`、`clear_owner(event)`

上限与过滤沿用：`merge_max_messages`、`merge_max_chars`、`merge_ignore_prefixes`、纯文本/图片/文件参与合并（引用、转发、语音等不参与）。

删除：`continuation` 阶段、`planning_captures`、`continued_at`、`merge_continuation_ttl`。

### 3.3 ReplyCoordinator（`reply_coordinator.py` 重构）

按会话（`unified_msg_origin`）维护活跃状态，删除全局队列/闸门/冷却：

- `admit_wakeup(event)`：会话级准入（幂等；取消集合中的事件直接拒绝；记录当前活跃事件）
- `supersede_active_event(event)`：记录取消 id、`stop_event()`、清除会话活跃状态
- `is_superseded(event)`：取消集合查询（守卫钩子用）
- `discard_superseded_result(event)`：出站阶段丢弃迟到结果并清理取消 id
- `finish_active(event)`：正常完成清理

同一会话同一时刻只有一个活跃 LLM 请求；不同会话互不影响。事件循环内状态操作天然串行，无需额外锁；AstrBot 自身的会话锁继续负责 LLM 调用的串行化。

### 3.4 SelfReplyMarker（`self_reply_marker.py`，由 request_cleaner 演化）

职责：

1. **记录最近自回复**：环形缓冲（按会话，默认保留最近 8 条 / 5 分钟），记录 bot 发出的文本与媒体（图片文件名、文件名校验失败时退化为 `[图片]`/`[文件]`）。数据来源：回复发送钩子（含 meme_manager 等插件附加到回复链的媒体）。
2. **注入 `<self_reply_mark>` 归属标记**：`on_llm_request` 时若缓冲非空，注入 `extra_user_content_parts`（`mark_as_temp()`，不进 AstrBot 历史）。标记为客观事实声明，见第 5 节。
3. **保留 assistant 历史媒体**：不再从上下文剔除（方案 A 已确认）；归属问题交给标记块解决。
4. **移除 `<recent_sent_meme>`**：继续剔除 meme_manager 拼进用户消息的上一轮自发表情包描述（配置 `strip_recent_self_meme_context`，默认开）。
5. **纯文字归属提示**：用户消息为纯文字时注入 `<media_note>`（配置 `guard_own_media_attribution`，默认开），明确"用户本轮未发图"。

接口：`record_sent_reply(event)`、`mark_own_recent_replies(req, event)`、`strip_recent_self_meme_context(req)`、`has_user_media(event)`、`append_text_only_media_note(req)`。

### 3.5 MergeGuards（`merge_guards.py` 新增）

两个高优先级钩子（`priority=1000`，livingmemory 等插件默认 0，保证先执行）：

- `on_llm_request_guard(event, req)`：若 `is_superseded(event)` → 直接返回（事件已被 stop，`call_event_hook` 检测到 stopped 后短路，livingmemory 的召回/消息记录不会执行）。
- `on_llm_response_guard(event, resp)`：若 `is_superseded(event)` → `event.stop_event()` → `call_event_hook` 短路，livingmemory 不会记录旧回复、不会触发提前反思；AstrBot 自身的 `_save_to_history` 也有 `is_stopped()` 保护，不保存旧回复。

守卫只依赖本插件 coordinator 状态，**不依赖 livingmemory 是否安装**。可选的诊断：探测 `astrbot_plugin_livingmemory.core.passive_group_capture.get_active_plugin()` 是否存在，仅用于日志。

### 3.6 main.py 钩子编排

- `on_message`（`event_message_type(ALL)`）：窗口期 → `capture()`；规划期且同用户可合并 → `promote_planning()`。
- `on_waiting_llm_request`（`priority=1000`）：
  1. 窗口期同用户唤醒 → `merge_wake()` + `stop_event()`；
  2. 规划期 → `take_planning()` → `supersede_active_event(old)` → 合并文本/媒体到当前事件 → `admit_wakeup` → `rearm_planning()`（**不再重新 sleep**，立即重新生成）；
  3. 正常路径：`admit_wakeup` → `start_window` → `sleep(merge_window_seconds)` → `finalize_window` 写回。
- `on_llm_request`（`priority=1000`）：守卫（superseded 早退）→ 准入幂等 → 自回复标记注入 → 内容防护。
- `on_llm_response`（`priority=1000`）：守卫（superseded → stop）。
- `on_decorating_result`：仅 `discard_superseded_result`；不做任何文本处理。
- `after_message_sent`：`record_sent_reply` + 会话清理。

## 4. 核心数据流

### 4.1 正常窗口流程（一次合并）

1. 用户发第一段（唤醒）→ `on_waiting_llm_request` 开窗 sleep。
2. 用户数秒后发第二段（无唤醒词）→ `on_message` `capture()` 并入缓冲；该事件本身不触发 LLM。
3. sleep 结束 → `finalize_window` 把"第一段\n第二段"写回 `event.message_str`，媒体附加到消息链。
4. `on_llm_request`：自回复标记注入 → 内容防护 → LLM（`req.prompt = event.message_str`，AstrBot 4.16 已核实）。
5. livingmemory `on_llm_request` 以合并文本召回并记录；`on_llm_response` 记录合并后的回复。
6. 回复由 AstrBot 官方分段发送。

### 4.2 规划期合并（带唤醒词 / 私聊）

1. 第一段已放行、LLM 运行中（planning）。
2. 用户发补充（私聊天然唤醒 / 群聊再次 @）→ `on_waiting_llm_request`。
3. `take_planning()` 返回旧状态 → `supersede_active_event(旧事件)`（stop + 记取消 + 清活跃）。
4. 当前事件 `message_str = join(旧文本, 新文本)`，旧媒体合并 → `admit_wakeup` → `rearm_planning` → 直接进入 LLM（不再等新窗口）。
5. 旧事件收尾：`on_llm_response` 守卫 stop → livingmemory 不记录旧回复；AstrBot 不保存旧历史；出站丢弃旧结果。

### 4.3 规划期合并（群聊无唤醒词）

1. 第一段放行、LLM 运行中（planning）。
2. 用户发补充（无 @、无前缀）→ 事件仅走到 `on_message`。
3. `promote_planning()` 校验通过 → 置 `is_at_or_wake_command = True`（AstrBot 4.16 `ProcessStage` 先执行插件 handler、后检查该标志，已核实源码）。
4. 同一事件继续走 `on_waiting_llm_request` → 与 4.2 第 3 步起相同。

### 4.4 与 livingmemory 的时序

（已核实 AstrBot 4.16 源码与 livingmemory 2.6.0-beta.3 源码）

```
on_waiting_llm_request（拿会话锁前，窗口 sleep 在此）
  → 拿锁 → req.prompt = event.message_str（合并后）
  → on_llm_request（livingmemory 召回：查询词 = get_message_str() = 合并文本；私聊记录用户消息）
  → agent 运行
  → on_llm_response（livingmemory 记录助手消息 + 触发反思）
```

- 窗口 sleep 在拿锁之前，因此**合并永远先于 livingmemory 的召回与记录**。
- 正常路径：合并文本 = 唯一用户消息 → livingmemory 会话库天然得到"一段 = 一条用户 + 一条助手"。
- 终止路径：`MergeGuards` 利用 `call_event_hook` 的 stop 短路语义，livingmemory 不会处理被终止的旧事件。
- 无需修改 livingmemory 配置；可选优化 `recall_engine.inject_with_recent_context` 由用户自行决定。

## 5. 自回复标记方案（方案 A 细化）

### 5.1 原则

- 不再从上下文中剔除 assistant 历史媒体，保留上下文完整性。
- 注入客观事实标记块声明归属；标记块优先级高于记忆/历史中的模糊表述。
- 继续移除拼进用户消息的 `<recent_sent_meme>` 文本块。

### 5.2 标记块格式

```
<self_reply_mark>
以下内容是机器人自己在最近 5 分钟内发送过的（属于机器人，不是用户发送的）：
- [文本] <截断的最近自回复文本>
- [图片] <文件名/描述>
用户本轮消息中出现的媒体/文件才属于用户。
任何记忆、总结或历史中声称"用户发送过图片/表情包"的内容若与本标记冲突，以本标记为准。
</self_reply_mark>
```

### 5.3 注入方式

- 通过 `req.extra_user_content_parts.append(TextPart(text=...).mark_as_temp())`，与 livingmemory 注入方式一致，不进 AstrBot 历史。
- 仅当缓冲非空时注入；缓冲按会话隔离。

### 5.4 窗口时长

- 新配置 `self_reply_mark_minutes`（默认 5.0，与 meme_manager 跟进窗口一致；0 表示关闭标记）。

## 6. 配置项（新 schema）

### 6.1 可见配置（面板精简）

| 配置 | 默认 | 说明 |
|---|---|---|
| `enable_message_merge` | true | 启用分段消息合并窗口 |
| `enable_self_reply_mark` | true | 启用 bot 自回复归属标记 |
| `enable_content_guard` | true | 启用群聊内容防护 |
| `content_guard_mode` | balanced | 防护模式 |
| `content_guard_block_terms` | "" | 防护词库 |

### 6.2 隐藏配置

| 配置 | 默认 | 说明 |
|---|---|---|
| `merge_window_seconds` | 6.0 | 合并窗口时长（1–30 秒） |
| `merge_max_messages` | 5 | 单窗口最多合并条数 |
| `merge_max_chars` | 2000 | 合并文本字符上限 |
| `merge_ignore_prefixes` | `/,!` | 不参与合并的前缀 |
| `merge_include_media` | true | 合并图片/文件消息 |
| `merge_task_cancel` | false | 4.25+ 真正取消旧 pipeline 任务 |
| `self_reply_mark_minutes` | 5.0 | 自回复标记窗口（0 关闭） |
| `strip_recent_self_meme_context` | true | 移除 `<recent_sent_meme>` |
| `guard_own_media_attribution` | true | 纯文字消息注入归属提示 |
| `onboarding_guard_minutes` | 30.0 | 新群聊严格防护时长 |
| `onboarding_guard_messages` | 20 | 新群聊严格防护消息数 |

## 7. 错误处理与边界

- **窗口 sleep 被取消**（插件重载/关闭）：`try/finally` 清理状态，事件按原文本继续。
- **超过条数/字符上限**：停止捕获；超出消息按普通消息处理（无唤醒词自然忽略，带唤醒词正常唤醒）。
- **媒体解析失败**：跳过该媒体组件，文本照常合并。
- **livingmemory 未安装**：守卫、标记、合并均不受影响。
- **AstrBot 4.16**：supersede 不加速（旧请求跑完，新回复需等会话锁释放）；4.25+ 开启 `merge_task_cancel` 可真正取消。
- **工具已执行或流式已开始**：不取消旧规划（尽力而为）；守卫仍丢弃其结果，回复不发出。
- **群聊无唤醒词补充**：仅当同一 `(origin, sender)` 存在 planning 状态时才提升为唤醒；其他用户消息不受影响。
- **内容防护**：合并后的文本照常经过 `evaluate_input`。

## 8. 测试计划

- `merge_window.py`：窗口期捕获/合并、上限、忽略前缀、sender 隔离、媒体合并、finalize 写回、`promote_planning`、`take_planning` + `rearm_planning` 递归。
- `reply_coordinator.py`：会话级准入、supersede、`is_superseded`、丢弃迟到结果、跨会话并行。
- `self_reply_marker.py`：标记块注入、缓冲窗口过期/条数上限、`<recent_sent_meme>` 移除、`<media_note>` 保留、media 描述退化。
- `merge_guards.py`：终止事件在 `on_llm_request`/`on_llm_response` 短路、正常事件不受影响。
- `content_guard.py`：保留现有测试。
- 配置 schema：新增/删除项、默认值、范围。
- 集成（复用 FakeEvent 桩，sleep 打桩为 0）：两段唤醒合并为单次 LLM 请求；规划期折叠旧文本、终止旧事件；群聊无唤醒词补充被提升并合并；内容防护仍作用于合并文本。
- 完整 `pytest`、`py_compile`、`git diff --check`。

## 9. 版本与文档

- 版本：`3.0.0`（破坏性重构）。
- `metadata.yaml`：更新 `desc`（聚焦合并 + 自回复标记 + 内容防护）、`version`；`display_name` 待确认（见第 10 节）。
- `README.md`：重写能力说明、配置表、流程、FAQ、更新日志、已知限制。
- `requirements.txt`：移除 Pillow 可选依赖。

## 10. 待确认事项

1. **插件显示名**：现为"回复优化大师"。重构后能力聚焦"消息合并 + 自回复标记"，可考虑改为如"消息合并与上下文管理"，或保留原名。请用户在审阅时确认。
2. **`merge_task_cancel` 默认保持 false**（4.16 无效、4.25+ 可选），确认无异议。
