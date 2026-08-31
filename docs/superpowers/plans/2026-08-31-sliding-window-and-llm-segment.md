# 滑动合并窗口 + LLM 智能分段 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把固定合并窗口改为"同用户新消息重置计时"的滑动窗口，彻底移除规划期打断/重生成机制，并新增基于 AstrBot provider 的轻量 LLM 智能分段（长纯文本回复按语义拆成多条逐条发送）。

**Architecture:** `merge_window.py` 变为单阶段窗口状态机（`last_activity_at` 支撑滑动收口，`finalize_window` 收口后即销毁状态）；`reply_coordinator.py` 瘦身为 admit/busy/finish；`main.py` 删除全部打断/重标/supersede 路径，窗口等待改为短眠循环；新增 `smart_segment.py` 在 `on_decorating_result` 拦截纯文本回复，调 `context.llm_generate(chat_provider_id=segment_provider_id, ...)` 分段并校验，`after_message_sent` 后台补发剩余段。

**Tech Stack:** Python 3.10+（`X | None` 语法）、AstrBot Star API（`context.llm_generate`、事件钩子）、`_conf_schema.json`（含 `_special: "select_provider"`）、pytest。

## Global Constraints

- Python 3.10+；禁止引入新运行时依赖（`requirements.txt` 保持空/注释态）。
- AstrBot 版本约束 `>=4.16,<5`（`metadata.yaml`）。
- 所有用户可见文案与日志使用中文（沿用现有约定）。
- 配置 schema 形状：顶层无 `properties`/`type`；每个键为 dict，`type` ∈ {string,bool,int,float,list,object}，可见性由 `invisible` 控制；`_special` 可作为附加键。
- 测试：`pytest` 从仓库根目录运行；`tests/conftest.py` 提供 AstrBot stub、`FakeEvent`、`FakeContext`、`make_optimizer`。
- 提交信息遵循 conventional commits（`feat`/`fix`/`refactor`/`docs`/`test`/`chore`）。
- 行为契约：回复绝不因分段丢失；分段零改写；窗口静默满 `merge_window_seconds` 才发起 LLM；在途回复不被打断。

---

## 文件地图

| 文件 | 操作 | 职责 |
|---|---|---|
| `merge_window.py` | 修改 | 单阶段滑动窗口状态机 |
| `reply_coordinator.py` | 修改 | 瘦身为 admit/busy/finish |
| `main.py` | 修改 | 删除打断机制、滑动开窗、分段集成 |
| `self_reply_marker.py` | 修改 | 新增 `record_sent_text(origin, text)` |
| `smart_segment.py` | 新增 | LLM/规则分段、校验、补发辅助 |
| `merge_guards.py` | 删除 | 打断/修正词/superseded 守卫 |
| `_conf_schema.json` | 修改 | 新增 7 项、删除 2 项、`merge_window_seconds` 转可见 |
| `tests/conftest.py` | 修改 | 模块表去 `merge_guards`；默认配置增删；`FakeContext.llm_generate` 桩 |
| `tests/test_merge_window.py` | 修改 | 滑动重置测试；删除 planning 测试 |
| `tests/test_reply_coordinator.py` | 修改 | 删除 supersede 测试 |
| `tests/test_merge_integration.py` | 修改 | 删除规划期/打断测试；新增滑动时序测试 |
| `tests/test_merge_guards.py` | 删除 | 守卫已移除 |
| `tests/test_smart_segment.py` | 新增 | 分段模块单测 |
| `tests/test_config_schema.py` | 修改 | 可见/删除键集更新 |
| `metadata.yaml` | 修改 | 版本 3.1.0、desc |
| `README.md` | 修改 | 能力、配置、流程、FAQ、更新日志 |

---

### Task 1: MergeWindowManager 单阶段滑动窗口

**Files:**
- Modify: `merge_window.py`
- Test: `tests/test_merge_window.py`

**Interfaces:**
- Consumes: `get_config(key, default)`；`_now()`（monotonic）。
- Produces:
  - `start_window(event) -> bool`
  - `is_window_open(event) -> bool`
  - `capture(event) -> bool`（非唤醒补充，成功时重置计时）
  - `merge_wake(event) -> bool`（唤醒补充，成功时重置计时）
  - `finalize_window(event) -> str`（**收口后销毁状态**）
  - `cancel_window(event) -> Any | None`
  - `clear_state(event) -> None`
  - `quiet_remaining(event, window_seconds, now=None) -> float`
  - `has_media(event) -> bool`、`message_has_quote(event) -> bool`、`window_key(event)`、`format_segments`、`append_segment`、`join_text`（保留）

- [ ] **Step 1: 写失败测试（滑动重置 + planning 删除）**

在 `tests/test_merge_window.py` 中：删除 `test_take_planning_returns_accumulated_text_and_rearm_supports_recursion`、`test_take_planning_requires_planning_phase`、`test_planning_state_expires_after_ttl`、`test_rearm_planning_resets_ttl`、`test_planning_ttl_zero_disables_expiry`；把 `test_clear_state_drops_window_state` 的断言从 `planning_active` 改为 `quiet_remaining`；新增：

```python
def test_capture_resets_sliding_quiet_remaining():
    clock = {"t": 100.0}
    manager = make_manager(now=lambda: clock["t"])
    owner = FakeEvent("u1", "group:1", "第一段", wake=True)

    assert manager.start_window(owner)
    assert manager.quiet_remaining(owner, 6.0) == 6.0

    clock["t"] += 3.0
    follow = FakeEvent("u1", "group:1", "第二段", wake=False)
    assert manager.capture(follow)
    # 捕获后计时重置，仍需完整 6 秒静默
    assert manager.quiet_remaining(owner, 6.0) == 6.0

    clock["t"] += 5.5
    assert manager.quiet_remaining(owner, 6.0) == 0.5
    clock["t"] += 0.6
    assert manager.quiet_remaining(owner, 6.0) == 0.0


def test_merge_wake_resets_sliding_quiet_remaining():
    clock = {"t": 100.0}
    manager = make_manager(now=lambda: clock["t"])
    owner = FakeEvent("u1", "group:1", "第一段", wake=True)
    manager.start_window(owner)

    clock["t"] += 4.0
    wake = FakeEvent("u1", "group:1", "@bot 补充", wake=True)
    assert manager.merge_wake(wake)
    assert manager.quiet_remaining(owner, 6.0) == 6.0


def test_finalize_window_destroys_state():
    manager = make_manager()
    owner = FakeEvent("u1", "group:1", "第一段", wake=True)
    manager.start_window(owner)

    manager.finalize_window(owner)

    assert manager.quiet_remaining(owner, 6.0) == 0.0
    assert manager.is_window_open(FakeEvent("u1", "group:1", "x", wake=True)) is False


def test_capture_rejects_after_finalize():
    manager = make_manager()
    owner = FakeEvent("u1", "group:1", "第一段", wake=True)
    manager.start_window(owner)
    manager.finalize_window(owner)

    assert not manager.capture(FakeEvent("u1", "group:1", "迟到消息", wake=False))
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_merge_window.py -v`
Expected: FAIL（`quiet_remaining` 不存在；旧 planning 测试被删后不再有 planning 相关用例）

