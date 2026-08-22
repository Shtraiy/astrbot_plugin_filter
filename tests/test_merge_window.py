from types import SimpleNamespace

from astrbot.api.message_components import File, Image, Plain

from _astrbot_plugin_filter_test.merge_window import MergeWindowManager


class FakeEvent:
    def __init__(self, sender, origin, text="", *, wake=False, chain=None):
        self.sender = sender
        self.unified_msg_origin = origin
        self.message_str = text
        self._wake = wake
        self.is_at_or_wake_command = wake
        self._chain = chain if chain is not None else ([Plain(text)] if text else [])
        self.message_obj = SimpleNamespace(message=self._chain)
        self.stopped = False

    def get_sender_id(self):
        return self.sender

    def get_messages(self):
        return self._chain

    def is_wake_up(self):
        return self._wake

    def stop_event(self):
        self.stopped = True


def make_manager(**config):
    return MergeWindowManager(
        get_config=lambda key, default: config.get(key, default),
        now=config.get("now"),
    )


def test_capture_only_during_window_phase():
    manager = make_manager()
    owner = FakeEvent("u1", "group:1", "第一段", wake=True)

    assert manager.start_window(owner)
    assert manager.capture(FakeEvent("u1", "group:1", "第二段", wake=False))

    assert manager.finalize_window(owner) == "第一段\n第二段"


def test_capture_requires_open_window():
    manager = make_manager()
    event = FakeEvent("u1", "group:1", "消息")

    assert not manager.capture(event)


def test_capture_ignores_owner_event():
    manager = make_manager()
    owner = FakeEvent("u1", "group:1", "第一段")
    manager.start_window(owner)

    assert not manager.capture(owner)


def test_capture_ignores_other_users():
    manager = make_manager()
    owner = FakeEvent("u1", "group:1", "第一段")
    manager.start_window(owner)

    assert not manager.capture(FakeEvent("u2", "group:1", "别人的消息"))
    assert manager.finalize_window(owner) == "第一段"


def test_capture_ignores_messages_from_other_sessions():
    manager = make_manager()
    owner = FakeEvent("u1", "group:1", "第一段")
    manager.start_window(owner)

    assert not manager.capture(FakeEvent("u1", "group:2", "其他会话"))
    assert manager.finalize_window(owner) == "第一段"


def test_capture_skips_non_plain_messages():
    manager = make_manager()
    owner = FakeEvent("u1", "group:1", "第一段")
    manager.start_window(owner)
    mixed = FakeEvent(
        "u1",
        "group:1",
        "带图",
        chain=[Plain("文字"), SimpleNamespace(type="Image")],
    )

    assert not manager.capture(mixed)
    assert manager.finalize_window(owner) == "第一段"


def test_capture_skips_wake_followups():
    manager = make_manager()
    owner = FakeEvent("u1", "group:1", "第一段", wake=True)
    manager.start_window(owner)
    follow = FakeEvent("u1", "group:1", "@bot 第二段", wake=True)
    follow.is_at_or_wake_command = True

    assert not manager.capture(follow)


def test_capture_ignores_ignore_prefix():
    manager = make_manager(merge_ignore_prefixes="/,!")
    owner = FakeEvent("u1", "group:1", "第一段")
    manager.start_window(owner)

    assert not manager.capture(FakeEvent("u1", "group:1", "/命令"))
    assert not manager.capture(FakeEvent("u1", "group:1", "!命令"))
    assert manager.capture(FakeEvent("u1", "group:1", "正常补充"))


def test_capture_enforces_message_limit():
    manager = make_manager(merge_max_messages=1)
    owner = FakeEvent("u1", "group:1", "第一段")
    manager.start_window(owner)

    assert manager.capture(FakeEvent("u1", "group:1", "第二段"))
    assert not manager.capture(FakeEvent("u1", "group:1", "第三段"))


def test_capture_enforces_char_limit():
    manager = make_manager(merge_max_chars=10)
    owner = FakeEvent("u1", "group:1", "第一段")
    manager.start_window(owner)

    assert not manager.capture(FakeEvent("u1", "group:1", "这段文字超过十个字肯定"))


