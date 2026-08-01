# Private Companion Coordination Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the reply gate user-prioritized and optionally coordinate cancellation/stale-output suppression with Private Companion without changing the existing output-filtering pipeline.

**Architecture:** Keep the existing per-origin gate, but only claim it for explicitly marked proactive/synthetic events. A normal inbound event marks the active proactive owner as superseded and continues through AstrBot. The decorating hook drops a superseded proactive result. A small optional adapter discovers `get_private_companion_api()` and calls `cancel_proactive_chat` when a token is available; all adapter failures fail open.

**Tech Stack:** Python 3.10+, asyncio, pytest, AstrBot event objects, optional runtime import.

## Global Constraints

- Do not modify or import Private Companion as a required dependency.
- Never call `stop_event()` for a normal user event merely because the same origin has an active proactive request.
- Preserve existing content guard, Markdown cleanup, segmentation, image rendering, reply locks, gate TTL, and cooldown behavior.
- Optional cancellation must be best-effort and must not delay or block the user event.

### Task 1: Add failing regression tests for user priority and event classification

**Files:**
- Modify: `tests/test_cooldown.py`

**Interfaces:**
- `FakeEvent(..., proactive=False)` exposes the same markers used by production detection.
- Tests will call `LanguageLogicOptimizer._is_proactive_event`, `on_waiting_llm_request`, and `on_decorating_result`.

- [ ] **Step 1: Extend the fake event with an explicit proactive marker.**

  Add a `proactive` argument and set `private_companion_proactive_framework` from it. Keep ordinary fake events unmarked so they model real platform input.

- [ ] **Step 2: Write a failing test proving a real user message is not stopped.**

```python
def test_real_user_message_passes_while_proactive_request_is_in_progress():
    optimizer = make_optimizer()
    proactive = FakeEvent(origin="default:FriendMessage:1", proactive=True)
    user = FakeEvent(origin="default:FriendMessage:1", request_id="user-1")

    async def run():
        await optimizer.on_waiting_llm_request(proactive)
        await optimizer.on_waiting_llm_request(user)

    asyncio.run(run())

    assert not user.stopped
    assert optimizer._gates["default:FriendMessage:1"].superseded_by_user
```

- [ ] **Step 3: Write a failing test proving proactive duplicates remain gated.**

```python
def test_marked_proactive_duplicate_is_stopped():
    optimizer = make_optimizer()
    owner = FakeEvent(origin="group:1", proactive=True, request_id="pro-1")
    duplicate = FakeEvent(origin="group:1", proactive=True, request_id="pro-2")

    async def run():
        await optimizer.on_waiting_llm_request(owner)
        await optimizer.on_waiting_llm_request(duplicate)

    asyncio.run(run())

    assert duplicate.stopped
```

- [ ] **Step 4: Write a failing test proving a superseded proactive result is discarded.**

  Construct a minimal result chain on the proactive owner, send a normal user event through the waiting hook first, then call `on_decorating_result(owner)` and assert the chain is empty and the gate is released.

- [ ] **Step 5: Run the focused tests and confirm they fail for the intended missing behavior.**

Run: `python -m pytest tests/test_cooldown.py -q`

Expected: new user-priority assertions fail because `_GateState` has no supersession state and unmarked events are currently gated.

### Task 2: Implement user-priority gate and optional Private Companion cancellation

**Files:**
- Modify: `main.py:35-85, 344-467` (gate state, hooks, classification, optional adapter)
- Modify: `tests/test_cooldown.py`
- Modify: `_conf_schema.json` only if a new opt-out setting is required; default behavior must remain enabled and fail-open.

**Interfaces:**
- Add `_GateState.superseded_by_user: bool = False`.
- Add `LanguageLogicOptimizer._is_proactive_event(event) -> bool`.
- Add `LanguageLogicOptimizer._mark_proactive_superseded(event) -> None`.
- Add `LanguageLogicOptimizer._schedule_private_companion_cancel(event) -> None`.

- [ ] **Step 1: Implement explicit proactive-event detection.**

  Return true when any supported marker is present: `private_companion_proactive_framework`, `_private_companion_external_proactive_source`, `_private_companion_proactive_chat_token`, `_private_companion_proactive_delivery_umo`, class name `SyntheticPrivateWakeEvent`, or platform metadata description `SyntheticPrivateWake`. Treat unmarked events as ordinary user input.

- [ ] **Step 2: Change gate claiming to bypass ordinary user events.**

  In `_claim_or_stop_wake_up`, return true for non-proactive events. For such an event with an active same-origin proactive state, set `superseded_by_user`, schedule best-effort cancellation, and never call `stop_event()`.

- [ ] **Step 3: Drop stale proactive output at the decorating boundary.**

  At the beginning of `on_decorating_result`, detect an active gate whose owner correlates with this proactive event and has `superseded_by_user`. Replace the result chain with an empty chain, stop the proactive event, and release its reply lock/gate. Do not run normal text transformations on the stale result.

- [ ] **Step 4: Add the optional Private Companion adapter with fail-open behavior.**

  Resolve `get_private_companion_api` through `importlib` from installed module paths. Read the owner event's UMO and `_private_companion_proactive_chat_token`; if both the API method and token exist, create a tracked task that awaits `cancel_proactive_chat(umo, token=token)`. Catch import, attribute, and runtime errors and log at debug/info level without touching the user event.

- [ ] **Step 5: Run the focused regression tests.**

Run: `python -m pytest tests/test_cooldown.py -q`

Expected: all cooldown, TTL, cross-origin, user-priority, stale-output, and API-fallback tests pass.

- [ ] **Step 6: Run the complete test suite.**

Run: `python -m pytest -q`

Expected: exit code 0 with no failures; existing content, security, reply-lock, and single-message tests remain green.

- [ ] **Step 7: Review the diff and verify no required dependency was added.**

Run: `git diff --check` and `git status --short`.

Expected: only the intended plugin source, tests, and design/plan documents are changed; the adapter uses optional runtime discovery.