- [ ] **Step 3: 实现单阶段滑动窗口**

`merge_window.py` 改动：

```python
@dataclass
class _MergeState:
    owner_event: Any
    pending_text: str
    segments: list[str] = field(default_factory=list)
    pending_media: list[Any] = field(default_factory=list)
    captured_events: set[Any] = field(default_factory=set)
    captured_count: int = 0
    last_captured_id: Any = None
    last_activity_at: float = 0.0
```

- 删除：`phase` 字段、`planning_active`、`take_planning`、`rearm_planning`、`_planning_expired`、`_planning_ttl`、`sync_pending_text`、`planning_started_at`。
- `start_window`：`last_activity_at=self._now()`；不再设置 `phase`。
- `capture` / `merge_wake`：合并成功（`state.captured_count += 1` 处）时 `state.last_activity_at = self._now()`；删除 `state.phase != "window"` 判断。
- `is_window_open`：`state is not None and state.owner_event is not event`。
- `cancel_window` / `clear_state`：删除 phase 判断。
- `finalize_window`：保留 owner 判断、`format_segments`、`attach_media`、`_record_last_message_id`；**末尾 `self._states.pop(key, None)` 销毁状态**。
- 新增：

```python
def quiet_remaining(
    self,
    event: Any,
    window_seconds: float,
    now: float | None = None,
) -> float:
    """距该窗口"静默满 window_seconds"还差的秒数；无窗口返回 0。"""
    key = self.window_key(event)
    if key is None:
        return 0.0
    state = self._states.get(key)
    if state is None:
        return 0.0
    current = self._now() if now is None else now
    return max(0.0, float(window_seconds) - (current - state.last_activity_at))
```

更新模块 docstring：单阶段窗口状态机，`finalize_window` 后状态销毁。

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_merge_window.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add merge_window.py tests/test_merge_window.py
git commit -m "refactor: single-phase sliding merge window without planning state"
```

---

### Task 2: ReplyCoordinator 瘦身

**Files:**
- Modify: `reply_coordinator.py`
- Test: `tests/test_reply_coordinator.py`

**Interfaces:**
- Consumes: `event.unified_msg_origin`、`event.get_sender_id()`（不再使用）。
- Produces:
  - `async admit_wakeup(event) -> bool`（幂等；已停止的唤醒事件返回 False）
  - `is_session_busy(event) -> bool`
  - `finish_active(event) -> bool`
  - `active_by_session` 属性

- [ ] **Step 1: 写失败测试（删除 supersede、新增 stopped 跳过）**

在 `tests/test_reply_coordinator.py` 中删除：`test_supersede_marks_cancelled_stops_and_clears`、`test_supersede_rejects_unrelated_event`、`test_active_event_for_three_states`、`test_active_event_for_ignores_other_sender`、`test_active_same_sender_distinguishes_users`、`test_active_same_sender_false_without_active`、`test_discard_superseded_result_clears_chain`、`test_discard_superseded_result_noop_for_normal_event`。新增：

```python
def test_admit_skips_stopped_wake_events():
    coordinator = make_coordinator()
    event = FakeEvent("u1", "group:1", wake=True)
    event.stop_event()

    assert asyncio.run(coordinator.admit_wakeup(event)) is False
    assert coordinator.active_by_session == {}
```

`FakeEvent` 需已有 `stop_event()`/`is_stopped()`（当前测试文件内的 FakeEvent 已有）。

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_reply_coordinator.py -v`
Expected: FAIL（`admit_wakeup` 未跳过已停止事件；`supersede_active_event` 相关用例已删）

- [ ] **Step 3: 实现瘦身**

`reply_coordinator.py` 改为：

```python
"""Per-session wake-up admission and active-reply tracking.

No merge supersede machinery: an in-flight reply is never interrupted; the
coordinator only admits wake-ups, reports whether the session is busy, and
clears the active slot after a reply is sent.
"""

from __future__ import annotations

from typing import Any, Callable


class ReplyCoordinator:
    """Track one active LLM reply per session for busy/admission decisions."""

    def __init__(
        self,
        *,
        event_is_wake_up: Callable[[Any], bool] | None = None,
    ) -> None:
        self._event_is_wake_up = event_is_wake_up or (lambda _event: True)
        self._active_by_session: dict[str, Any] = {}

    @property
    def active_by_session(self) -> dict[str, Any]:
        return dict(self._active_by_session)

    async def admit_wakeup(self, event: Any) -> bool:
        """Admit a wake event and track it as the session's active reply."""
        if not event:
            return False
        if self._event_is_wake_up(event):
            if self._is_stopped(event):
                return False
            session = self._session_key(event)
            if session not in self._active_by_session:
                self._active_by_session[session] = event
        return True

    def is_session_busy(self, event: Any) -> bool:
        """True when another event is still the active reply of this session."""
        session = self._session_key(event)
        active = self._active_by_session.get(session)
        return active is not None and active is not event

    def finish_active(self, event: Any) -> bool:
        """Finish an active reply normally and clear the session slot."""
        if event is None:
            return False
        session = self._session_key(event)
        if self._active_by_session.get(session) is not event:
            return False
        self._active_by_session.pop(session, None)
        return True

    @staticmethod
    def _session_key(event: Any) -> str:
        return str(getattr(event, "unified_msg_origin", "") or "__unified_default__")

    @staticmethod
    def _is_stopped(event: Any) -> bool:
        checker = getattr(event, "is_stopped", None)
        if callable(checker):
            try:
                return bool(checker())
            except Exception:
                return False
        return False


__all__ = ["ReplyCoordinator"]
```

