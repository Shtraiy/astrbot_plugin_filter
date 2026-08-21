import asyncio
from types import SimpleNamespace

from astrbot.api.message_components import Image, Plain

from main import LanguageLogicOptimizer
from request_cleaner import (
    append_attribution_note,
    append_image_text_note,
    asks_about_image_text,
    asks_about_own_media,
    has_user_media,
    strip_assistant_media,
    strip_recent_self_meme_context,
)
from test_merge_integration import FakeEvent, make_optimizer


class TextPart:
    def __init__(self, text):
        self.text = text


class FakeReq:
    def __init__(self, contexts=None, extra_user_content_parts=None):
        self.contexts = contexts or []
        self.extra_user_content_parts = extra_user_content_parts or []


def test_has_user_media_detects_image_and_file():
    image_event = FakeEvent("u1", "g:1", chain=[Plain("看图"), Image("a.png")])
    assert has_user_media(image_event) is True

    text_event = FakeEvent("u1", "g:1", "没有图")
    assert has_user_media(text_event) is False

    bare_event = SimpleNamespace()
    assert has_user_media(bare_event) is False


def test_strip_assistant_media_removes_openai_image_blocks():
    req = FakeReq(
        contexts=[
            {"role": "user", "content": "新表情"},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "给你一个"},
                    {"type": "image_url", "image_url": {"url": "bot_meme.png"}},
                ],
            },
            {"role": "assistant", "content": "纯文本回复"},
        ]
    )

    removed = strip_assistant_media(req)

    assert removed == 1
    assert req.contexts[0]["content"] == "新表情"
    assert req.contexts[1]["content"] == [{"type": "text", "text": "给你一个"}]
    assert req.contexts[2]["content"] == "纯文本回复"


def test_strip_assistant_media_handles_component_objects():
    req = FakeReq(
        contexts=[
            {
                "role": "assistant",
                "content": [Plain("回复"), Image("bot.png"), Image("bot2.png")],
            }
        ]
    )

    removed = strip_assistant_media(req)

    assert removed == 2
    content = req.contexts[0]["content"]
    assert len(content) == 1
    assert content[0].text == "回复"


def test_strip_assistant_media_only_media_becomes_empty_text():
    req = FakeReq(
        contexts=[
            {
                "role": "assistant",
                "content": [{"type": "image_url", "image_url": {"url": "x"}}],
            }
        ]
    )

    removed = strip_assistant_media(req)

    assert removed == 1
    assert req.contexts[0]["content"] == ""


def test_strip_assistant_media_ignores_unrecognized_shapes():
    req = FakeReq(
        contexts=[
            {"role": "assistant", "content": "文本"},
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "u"}}]},
        ]
    )

    removed = strip_assistant_media(req)

    assert removed == 0
    assert req.contexts[1]["content"][0]["type"] == "image_url"


def test_strip_recent_self_meme_context_removes_only_meme_block():
    req = FakeReq(
        extra_user_content_parts=[
            TextPart('<meme_send_receipt status="sent">已发送</meme_send_receipt>'),
            TextPart("<recent_sent_meme>上一轮自发表情包描述</recent_sent_meme>"),
        ]
    )

    removed = strip_recent_self_meme_context(req)

    assert removed == 1
    assert len(req.extra_user_content_parts) == 1
    assert "<recent_sent_meme>" not in req.extra_user_content_parts[0].text


def test_strip_recent_self_meme_context_noop_without_meme():
    req = FakeReq(extra_user_content_parts=[TextPart("普通补充")])

    assert strip_recent_self_meme_context(req) == 0
    assert len(req.extra_user_content_parts) == 1


def test_cleaners_tolerate_none_and_unknown_objects():
    assert strip_assistant_media(None) == 0
    assert strip_recent_self_meme_context(object()) == 0
    assert strip_assistant_media(object()) == 0
    assert append_attribution_note(object()) is False


def test_asks_about_own_media_detection():
    assert asks_about_own_media("我发了什么表情包") is True
    assert asks_about_own_media("我发过图吗") is True
    assert asks_about_own_media("我发过什么图片") is True
    assert asks_about_own_media("晚上好") is False
    assert asks_about_own_media("我发给你") is False


def test_asks_about_image_text_detection():
    assert asks_about_image_text("这上面有字吗？？？") is True
    assert asks_about_image_text("上面写了什么") is True
    assert asks_about_image_text("这表情包写着什么") is True
    assert asks_about_image_text("有字吗") is True
    assert asks_about_image_text("你写的什么") is False
    assert asks_about_image_text("晚上好") is False
    assert asks_about_image_text("") is False


def test_append_attribution_note_adds_part():
    req = FakeReq(extra_user_content_parts=[])

    added = append_attribution_note(req)

    assert added is True
    assert len(req.extra_user_content_parts) == 1
    assert "attribution_note" in req.extra_user_content_parts[0].text


def test_append_image_text_note_adds_part():
    req = FakeReq(extra_user_content_parts=[])

    added = append_image_text_note(req)

    assert added is True
    assert len(req.extra_user_content_parts) == 1
    assert "media_note" in req.extra_user_content_parts[0].text


