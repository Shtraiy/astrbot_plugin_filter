import asyncio
from types import SimpleNamespace

from astrbot.api.message_components import File, Image, Plain

from _astrbot_plugin_filter_test.self_reply_marker import (
    SelfReplyMarker,
    annotate_memory_media_attribution,
    append_referenced_image_note,
    append_text_only_media_note,
    append_user_media_note,
    attach_quoted_images,
    has_referenced_image,
    has_user_media,
    mark_current_prompt_media_boundary,
    mark_context_media_ownership,
    mark_recent_self_meme_context,
    strip_recent_self_meme_context,
)

from tests.conftest import FakeEvent


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
    assert "不能假定用户本轮又发了同一张" in text
    assert "禁止用机器人自己发送的表情包" in text


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


def test_recently_sent_duplicate_detects_same_text_within_window():
    clock = {"now": 1000.0}
    marker = make_marker(minutes=5, now=lambda: clock["now"])
    event = FakeEvent("u1", "group:1")
    event.set_result(SimpleNamespace(chain=[Plain("那张脸P得也太违和了")]))
    marker.record_sent_reply(event)

    assert (
        marker.recently_sent_duplicate("group:1", "那张脸P得也太违和了") is True
    )
    assert (
        marker.recently_sent_duplicate("group:1", "完全不同的内容") is False
    )


def test_recently_sent_duplicate_expires_after_window():
    clock = {"now": 1000.0}
    marker = make_marker(minutes=5, now=lambda: clock["now"])
    event = FakeEvent("u1", "group:1")
    event.set_result(SimpleNamespace(chain=[Plain("重复文本")]))
    marker.record_sent_reply(event)

    clock["now"] += 16

    assert marker.recently_sent_duplicate("group:1", "重复文本") is False


def test_recently_sent_duplicate_isolated_by_session():
    clock = {"now": 1000.0}
    marker = make_marker(minutes=5, now=lambda: clock["now"])
    event = FakeEvent("u1", "group:1")
    event.set_result(SimpleNamespace(chain=[Plain("重复文本")]))
    marker.record_sent_reply(event)

    assert marker.recently_sent_duplicate("group:2", "重复文本") is False


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


def test_mark_recent_self_meme_context_rewrites_to_bot_owned_mark():
    part = SimpleNamespace(
        text=(
            "<recent_sent_meme>\n"
            "本插件刚刚在上一轮向当前会话发送了下面这张表情包。若用户提到“刚才的表情”……\n"
            "文件：meme_47aa9ce73659.jpg\n"
            "分类：吐槽\n"
            "画面描述：银发动漫少女满脸通红、神情羞恼地大声斥责。\n"
            "情绪：羞恼\n"
            "图片文字：真是H!\n"
            "标签：吐槽, 真是H\n"
            "</recent_sent_meme>"
        )
    )
    req = SimpleNamespace(extra_user_content_parts=[part])

    assert mark_recent_self_meme_context(req) == 1
    assert "<bot_sent_meme>" in part.text
    assert "机器人（assistant，也就是你自己）刚刚在上一轮发送的" in part.text
    assert "用户本轮没有发送这张表情包" in part.text
    assert "文件：meme_47aa9ce73659.jpg" in part.text
    assert "图片文字：真是H!" in part.text
    assert "<recent_sent_meme>" not in part.text


def test_mark_recent_self_meme_context_is_idempotent():
    part = SimpleNamespace(
        text=(
            "<recent_sent_meme>\n"
            "文件：a.png\n"
            "图片文字：哈哈\n"
            "</recent_sent_meme>"
        )
    )
    req = SimpleNamespace(extra_user_content_parts=[part])

    assert mark_recent_self_meme_context(req) == 1
    assert mark_recent_self_meme_context(req) == 0
    assert "<bot_sent_meme>" in part.text


def test_mark_recent_self_meme_context_handles_dict_parts():
    req = SimpleNamespace(
        extra_user_content_parts=[
            {
                "type": "text",
                "text": "<recent_sent_meme>\n文件：b.png\n</recent_sent_meme>",
            }
        ]
    )

    assert mark_recent_self_meme_context(req) == 1
    part = req.extra_user_content_parts[0]
    assert "<bot_sent_meme>" in part["text"]
    assert "文件：b.png" in part["text"]


def test_mark_recent_self_meme_context_keeps_normal_parts():
    part = SimpleNamespace(text="普通内容")
    req = SimpleNamespace(extra_user_content_parts=[part])

    assert mark_recent_self_meme_context(req) == 0
    assert req.extra_user_content_parts == [part]


