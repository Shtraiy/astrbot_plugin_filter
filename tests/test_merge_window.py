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
        self.message_id = f"mid-{id(self)}"
        self.message_obj = SimpleNamespace(
            message=self._chain,
            message_id=self.message_id,
        )
        self.stopped = False
        self._extras = {}

    def get_sender_id(self):
        return self.sender

    def get_messages(self):
        return self._chain

    def is_wake_up(self):
        return self._wake

    def stop_event(self):
        self.stopped = True

    def set_extra(self, key, value):
        self._extras[key] = value

    def get_extra(self, key, default=None):
        return self._extras.get(key, default)


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

    assert manager.finalize_window(owner) == manager.format_segments(
        ["第一段", "第二段"]
    )


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
    assert manager.finalize_window(owner) == manager.format_segments(
        ["第一段", "第二段"]
    )


def test_media_merge_attaches_to_owner():
    manager = make_manager(merge_include_media=True)
    owner = FakeEvent("u1", "group:1", "第一段", wake=True)
    manager.start_window(owner)
    image = Image("file:///a.png")

    assert manager.capture(
        FakeEvent("u1", "group:1", "看图", chain=[Plain("看图"), image])
    )
    merged = manager.finalize_window(owner)

    assert merged == manager.format_segments(["第一段", "看图"])
    assert image in owner.message_obj.message


def test_clear_state_drops_window_state():
    manager = make_manager()
    owner = FakeEvent("u1", "group:1", "第一段", wake=True)
    manager.start_window(owner)

    manager.clear_state(owner)
    assert manager.quiet_remaining(owner, 6.0) == 0.0
    assert manager.is_window_open(FakeEvent("u1", "group:1", "x", wake=True)) is False


def test_finalize_records_last_captured_message_id():
    manager = make_manager()
    first = FakeEvent("u1", "group:1", "第一段", wake=True)
    second = FakeEvent("u1", "group:1", "补充", wake=False)
    third = FakeEvent("u1", "group:1", "再补充", wake=False)
    manager.start_window(first)
    manager.capture(second)
    manager.capture(third)

    manager.finalize_window(first)

    assert first.get_extra("merge_last_message_id") == third.message_id


def test_join_text_strips_leading_mention():
    manager = make_manager()

    assert manager.join_text("第一段", "@bot 第二段") == "第一段\n第二段"
    assert manager.join_text("第一段", "第二段") == "第一段\n第二段"
    assert manager.join_text("", "第二段") == "第二段"


def test_format_segments_single_message_stays_raw():
    manager = make_manager()

    assert manager.format_segments(["第一段"]) == "第一段"
    assert manager.format_segments([]) == ""
    assert manager.format_segments(["", "  "]) == ""


def test_format_segments_numbers_multiple_messages():
    manager = make_manager()

    assert manager.format_segments(["唉", "看来你是真的没有了"]) == (
        "用户消息1：唉\n"
        "用户消息2：看来你是真的没有了\n"
        "（以上是用户在同一次唤醒中连续发送的 2 条消息，"
        "请整体回应，不要遗漏任何一条。）"
    )
    assert manager.format_segments(["第一段", "第二段", "第三句"]).startswith(
        "用户消息1：第一段\n用户消息2：第二段\n用户消息3：第三句"
    )


def test_append_segment_strips_mention_and_keeps_existing():
    manager = make_manager()

    assert manager.append_segment(["第一段"], "@bot 第二段") == [
        "第一段",
        "第二段",
    ]
    assert manager.append_segment(["第一段"], "  ") == ["第一段"]
    assert manager.append_segment([], "补充") == ["补充"]


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


def test_message_has_quote_detects_reply_components():
    manager = make_manager()
    quote = SimpleNamespace(type="Reply", chain=[Plain("旧消息")], message_str="旧消息")

    assert manager.message_has_quote(FakeEvent("u1", "group:1", "普通消息")) is False
    assert (
        manager.message_has_quote(
            FakeEvent("u1", "group:1", chain=[quote, Plain("这个图是什么意思")])
        )
        is True
    )


def test_cancel_window_returns_owner_and_clears_state():
    manager = make_manager()
    owner = FakeEvent("u1", "group:1", "第一段", wake=True)
    manager.start_window(owner)

    cancelled = manager.cancel_window(FakeEvent("u1", "group:1", "引用+@bot", wake=True))

    assert cancelled is owner
    assert not manager.is_window_open(FakeEvent("u1", "group:1", "x", wake=True))
    assert manager.cancel_window(FakeEvent("u1", "group:1", "x", wake=True)) is None


def test_cancel_window_requires_window_phase():
    manager = make_manager()
    owner = FakeEvent("u1", "group:1", "第一段", wake=True)
    manager.start_window(owner)
    manager.finalize_window(owner)

    assert manager.cancel_window(FakeEvent("u1", "group:1", "x", wake=True)) is None


def test_capture_resets_sliding_quiet_remaining():
    clock = {"t": 100.0}
    manager = make_manager(now=lambda: clock["t"])
    owner = FakeEvent("u1", "group:1", "第一段", wake=True)

    assert manager.start_window(owner)
    assert manager.quiet_remaining(owner, 6.0) == 6.0

    clock["t"] += 3.0
    follow = FakeEvent("u1", "group:1", "第二段", wake=False)
    assert manager.capture(follow)
    # 捕获后计时重置，仍需完整 6 秒静默
    assert manager.quiet_remaining(owner, 6.0) == 6.0

    clock["t"] += 5.5
    assert manager.quiet_remaining(owner, 6.0) == 0.5
    clock["t"] += 0.6
    assert manager.quiet_remaining(owner, 6.0) == 0.0


def test_merge_wake_resets_sliding_quiet_remaining():
    clock = {"t": 100.0}
    manager = make_manager(now=lambda: clock["t"])
    owner = FakeEvent("u1", "group:1", "第一段", wake=True)
    manager.start_window(owner)

    clock["t"] += 4.0
    wake = FakeEvent("u1", "group:1", "@bot 补充", wake=True)
    assert manager.merge_wake(wake)
    assert manager.quiet_remaining(owner, 6.0) == 6.0


def test_finalize_window_destroys_state():
    manager = make_manager()
    owner = FakeEvent("u1", "group:1", "第一段", wake=True)
    manager.start_window(owner)

    manager.finalize_window(owner)

    assert manager.quiet_remaining(owner, 6.0) == 0.0
    assert manager.is_window_open(FakeEvent("u1", "group:1", "x", wake=True)) is False


def test_capture_rejects_after_finalize():
    manager = make_manager()
    owner = FakeEvent("u1", "group:1", "第一段", wake=True)
    manager.start_window(owner)
    manager.finalize_window(owner)

    assert not manager.capture(FakeEvent("u1", "group:1", "迟到消息", wake=False))
