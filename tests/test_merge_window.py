from types import SimpleNamespace

from astrbot.api.message_components import File, Image, Plain

from _astrbot_plugin_filter_test.merge_window import MergeWindowManager


class FakeEvent:
    def __init__(self, sender, origin, text="", *, wake=True, chain=None):
        self.sender = sender
        self.unified_msg_origin = origin
        self.message_str = text
        self._wake = wake
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
        get_config=lambda key, default: config.get(key, default)
    )


def test_capture_appends_same_user_text_during_window():
    manager = make_manager()
    owner = FakeEvent("u1", "group:1", "第一段")

    assert manager.start_window(owner)
    assert manager.capture(FakeEvent("u1", "group:1", "第二段"))

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


def test_capture_respects_ignore_prefixes():
    manager = make_manager(merge_ignore_prefixes="/,!")
    owner = FakeEvent("u1", "group:1", "第一段")
    manager.start_window(owner)

    assert not manager.capture(FakeEvent("u1", "group:1", "/help"))
    assert not manager.capture(FakeEvent("u1", "group:1", "!ping"))
    assert manager.capture(FakeEvent("u1", "group:1", "普通消息"))


def test_capture_respects_message_cap():
    manager = make_manager(merge_max_messages=2)
    owner = FakeEvent("u1", "group:1", "a")
    manager.start_window(owner)

    assert manager.capture(FakeEvent("u1", "group:1", "b"))
    assert manager.capture(FakeEvent("u1", "group:1", "c"))
    assert not manager.capture(FakeEvent("u1", "group:1", "d"))
    assert manager.finalize_window(owner) == "a\nb\nc"


def test_capture_respects_char_cap():
    manager = make_manager(merge_max_chars=10)
    owner = FakeEvent("u1", "group:1", "abcdef")
    manager.start_window(owner)

    assert not manager.capture(FakeEvent("u1", "group:1", "ghijkl"))
    assert manager.finalize_window(owner) == "abcdef"


def test_capture_strips_leading_mention():
    manager = make_manager()
    owner = FakeEvent("u1", "group:1", "可以")
    manager.start_window(owner)

    manager.capture(FakeEvent("u1", "group:1", "@bot 我觉得可爱"))

    assert manager.finalize_window(owner) == "可以\n我觉得可爱"


def test_finalize_moves_to_planning_and_take_consumes():
    manager = make_manager()
    owner = FakeEvent("u1", "group:1", "第一段")
    manager.start_window(owner)
    manager.finalize_window(owner)

    follow = FakeEvent("u1", "group:1", "第二段")
    result = manager.take_planning(follow)

    assert result is not None
    old_event, text, _media, _task = result
    assert old_event is owner
    assert text == "第一段"
    assert manager.take_planning(follow) is None


def test_take_planning_skips_stopped_owner():
    manager = make_manager()
    owner = FakeEvent("u1", "group:1", "第一段")
    manager.start_window(owner)
    manager.finalize_window(owner)
    owner.stopped = True

    follow = FakeEvent("u1", "group:1", "第二段")

    assert manager.take_planning(follow) is None


def test_start_window_skips_events_without_sender():
    manager = make_manager()

    assert not manager.start_window(FakeEvent("", "group:1", "a"))


def test_join_text_strips_leading_mention():
    assert MergeWindowManager.join_text("第一段", "@bot 第二段") == "第一段\n第二段"
    assert MergeWindowManager.join_text("", "@bot 第二段") == "第二段"


def test_capture_merges_image_followup():
    manager = make_manager()
    owner = FakeEvent("u1", "group:1", "看看这张图")
    manager.start_window(owner)
    img = Image("http://example.com/a.png")

    assert manager.capture(FakeEvent("u1", "group:1", chain=[img]))
    assert manager.finalize_window(owner) == "看看这张图"
    assert owner.message_obj.message[-1] is img


def test_capture_merges_text_with_image():
    manager = make_manager()
    owner = FakeEvent("u1", "group:1", "第一段")
    manager.start_window(owner)
    img = Image("http://example.com/a.png")

    assert manager.capture(
        FakeEvent("u1", "group:1", chain=[Plain("配图"), img])
    )
    assert manager.finalize_window(owner) == "第一段\n配图"
    assert owner.message_obj.message[-1] is img


def test_capture_merges_file_followup():
    manager = make_manager()
    owner = FakeEvent("u1", "group:1", "看文件")
    manager.start_window(owner)
    file_comp = File("doc.pdf")

    assert manager.capture(FakeEvent("u1", "group:1", chain=[file_comp]))
    assert manager.finalize_window(owner) == "看文件"
    assert owner.message_obj.message[-1] is file_comp


def test_capture_skips_media_when_disabled():
    manager = make_manager(merge_include_media=False)
    owner = FakeEvent("u1", "group:1", "第一段")
    manager.start_window(owner)

    assert not manager.capture(
        FakeEvent("u1", "group:1", chain=[Image("http://example.com/a.png")])
    )
    assert manager.finalize_window(owner) == "第一段"


def test_capture_skips_quoted_media():
    manager = make_manager()
    owner = FakeEvent("u1", "group:1", "第一段")
    manager.start_window(owner)

    assert not manager.capture(
        FakeEvent("u1", "group:1", chain=[SimpleNamespace(type="Reply")])
    )
    assert manager.finalize_window(owner) == "第一段"


def test_take_planning_carries_media():
    manager = make_manager()
    owner = FakeEvent("u1", "group:1", "第一段")
    manager.start_window(owner)
    img = Image("http://example.com/a.png")
    manager.capture(FakeEvent("u1", "group:1", chain=[img]))
    manager.finalize_window(owner)

    result = manager.take_planning(FakeEvent("u1", "group:1", "第二段"))

    assert result is not None
    _old_event, text, media, _task = result
    assert text == "第一段"
    assert media == [img]
