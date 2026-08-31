# 重构设计：滑动合并窗口 + LLM 智能分段（v3.1.0）

> 日期：2026-08-31
> 状态：已批准（用户确认设计后开始重构）
> 范围：`astrbot_plugin_filter` 行为重构（破坏性变更，版本升至 3.1.0）

## 1. 背景与目标

当前插件（v3.0.x）的合并窗口是**固定时长**：首次唤醒后等待 `merge_window_seconds`（默认 6 秒），期间并入同用户消息；窗口关闭进入"规划期"后，若同用户新消息到达，会**立刻终止旧 Agent 并合并重生成**（含 AstrBot 4.25+ 的 `active_event_registry` 停止、`agent_stop_requested` 重标等一系列机制）。

该"立刻截断"机制存在两个问题：

1. **token 浪费**：每条规划期新消息都会触发一次"停止旧请求 + 全量上下文重生成"，用户连发多条补充时，重生成次数随消息数增长；
2. **复杂度过高**：为对抗 AstrBot 4.27 的 follow-up 捕获，引入了停止请求、重标竞态、supersede、迟到结果丢弃等大量守卫代码。

本次重构的目标：

1. **滑动合并窗口**：窗口期内同用户任何新消息（群聊无需再 @）都重置计时器，连续静默满 `merge_window_seconds` 才合并并发起 LLM 请求；彻底移除"规划期打断/重生成"机制，回复一旦开始生成，插件不再对新消息做任何事。
2. **LLM 智能分段（轻量）**：抓取即将发送的 bot 纯文本回复，调用用户配置的轻量模型（如 `Siliconflow/Qwen/Qwen3.5-4B` / `-9B`）按语义拆成多条消息逐条发送；只分段、零改写，不接触 bot 人物设定；超时/失败自动回退规则分段，回复绝不丢失。
3. **provider 可配置**：按 AstrBot 官方插件设计，设置面板提供 `_special: "select_provider"` 下拉框选择用于分段的 LLM provider，可随时切换模型。

## 2. 范围：删除 / 保留 / 新增

### 2.1 删除

| 文件 | 删除的功能 |
|---|---|
| `merge_guards.py` | 整体删除：修正词打断（`is_correction_follow_up`）、`should_interrupt_running_reply`、`is_superseded_event` / `stop_if_superseded` |
| `reply_coordinator.py` | supersede 相关：`supersede_active_event`、`is_superseded`、`discard_superseded_result`、`active_event_for`、`active_same_sender`、`_cancelled_event_ids`、`_remember_cancelled`、`MAX_CANCELLED_IDS` |
| `merge_window.py` | planning 阶段全部状态与路径：`planning_active`、`take_planning`、`rearm_planning`、`_planning_expired`、`planning_started_at`、`_planning_ttl` |
| `main.py` | `_handle_planning_phase`、`_request_agent_stop`、`_mark_agent_stop_requested`、`_schedule_stop_remark`、`_should_interrupt_active_reply`、`_event_is_private_chat`、`_get_float_config`（若无剩余使用处）；`on_llm_response_guard` 中的 `stop_if_superseded`；`on_decorating_result` 中的 `discard_superseded_result` |

删除的配置项：
`merge_planning_ttl`、`merge_stop_remark_seconds`。

删除的调度概念：规划期补充合并重生成、修正词强制打断、停止标记重标竞态窗口、supersede 事件跟踪。

### 2.2 保留

| 文件 | 保留的功能 |
|---|---|
| `merge_window.py` | 窗口状态机（改造为滑动窗口，见 §3）、`start_window` / `capture` / `merge_wake` / `finalize_window` / `cancel_window` / `clear_state` / `sync_pending_text`、条数/字数上限、忽略前缀、媒体合并、`格式 segments 编号` |
| `reply_coordinator.py` | 瘦身后的 `admit_wakeup`（幂等 + 跳过已停止事件）、`is_session_busy`、`finish_active` |
| `main.py` | 钩子编排（瘦身）、内容防护、自回复标记、任务执行指令、去重、引用重定向、结构标签清洗、中断占位符拦截 |
| `content_guard.py` / `event_access.py` / `interruption_guard.py` / `onboarding_guard.py` / `self_reply_marker.py` / `task_commitment_guard.py` | 现状保留 |

