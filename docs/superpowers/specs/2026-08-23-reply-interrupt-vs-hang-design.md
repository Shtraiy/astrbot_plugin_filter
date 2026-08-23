# 设计：规划期新消息「打断 vs 悬挂」（v3.0.14 → v3.0.15）

> 日期：2026-08-23
> 状态：已确认（打断边界 = 活跃事件是否已产出 LLM 响应）
> 范围：`astrbot_plugin_filter` 增量修复，无新配置项、无破坏性变更

## 1. 背景与目标

用户在 AstrBot 会话中先发一张图片，模型开始识图并规划回复；随后用户在窗口内连续补发两句话。期望行为是：每一条新消息都与之前的内容**合并**，模型基于完整上下文重新规划，而不是答非所问。

实测日志显示第二阶段（规划期补充）出现答非所问。根因在 AstrBot 核心的 follow-up 吞噬机制：

- `astrbot/core/pipeline/process_stage/method/agent_sub_stages/internal.py` 的 `try_capture_follow_up(event)` 在广播 `OnWaitingLLMRequestEvent` **之前**执行；
- 当 runner 已注册（`provider_request` 已设置、agent 正在运行）时，同会话新消息被吞为 follow-up，仅以 `FOLLOW_UP_NOTICE_TEMPLATE` 注入 agent 的下一轮，而 agent 下一轮可能已在进行其它子任务 → 输出与用户新消息无关；
- 本插件 `_request_agent_stop(old_event)` 会经 `active_event_registry.request_agent_stop_all` 设置 `agent_stop_requested`，AstrBot 4.27 起 follow-up capture 会跳过该标记 → 消息走正常管线，这是"打断"分支可行的关键；
- 但盲目打断也会误伤：若 provider 已开始调用（`provider_request` 已 built），agent 停止会让当前回复丢失，且 `Output stopped.` 占位符污染历史（v3.0.11 已清理记忆库一侧）。

目标：在"窗口期"之外，按活跃事件的真实状态分流——**provider 尚未开始调用时打断重生成；已开始调用时悬挂，让 AstrBot 原生 follow-up 接管**；修正词（再想想等）一律打断。

## 2. 规则表（已确认）

| 场景 | 判定 | 行为 |
|---|---|---|
| 窗口期 | 现有 `is_window_open` | 合并（不变） |
| 规划期，活跃事件**尚未产出 LLM 响应** | `active_event.get_extra("llm_output_started")` 为空 | 打断：stop 旧 agent + supersede 旧事件 + 合并重生成 |
| 规划期，活跃事件**已产出 LLM 响应** | `...` 非空 | 悬挂：不 stop、不 supersede、不合并；让核心 follow-up 接管，当前回复发完 |
| 规划期，任何状态 + 修正词 | `is_correction_follow_up(text)` | 一律打断 + 合并重生成 |

## 3. 关键机制依据

### 3.1 `provider_request` 标记为何不可用（v3.0.14 教训）

AstrBot 核心在 `build_main_agent` 内、`OnWaitingLLMRequestEvent` 钩子之后、agent 启动之前就把 `provider_request` 写入事件 extra（`astr_main_agent.py` 的 `event.set_extra("provider_request", req)`）。因此"第一条已开始构建请求"并不等于"已经开始调用 LLM"：

- v3.0.14 用 `provider_request` 是否设置作为"provider 已开始"的判定，导致几乎所有规划期补充都被判为悬挂；
- 实测日志（18:23:51 第一条 → 18:23:58 第二条）第二条被核心 `Captured follow-up message` 吞掉，第一条继续跑完并分别回复，与用户期望（打断 + 合并 + 只回复一次）相反；
- 结论：悬挂的判定必须晚于 `provider_request`，即"活跃事件是否已真正产出过 LLM 响应"。

### 3.2 `on_llm_response` 标记可用

AstrBot 的 `MainAgentHooks.on_agent_done` 在每轮 LLM 调用完成时广播 `OnLLMResponseEvent`（`astr_agent_hooks.py`），插件可在 `on_llm_response` 钩子（priority=1000）中给事件写入 `llm_output_started=True`。该标记晚于 `provider_request`，能区分"仍在准备/规划"与"已开始真实生成"。

### 3.3 follow-up 吞噬与 `agent_stop_requested`

- `try_capture_follow_up` 在 `OnWaitingLLMRequestEvent` 之前运行 → 本插件的 `on_message` / `on_waiting_llm_request` 必须赶在吞噬之前完成"打断"动作；
- `_request_agent_stop` 设置 `agent_stop_requested` 后，4.27+ 的 follow-up capture 不再吞消息（AstrBotDevs/AstrBot PR #6656 确认 `/stop` 后新消息走正常管线）；
- 因此：打断分支 = stop + supersede + 合并重生成（现有 `_handle_planning_phase` 路径）；悬挂分支 = 什么都不做，让核心 follow-up 自然接管。

## 4. 组件设计

### 4.1 `reply_coordinator.py`：新增 `active_event_for`

```python
def active_event_for(self, event: Any) -> Any | None:
    """Return the session's active event, or None when none is active."""
    session = self._session_key(event)
    active = self._active_by_session.get(session)
    if active is None or active is event:
        return None
    return active
```