删除 `MAX_CANCELLED_IDS`、`active_event_for`、`active_same_sender`、`supersede_active_event`、`is_superseded`、`discard_superseded_result`、`_remember_cancelled`、`_stop_event`。

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_reply_coordinator.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add reply_coordinator.py tests/test_reply_coordinator.py
git commit -m "refactor: slim reply coordinator to admit/busy/finish"
```

---

### Task 3: main.py 移除打断机制 + 滑动开窗

**Files:**
- Modify: `main.py`
- Test: `tests/test_merge_integration.py`

**Interfaces:**
- Consumes: Task 1 的 `MergeWindowManager.quiet_remaining`；Task 2 的 `ReplyCoordinator`。
- Produces:
  - `_handle_window_phase(event, merger) -> str`（不再接收 coordinator）
  - `_open_merge_window(event, merger)`（短眠循环等待静默）
  - `_stop_event(event)` 模块级辅助

- [ ] **Step 1: 更新集成测试**

在 `tests/test_merge_integration.py` 中删除以下用例（规划期/打断/重标/supersede/输出标记）：

`test_planning_supplement_supersedes_and_regenerates`、`test_group_non_wake_supplement_not_promoted_during_planning`、`test_on_llm_response_guard_stops_superseded`、`test_media_only_supplement_gets_recognition_prompt_during_planning`、`test_media_supplement_keeps_existing_text_without_placeholder`、`test_expired_planning_does_not_promote_later_media_message`、`test_same_sender_wakeup_requests_agent_stop_even_without_merge_state`、`test_interrupt_branch_marks_active_event_agent_stop_requested`、`test_planning_merge_marks_old_event_agent_stop_requested`、`test_stop_remark_reattaches_flag_after_astrbot_reset`、`test_group_planning_wakeup_marks_active_event_agent_stop_requested`、`test_group_streaming_reply_hang_does_not_mark_agent_stop_requested`、`test_other_sender_wakeup_does_not_request_agent_stop`、`test_non_wake_same_sender_message_does_not_stop_agent`、`test_superseded_result_discarded_on_decoration`、`test_empty_event_during_planning_does_not_request_agent_stop`、`test_non_wake_text_event_during_planning_does_not_request_agent_stop`、`test_llm_output_started_supplement_hangs_without_stop_or_promote`、`test_llm_output_started_supplement_hangs_on_waiting_and_clears_planning_state`、`test_provider_request_without_llm_output_still_interrupts_and_regenerates`、`test_correction_interrupts_even_after_llm_output_started`、`test_llm_response_guard_marks_output_started`、`test_streaming_started_supplement_hangs_without_stop_or_promote`、`test_wake_supplement_hangs_after_llm_output_started`、`test_private_supplement_interrupts_even_after_llm_output_started`、`test_planning_supplement_records_last_message_id`、`test_other_sender_message_does_not_clear_planning_state`、`test_empty_event_does_not_open_merge_window`、`test_planning_supplements_keep_accumulating_text_and_image`。

新增滑动重置集成用例：

```python
def test_window_resets_on_new_message_until_silence():
    optimizer = make_optimizer()
    optimizer._get_merge_window_seconds = lambda: 0.15
    first = FakeEvent("u1", "group:1", "第一段", wake=True)
    second = FakeEvent("u1", "group:1", "第二段", wake=False)
    third = FakeEvent("u1", "group:1", "第三段", wake=False)

    async def run():
        first_task = asyncio.create_task(optimizer.on_waiting_llm_request(first))
        await asyncio.sleep(0.05)
        await optimizer.on_message(second)  # 重置计时
        await asyncio.sleep(0.10)          # 未满静默，仍在窗口
        await optimizer.on_message(third)  # 再次重置
        await first_task                    # 静默满后收口
        return first.message_str

    merged = asyncio.run(run())

    assert merged == MergeWindowManager.format_segments(
        ["第一段", "第二段", "第三段"]
    )
```

新增"在途消息不再打断"用例：

```python
def test_inflight_reply_gets_no_stop_on_new_message():
    optimizer = make_optimizer()
    old = FakeEvent("u1", "group:1", "第一段", wake=True)
    asyncio.run(optimizer.on_waiting_llm_request(old))  # 窗口已收口
    assert optimizer._get_message_merger().quiet_remaining(old, 6.0) == 0.0

    follow = FakeEvent("u1", "group:1", "@bot 补充", wake=True)
    asyncio.run(optimizer.on_message(follow))
    asyncio.run(optimizer.on_waiting_llm_request(follow))

    assert old.stopped is False
    assert follow.message_str == "@bot 补充"  # 未被合并改写
```

`test_on_llm_response_guard_stops_superseded` 的删除意味着 `on_llm_response_guard` 不再有 superseded 断言；保留 `test_llm_response_guard_marks_output_started` 的删除（`llm_output_started` 一并移除）。

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_merge_integration.py -v`
Expected: FAIL（`_handle_window_phase` 签名、`quiet_remaining` 行为、`on_waiting_llm_request` 仍引用已删方法）

- [ ] **Step 3: 实现 main.py 改造**

删除 import：`from .merge_guards import (...)` 整行。

`on_message` 改为：

```python
@_event_filter.event_message_type(_event_filter.EventMessageType.ALL)
async def on_message(self, event: AstrMessageEvent) -> None:
    """Capture same-user non-wake follow-ups while a window is open.

    In-flight replies are never interrupted: once the window closes and the
    reply is being generated, new messages are left to AstrBot's native
    follow-up handling.
    """
    if not self._get_config("enable_message_merge", True):
        return
    if not event_has_content(event):
        return
    try:
        merger = self._get_message_merger()
        if merger.is_window_open(event):
            merger.capture(event)
    except Exception:
        logger.warning("[消息合并] 捕获消息失败", exc_info=True)
```

`on_waiting_llm_request` 改为：

