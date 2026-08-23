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


def test_supersede_marks_cancelled_stops_and_clears():
    coordinator = make_coordinator()
    a = FakeEvent("u1", "group:1")
    asyncio.run(coordinator.admit_wakeup(a))

    assert coordinator.supersede_active_event(a) is True
    assert coordinator.is_superseded(a)
    assert a.stopped is True
    assert coordinator.is_session_busy(a) is False


def test_supersede_rejects_unrelated_event():
    coordinator = make_coordinator()
    a = FakeEvent("u1", "group:1")
    b = FakeEvent("u1", "group:1")
    asyncio.run(coordinator.admit_wakeup(a))

    assert coordinator.supersede_active_event(b) is False
    assert not coordinator.is_superseded(b)


def test_session_busy_only_for_other_active_event():
    coordinator = make_coordinator()
    a = FakeEvent("u1", "group:1")
    asyncio.run(coordinator.admit_wakeup(a))
    b = FakeEvent("u1", "group:1")

    assert coordinator.is_session_busy(b) is True
    assert coordinator.is_session_busy(a) is False


def test_active_event_for_three_states():
    coordinator = make_coordinator()
    a = FakeEvent("u1", "group:1")
    b = FakeEvent("u1", "group:1")

    # 无活跃事件
    assert coordinator.active_event_for(a) is None

    asyncio.run(coordinator.admit_wakeup(a))
    # 活跃事件是自己
    assert coordinator.active_event_for(a) is None
    # 活跃事件是同会话的另一个事件
    assert coordinator.active_event_for(b) is a

    other_session = FakeEvent("u1", "group:2")
    assert coordinator.active_event_for(other_session) is None


def test_active_event_for_ignores_other_sender():
    coordinator = make_coordinator()
    a = FakeEvent("u1", "group:1")
    asyncio.run(coordinator.admit_wakeup(a))
    other = FakeEvent("u2", "group:1")

    assert coordinator.active_event_for(other) is None


def test_active_same_sender_distinguishes_users():
    coordinator = make_coordinator()
    a = FakeEvent("u1", "group:1")
    asyncio.run(coordinator.admit_wakeup(a))

    same_user = FakeEvent("u1", "group:1")
    other_user = FakeEvent("u2", "group:1")

    assert coordinator.active_same_sender(same_user) is True
    assert coordinator.active_same_sender(other_user) is False
    assert coordinator.active_same_sender(a) is False  # 自身不算


def test_active_same_sender_false_without_active():
    coordinator = make_coordinator()
    event = FakeEvent("u1", "group:1")

    assert coordinator.active_same_sender(event) is False


def test_discard_superseded_result_clears_chain():
    coordinator = make_coordinator()
    a = FakeEvent("u1", "group:1")
    asyncio.run(coordinator.admit_wakeup(a))
    coordinator.supersede_active_event(a)
    a.set_result(SimpleNamespace(chain=[object()]))

    assert coordinator.discard_superseded_result(a) is True
    assert a.get_result().chain == []


def test_discard_superseded_result_noop_for_normal_event():
    coordinator = make_coordinator()
    a = FakeEvent("u1", "group:1")
    asyncio.run(coordinator.admit_wakeup(a))

    assert coordinator.discard_superseded_result(a) is False


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