def test_on_llm_request_cleans_context_when_user_sends_image():
    optimizer = make_optimizer()
    event = FakeEvent("u1", "g:1", "这张图什么意思", wake=True, chain=[Image("user.png")])
    req = FakeReq(
        contexts=[
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "给你一个"},
                    {"type": "image_url", "image_url": {"url": "bot_meme.png"}},
                ],
            }
        ],
        extra_user_content_parts=[TextPart("<recent_sent_meme>自发表情包</recent_sent_meme>")],
    )

    async def run():
        await optimizer.on_llm_request(event, req)

    asyncio.run(run())

    assert req.contexts[0]["content"] == [{"type": "text", "text": "给你一个"}]
    assert req.extra_user_content_parts == []


def test_on_llm_request_strips_self_media_even_for_text_only_message():
    optimizer = make_optimizer()
    event = FakeEvent("u1", "g:1", "你好", wake=True)
    req = FakeReq(
        contexts=[
            {
                "role": "assistant",
                "content": [{"type": "image_url", "image_url": {"url": "bot_meme.png"}}],
            }
        ],
        extra_user_content_parts=[TextPart("<recent_sent_meme>自发表情包</recent_sent_meme>")],
    )

    async def run():
        await optimizer.on_llm_request(event, req)

    asyncio.run(run())

    # Bot's own history image is always stripped, even on a plain-text message.
    assert req.contexts[0]["content"] == ""
    # The self-meme description is also stripped: it is appended into the user
    # message and makes the model think the user just sent a meme.
    assert req.extra_user_content_parts == []


def test_on_llm_request_strips_recent_meme_bridge_for_text_only_message():
    optimizer = make_optimizer()
    event = FakeEvent("u1", "g:1", "刚才那个表情什么意思", wake=True)
    req = FakeReq(
        contexts=[
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "给你一个"},
                    {"type": "image_url", "image_url": {"url": "bot_meme.png"}},
                ],
            }
        ],
        extra_user_content_parts=[TextPart("<recent_sent_meme>自发表情包</recent_sent_meme>")],
    )

    async def run():
        await optimizer.on_llm_request(event, req)

    asyncio.run(run())

    assert req.contexts[0]["content"] == [{"type": "text", "text": "给你一个"}]
    assert req.extra_user_content_parts == []


def test_on_llm_request_keeps_recent_meme_bridge_when_configured_off():
    optimizer = make_optimizer(strip_recent_self_meme_context=False)
    event = FakeEvent("u1", "g:1", "刚才那个表情什么意思", wake=True)
    req = FakeReq(
        contexts=[],
        extra_user_content_parts=[TextPart("<recent_sent_meme>自发表情包</recent_sent_meme>")],
    )

    async def run():
        await optimizer.on_llm_request(event, req)

    asyncio.run(run())

    assert len(req.extra_user_content_parts) == 1


def test_on_llm_request_injects_attribution_note_for_own_media_question():
    optimizer = make_optimizer()
    event = FakeEvent("u1", "g:1", "我发了什么表情包", wake=True)
    req = FakeReq(extra_user_content_parts=[])

    async def run():
        await optimizer.on_llm_request(event, req)

    asyncio.run(run())

    assert len(req.extra_user_content_parts) == 1
    assert "attribution_note" in req.extra_user_content_parts[0].text


def test_on_llm_request_skips_attribution_note_for_normal_message():
    optimizer = make_optimizer()
    event = FakeEvent("u1", "g:1", "晚上好", wake=True)
    req = FakeReq(extra_user_content_parts=[])

    async def run():
        await optimizer.on_llm_request(event, req)

    asyncio.run(run())

    assert req.extra_user_content_parts == []


def test_on_llm_request_injects_image_text_note_for_text_only_question():
    optimizer = make_optimizer()
    event = FakeEvent("u1", "g:1", "这上面有字吗？？？", wake=True)
    req = FakeReq(extra_user_content_parts=[])

    async def run():
        await optimizer.on_llm_request(event, req)

    asyncio.run(run())

    assert len(req.extra_user_content_parts) == 1
    assert "media_note" in req.extra_user_content_parts[0].text


def test_on_llm_request_skips_image_text_note_when_user_sent_media():
    optimizer = make_optimizer()
    event = FakeEvent(
        "u1",
        "g:1",
        "这上面有字吗？？？",
        wake=True,
        chain=[Plain("这上面有字吗？？？"), Image("user.png")],
    )
    req = FakeReq(extra_user_content_parts=[])

    async def run():
        await optimizer.on_llm_request(event, req)

    asyncio.run(run())

    assert req.extra_user_content_parts == []


def test_on_llm_request_respects_master_switch():
    optimizer = make_optimizer(protect_user_media_focus=False)
    event = FakeEvent("u1", "g:1", "看图", wake=True, chain=[Image("user.png")])
    req = FakeReq(
        contexts=[
            {
                "role": "assistant",
                "content": [{"type": "image_url", "image_url": {"url": "bot_meme.png"}}],
            }
        ],
        extra_user_content_parts=[TextPart("<recent_sent_meme>自发表情包</recent_sent_meme>")],
    )

    async def run():
        await optimizer.on_llm_request(event, req)

    asyncio.run(run())

    assert req.contexts[0]["content"][0]["type"] == "image_url"
    assert len(req.extra_user_content_parts) == 1


def test_on_llm_request_tolerates_missing_req():
    optimizer = make_optimizer()
    event = FakeEvent("u1", "g:1", "看图", wake=True, chain=[Image("user.png")])

    async def run():
        await optimizer.on_llm_request(event, None)

    asyncio.run(run())