```python
@_event_filter.on_waiting_llm_request(priority=1000)
async def on_waiting_llm_request(self, event: AstrMessageEvent) -> None:
    """Open/merge the sliding window; never interrupt an in-flight reply."""
    if not event_has_content(event):
        return
    merger = self._get_message_merger()
    coordinator = self._get_reply_coordinator()
    merge_key = (
        merger.window_key(event)
        if self._get_config("enable_message_merge", True)
        else None
    )
    window_result = "none"

    if merge_key is not None:
        window_result = await self._handle_window_phase(event, merger)
        if window_result == "consumed":
            return

    if not await coordinator.admit_wakeup(event):
        return
    if (
        merge_key is not None
        and not coordinator.is_session_busy(event)
        and window_result != "cancel_quote"
    ):
        await self._open_merge_window(event, merger)
```

`_handle_window_phase` 改为：

```python
async def _handle_window_phase(
    self,
    event: AstrMessageEvent,
    merger: MergeWindowManager,
) -> str:
    """Handle a same-user follow-up while the window is open."""
    if not merger.is_window_open(event):
        return "none"
    if merger.message_has_quote(event):
        old = merger.cancel_window(event)
        if old is not None:
            _stop_event(old)
        return "cancel_quote"
    if merger.merge_wake(event):
        event.stop_event()
        return "consumed"
    if merger.is_captured(event):
        event.stop_event()
        return "consumed"
    return "none"
```

`_open_merge_window` 改为：

```python
async def _open_merge_window(
    self,
    event: AstrMessageEvent,
    merger: MergeWindowManager,
) -> None:
    """Hold the event until the user stays silent for the full window."""
    if not merger.start_window(event):
        return
    try:
        while True:
            if _event_is_stopped(event):
                return
            remaining = merger.quiet_remaining(
                event, self._get_merge_window_seconds()
            )
            if remaining <= 0:
                break
            await asyncio.sleep(min(remaining, 0.2))
    finally:
        if _event_is_stopped(event):
            merger.clear_state(event)
            return
        merged = merger.finalize_window(event)
        if (
            not (merged or "").strip()
            and merger.has_media(event)
        ):
            merged = MEDIA_ONLY_PROMPT
        event.message_str = merged
```

删除方法：`_handle_planning_phase`、`_request_agent_stop`、`_mark_agent_stop_requested`、`_schedule_stop_remark`、`_should_interrupt_active_reply`、`_event_is_private_chat`。

`on_llm_response_guard` 改为（只保留占位符拦截，删除 superseded 与 `llm_output_started`）：

```python
@_event_filter.on_llm_response(priority=1000)
async def on_llm_response_guard(self, event: AstrMessageEvent, resp) -> None:
    """Stop AstrBot interruption placeholders from being recorded."""
    try:
        if not _event_is_stopped(event) and _resp_is_interruption_placeholder(resp):
            event.stop_event()
            logger.info("[自回复标记] 已拦截中断占位符响应，阻止写入记忆")
    except Exception:
        logger.debug("[消息合并] 响应守卫失败", exc_info=True)
```

`on_decorating_result` 改为（删除 `discard_superseded_result` 分支）：

```python
@_event_filter.on_decorating_result()
async def on_decorating_result(self, event: AstrMessageEvent) -> None:
    if not event:
        return
    try:
        result = event.get_result()
        chain = getattr(result, "chain", None)
        origin = getattr(event, "unified_msg_origin", None)
        if origin and result is not None:
            text = _result_plain_text(result)
            if text and self._get_self_reply_marker().recently_sent_duplicate(
                origin, text
            ):
                logger.info("[消息合并] 检测到重复回复，丢弃避免复读")
                if chain is not None:
                    chain[:] = []
                self._get_message_merger().clear_state(event)
                return
        self._get_message_merger().clear_state(event)
        try:
            last_id = event.get_extra("merge_last_message_id")
            message_obj = getattr(event, "message_obj", None)
            if last_id is not None and message_obj is not None:
                message_obj.message_id = last_id
        except Exception:
            logger.debug("[消息合并] 重定向引用失败", exc_info=True)
        if chain:
            for comp in chain:
                if isinstance(comp, Plain) and comp.text:
                    cleaned = _strip_structure_tags(comp.text)
                    if cleaned != comp.text:
                        comp.text = cleaned
        await self._maybe_segment_reply(event)  # Task 5 实现
    except Exception:
        logger.debug("[消息合并] 结果清理失败", exc_info=True)
```

新增模块级辅助：

```python
def _stop_event(event) -> None:
    stopper = getattr(event, "stop_event", None)
    if callable(stopper):
        try:
            stopper()
        except Exception:
            logger.debug("[消息合并] 停止事件失败", exc_info=True)
```

注意：`_maybe_segment_reply` 在 Task 5 中实现；为保持本任务可独立测试，先在 `main.py` 添加空实现：

```python
async def _maybe_segment_reply(self, event) -> None:
    """LLM 智能分段（Task 5 实现）。"""
    return
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_merge_integration.py tests/test_merge_window.py tests/test_reply_coordinator.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add main.py tests/test_merge_integration.py
git commit -m "refactor: remove planning-phase interrupt, sliding window wait loop"
```

---

### Task 4: smart_segment 模块

**Files:**
- Create: `smart_segment.py`
- Test: `tests/test_smart_segment.py`

**Interfaces:**
- Consumes: `context.llm_generate(*, chat_provider_id: str, prompt: str) -> resp`（`resp.completion_text`）；`get_config(key, default)`。
- Produces:
  - `SEGMENT_PROMPT`（含 `{text}` 占位）
  - `parse_segment_json(raw: str) -> list[str] | None`
  - `validate_segments(original: str, segments: list[str], max_messages: int) -> bool`
  - `rule_split(text: str, max_messages: int) -> list[str]`
  - `async split_reply(text: str, context, get_config) -> list[str] | None`（`None` = 原文单条发送）

- [ ] **Step 1: 写失败测试**

新增 `tests/test_smart_segment.py`：