def test_memory_attribution_fix_annotates_expression_and_sticker_claims():
    part = SimpleNamespace(
        text=(
            "之前用户用眼神看着我，用户的表情很无奈，"
            "用户还发了一个表情包来回应。"
        )
    )
    req = SimpleNamespace(extra_user_content_parts=[part])

    assert annotate_memory_media_attribution(req) == 3
    assert "用户用眼神（疑为机器人自己发送的表情包画面" in part.text
    assert "用户的表情（疑为机器人自己发送的表情包画面" in part.text
    assert "用户还发了一个表情包（疑为机器人自己发送的表情包" in part.text


def test_memory_attribution_fix_is_idempotent():
    part = SimpleNamespace(text="用户发了表情包，用户翻了个白眼")
    req = SimpleNamespace(extra_user_content_parts=[part])

    assert annotate_memory_media_attribution(req) == 2
    assert annotate_memory_media_attribution(req) == 0
    assert part.text.count("疑为机器人自己发送") == 2


def test_memory_attribution_fix_skips_negated_or_true_statements():
    part = SimpleNamespace(
        text=(
            "用户没有发送表情包。"
            "不是用户发送的表情包。"
            "用户本轮没有发送这张表情包。"
            "用户发了一张普通图片。"
        )
    )
    req = SimpleNamespace(extra_user_content_parts=[part])

    assert annotate_memory_media_attribution(req) == 0
    assert "疑为机器人自己发送" not in part.text


def test_memory_attribution_fix_handles_dict_parts_and_counts():
    req = SimpleNamespace(
        extra_user_content_parts=[
            {"type": "text", "text": "用户的眼神很可怕。"},
            {"type": "text", "text": "完全不相关的普通内容。"},
        ]
    )

    assert annotate_memory_media_attribution(req) == 1
    assert "用户的眼神（疑为机器人自己发送的表情包画面" in req.extra_user_content_parts[0]["text"]
    assert req.extra_user_content_parts[1]["text"] == "完全不相关的普通内容。"


def test_strip_recent_self_meme_context_keeps_normal_parts():
    part = SimpleNamespace(text="普通内容")
    req = SimpleNamespace(extra_user_content_parts=[part])

    assert strip_recent_self_meme_context(req) == 0
    assert req.extra_user_content_parts == [part]


def test_append_text_only_media_note():
    req = SimpleNamespace(extra_user_content_parts=[])

    assert append_text_only_media_note(req) is True
    assert "用户本轮没有发送任何图片" in req.extra_user_content_parts[0].text
    assert "不要虚构或推断用户的表情" in req.extra_user_content_parts[0].text
    assert "这是我（机器人）自己发送的表情包" in req.extra_user_content_parts[0].text


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


def test_has_referenced_image_ignores_plain_word_image_in_quote_text():
    quote = SimpleNamespace(
        type="Reply",
        chain=[Plain("这张图片怎么样")],
        message_str="这张图片怎么样",
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


def test_append_user_media_note_embeds_current_media_names():
    event = FakeEvent("u1", "group:1", chain=[Image("file:///new.png")])
    req = SimpleNamespace(extra_user_content_parts=[])

    assert append_user_media_note(req, event) is True
    text = req.extra_user_content_parts[0].text
    assert "new.png" in text
    assert "不能假定用户本轮又发了同一张" in text
    assert "直接说明无法看到" in text


def test_attach_quoted_images_adds_path_and_note():
    quote = SimpleNamespace(
        type="Reply",
        chain=[Image("file:///quoted.png")],
        message_str="[图片]",
    )
    event = FakeEvent("u1", "group:1", chain=[quote, Plain("这个图是什么意思")])
    req = SimpleNamespace(image_urls=[], extra_user_content_parts=[])

    attached = asyncio.run(attach_quoted_images(req, event))

    assert attached == 1
    assert any("quoted.png" in str(url) for url in req.image_urls)
    assert any(
        "Image Attachment in quoted message" in getattr(part, "text", "")
        for part in req.extra_user_content_parts
    )


def test_attach_quoted_images_is_idempotent_against_existing_paths():
    quote = SimpleNamespace(
        type="Reply",
        chain=[Image("file:///already.png")],
        message_str="[图片]",
    )
    event = FakeEvent("u1", "group:1", chain=[quote, Plain("看图")])
    req = SimpleNamespace(
        image_urls=["file:///already.png"],
        extra_user_content_parts=[],
    )

    attached = asyncio.run(attach_quoted_images(req, event))

    assert attached == 0
    assert len(req.image_urls) == 1


def test_attach_quoted_images_noop_without_reply():
    event = FakeEvent("u1", "group:1", "普通消息")
    req = SimpleNamespace(image_urls=[], extra_user_content_parts=[])

    assert asyncio.run(attach_quoted_images(req, event)) == 0


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


def test_mark_context_media_ownership_annotates_object_media_without_text():
    """ImageURLPart-style objects (no .text) must still get an ownership mark."""
    assistant_part = SimpleNamespace(type="image_url", image_url={"url": "x.png"})
    user_part = SimpleNamespace(type="image_url", url="http://y.png")
    req = SimpleNamespace(
        contexts=[
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "看"}, assistant_part],
            },
            {"role": "user", "content": [user_part]},
        ]
    )

    marked = mark_context_media_ownership(req)

    assert marked == 2
    assert assistant_part.text.startswith("[机器人自己发送]")
    assert "x.png" in assistant_part.text
    assert user_part.text.startswith("[用户发送]")
    assert "y.png" in user_part.text


