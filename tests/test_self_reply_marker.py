from types import SimpleNamespace

from astrbot.api.message_components import File, Image, Plain

from _astrbot_plugin_filter_test.self_reply_marker import (
    SelfReplyMarker,
    append_referenced_image_note,
    append_text_only_media_note,
    append_user_media_note,
    has_referenced_image,
    has_user_media,
    mark_context_media_ownership,
    strip_recent_self_meme_context,
)


class FakeEvent:
    def __init__(self, sender, origin, text="", *, chain=None):
        self.sender = sender
        self.unified_msg_origin = origin
        self.message_str = text
        self._chain = chain if chain is not None else ([Plain(text)] if text else [])
        self.message_obj = SimpleNamespace(message=self._chain)
        self._result = None

    def get_sender_id(self):
        return self.sender

    def get_messages(self):
        return self._chain

    def set_result(self, result):
        self._result = result

    def get_result(self):
        return self._result


def make_marker(minutes=5.0, *, now=None, enabled=True):
    return SelfReplyMarker(
        get_config=lambda key, default: {
            "enable_self_reply_mark": enabled,
            "self_reply_mark_minutes": minutes,
        }.get(key, default),
        now=now,
    )


def test_record_and_mark_recent_reply():
    marker = make_marker(minutes=5)
    event = FakeEvent("u1", "group:1")
    event.set_result(
        SimpleNamespace(chain=[Plain("好的"), Image("file:///meme.png")])
    )
    marker.record_sent_reply(event)

    req = SimpleNamespace(extra_user_content_parts=[])
    assert marker.mark_own_recent_replies(req, FakeEvent("u1", "group:1")) is True
    text = req.extra_user_content_parts[0].text
    assert "机器人自己" in text
    assert "meme.png" in text
    assert "好的" in text


def test_record_ignores_empty_replies():
    marker = make_marker(minutes=5)
    event = FakeEvent("u1", "group:1")
    event.set_result(SimpleNamespace(chain=[]))
    marker.record_sent_reply(event)

    req = SimpleNamespace(extra_user_content_parts=[])
    assert marker.mark_own_recent_replies(req, event) is False


def test_mark_expires_after_window():
    clock = {"now": 1000.0}
    marker = make_marker(minutes=5, now=lambda: clock["now"])
    event = FakeEvent("u1", "group:1")
    event.set_result(SimpleNamespace(chain=[Plain("hi")]))
    marker.record_sent_reply(event)

    clock["now"] += 301
    req = SimpleNamespace(extra_user_content_parts=[])
    assert marker.mark_own_recent_replies(req, event) is False


def test_mark_isolated_by_session():
    marker = make_marker(minutes=5)
    event = FakeEvent("u1", "group:1")
    event.set_result(SimpleNamespace(chain=[Plain("hi")]))
    marker.record_sent_reply(event)

    req = SimpleNamespace(extra_user_content_parts=[])
    assert marker.mark_own_recent_replies(req, FakeEvent("u1", "group:2")) is False


def test_mark_disabled_by_config():
    marker = make_marker(minutes=5, enabled=False)
    event = FakeEvent("u1", "group:1")
    event.set_result(SimpleNamespace(chain=[Plain("hi")]))
    marker.record_sent_reply(event)

    req = SimpleNamespace(extra_user_content_parts=[])
    assert marker.mark_own_recent_replies(req, event) is False


def test_zero_window_disables_recording():
    marker = make_marker(minutes=0)
    event = FakeEvent("u1", "group:1")
    event.set_result(SimpleNamespace(chain=[Plain("hi")]))
    marker.record_sent_reply(event)

    req = SimpleNamespace(extra_user_content_parts=[])
    assert marker.mark_own_recent_replies(req, event) is False


def test_media_description_fallbacks():
    marker = make_marker(minutes=5)
    event = FakeEvent("u1", "group:1")
    event.set_result(
        SimpleNamespace(chain=[Image("file:///a/b.png"), File("报表.xlsx")])
    )
    marker.record_sent_reply(event)

    req = SimpleNamespace(extra_user_content_parts=[])
    assert marker.mark_own_recent_replies(req, event) is True
    text = req.extra_user_content_parts[0].text
    assert "[图片] b.png" in text
    assert "[文件] 报表.xlsx" in text