保留的配置项：`enable_message_merge`、`merge_max_messages`、`merge_max_chars`、`merge_ignore_prefixes`、`merge_include_media`、`enable_task_execution_guard`、`enable_self_reply_mark`、`self_reply_mark_minutes`、`mark_recent_self_meme_context`、`fix_memory_media_attribution`、`annotate_assistant_expression_claims`、`strip_recent_self_meme_context`、`guard_own_media_attribution`、`enable_content_guard`、`content_guard_mode`、`content_guard_block_terms`、`onboarding_guard_minutes`、`onboarding_guard_messages`。

### 2.3 新增

| 文件 | 职责 |
|---|---|
| `smart_segment.py` | LLM 智能分段 + 规则分段回退 + 结果校验 + 补发调度辅助（纯函数为主，便于单测） |

新增配置项：
`enable_llm_segment`（可见）、`segment_provider_id`（可见，`_special: "select_provider"`）、`segment_min_chars`（可见）、`segment_max_messages`（可见）、`segment_timeout_seconds`（隐藏）、`segment_delay_min` / `segment_delay_max`（隐藏）。

`merge_window_seconds` 由隐藏改为可见。

## 3. 滑动合并窗口

### 3.1 状态机（窗口期单阶段）

按 `(unified_msg_origin, sender_id)` 维护窗口状态：

- **closed**：无窗口。首次唤醒（`is_at_or_wake_command`，私聊天然唤醒）到达时 `start_window`，进入 **window**。
- **window**：持有首个唤醒事件；该用户后续**任何**消息（带不带唤醒词均可，群聊无需再 @）通过 `capture`（非唤醒）或 `merge_wake`（唤醒）并入缓冲，并更新 `last_activity_at`。
  - 条数超过 `merge_max_messages`（默认 5，0=不限）或累计字符超过 `merge_max_chars`（默认 2000，0=不限）时，该条不再并入（沿用现有 `_within_limits` 语义）；
  - 带引用组件的唤醒消息：`cancel_window` 取消窗口，旧事件直接 `stop_event()`（不再走 supersede），引用消息放行由 AstrBot 原生处理；
  - 无法合并的消息（忽略前缀 / 超限 / 不可合并组件）放行，按独立消息处理（沿用 v3.0.23 语义）。
- **收口**：`_open_merge_window` 循环等待——每次检查距 `last_activity_at` 是否已满 `merge_window_seconds`（默认 6 秒，配置范围 1~30），未满则短眠（≤0.2 秒）后复查；满后 `finalize_window`：合并文本（沿用"用户消息1/2/…"编号与整体回应提示），媒体挂回事件，进入正常 LLM 管线。

### 3.2 在途回复的处理

窗口已收口、回复开始生成后：

- 新消息到达时插件**不做任何事**：不 stop、不合并、不重生成、不开新窗口；
- 交由 AstrBot 原生 follow-up 捕获（4.27+）处理；群聊未唤醒消息本就由 AstrBot 判定是否理会；
- `is_session_busy` 仍用于"窗口已关闭但旧回复尚未结束"时阻止为新消息开窗（避免与在途回复抢会话）。

已知取舍（写进 README）：v3.0.19 修复的"私聊旧 Agent 卡在 LLM 超时重试时新消息被 follow-up 吞掉、迟迟不回复"问题会回归；如需恢复可后续增加配置开关。

### 3.3 窗口期间的媒体与空消息

- 纯图片/文件消息（无文字）沿用 `MEDIA_ONLY_PROMPT` 占位；
- 空内容事件（戳一戳/通知类）不参与、不重置（沿用 v3.0.13 语义）；
- 其他用户消息不参与、不重置（按发送者隔离）。

## 4. LLM 智能分段

### 4.1 调用方式

```python
llm_resp = await asyncio.wait_for(
    context.llm_generate(
        chat_provider_id=segment_provider_id,
        prompt=_SEGMENT_PROMPT.format(text=text),
    ),
    timeout=segment_timeout_seconds,
)
```

