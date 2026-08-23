from types import SimpleNamespace

from _astrbot_plugin_filter_test.merge_guards import (
    is_correction_follow_up,
    is_superseded_event,
    should_interrupt_running_reply,
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


def test_is_correction_follow_up_matches_terms():
    assert is_correction_follow_up("再想想") is True
    assert is_correction_follow_up("不对，重新规划") is True
    assert is_correction_follow_up("等一下，我换个说法") is True


def test_is_correction_follow_up_strips_mention_prefix():
    assert is_correction_follow_up("@bot 再想想") is True
    assert is_correction_follow_up("@bot 换一个方案") is True


def test_is_correction_follow_up_ignores_negation_and_normal_supplement():
    assert is_correction_follow_up("不用再想想了，就按这个来") is False
    assert is_correction_follow_up("补充一句") is False
    assert is_correction_follow_up("") is False


def test_should_interrupt_running_reply_truth_table():
    # provider 未开始 -> 打断；provider 已开始 -> 悬挂；修正词 -> 一律打断
    assert should_interrupt_running_reply(False, False) is True
    assert should_interrupt_running_reply(True, False) is False
    assert should_interrupt_running_reply(True, True) is True
    assert should_interrupt_running_reply(False, True) is True