def test_merge_wake_appends_wake_followup_during_window():
    manager = make_manager()
    owner = FakeEvent("u1", "group:1", "第一段", wake=True)
    manager.start_window(owner)
    follow = FakeEvent("u1", "group:1", "@bot 第二段", wake=True)

    assert manager.merge_wake(follow)
    assert manager.finalize_window(owner) == "第一段\n第二段"


def test_promote_planning_enables_group_followup():
    manager = make_manager()
    owner = FakeEvent("u1", "group:1", "第一段", wake=True)
    manager.start_window(owner)
    manager.finalize_window(owner)
    follow = FakeEvent("u1", "group:1", "补充", wake=False)

    assert manager.promote_planning(follow)
    assert follow.is_at_or_wake_command is True


def test_promote_planning_rejects_wake_and_other_users():
    manager = make_manager()
    owner = FakeEvent("u1", "group:1", "第一段", wake=True)
    manager.start_window(owner)
    manager.finalize_window(owner)
    wake_follow = FakeEvent("u1", "group:1", "@bot 补充", wake=True)
    wake_follow.is_at_or_wake_command = True

    assert not manager.promote_planning(wake_follow)
    assert not manager.promote_planning(FakeEvent("u2", "group:1", "别人的", wake=False))


def test_promote_planning_requires_planning_phase():
    manager = make_manager()
    owner = FakeEvent("u1", "group:1", "第一段", wake=True)
    manager.start_window(owner)

    assert not manager.promote_planning(FakeEvent("u1", "group:1", "窗口期", wake=False))


def test_take_planning_returns_accumulated_text_and_rearm_supports_recursion():
    manager = make_manager()
    owner = FakeEvent("u1", "group:1", "第一段", wake=True)
    manager.start_window(owner)
    manager.finalize_window(owner)
    follow = FakeEvent("u1", "group:1", "补充", wake=False)

    old, text, media, task = manager.take_planning(follow)
    assert old is owner
    assert text == "第一段"
    assert media == []
    assert task is None

    merged = manager.join_text(text, follow.message_str)
    assert merged == "第一段\n补充"
    assert manager.rearm_planning(follow, merged)

    old2, text2, _, _ = manager.take_planning(follow)
    assert old2 is follow
    assert text2 == "第一段\n补充"


def test_take_planning_requires_planning_phase():
    manager = make_manager()
    owner = FakeEvent("u1", "group:1", "第一段", wake=True)

    assert manager.start_window(owner)
    assert manager.take_planning(FakeEvent("u1", "group:1", "窗口期")) is None


def test_media_merge_attaches_to_owner():
    manager = make_manager(merge_include_media=True)
    owner = FakeEvent("u1", "group:1", "第一段", wake=True)
    manager.start_window(owner)
    image = Image("file:///a.png")

    assert manager.capture(
        FakeEvent("u1", "group:1", "看图", chain=[Plain("看图"), image])
    )
    merged = manager.finalize_window(owner)

    assert merged == "第一段\n看图"
    assert image in owner.message_obj.message


def test_clear_owner_drops_state():
    manager = make_manager()
    owner = FakeEvent("u1", "group:1", "第一段", wake=True)
    manager.start_window(owner)
    manager.finalize_window(owner)

    manager.clear_owner(owner)
    assert not manager.promote_planning(FakeEvent("u1", "group:1", "补充", wake=False))


def test_join_text_strips_leading_mention():
    manager = make_manager()

    assert manager.join_text("第一段", "@bot 第二段") == "第一段\n第二段"
    assert manager.join_text("第一段", "第二段") == "第一段\n第二段"
    assert manager.join_text("", "第二段") == "第二段"


def test_has_media_detects_image_components():
    manager = make_manager()

    assert manager.has_media(FakeEvent("u1", "group:1", "文字")) is False
    assert (
        manager.has_media(
            FakeEvent("u1", "group:1", chain=[Image("file:///a.png")])
        )
        is True
    )
    assert (
        manager.has_media(
            FakeEvent("u1", "group:1", chain=[Plain("文字"), File("a.xlsx")])
        )
        is True
    )