- 仅传入待分文本与分段提示词，**不传**任何会话上下文、人物设定、system prompt；
- provider 由配置下拉框选择，模型在 provider 内配置（4B/9B 可通过配置两个 SiliconFlow provider 或修改 provider 内模型切换）；
- `segment_provider_id` 为空：不调 LLM，仅规则分段，并打 warning 日志；
- 超时/异常：回退规则分段，绝不阻塞主回复超过超时上限。

### 4.2 提示词（样板，后续可微调）

```
你是聊天消息分段助手。你的唯一任务是把一段文本拆分成适合在聊天窗口逐条发送的多条消息。
严格要求：
1. 不增删、不改写、不润色、不翻译原文的任何文字，只决定在哪里分段；
2. 每个分段是一个完整、自然、可独立阅读的语义块；
3. 只输出 JSON 数组，每个元素是一条消息的完整文本；不要输出解释或前后缀；
4. 若原文不适合分段，输出只含一个元素的数组。
原文：
{text}
```

### 4.3 结果校验与回退

校验（任一失败 → 规则分段回退）：

1. JSON 解析成功，结果为字符串数组；
2. 每段非空；
3. 各段拼接后去掉所有空白字符，与原文去掉空白字符后**完全一致**（内容零改动）；
4. 代码围栏完整性：任一分段内 `` ``` `` 出现次数为奇数视为切断围栏，回退；
5. 段数在 2~`segment_max_messages`（默认 3）之间；1 段视为"无需分段"，按原文单条发送。

规则分段（`_rule_split`）：

- 优先按已有空行分块，再对超长块按句号/感叹号/问号断句；
- 不切分代码围栏内部；单段上限与 `segment_max_messages` 一致；
- 纯文本且长度 < `segment_min_chars`（默认 150）时直接返回原文（不调 LLM）。

### 4.4 发送

- `on_decorating_result`（priority 1000，清洗结构标签之后）：仅当回复链为**纯文本**（全为 `Plain`）且启用且长度达标时执行分段；
  - 分段成功且段数 ≥ 2：链替换为第一段；剩余段存入 `event.set_extra("segment_followups", [...])`；
  - 否则链不变（原文单条发送）。
- `after_message_sent`：主消息发送成功后，若存在 `segment_followups`，创建后台任务逐条 `context.send_message(origin, MessageChain().message(seg))`，段间随机延迟 `segment_delay_min`~`segment_delay_max`（默认 0.8~2.0 秒），逐条调用自回复标记记录（防复读/归属）；
  - 后台任务登记在插件级 task set，`done` 回调自动清理，避免泄漏；
  - 某条补发失败仅 warning 日志，继续后续段。
- 带图片/文件等非纯文本的回复不分段。

### 4.5 并发与延迟

- 长回复首条消息会等待分段结果，最长 `segment_timeout_seconds`（默认 10 秒），典型小模型耗时 1~3 秒；
- 分段调用发生在装饰阶段（主 LLM 已生成完毕），不占用会话锁；
- 多个会话可并行分段；v1 不做并发上限，若遇限流后续再加信号量。

## 5. 数据流

```
用户消息
  → on_message           窗口捕获 + 重置计时器（含图片/文件）；在途回复时不动手
  → on_waiting_llm_request  滑动开窗（循环等待静默）/ 引用取消 / 放行
  → on_llm_request        内容防护 / 任务执行指令 / 自回复标记（不变）
  → LLM 生成
  → on_llm_response       中断占位符拦截（不变；去掉 superseded 部分）
  → on_decorating_result  去重 → 引用重定向 → 结构标签清洗 → LLM 智能分段 → 首段入链
  → 主消息发送
  → after_message_sent    记录自回复 → 后台补发剩余段（随机延迟 + 逐条记录）
