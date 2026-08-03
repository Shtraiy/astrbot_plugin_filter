# Reply Stability Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make delayed replies cancelable, apply the same outbound safety pipeline to every paragraph, and ensure one reply session is owned by one follow-up task.

**Architecture:** `ReplyCoordinator` remains the source of truth for gate/session invalidation. `MessageDispatcher` asks the coordinator for current cancellation state before each delayed send. `main.py` processes all Plain components first, collects follow-up paragraphs, then schedules one dispatcher task with a reusable pipeline callback.

**Tech Stack:** Python 3.10+, asyncio, pytest, existing AstrBot test stubs.

## Global Constraints

- Preserve existing cleanup functions, sensitive-content rules, Markdown handling, segmentation behavior, and configuration meanings.
- Add no third-party runtime dependency.
- Keep Private Companion optional; cancellation failure must never block ordinary replies.
- Every behavior change must have a regression test written and observed failing before implementation.

---

### Task 1: Make follow-up cancellation state-derived

**Files:**
- Modify: `reply_coordinator.py`
- Modify: `message_dispatcher.py`
- Test: `tests/test_reply_coordinator.py`
- Test: `tests/test_message_dispatcher.py`

**Interfaces:**
- Consumes: `ReplySession`, `GateState`, and the existing `mark_user_priority()` transition.
- Produces: `ReplyCoordinator.session_cancelled(session: ReplySession) -> bool`.

- [ ] **Step 1: Write the failing coordinator test**

Add a test that acquires a proactive session, marks a same-origin user event as priority, and asserts the live session is cancelled:

```python
def test_session_cancelled_reflects_user_priority_after_acquire():
    coordinator = make_coordinator()
    proactive = FakeEvent(request_id="pro-1")
    user = FakeEvent(proactive=False, request_id="user-1")

    async def scenario():
        coordinator.claim_wakeup(proactive)
        session = await coordinator.acquire_reply(proactive)
        coordinator.mark_user_priority(user)
        return coordinator.session_cancelled(session)

    assert asyncio.run(scenario())
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `\.venv\Scripts\python.exe -m pytest -q tests/test_reply_coordinator.py::test_session_cancelled_reflects_user_priority_after_acquire`

Expected: FAIL because `ReplyCoordinator` has no live session cancellation query.

- [ ] **Step 3: Implement the minimal coordinator query**

Add `session_cancelled()` to `ReplyCoordinator`. It returns `True` when the session’s gate is absent due to invalidation, or when the current gate state correlates to the session owner and has `superseded_by_user` or `cancel_requested`. It must not treat a newer proactive attempt owning the same origin as the old session’s active owner.

- [ ] **Step 4: Update dispatcher checks and add its failing behavior test**

Add a dispatcher test that marks the owner session superseded from a `process_text` callback and asserts no later paragraph is sent:

```python
def test_dispatcher_stops_when_session_is_superseded(monkeypatch):
    context = FakeContext()
    coordinator = make_coordinator()
    owner = FakeEvent()
    user = FakeEvent()
    coordinator.claim_wakeup(owner)

    async def no_delay(_seconds):
        return None

    monkeypatch.setattr("_astrbot_plugin_filter_test.message_dispatcher.asyncio.sleep", no_delay)

    async def scenario():
        session = await coordinator.acquire_reply(owner)
        dispatcher = MessageDispatcher(context, coordinator)
        calls = 0

        async def process(text):
            nonlocal calls
            calls += 1
            if calls == 1:
                coordinator.mark_user_priority(user)
            return text

        await dispatcher.send_followups(
            owner.unified_msg_origin,
            ["part-0", "part-1"],
            policy=DispatchPolicy(0, 0),
            session=session,
            process_text=process,
        )

    asyncio.run(scenario())
    assert [text for _, text in context.sent] == ["part-0"]
