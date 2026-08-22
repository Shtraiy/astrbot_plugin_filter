from types import SimpleNamespace

from _astrbot_plugin_filter_test.merge_guards import (
    is_superseded_event,
    stop_if_superseded,
)


def test_stop_if_superseded_stops_and_returns_true():
    event = SimpleNamespace(stopped=False)
    coordinator = SimpleNamespace(is_superseded=lambda e: e is event)
    event.stop_event = lambda: setattr(event, "stopped", True)

    assert stop_if_superseded(coordinator, event) is True
    assert event.stopped is True


def test_stop_if_superseded_noop_for_normal_events():
    event = SimpleNamespace(stopped=False)
    coordinator = SimpleNamespace(is_superseded=lambda e: False)

    assert stop_if_superseded(coordinator, event) is False
    assert event.stopped is False


def test_is_superseded_event_defensive():
    event = SimpleNamespace()

    assert is_superseded_event(None, event) is False
    assert is_superseded_event(SimpleNamespace(is_superseded=None), event) is False