```

## 6. 错误处理

| 场景 | 处理 |
|---|---|
| 分段 LLM 超时 / 异常 | 规则分段回退 |
| 分段结果校验失败 | 规则分段回退 |
| 规则分段异常 / 无 provider | 原文单条发送（回复绝不丢失） |
| 补发消息失败 | warning 日志，继续发送后续段 |
| 窗口 sleep 期间事件被停止（引用取消等） | 直接收口，事件不再进入 LLM 管线 |
| provider 不存在 / key 失效 | `llm_generate` 抛异常 → 规则分段回退 + warning |

## 7. 配置清单

### 可见配置（设置面板）

| 配置 | 类型 | 默认 | 说明 |
|---|---|---|---|
| 启用分段消息合并窗口 | bool | true | 主开关（沿用） |
| 合并窗口时长（秒） | float | 6.0 | **由隐藏改可见**；1~30 秒，窗口期内同用户新消息会重置计时 |
| 启用 LLM 智能分段 | bool | false | 长回复按语义拆分逐条发送 |
| 分段 LLM provider | string（`_special: "select_provider"`） | "" | 下拉选择 provider；空则仅规则分段 |
| 分段最小字数 | int | 150 | 短于此直接原文发送 |
| 最大分段数 | int | 3 | 2~5 之间生效，超出合并到最后一段 |
| 启用 bot 自回复归属标记 | bool | true | 沿用 |
| 启用群聊内容防护 | bool | true | 沿用 |
| 内容防护模式 / 词库 | string | balanced / "" | 沿用 |

### 隐藏配置

| 配置 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `segment_timeout_seconds` | float | 10.0 | 分段请求超时，1~30 秒 |
| `segment_delay_min` / `segment_delay_max` | float | 0.8 / 2.0 | 补发段间随机延迟区间 |
| `merge_max_messages` | int | 5 | 沿用 |
| `merge_max_chars` | int | 2000 | 沿用 |
| `merge_ignore_prefixes` | string | "/,!" | 沿用 |
| `merge_include_media` | bool | true | 沿用 |
| 其余自回复标记 / 内容防护隐藏项 | - | - | 沿用 |

## 8. 测试策略

- `test_merge_window.py`：滑动重置（capture/merge_wake 更新 `last_activity_at`）、静默后收口、超限不并入、`cancel_window`、planning 相关方法已删除、`sync_pending_text` 保留；
- `test_merge_integration.py` 等集成测试：窗口循环时序（注入 fake clock）、引用取消时旧事件被 stop 且不进入 LLM、在途回复收到新消息不产生任何 stop / 合并调用；
- `test_reply_coordinator.py`：瘦身后 admit（幂等 + 跳过已停止事件）、`is_session_busy`、`finish_active`；supersede 测试删除；
- 新增 `test_smart_segment.py`：提示词构造、JSON 解析、零改动校验、围栏校验、段数上限、超时回退、规则分段、无 provider 回退；
- `test_config_schema.py`：`VISIBLE_KEYS` 与 `REMOVED_KEYS` 更新（新增 5 项、`merge_window_seconds` 转可见、移除 `merge_planning_ttl` / `merge_stop_remark_seconds`）；
- `tests/conftest.py`：`make_optimizer` 默认配置补新键，`FakeContext` 增加 `llm_generate` 桩（可选）；
- 全量 `pytest` 通过。

## 9. 版本与文档

- `metadata.yaml` 版本升至 `3.1.0`，desc 同步；
- README：合并窗口描述改为滑动窗口；新增"LLM 智能分段"能力、配置表、工作流程图、常见问题（如"为什么分段要等几秒"、"4B 与 9B 怎么切换"）；更新日志补 v3.1.0 条目；已知限制补充私聊超时重试挂起取舍。

## 10. 验收标准

1. 窗口期内同用户连发 N 条消息，只在静默满 6 秒后触发一次 LLM 请求，请求内容为合并后的完整文本；
2. 回复生成中收到新消息：不产生 stop / 重生成调用，回复照常发送；
3. 启用 LLM 分段后，超长纯文本回复被拆成 ≤ 3 条逐条发送，逐段内容拼接与原文完全一致；
4. 分段 provider 未配置 / 超时 / 异常时，回复仍按原文或规则分段发送，不丢失；
5. 全量测试通过，README / 版本号 / 配置面板与上述一致。
