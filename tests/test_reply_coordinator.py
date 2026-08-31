import asyncio
from types import SimpleNamespace

from _astrbot_plugin_filter_test.reply_coordinator import ReplyCoordinator


class FakeEvent:
    def __init__(self, sender, origin, *, wake=True):
        self.sender = sender
        self.unified_msg_origin = origin
        self.is_at_or_wake_command = wake
        self.stopped = False
        self._result = None

    def get_sender_id(self):
        return self.sender

    def stop_event(self):
        self.stopped = True

    def is_stopped(self):
        return self.stopped

    def set_result(self, result):
        self._result = result

    def get_result(self):
        return self._result


def make_coordinator():
    return ReplyCoordinator(
        event_is_wake_up=lambda event: getattr(
            event, "is_at_or_wake_command", False
        )
    )


def test_admit_tracks_per_session_and_idempotent():
    coordinator = make_coordinator()
    a = FakeEvent("u1", "group:1")

    assert asyncio.run(coordinator.admit_wakeup(a)) is True
    assert asyncio.run(coordinator.admit_wakeup(a)) is True  # idempotent

    b = FakeEvent("u1", "group:2")
    assert asyncio.run(coordinator.admit_wakeup(b)) is True  # other session parallel


def test_admit_skips_non_wake_events():
    coordinator = make_coordinator()
    event = FakeEvent("u1", "group:1", wake=False)

    assert asyncio.run(coordinator.admit_wakeup(event)) is True
    assert coordinator.active_by_session == {}


def test_session_busy_only_for_other_active_event():
    coordinator = make_coordinator()
    a = FakeEvent("u1", "group:1")
    asyncio.run(coordinator.admit_wakeup(a))
    b = FakeEvent("u1", "group:1")

    assert coordinator.is_session_busy(b) is True
    assert coordinator.is_session_busy(a) is False


def test_finish_active_clears_session():
    coordinator = make_coordinator()
    a = FakeEvent("u1", "group:1")
    asyncio.run(coordinator.admit_wakeup(a))

    assert coordinator.finish_active(a) is True
    assert coordinator.is_session_busy(a) is False


def test_finish_active_noop_for_unknown_event():
    coordinator = make_coordinator()
    a = FakeEvent("u1", "group:1")

    assert coordinator.finish_active(a) is False


def test_admit_skips_stopped_wake_events():
    coordinator = make_coordinator()
    event = FakeEvent("u1", "group:1", wake=True)
    event.stop_event()

    assert asyncio.run(coordinator.admit_wakeup(event)) is False
    assert coordinator.active_by_session == {}