```

- [ ] **Step 5: Run the dispatcher test to verify the pre-fix failure**

Run: `\.venv\Scripts\python.exe -m pytest -q tests/test_message_dispatcher.py::test_dispatcher_stops_when_session_is_superseded`

Expected: FAIL because the dispatcher only reads the stale boolean fields copied into `ReplySession`.

- [ ] **Step 6: Make dispatcher cancellation checks use the coordinator**

Replace the direct boolean checks at the pre-sleep, post-sleep, and pre-processing points with `self.coordinator.session_cancelled(session)`. Keep the existing `finally` release and per-send exception handling.

- [ ] **Step 7: Run focused tests**

Run: `\.venv\Scripts\python.exe -m pytest -q tests/test_reply_coordinator.py tests/test_message_dispatcher.py`

Expected: all focused tests pass.

### Task 2: Reuse the outbound pipeline and serialize follow-ups

**Files:**
- Modify: `main.py:167-257,277-289`
- Modify: `message_dispatcher.py` only if Task 1’s callback boundary needs a type-safe adjustment.
- Test: `tests/test_single_message.py`
- Test: `tests/test_reply_lock_unittest.py`

**Interfaces:**
- Consumes: `OutboundTextPipeline.process()`, `prepare_multi_message_parts()`, and `MessageDispatcher.send_followups(..., process_text=...)`.
- Produces: one follow-up task per decorating result and a reusable async callback that returns processed text or `None`.

- [ ] **Step 1: Add a failing follow-up safety test**

Extend the fake context in `tests/test_single_message.py` to retain sent text and add a test where the first paragraph stays in the result and the follow-up contains a sensitive password/tool trace. Assert the follow-up sent text contains neither the secret nor the tool protocol marker.

- [ ] **Step 2: Run the safety test and verify it fails**

Run: `\.venv\Scripts\python.exe -m pytest -q tests/test_single_message.py::test_followups_reuse_outbound_pipeline`

Expected: FAIL because `main.py` currently calls the dispatcher without `process_text`.

- [ ] **Step 3: Add a failing single-task ownership test**

Create a result chain with two non-adjacent Plain components, each containing multiple paragraphs. Patch `MessageDispatcher.send_followups` to record calls and hold until released. Assert the callback is invoked once with all follow-up paragraphs and the reply lock stays held until that single task completes.

- [ ] **Step 4: Run the ownership test and verify it fails**

Run: `\.venv\Scripts\python.exe -m pytest -q tests/test_reply_lock_unittest.py::test_multiple_plain_components_share_one_followup_task`

Expected: FAIL because the current loop creates one task per Plain component.

- [ ] **Step 5: Refactor `on_decorating_result` minimally**

During the existing Plain-component pass, keep each component’s first paragraph in place and append every remaining paragraph to one local `followup_paragraphs` list. Build one async callback around the same `OutboundTextPipeline`; merge its statistics into the existing pipeline stats and return `None` for empty processed text. After the pass, create at most one dispatcher task with all collected follow-ups and pass `process_text` to it.

- [ ] **Step 6: Preserve existing direct-send and lock branches**

Keep image rendering and pending-send registration behavior unchanged. Only set `lock_owned = False` after the single follow-up task is successfully registered. If no follow-ups exist, retain the existing direct-send/pending-send/empty-result release decisions.

- [ ] **Step 7: Run focused integration tests**

Run: `\.venv\Scripts\python.exe -m pytest -q tests/test_single_message.py tests/test_reply_lock_unittest.py tests/test_outbound_pipeline.py`

Expected: all focused tests pass, including existing lock and Markdown regressions.

### Task 3: Remove dead duplicate lifecycle implementation

**Files:**
- Modify: `main.py:349-558`
- Test: `tests/test_cooldown.py`
- Test: `tests/test_reply_coordinator.py`

**Interfaces:**
- Consumes: existing compatibility shims at `main.py:562-602`.
- Produces: one authoritative implementation for each gate, cancellation, correlation, and reply-release helper.

- [ ] **Step 1: Record the compatibility surface before deletion**

Confirm the remaining helpers `_mark_proactive_gate_superseded`, `_discard_superseded_proactive_result`, `_claim_or_stop_wake_up`, `_gate_is_active`, `_release_gate`, `_events_correlate`, `_finish_reply`, `_is_proactive_event`, `_schedule_private_companion_cancel`, and `_cancel_private_companion_proactive` all delegate to the extracted modules.

- [ ] **Step 2: Delete only the shadowed old implementations and obsolete imports**

Remove the first definitions of the lifecycle helpers and the unused `importlib` import. Do not remove the compatibility wrappers or change their signatures.

- [ ] **Step 3: Run lifecycle regressions**

Run: `\.venv\Scripts\python.exe -m pytest -q tests/test_cooldown.py tests/test_reply_coordinator.py tests/test_private_companion_adapter.py`

Expected: all lifecycle and optional-integration tests pass.

### Task 4: Full verification

**Files:**
- Inspect: all modified files and `git diff`.

- [ ] **Step 1: Run the full test suite**

Run: `\.venv\Scripts\python.exe -m pytest -q`

Expected: zero failures.

- [ ] **Step 2: Compile all runtime modules**

Run: `\.venv\Scripts\python.exe -m py_compile main.py content_guard.py pipelines.py segmentation.py image_renderer.py outbound_pipeline.py reply_coordinator.py message_dispatcher.py private_companion_adapter.py`

Expected: exit code 0.

- [ ] **Step 3: Check patch formatting and inspect the final diff**

Run: `git diff --check` and `git diff --stat`.

Expected: no whitespace errors; only the planned source, tests, and plan files are changed.
