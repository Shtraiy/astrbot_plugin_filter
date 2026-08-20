import asyncio
from types import SimpleNamespace

from _astrbot_plugin_filter_test.reply_coordinator import ReplyCoordinator


class FakeEvent:
    def __init__(
        self,
        origin="group:1",
        *,
        request_id=None,
        result=None,
    ):
        self.unified_msg_origin = origin
        self.request_id = request_id
        self.stopped = False
        self._result = result

    def is_wake_up(self):
        return True

    def stop_event(self):
        self.stopped = True

    def get_result(self):
        return self._result


def make_coordinator(now=None):
    return ReplyCoordinator(
        get_gate_seconds=lambda: 0.0,
        get_gate_ttl_seconds=lambda: 300.0,
        get_wakeup_interval=lambda: (0.0, 0.0),
        event_is_wake_up=lambda event: bool(event.is_wake_up()),
        now=now,
    )


def test_user_message_is_queued_without_preempting_active_reply():
    coordinator = make_coordinator()
    active = FakeEvent(request_id="active")
    user = FakeEvent(request_id="user")

    async def scenario():
        assert await coordinator.admit_wakeup(active)
        user_task = asyncio.create_task(coordinator.admit_wakeup(user))
        await asyncio.sleep(0)
        assert not user_task.done()
        assert not user.stopped
        assert coordinator.active_event is active

        assert not coordinator.session_cancelled(
            SimpleNamespace(
                owner_event=active,
                gate_tracked=True,
            )
        )

        coordinator.finish_active(active)
        assert await user_task
        assert coordinator.active_event is user

    asyncio.run(scenario())


def test_late_result_after_ttl_is_discarded():
    now = [1000.0]
    coordinator = make_coordinator(now=lambda: now[0])
    active = FakeEvent(result=SimpleNamespace(chain=["stale"]))

    async def scenario():
        assert await coordinator.admit_wakeup(active)
        now[0] += 301
        assert coordinator.discard_superseded_result(active)

    asyncio.run(scenario())
    assert active.stopped
    assert active.get_result().chain == []


def test_reply_session_releases_lock_and_active_slot():
    coordinator = make_coordinator()
    event = FakeEvent()

    async def scenario():
        assert await coordinator.admit_wakeup(event)
        session = await coordinator.acquire_reply(event)
        assert session.reply_lock.locked()
        coordinator.release(session)
        await asyncio.sleep(0)
        return session.reply_lock.locked(), coordinator.active_event

    locked, active = asyncio.run(scenario())
    assert not locked
    assert active is None


def test_session_is_cancelled_when_active_slot_expires():
    now = [1000.0]
    coordinator = make_coordinator(now=lambda: now[0])
    event = FakeEvent(request_id="active")

    async def scenario():
        assert await coordinator.admit_wakeup(event)
        session = await coordinator.acquire_reply(event)
        now[0] += 301
        return coordinator.session_cancelled(session)

    assert asyncio.run(scenario())


def test_cancelled_event_ids_stay_bounded():
    now = [100.0]
    coordinator = ReplyCoordinator(
        get_gate_seconds=lambda: 0.0,
        get_gate_ttl_seconds=lambda: 1.0,
        get_wakeup_interval=lambda: (0.0, 0.0),
        event_is_wake_up=lambda event: True,
        now=lambda: now[0],
        max_gate_states=4,
    )
    events = [FakeEvent(request_id=f"event-{index}") for index in range(10)]

    async def scenario():
        for event in events:
            assert await coordinator.admit_wakeup(event)
            now[0] += 2.0
            _ = coordinator.active_event  # triggers expiry cleanup
        return len(coordinator._cancelled_event_ids)

    assert asyncio.run(scenario()) <= 4
