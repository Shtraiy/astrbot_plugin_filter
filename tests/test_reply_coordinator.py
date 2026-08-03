import asyncio
from types import SimpleNamespace

from _astrbot_plugin_filter_test.reply_coordinator import ReplyCoordinator


class FakeEvent:
    def __init__(
        self,
        origin="group:1",
        *,
        proactive=True,
        request_id=None,
        attempt_id=None,
        result=None,
    ):
        self.unified_msg_origin = origin
        self.private_companion_proactive_framework = proactive
        self.request_id = request_id
        self._private_companion_proactive_chat_attempt_id = attempt_id
        self.stopped = False
        self._result = result

    def is_wake_up(self):
        return True

    def stop_event(self):
        self.stopped = True

    def get_result(self):
        return self._result


def make_coordinator(scheduled=None, gates=None, reply_locks=None):
    return ReplyCoordinator(
        get_gate_seconds=lambda: 0.0,
        get_gate_ttl_seconds=lambda: 300.0,
        event_is_wake_up=lambda event: bool(event.is_wake_up()),
        is_proactive_event=lambda event: bool(
            getattr(event, "private_companion_proactive_framework", False)
        ),
        schedule_cancel=(scheduled or (lambda _event: None)),
        gates=gates,
        reply_locks=reply_locks,
    )


def test_user_priority_supersedes_proactive_session_once():
    scheduled = []
    coordinator = make_coordinator(scheduled.append)
    proactive = FakeEvent(request_id="pro-1")
    user = FakeEvent(proactive=False, request_id="user-1")

    assert coordinator.claim_wakeup(proactive)
    assert coordinator.mark_user_priority(user)
    assert coordinator.mark_user_priority(user)
    assert scheduled == [proactive]
    assert coordinator.gates["group:1"].superseded_by_user


def test_superseded_result_is_cleared_and_gate_released():
    coordinator = make_coordinator()
    proactive = FakeEvent(request_id="pro-1")
    user = FakeEvent(proactive=False, request_id="user-1")
    proactive._result = SimpleNamespace(chain=["stale"])

    coordinator.claim_wakeup(proactive)
    coordinator.mark_user_priority(user)

    assert coordinator.discard_superseded_result(proactive)
    assert proactive._result.chain == []
    assert proactive.stopped
    assert not coordinator.gates


def test_reply_session_releases_lock_and_gate():
    coordinator = make_coordinator()
    event = FakeEvent()

    async def scenario():
        assert coordinator.claim_wakeup(event)
        session = await coordinator.acquire_reply(event)
        assert session.reply_lock.locked()
        coordinator.release(session)
        return session.reply_lock.locked()

    assert not asyncio.run(scenario())


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


def test_session_cancelled_when_tracked_gate_expires():
    coordinator = make_coordinator()
    event = FakeEvent(request_id="pro-1")

    async def scenario():
        coordinator.claim_wakeup(event)
        session = await coordinator.acquire_reply(event)
        coordinator.gates[event.unified_msg_origin].created_at = (
            coordinator._now() - 301
        )
        return coordinator.session_cancelled(session)

    assert asyncio.run(scenario())


def test_new_private_companion_attempt_takes_over_stuck_proactive_session():
    scheduled = []
    coordinator = make_coordinator(scheduled.append)
    old_result = SimpleNamespace(chain=["stale"])
    owner = FakeEvent(attempt_id="attempt-1", result=old_result)
    incoming = FakeEvent(attempt_id="attempt-2")

    assert coordinator.claim_wakeup(owner)
    assert coordinator.claim_wakeup(incoming)
    assert not incoming.stopped
    assert scheduled == [owner]
    assert coordinator.gates["group:1"].owner_event is incoming


def test_late_result_from_taken_over_attempt_is_discarded_without_releasing_new_gate():
    coordinator = make_coordinator()
    owner = FakeEvent(
        attempt_id="attempt-1",
        result=SimpleNamespace(chain=["stale"]),
    )
    incoming = FakeEvent(attempt_id="attempt-2")

    coordinator.claim_wakeup(owner)
    coordinator.claim_wakeup(incoming)

    assert coordinator.discard_superseded_result(owner)
    assert owner.get_result().chain == []
    assert coordinator.gates["group:1"].owner_event is incoming