```python
import asyncio
from types import SimpleNamespace

from _astrbot_plugin_filter_test.smart_segment import (
    SEGMENT_PROMPT,
    parse_segment_json,
    rule_split,
    split_reply,
    validate_segments,
)


def test_prompt_contains_text_placeholder():
    assert "{text}" in SEGMENT_PROMPT


def test_parse_segment_json_accepts_plain_and_fenced_json():
    assert parse_segment_json('["a", "b"]') == ["a", "b"]
    assert parse_segment_json('```json\n["a", "b"]\n```') == ["a", "b"]
    assert parse_segment_json("") is None
    assert parse_segment_json("not json") is None
    assert parse_segment_json('{"a": 1}') is None
    assert parse_segment_json("[1, 2]") is None  # 非字符串元素


def test_validate_segments_enforces_zero_rewrite():
    assert validate_segments("你好世界", ["你好", "世界"], 3) is True
    assert validate_segments("你好世界", ["你好", "世界！"], 3) is False  # 改动
    assert validate_segments("你好世界", ["你好世界"], 3) is False  # 只有 1 段
    assert validate_segments("你好世界", ["你好", "世界", "多", "四段"], 3) is False
    assert validate_segments("你好世界", ["你好", "  "], 3) is False  # 空段
    assert validate_segments("```\ncode\n```", ["```\ncode\n```"], 3) is False
    assert validate_segments("```\ncode\n```", ["```\ncode", "\n```"], 3) is False


def test_rule_split_caps_and_preserves_content():
    text = "第一句。第二句！第三句？"
    parts = rule_split(text, 3)
    assert 2 <= len(parts) <= 3
    assert "".join(parts) == text

    long = "\n\n".join(f"第{i}段" for i in range(5))
    capped = rule_split(long, 3)
    assert len(capped) == 3
    assert "".join(part.replace("\n\n", "") for part in capped) == long.replace(
        "\n\n", ""
    )


def test_split_reply_skips_short_text():
    context = SimpleNamespace()
    assert asyncio.run(split_reply("太短", context, lambda k, d: d)) is None


def test_split_reply_llm_ok_with_validation():
    context = SimpleNamespace(
        llm_generate=async_stub('["第一段", "第二段"]')
    )
    config = {
        "segment_provider_id": "p1",
        "segment_min_chars": 10,
        "segment_max_messages": 3,
        "segment_timeout_seconds": 5,
    }
    result = asyncio.run(split_reply("第一段第二段", context, lambda k, d: config.get(k, d)))
    assert result == ["第一段", "第二段"]


def test_split_reply_llm_invalid_falls_back_to_rules():
    context = SimpleNamespace(llm_generate=async_stub("['改了原文']"))
    config = {
        "segment_provider_id": "p1",
        "segment_min_chars": 10,
        "segment_max_messages": 3,
        "segment_timeout_seconds": 5,
    }
    result = asyncio.run(split_reply("第一段第二段", context, lambda k, d: config.get(k, d)))
    assert result is not None
    assert "".join(result) == "第一段第二段"


def test_split_reply_without_provider_uses_rules():
    context = SimpleNamespace()
    config = {
        "segment_provider_id": "",
        "segment_min_chars": 2,
        "segment_max_messages": 3,
        "segment_timeout_seconds": 5,
    }
    result = asyncio.run(split_reply("第一段。第二段。", context, lambda k, d: config.get(k, d)))
    assert result is not None
    assert "".join(result) == "第一段。第二段。"


def async_stub(raw: str):
    async def _stub(*args, **kwargs):
        return SimpleNamespace(completion_text=raw)
    return _stub
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_smart_segment.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 smart_segment.py**

```python
"""Lightweight LLM-based reply segmentation with rule fallback."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Callable

from astrbot.api import logger

SEGMENT_PROMPT = (
    "你是聊天消息分段助手。你的唯一任务是把一段文本拆分成适合在聊天窗口逐条发送的多条消息。\n"
    "严格要求：\n"
    "1. 不增删、不改写、不润色、不翻译原文的任何文字，只决定在哪里分段；\n"
    "2. 每个分段是一个完整、自然、可独立阅读的语义块；\n"
    "3. 只输出 JSON 数组，每个元素是一条消息的完整文本；不要输出解释或前后缀；\n"
    "4. 若原文不适合分段，输出只含一个元素的数组。\n"
    "原文：\n{text}"
)

_FENCE_RE = re.compile(r"```")
_SENT_BOUNDARY_RE = re.compile(r"(?<=[。！？!?；;])\s*")


def _compact(text: str) -> str:
    return "".join(ch for ch in (text or "") if not ch.isspace())


def _fences_balanced(text: str) -> bool:
    return len(_FENCE_RE.findall(text or "")) % 2 == 0


def parse_segment_json(raw: str) -> list[str] | None:
    """Parse an LLM JSON-array reply into a list of strings."""
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw).strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except Exception:
        return None
    if not isinstance(data, list) or not data:
        return None
    parts = [item for item in data if isinstance(item, str)]
    if len(parts) != len(data):
        return None
    return parts


def validate_segments(
    original: str,
    segments: list[str],
    max_messages: int,
) -> bool:
    """Content-preservation check: join must equal original, fences intact."""
    if not segments or len(segments) > max_messages:
        return False
    if any(not (seg or "").strip() for seg in segments):
        return False
    if _compact("".join(segments)) != _compact(original):
        return False
    if any(not _fences_balanced(seg) for seg in segments):
        return False
    return True


def _protect_fences(parts: list[str]) -> list[str]:
    """Merge a part into the previous one when the cut fell inside a fence."""
    merged: list[str] = []
    for part in parts:
        if merged and not _fences_balanced(merged[-1]):
            merged[-1] = merged[-1] + "\n\n" + part
        else:
            merged.append(part)
    return merged


def _cap_parts(parts: list[str], max_messages: int) -> list[str]:
    if max_messages < 2:
        max_messages = 2
    if len(parts) <= max_messages:
        return parts
    head = parts[: max_messages - 1]
    tail = "\n\n".join(parts[max_messages - 1 :])
    return head + [tail]


def rule_split(text: str, max_messages: int) -> list[str]:
    """Split on blank lines / sentence boundaries, capped at max_messages."""
    text = (text or "").strip()
    if not text:
        return []
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if len(paragraphs) < 2:
        sentences = [s.strip() for s in _SENT_BOUNDARY_RE.split(text) if s.strip()]
        paragraphs = sentences or [text]
    paragraphs = _protect_fences(paragraphs)
    return _cap_parts(paragraphs, max_messages)


def _get_min_chars(get_config: Callable[[str, Any], Any]) -> int:
    try:
        value = int(get_config("segment_min_chars", 150))
    except (TypeError, ValueError):
        return 150
    return max(20, min(value, 1000))


def _get_max_messages(get_config: Callable[[str, Any], Any]) -> int:
    try:
        value = int(get_config("segment_max_messages", 3))
    except (TypeError, ValueError):
        return 3
    return max(2, min(value, 5))


def _get_timeout(get_config: Callable[[str, Any], Any]) -> float:
    try:
        value = float(get_config("segment_timeout_seconds", 10.0))
    except (TypeError, ValueError):
        return 10.0
    return max(1.0, min(value, 30.0))


async def _try_llm_segment(
    text: str,
    provider_id: str,
    context: Any,
    get_config: Callable[[str, Any], Any],
) -> list[str] | None:
    max_messages = _get_max_messages(get_config)
    timeout = _get_timeout(get_config)
    try:
        logger.info("[智能分段] 请求 provider=%s", provider_id)
        llm_resp = await asyncio.wait_for(
            context.llm_generate(
                chat_provider_id=provider_id,
                prompt=SEGMENT_PROMPT.format(text=text),
            ),
            timeout=timeout,
        )
        raw = (getattr(llm_resp, "completion_text", "") or "").strip()
        parts = parse_segment_json(raw)
        if parts is None:
            logger.warning("[智能分段] LLM 输出不是合法 JSON 数组，回退规则分段")
            return None
        if not validate_segments(text, parts, max_messages):
            logger.warning("[智能分段] 校验失败（内容被改动/段数超限/围栏切断），回退规则分段")
            return None
        return parts
    except asyncio.TimeoutError:
        logger.warning("[智能分段] 请求超时，回退规则分段")
        return None
    except Exception:
        logger.warning("[智能分段] 请求失败，回退规则分段", exc_info=True)
        return None


async def split_reply(
    text: str,
    context: Any,
    get_config: Callable[[str, Any], Any],
) -> list[str] | None:
    """Return 2..max segments, or None to send the reply as-is."""
    text = (text or "").strip()
    if not text or len(text) < _get_min_chars(get_config):
        return None
    max_messages = _get_max_messages(get_config)
    provider_id = str(get_config("segment_provider_id", "") or "").strip()
    segments: list[str] | None = None
    if provider_id:
        segments = await _try_llm_segment(text, provider_id, context, get_config)
    else:
        logger.warning("[智能分段] 未配置 segment_provider_id，仅使用规则分段")
    if segments is None:
        segments = _protect_fences(rule_split(text, max_messages))
    segments = _cap_parts(segments, max_messages)
    if len(segments) < 2:
        return None
    return segments


__all__ = [
    "SEGMENT_PROMPT",
    "parse_segment_json",
    "rule_split",
    "split_reply",
    "validate_segments",
]
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_smart_segment.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add smart_segment.py tests/test_smart_segment.py
git commit -m "feat: lightweight LLM reply segmentation with rule fallback"
```

---

### Task 5: main.py 分段集成 + 补发 + SelfReplyMarker.record_sent_text

**Files:**
- Modify: `main.py`、`self_reply_marker.py`
- Test: `tests/test_smart_segment.py`（追加）、`tests/test_merge_integration.py`（追加）

**Interfaces:**
- Consumes: Task 4 的 `split_reply`；`SelfReplyMarker.record_sent_text(origin, text)`。
- Produces: `_maybe_segment_reply(event)`、`_send_segment_followups(event)`、`_pending_segment_tasks` 集合。

- [ ] **Step 1: 写失败测试**

`tests/conftest.py` 的 `FakeContext` 增加：

```python
class FakeContext:
    def __init__(self):
        self.sent = []
        self.llm_responses = {}

    async def send_message(self, origin, chain):
        self.sent.append((origin, chain))

    async def llm_generate(self, *, chat_provider_id, prompt, **kwargs):
        response = self.llm_responses.get(chat_provider_id)
        if callable(response):
            return await response(chat_provider_id, prompt)
        if response is None:
            return SimpleNamespace(completion_text="")
        return response
```

`make_optimizer` 默认配置新增：`enable_llm_segment=False`、`segment_provider_id=""`、`segment_min_chars=150`、`segment_max_messages=3`、`segment_timeout_seconds=10.0`、`segment_delay_min=0.8`、`segment_delay_max=2.0`；删除 `merge_planning_ttl`、`merge_stop_remark_seconds`。

在 `tests/test_merge_integration.py` 追加：

```python
def test_decoration_segments_long_text_reply():
    optimizer = make_optimizer(
        enable_llm_segment=True,
        segment_provider_id="p1",
        segment_min_chars=10,
        segment_max_messages=3,
    )
    optimizer.context.llm_responses["p1"] = SimpleNamespace(
        completion_text='["第一段内容", "第二段内容"]'
    )
    result = SimpleNamespace(chain=[Plain("第一段内容第二段内容")])
    event = FakeEvent("u1", "group:1", wake=True)
    event.set_result(result)

    asyncio.run(optimizer.on_decorating_result(event))

    assert result.chain[0].text == "第一段内容"
    assert event.get_extra("segment_followups") == ["第二段内容"]


def test_decoration_skips_segment_for_short_or_media_reply():
    optimizer = make_optimizer(
        enable_llm_segment=True,
        segment_provider_id="p1",
        segment_min_chars=100,
    )
    result = SimpleNamespace(chain=[Plain("短回复")])
    event = FakeEvent("u1", "group:1", wake=True)
    event.set_result(result)

    asyncio.run(optimizer.on_decorating_result(event))

    assert result.chain[0].text == "短回复"
    assert event.get_extra("segment_followups") is None


def test_decoration_skips_segment_when_disabled_or_media_chain():
    optimizer = make_optimizer(
        enable_llm_segment=True,
        segment_provider_id="p1",
        segment_min_chars=5,
    )
    media_result = SimpleNamespace(chain=[Plain("带图回复"), Image("file:///x.png")])
    event = FakeEvent("u1", "group:1", wake=True)
    event.set_result(media_result)

    asyncio.run(optimizer.on_decorating_result(event))

    assert [comp.text for comp in media_result.chain if isinstance(comp, Plain)] == [
        "带图回复"
    ]
    assert event.get_extra("segment_followups") is None


def test_after_message_sent_sends_segment_followups():
    optimizer = make_optimizer(segment_delay_min=0.0, segment_delay_max=0.0)
    event = FakeEvent("u1", "group:1", wake=True)
    event.set_extra("segment_followups", ["第二段", "第三段"])
    event.set_result(SimpleNamespace(chain=[Plain("第一段")]))

    async def run():
        await optimizer.after_message_sent(event)
        await asyncio.sleep(0.05)
        return optimizer.context.sent

    sent = asyncio.run(run())

    texts = [chain.chain[0].text for _, chain in sent]
    assert texts == ["第二段", "第三段"]
```

`tests/test_self_reply_marker.py` 追加：

```python
def test_record_sent_text_stores_plain_entry():
    from _astrbot_plugin_filter_test.self_reply_marker import SelfReplyMarker

    marker = SelfReplyMarker(get_config=lambda k, d: d)
    marker.record_sent_text("group:1", "补充段")

    assert marker.recently_sent_duplicate("group:1", "补充段") is True
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_smart_segment.py tests/test_merge_integration.py tests/test_self_reply_marker.py -v`
Expected: FAIL（`_maybe_segment_reply` 为空实现、`record_sent_text` 不存在）

- [ ] **Step 3: 实现**

`main.py` 顶部 import 增加 `import random` 与 `from .smart_segment import split_reply`。

`__init__` 增加：

```python
self._pending_segment_tasks: set[asyncio.Task] = set()
```

替换 Task 3 的空 `_maybe_segment_reply`：

```python
async def _maybe_segment_reply(self, event: AstrMessageEvent) -> None:
    """Split a long plain-text reply via the configured segment provider."""
    if not self._get_config("enable_llm_segment", False):
        return
    result = getattr(event, "get_result", lambda: None)()
    chain = getattr(result, "chain", None) if result is not None else None
    if not isinstance(chain, list) or not chain:
        return
    if not all(isinstance(comp, Plain) for comp in chain):
        return
    text = _result_plain_text(result)
    if not text:
        return
    try:
        segments = await split_reply(text, self.context, self._get_config)
    except Exception:
        logger.warning("[智能分段] 分段失败，按原文发送", exc_info=True)
        return
    if not segments or len(segments) < 2:
        return
    chain[:] = [Plain(segments[0])]
    try:
        event.set_extra("segment_followups", list(segments[1:]))
    except Exception:
        logger.debug("[智能分段] 记录补发段失败", exc_info=True)
```

`after_message_sent` 末尾追加：

```python
await self._send_segment_followups(event)
```

新增方法：

```python
def _send_segment_followups(self, event: AstrMessageEvent) -> None:
    """Queue the remaining segments for delayed follow-up sends."""
    getter = getattr(event, "get_extra", None)
    if not callable(getter):
        return
    try:
        followups = getter("segment_followups")
    except Exception:
        return
    if not followups:
        return
    origin = getattr(event, "unified_msg_origin", None)
    sender = getattr(self.context, "send_message", None)
    if not origin or not callable(sender):
        return
    delay_min = self._get_float_config("segment_delay_min", 0.8)
    delay_max = self._get_float_config("segment_delay_max", 2.0)
    if delay_min < 0.0 or delay_max < delay_min:
        delay_min, delay_max = 0.8, 2.0
    task = asyncio.create_task(
        self._send_segment_task(origin, followups, delay_min, delay_max)
    )
    self._pending_segment_tasks.add(task)
    task.add_done_callback(self._pending_segment_tasks.discard)


async def _send_segment_task(
    self,
    origin: str,
    followups: list[str],
    delay_min: float,
    delay_max: float,
) -> None:
    for index, seg in enumerate(followups, start=2):
        await asyncio.sleep(random.uniform(delay_min, delay_max))
        try:
            chain = MessageChain().message(seg)
            await self.context.send_message(origin, chain)
            logger.info("[智能分段] 已发送第 %d 段", index)
            self._get_self_reply_marker().record_sent_text(origin, seg)
        except Exception:
            logger.warning("[智能分段] 补发第 %d 段失败", index, exc_info=True)
```

`self_reply_marker.py` 新增：

```python
def record_sent_text(self, origin: str, text: str) -> None:
    """Record a single plain-text follow-up segment as a self reply."""
    origin = str(origin or "")
    text = (text or "").strip()
    if not origin or not text:
        return
    self._prune(origin)
    queue = self._entries.setdefault(origin, deque(maxlen=MAX_MARK_ENTRIES))
    queue.append(_SentEntry(timestamp=self._now(), text=text, media=[]))
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_smart_segment.py tests/test_merge_integration.py tests/test_self_reply_marker.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add main.py self_reply_marker.py tests/conftest.py tests/test_merge_integration.py tests/test_self_reply_marker.py tests/test_smart_segment.py
git commit -m "feat: integrate LLM reply segmentation and paced follow-up sends"
```

---

### Task 6: 配置 schema + conftest 模块表 + 测试清理

**Files:**
- Modify: `_conf_schema.json`、`tests/conftest.py`、`tests/test_config_schema.py`
- Delete: `merge_guards.py`、`tests/test_merge_guards.py`

**Interfaces:**
- Consumes: 无外部依赖。
- Produces: 配置键集合与测试断言一致。

- [ ] **Step 1: 写失败测试**

`tests/test_config_schema.py`：

- `VISIBLE_KEYS` 增加：`enable_llm_segment`、`segment_provider_id`、`segment_min_chars`、`segment_max_messages`。
- `REMOVED_KEYS` 增加：`merge_planning_ttl`、`merge_stop_remark_seconds`。
- `test_merge_defaults` 删除 `merge_planning_ttl` 断言，追加：

```python
self.assertEqual(schema["segment_min_chars"]["default"], 150)
self.assertEqual(schema["segment_max_messages"]["default"], 3)
self.assertEqual(schema["segment_timeout_seconds"]["default"], 10.0)
self.assertEqual(schema["segment_delay_min"]["default"], 0.8)
self.assertEqual(schema["segment_delay_max"]["default"], 2.0)
```

新增：

```python
def test_segment_provider_uses_select_provider_special(self):
    schema = self._schema()
    self.assertEqual(schema["segment_provider_id"].get("_special"), "select_provider")
```

`tests/conftest.py` 的 `_import_plugin_as_package` 模块表删除 `"merge_guards"`，新增 `"smart_segment"`。

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_config_schema.py -v`
Expected: FAIL（新键缺失 / 旧键仍存在 / `_special` 缺失）

- [ ] **Step 3: 实现 schema**

`_conf_schema.json`：

- `merge_window_seconds`：`"invisible": false`（删除 `"invisible": true`），hint 改为"窗口期内同用户新消息会重置计时器；运行时限制 1~30 秒，默认 6 秒"。
- 删除 `merge_planning_ttl`、`merge_stop_remark_seconds`。
- 新增可见：

```json
{
  "enable_llm_segment": {
    "description": "启用 LLM 智能分段",
    "type": "bool",
    "hint": "开启后，超过分段最小字数的纯文本回复会调用分段 provider 按语义拆成多条消息逐条发送；未配置 provider 或请求失败时自动回退规则分段。",
    "default": false
  },
  "segment_provider_id": {
    "description": "分段 LLM provider",
    "type": "string",
    "_special": "select_provider",
    "hint": "选择用于智能分段的 LLM provider（在 AstrBot Provider 管理里创建，如 Siliconflow/Qwen/Qwen3.5-4B 或 -9B）；留空则仅使用规则分段。",
    "default": ""
  },
  "segment_min_chars": {
    "description": "分段最小字数",
    "type": "int",
    "hint": "纯文本回复达到该字数才尝试智能分段，短于此直接一条发送。",
    "default": 150
  },
  "segment_max_messages": {
    "description": "最大分段数",
    "type": "int",
    "hint": "回复最多拆成 N 条消息（2~5），超出部分合并到末段。",
    "default": 3
  }
}
```

- 新增隐藏：

```json
{
  "segment_timeout_seconds": {
    "description": "分段请求超时（秒）",
    "type": "float",
    "hint": "分段 LLM 请求的最长等待时间，超时回退规则分段；运行时限制 1~30 秒。",
    "default": 10.0,
    "invisible": true
  },
  "segment_delay_min": {
    "description": "补发消息最小间隔（秒）",
    "type": "float",
    "hint": "分段后的补发消息之间的随机延迟下限。",
    "default": 0.8,
    "invisible": true
  },
  "segment_delay_max": {
    "description": "补发消息最大间隔（秒）",
    "type": "float",
    "hint": "分段后的补发消息之间的随机延迟上限。",
    "default": 2.0,
    "invisible": true
  }
}
```

删除 `merge_guards.py` 与 `tests/test_merge_guards.py`。

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_config_schema.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add _conf_schema.json tests/conftest.py tests/test_config_schema.py
git rm merge_guards.py tests/test_merge_guards.py
git commit -m "chore: config schema for smart segment, drop planning/interrupt keys"
```

---

### Task 7: 元数据、README、全量验证

**Files:**
- Modify: `metadata.yaml`、`README.md`

- [ ] **Step 1: 更新 metadata**

`metadata.yaml`：`version: 3.1.0`；`desc` 改为"滑动合并窗口 + LLM 智能分段：同用户连续消息静默满 6 秒合并为一次回复，在途回复不打断；长回复可调用轻量模型按语义拆条逐发；内置 bot 自回复标记与群聊内容防护。"

- [ ] **Step 2: 更新 README**

要点（按现有文档结构增量修改）：
- 顶部三大能力改为：滑动合并窗口、LLM 智能分段、bot 自回复标记 + 群聊内容防护。
- "主要能力"：窗口期描述改为"同用户任何消息重置计时器，连续静默满 6 秒才发起 LLM"；删除规划期打断/重生成条目；新增智能分段条目（provider 下拉、零改写、校验回退、逐条补发）。
- 配置表：可见配置增加 `merge_window_seconds`、`enable_llm_segment`、`segment_provider_id`、`segment_min_chars`、`segment_max_messages`；隐藏表增加 `segment_timeout_seconds`、`segment_delay_min/max`；删除 `merge_planning_ttl`、`merge_stop_remark_seconds`。
- 工作流程：更新为滑动窗口时序图；删除规划期补充流程。
- 常见问题：新增"为什么回复要等几秒/拆成多条"、"4B 与 9B 怎么切换"（配置两个 SiliconFlow provider 在下拉切换，或改 provider 内模型）；更新"私聊不打断"说明为已知取舍。
- 更新日志：新增 v3.1.0 条目（滑动窗口、移除规划期打断、LLM 智能分段、provider 下拉、配置增删）。
- 已知限制：补充"私聊旧 Agent 卡在超时重试时新消息可能被 follow-up 吞掉（不再打断，如需可后续加开关）"；删除规划期打断相关限制。

- [ ] **Step 3: 全量测试**

Run: `pytest -v`
Expected: 全部 PASS（含 config schema、merge、guard、marker、smart_segment）

- [ ] **Step 4: 快速自检（验收标准核对）**

- `rg -n "planning|supersede|agent_stop_requested|stop_remark|merge_guards" main.py merge_window.py reply_coordinator.py tests` → 无残留（除文档）。
- `rg -n "merge_planning_ttl|merge_stop_remark_seconds" _conf_schema.json tests` → 无残留。

- [ ] **Step 5: 提交**

```bash
git add metadata.yaml README.md
git commit -m "docs: v3.1.0 sliding window and LLM smart segment"
```

---

## Self-Review

**1. Spec coverage：**
- §3 滑动窗口 → Task 1（状态机）+ Task 3（等待循环与引用取消）；
- §2.1 删除清单 → Task 1/2/3/6；
- §4 LLM 分段 → Task 4/5；
- §7 配置 → Task 6；
- §8 测试 → 各 Task 测试步骤；
- §9 文档/版本 → Task 7。

**2. Placeholder scan：** 无 TBD/TODO；Task 3 的 `_maybe_segment_reply` 空实现是 Task 5 的显式前置，非占位。

**3. Type consistency：**
- `quiet_remaining(event, window_seconds, now=None)` 在 Task 1 定义、Task 3 调用，签名一致；
- `split_reply(text, context, get_config) -> list[str] | None` 在 Task 4 定义、Task 5 调用，一致；
- `record_sent_text(origin, text)` 在 Task 5 定义并调用，一致；
- `_handle_window_phase(event, merger) -> str` 在 Task 3 定义并调用，一致。