def test_mark_context_media_ownership_annotates_string_list_items():
    req = SimpleNamespace(
        contexts=[
            {"role": "assistant", "content": ["先说一句", "[图片]"]},
            {"role": "user", "content": ["[图片]", "这是用户的"]},
        ]
    )

    assert mark_context_media_ownership(req) == 2
    assert req.contexts[0]["content"][1] == "[机器人自己发送的图片]"
    assert req.contexts[1]["content"][0] == "[用户发送的图片]"


def test_mark_context_media_ownership_object_annotation_is_idempotent():
    assistant_part = SimpleNamespace(type="image", url="x.png")
    req = SimpleNamespace(
        contexts=[{"role": "assistant", "content": [assistant_part]}]
    )

    assert mark_context_media_ownership(req) == 1
    assert mark_context_media_ownership(req) == 0
    assert assistant_part.text.startswith("[机器人自己发送]")


def test_mark_context_media_ownership_annotates_string_placeholders():
    req = SimpleNamespace(
        contexts=[
            {"role": "assistant", "content": "这图哪来的 [图片] 旧图"},
            {"role": "user", "content": "[图片]"},
        ]
    )

    assert mark_context_media_ownership(req) == 2
    assert "[机器人自己发送的图片]" in req.contexts[0]["content"]
    assert "[用户发送的图片]" in req.contexts[1]["content"]


def test_mark_context_media_ownership_string_annotation_is_idempotent():
    req = SimpleNamespace(
        contexts=[
            {"role": "assistant", "content": "旧图 [图片]"},
        ]
    )

    assert mark_context_media_ownership(req) == 1
    assert mark_context_media_ownership(req) == 0
    assert "[机器人自己发送的图片]" in req.contexts[0]["content"]
    assert "[图片]" not in req.contexts[0]["content"]


def test_mark_context_media_ownership_annotates_text_placeholder_parts():
    req = SimpleNamespace(
        contexts=[
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "[图片] 机器人发的"},
                    {"type": "image", "image": "x.png"},
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "[图片] 我的图"},
                    {"type": "image", "image": "y.png"},
                ],
            },
        ]
    )

    assert mark_context_media_ownership(req) == 2
    assistant_parts = req.contexts[0]["content"]
    user_parts = req.contexts[1]["content"]
    assert any(
        "[机器人自己发送的图片]" in str(part.get("text", ""))
        for part in assistant_parts
    )
    assert any(
        "[用户发送的图片]" in str(part.get("text", "")) for part in user_parts
    )


def test_mark_current_prompt_media_boundary_rewrites_placeholder():
    event = FakeEvent("u1", "group:1", chain=[Image("file:///new.png")])
    req = SimpleNamespace(prompt="[图片]", extra_user_content_parts=[])

    assert mark_current_prompt_media_boundary(req, event) is True
    assert req.prompt == "[用户本轮发送的图片]"


def test_mark_current_prompt_media_boundary_skips_text_only_message():
    event = FakeEvent("u1", "group:1", "纯文字")
    req = SimpleNamespace(prompt="[图片]", extra_user_content_parts=[])

    assert mark_current_prompt_media_boundary(req, event) is False
    assert req.prompt == "[图片]"


def test_mark_current_prompt_media_boundary_noop_without_placeholder():
    event = FakeEvent("u1", "group:1", chain=[Image("file:///new.png")])
    req = SimpleNamespace(
        prompt="用户发送了一张图片，请识别。", extra_user_content_parts=[]
    )

    assert mark_current_prompt_media_boundary(req, event) is False
    assert "用户发送了一张图片" in req.prompt


def test_mark_current_prompt_media_boundary_is_idempotent():
    event = FakeEvent("u1", "group:1", chain=[Image("file:///new.png")])
    req = SimpleNamespace(prompt="[图片]", extra_user_content_parts=[])

    assert mark_current_prompt_media_boundary(req, event) is True
    assert mark_current_prompt_media_boundary(req, event) is False
    assert req.prompt == "[用户本轮发送的图片]"


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