复用现有 `_session_key`，语义与 `is_session_busy` / `active_same_sender` 一致：只认同会话、且不是自己。`on_message` 与 `_handle_planning_phase` 都通过它取活跃事件，避免各自重复拼 session key。

### 4.2 `merge_guards.py`：两个纯函数

```python
CORRECTION_TERMS = ("再想想", "不对", "等一下", "换一个", "重新", "忘了", "不是这个")

def is_correction_follow_up(text: str) -> bool:
    """True when the follow-up text reads like a correction to the in-flight reply."""
    ...

def should_interrupt_running_reply(reply_output_started: bool, is_correction: bool) -> bool:
    """No LLM output yet -> interrupt; output already started -> hang, unless correction."""
    return is_correction or not reply_output_started
```

修正词判定规则：去空白/去 `@bot` 前缀后，整段文本匹配词表（精确等于，或文本以词开头且长度不超过词表项 + 少量容忍长度，如 8 字符），或文本包含任意词表项且不含"不要/别"等否定前缀。具体实现以测试锁定边界。

### 4.3 `main.py` 变更

`on_llm_response_guard`（priority=1000，已存在）中，在现有 `stop_if_superseded` 之后增加：

- 若事件未被停止，写入 `event.set_extra("llm_output_started", True)`，作为"活跃事件已产出 LLM 响应"的标记。

`on_message`（当前无条件 `_request_agent_stop` + `promote_planning`）改为：

1. 仍先处理窗口期（不变）；
2. 取 `active = coordinator.active_event_for(event)`；
3. `reply_output_started = active is not None and bool(active.get_extra("llm_output_started"))`（异常时按 False 处理，安全侧为打断）；
4. 若 `active is None` 或 `should_interrupt_running_reply(reply_output_started, is_correction_follow_up(event.message_str))` 为 False → **提前 return，不 stop、不 promote、不合并**；
5. 否则走现有 `_request_agent_stop` + `promote_planning` 打断路径。

`on_waiting_llm_request` 的 `_handle_planning_phase` 同规则：取活跃事件 → 悬挂判定 → 悬挂时直接返回 False（不 `take_planning`、不 stop、不 supersede），让事件落入后续 `admit_wakeup` 普通管线。

**悬挂分支必须清理 planning state**：悬挂时 `merger.take_planning(event)` 未被消费，`planning_active` 会继续把下一次正常唤醒误判为规划期补充。因此悬挂分支调用新增的幂等清理（`merger.clear_state(old_event)`），只清旧事件的 state，不动 `_cancelled_event_ids`，也不 `supersede_active_event`——让活跃事件自然结束、走核心 follow-up 收尾。

## 5. 数据流

### 5.1 打断路径（尚未产出 LLM 响应 / 修正词）

```
规划期新消息
  -> on_message / _handle_planning_phase
  -> 判定 should_interrupt == True
  -> _request_agent_stop(old_event)      # 设 agent_stop_requested，follow-up 不再吞
  -> coordinator.supersede_active_event(old_event)
  -> take_planning + join_text + rearm_planning
  -> 合并后的新请求进入正常管线
```

### 5.2 悬挂路径（已产出 LLM 响应且非修正词）

```
规划期新消息
  -> 判定 should_interrupt == False
  -> 不 stop、不 supersede、不 take_planning
  -> merger.clear_state(old_event)        # 下一次消息不再被误判为规划期补充
  -> 核心 try_capture_follow_up 吞为 follow-up
  -> 当前回复正常发完；用户下一条消息走全新会话管线
```

## 6. 错误处理

- `active_event_for` 内部 `_session_key` 异常 → 返回 None，视为无可打断事件；
- `active.get_extra` 不存在/抛异常 → `reply_output_started = False`（安全侧：打断），并记录 debug 日志；
- 修正词判定对空文本/None → False（不打断）；
- 悬挂分支清理失败不影响主流程（`clear_state` 自身幂等、不抛异常）。

## 7. 测试（TDD）

新增/扩展：

1. `is_correction_follow_up`：命中词表、带 `@bot` 前缀、含否定前缀不命中、普通补充不命中；
2. `should_interrupt_running_reply`：`(False, False)=True`、`(True, False)=False`、`(True, True)=True`；
3. `ReplyCoordinator.active_event_for`：无活跃、活跃是自己、活跃是他人（同会话）三态；
4. 集成 `on_message`：`provider_request` 存在但未产出 LLM 响应 → 仍打断 + promote；已产出 LLM 响应且非修正 → 不 stop、不 promote；
5. 集成 `on_waiting_llm_request`：悬挂分支不合并、不 supersede、planning state 被清理；`on_llm_response_guard` 对正常响应写入 `llm_output_started`；
6. 回归：窗口期、打断重生成、`Output stopped.` 清洗全部保持通过。

## 8. 非目标

- 不引入轻量模型判断（维持规则式判定，混入方案已搁置）；
- 不处理白名单问题；
- 不新增配置项；
- 不修改 AstrBot 核心。

版本：v3.0.15，更新 `metadata.yaml` 与 README badge。