def test_strip_recent_self_meme_context():
    part = SimpleNamespace(text="<recent_sent_meme>x</recent_sent_meme>")
    req = SimpleNamespace(extra_user_content_parts=[part])

    assert strip_recent_self_meme_context(req) == 1
    assert req.extra_user_content_parts == []


def test_strip_recent_self_meme_context_keeps_normal_parts():
    part = SimpleNamespace(text="普通内容")
    req = SimpleNamespace(extra_user_content_parts=[part])

    assert strip_recent_self_meme_context(req) == 0
    assert req.extra_user_content_parts == [part]


def test_append_text_only_media_note():
    req = SimpleNamespace(extra_user_content_parts=[])

    assert append_text_only_media_note(req) is True
    assert "用户本轮没有发送任何图片" in req.extra_user_content_parts[0].text


def test_has_user_media_detects_media_components():
    assert has_user_media(FakeEvent("u1", "group:1", "文字")) is False
    assert (
        has_user_media(
            FakeEvent("u1", "group:1", chain=[Plain("文字"), Image("x.png")])
        )
        is True
    )


def test_has_referenced_image_detects_quoted_image_chain():
    quote = SimpleNamespace(
        type="Reply",
        chain=[Plain("旧消息"), Image("file:///quoted.png")],
        message_str="旧消息",
    )
    event = FakeEvent("u1", "group:1", chain=[quote, Plain("这个图是什么意思")])

    assert has_referenced_image(event) is True


def test_has_referenced_image_detects_image_placeholder_text():
    quote = SimpleNamespace(
        type="Reply",
        chain=[],
        message_str="[图片]",
    )
    event = FakeEvent("u1", "group:1", chain=[quote, Plain("这个图是什么意思")])

    assert has_referenced_image(event) is True


def test_has_referenced_image_false_for_plain_quote():
    quote = SimpleNamespace(
        type="Reply",
        chain=[Plain("旧消息")],
        message_str="旧消息",
    )
    event = FakeEvent("u1", "group:1", chain=[quote, Plain("接着说")])

    assert has_referenced_image(event) is False


def test_append_referenced_image_note():
    req = SimpleNamespace(extra_user_content_parts=[])

    assert append_referenced_image_note(req) is True
    assert "用户引用了一张历史消息中的图片" in req.extra_user_content_parts[0].text


def test_append_user_media_note():
    req = SimpleNamespace(extra_user_content_parts=[])

    assert append_user_media_note(req) is True
    text = req.extra_user_content_parts[0].text
    assert "用户本轮发送了图片/文件" in text
    assert "assistant" in text


def test_mark_context_media_ownership_annotates_dict_parts():
    req = SimpleNamespace(
        contexts=[
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "看这个"},
                    {"type": "image", "image": "x.png"},
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "我的图"},
                    {"type": "image", "image": "y.png"},
                ],
            },
        ]
    )

    marked = mark_context_media_ownership(req)

    assert marked == 2
    assistant_content = req.contexts[0]["content"]
    user_content = req.contexts[1]["content"]
    assert any(
        "机器人自己发送" in str(part.get("text", "")) for part in assistant_content
    )
    assert any("用户发送" in str(part.get("text", "")) for part in user_content)


def test_mark_context_media_ownership_annotates_object_parts():
    assistant_part = SimpleNamespace(text="[Image Attachment: path /a.png]")
    req = SimpleNamespace(
        contexts=[
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "看"}, assistant_part],
            },
        ]
    )

    marked = mark_context_media_ownership(req)

    assert marked == 1
    assert assistant_part.text.startswith("[机器人自己发送]")


def test_mark_context_media_ownership_skips_string_content():
    req = SimpleNamespace(
        contexts=[
            {"role": "assistant", "content": "[Image] 旧图"},
            {"role": "user", "content": "纯文字"},
        ]
    )

    assert mark_context_media_ownership(req) == 0


def test_mark_context_media_ownership_is_idempotent():
    req = SimpleNamespace(
        contexts=[
            {
                "role": "assistant",
                "content": [{"type": "image", "text": "x.png"}],
            },
        ]
    )

    assert mark_context_media_ownership(req) == 1
    assert mark_context_media_ownership(req) == 0  # 已加前缀，不重复
