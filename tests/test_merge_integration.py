import asyncio
from types import SimpleNamespace

from astrbot.api.message_components import Image, Plain

from _astrbot_plugin_filter_test.merge_window import MergeWindowManager
from main import MEDIA_ONLY_PROMPT, _strip_structure_tags

from tests.conftest import FakeEvent, make_optimizer


def test_two_segments_merge_into_one_wake():
    optimizer = make_optimizer()
    first = FakeEvent("u1", "group:1", "你可以发一个表情包吗", wake=True)
    second = FakeEvent("u1", "group:1", "我觉得可爱的表情包不错", wake=False)

    async def run():
        first_task = asyncio.create_task(optimizer.on_waiting_llm_request(first))
        await asyncio.sleep(0.05)
        await optimizer.on_message(second)
        await first_task
        return first.message_str

    merged = asyncio.run(run())

    assert "你可以发一个表情包吗" in merged
    assert "我觉得可爱的表情包不错" in merged


def test_wake_followup_during_window_is_merged_and_stopped():
    optimizer = make_optimizer()
    first = FakeEvent("u1", "group:1", "第一段", wake=True)
    second = FakeEvent("u1", "group:1", "@bot 第二段", wake=True)

    async def run():
        first_task = asyncio.create_task(optimizer.on_waiting_llm_request(first))
        await asyncio.sleep(0.05)
        await optimizer.on_waiting_llm_request(second)
        await first_task
        return first.message_str, second.stopped

    merged, stopped = asyncio.run(run())

    assert merged == MergeWindowManager.format_segments(["第一段", "第二段"])
    assert stopped is True


def test_content_guard_blocks_merged_text():
    optimizer = make_optimizer(
        enable_content_guard=True,
        content_guard_block_terms="违禁词",
    )
    event = FakeEvent("u1", "group:1", "第一段\n违禁词", wake=True)
    req = SimpleNamespace(prompt="第一段\n违禁词")

    async def run():
        await optimizer.on_waiting_llm_request(event)
        await optimizer.on_llm_request(event, req)
        return event.stopped

    assert asyncio.run(run()) is True


def test_self_reply_mark_injected_on_llm_request():
    optimizer = make_optimizer()
    marker = optimizer._get_self_reply_marker()
    sent = FakeEvent("u1", "group:1")
    sent.set_result(SimpleNamespace(chain=[Plain("好的"), Image("file:///meme.png")]))
    marker.record_sent_reply(sent)

    event = FakeEvent("u1", "group:1", "你刚才发了什么", wake=True)
    req = SimpleNamespace(
        prompt="你刚才发了什么",
        extra_user_content_parts=[],
    )

    asyncio.run(optimizer.on_llm_request_marking(event, req))

    injected = any(
        "机器人自己" in getattr(part, "text", "")
        for part in req.extra_user_content_parts
    )
    assert injected is True


def test_quoted_image_message_gets_recognition_note_not_text_only_note():
    optimizer = make_optimizer()
    quote = SimpleNamespace(
        type="Reply",
        chain=[Image("file:///q.png")],
        message_str="[图片]",
    )
    event = FakeEvent(
        "u1",
        "group:1",
        chain=[quote, Plain("这个图是什么意思")],
        wake=True,
    )
    req = SimpleNamespace(
        prompt="这个图是什么意思",
        image_urls=[],
        extra_user_content_parts=[],
    )

    asyncio.run(optimizer.on_llm_request_marking(event, req))

    texts = [getattr(part, "text", "") for part in req.extra_user_content_parts]
    assert any("用户引用了一张历史消息中的图片" in text for text in texts)
    assert not any("用户本条消息为纯文字" in text for text in texts)
    assert any("q.png" in str(url) for url in req.image_urls)


def test_user_media_message_gets_attribution_note():
    optimizer = make_optimizer()
    event = FakeEvent(
        "u1",
        "group:1",
        chain=[Image("file:///user.png")],
        wake=True,
    )
    req = SimpleNamespace(
        prompt="[图片]",
        extra_user_content_parts=[],
    )

    asyncio.run(optimizer.on_llm_request_marking(event, req))

    texts = [getattr(part, "text", "") for part in req.extra_user_content_parts]
    assert any("用户本轮发送了图片/文件" in text for text in texts)
    assert not any("用户本条消息为纯文字" in text for text in texts)


def test_media_only_message_gets_recognition_prompt_after_window():
    optimizer = make_optimizer()
    event = FakeEvent(
        "u1",
        "group:1",
        text="",
        chain=[Image("file:///user.png")],
        wake=True,
    )

    asyncio.run(optimizer.on_waiting_llm_request(event))

    assert event.message_str == MEDIA_ONLY_PROMPT


def test_quoted_wakeup_cancels_media_window_and_keeps_own_text():
    optimizer = make_optimizer()
    optimizer._get_merge_window_seconds = lambda: 0.2
    first = FakeEvent(
        "u1",
        "group:1",
        text="",
        chain=[Image("file:///first.png")],
        wake=True,
    )
    quote = SimpleNamespace(
        type="Reply",
        chain=[Image("file:///first.png")],
        message_str="[图片]",
    )
    second = FakeEvent(
        "u1",
        "group:1",
        text="这个图是什么意思",
        chain=[quote, Plain("这个图是什么意思")],
        wake=True,
    )

    async def run():
        first_task = asyncio.create_task(optimizer.on_waiting_llm_request(first))
        await asyncio.sleep(0.05)
        await optimizer.on_waiting_llm_request(second)
        await first_task
        return first.message_str, first.stopped, second.message_str

    first_text, first_stopped, second_text = asyncio.run(run())

    assert first_text == ""  # 窗口被取消，不补占位、不发送
    assert first_stopped is True
    assert second_text == "这个图是什么意思"  # 引用消息独立走管道


def test_plain_wake_followup_still_merges_during_window():
    optimizer = make_optimizer()
    first = FakeEvent("u1", "group:1", "第一段", wake=True)
    second = FakeEvent("u1", "group:1", "补充", wake=True)

    async def run():
        first_task = asyncio.create_task(optimizer.on_waiting_llm_request(first))
        await asyncio.sleep(0.05)
        await optimizer.on_waiting_llm_request(second)
        await first_task
        return first.message_str, second.stopped

    merged, stopped = asyncio.run(run())

    assert merged == MergeWindowManager.format_segments(["第一段", "补充"])
    assert stopped is True


def test_window_ignore_prefix_message_not_swallowed():
    """窗口期：以忽略前缀开头的消息无法合并，必须放行而不是被吞掉。"""
    optimizer = make_optimizer()
    first = FakeEvent("u1", "FriendMessage:1", "第一段", wake=True)
    second = FakeEvent("u1", "FriendMessage:1", "/roll", wake=True)

    async def run():
        first_task = asyncio.create_task(optimizer.on_waiting_llm_request(first))
        await asyncio.sleep(0.02)
        await optimizer.on_message(second)
        await optimizer.on_waiting_llm_request(second)
        await first_task
        return second.stopped, second.message_str

    stopped, text = asyncio.run(run())
    assert stopped is False
    assert text == "/roll"


def test_window_over_limit_message_not_swallowed():
    """窗口期：超过合并字数上限的消息无法合并，必须放行而不是被吞掉。"""
    optimizer = make_optimizer(merge_max_chars=10)
    first = FakeEvent("u1", "FriendMessage:1", "第一段", wake=True)
    second = FakeEvent("u1", "FriendMessage:1", "X" * 50, wake=True)

    async def run():
        first_task = asyncio.create_task(optimizer.on_waiting_llm_request(first))
        await asyncio.sleep(0.02)
        await optimizer.on_message(second)
        await optimizer.on_waiting_llm_request(second)
        await first_task
        return second.stopped, second.message_str

    stopped, text = asyncio.run(run())
    assert stopped is False
    assert text == "X" * 50


def test_window_unmergeable_component_message_not_swallowed():
    """窗口期：含不可合并组件（如 At 他人）的消息必须放行而不是被吞掉。"""
    optimizer = make_optimizer()
    first = FakeEvent("u1", "group:1", "第一段", wake=True)
    at = SimpleNamespace(type="At", target="u2")
    second = FakeEvent(
        "u1",
        "group:1",
        text="@u2 你好",
        chain=[at, Plain("@u2 你好")],
        wake=False,
    )

    async def run():
        first_task = asyncio.create_task(optimizer.on_waiting_llm_request(first))
        await asyncio.sleep(0.02)
        await optimizer.on_message(second)
        await optimizer.on_waiting_llm_request(second)
        await first_task
        return second.stopped, second.message_str

    stopped, text = asyncio.run(run())
    assert stopped is False
    assert text == "@u2 你好"


def test_window_captured_message_still_consumed():
    """窗口期：已被 on_message 捕获的普通补充消息仍应被消费，不重复触发。"""
    optimizer = make_optimizer()
    first = FakeEvent("u1", "FriendMessage:1", "第一段", wake=True)
    second = FakeEvent("u1", "FriendMessage:1", "补充", wake=False)

    async def run():
        first_task = asyncio.create_task(optimizer.on_waiting_llm_request(first))
        await asyncio.sleep(0.02)
        await optimizer.on_message(second)
        await optimizer.on_waiting_llm_request(second)
        await first_task
        return first.message_str, second.stopped

    merged, stopped = asyncio.run(run())
    assert merged == MergeWindowManager.format_segments(["第一段", "补充"])
    assert stopped is True


def test_decoration_strips_structure_tags():
    optimizer = make_optimizer()
    result = SimpleNamespace(chain=[Plain("原来是这样呀</blockquote> [图片]")])
    event = FakeEvent("u1", "group:1", wake=True)
    event.set_result(result)

    asyncio.run(optimizer.on_decorating_result(event))

    assert result.chain[0].text == "原来是这样呀 [图片]"


def test_decoration_drops_duplicate_reply_just_sent():
    optimizer = make_optimizer()
    marker = optimizer._get_self_reply_marker()
    sent = FakeEvent("u1", "group:1")
    sent.set_result(SimpleNamespace(chain=[Plain("那张脸P得也太违和了")]))
    marker.record_sent_reply(sent)

    result = SimpleNamespace(chain=[Plain("那张脸P得也太违和了")])
    event = FakeEvent("u1", "group:1", wake=True)
    event.set_result(result)

    asyncio.run(optimizer.on_decorating_result(event))

    assert result.chain == []


def test_decoration_keeps_new_reply_text():
    optimizer = make_optimizer()
    marker = optimizer._get_self_reply_marker()
    sent = FakeEvent("u1", "group:1")
    sent.set_result(SimpleNamespace(chain=[Plain("旧回复")]))
    marker.record_sent_reply(sent)

    result = SimpleNamespace(chain=[Plain("新回复内容")])
    event = FakeEvent("u1", "group:1", wake=True)
    event.set_result(result)

    asyncio.run(optimizer.on_decorating_result(event))

    assert result.chain[0].text == "新回复内容"


def test_strip_structure_tags_handles_both_tags():
    assert _strip_structure_tags("<blockquote>引用</blockquote> 内容") == "引用 内容"
    assert _strip_structure_tags("正常文本") == "正常文本"
    assert _strip_structure_tags("") == ""
    assert _strip_structure_tags("<p>段落</p><br>换行") == "段落换行"
    assert _strip_structure_tags("<div>盒子</div><code>x</code>") == "盒子x"


def test_marking_hook_annotates_history_and_injects_notes():
    optimizer = make_optimizer()
    event = FakeEvent("u1", "group:1", "这个不是我发的呀", wake=True)
    req = SimpleNamespace(
        prompt="这个不是我发的呀",
        contexts=[
            {
                "role": "assistant",
                "content": [{"type": "image", "text": "meme.png"}],
            },
        ],
        extra_user_content_parts=[],
    )

    asyncio.run(optimizer.on_llm_request_marking(event, req))

    assert "机器人自己发送" in str(req.contexts[0]["content"])
    assert any(
        "纯文字" in getattr(part, "text", "")
        for part in req.extra_user_content_parts
    )


def test_marking_hook_skips_stopped_events():
    optimizer = make_optimizer()
    event = FakeEvent("u1", "group:1", "旧", wake=True)
    event.stop_event()
    req = SimpleNamespace(prompt="旧", contexts=[], extra_user_content_parts=[])

    asyncio.run(optimizer.on_llm_request_marking(event, req))

    assert req.extra_user_content_parts == []


def test_on_decorating_result_retargets_quote_to_last_merged_message():
    optimizer = make_optimizer()
    first = FakeEvent("u1", "group:1", "第一段", wake=True)
    second = FakeEvent("u1", "group:1", "补充", wake=False)
    first.set_extra("merge_last_message_id", second.message_obj.message_id)
    first.set_result(SimpleNamespace(chain=[Plain("回复")]))

    asyncio.run(optimizer.on_decorating_result(first))

    assert first.message_obj.message_id == second.message_obj.message_id


def test_on_decorating_result_keeps_own_quote_without_merge():
    optimizer = make_optimizer()
    event = FakeEvent("u1", "group:1", "普通消息", wake=True)
    event.set_result(SimpleNamespace(chain=[Plain("回复")]))
    original = event.message_obj.message_id

    asyncio.run(optimizer.on_decorating_result(event))

    assert event.message_obj.message_id == original


def test_image_and_two_texts_within_window_merge_into_one_request():
    optimizer = make_optimizer()
    optimizer._get_merge_window_seconds = lambda: 0.2
    first = FakeEvent(
        "u1",
        "group:1",
        text="",
        chain=[Image("file:///a.png")],
        wake=True,
    )
    second = FakeEvent("u1", "group:1", "第一句", wake=False)
    third = FakeEvent("u1", "group:1", "第二句", wake=False)

    async def run():
        first_task = asyncio.create_task(optimizer.on_waiting_llm_request(first))
        await asyncio.sleep(0.05)
        await optimizer.on_waiting_llm_request(second)
        await optimizer.on_waiting_llm_request(third)
        await first_task
        return (
            first.message_str,
            first.get_messages(),
            second.stopped,
            third.stopped,
        )

    merged, chain, second_stopped, third_stopped = asyncio.run(run())

    assert merged == MergeWindowManager.format_segments(["第一句", "第二句"])
    assert any(isinstance(comp, Image) for comp in chain)
    assert second_stopped is True
    assert third_stopped is True


def test_window_resets_on_new_message_until_silence():
    optimizer = make_optimizer()
    optimizer._get_merge_window_seconds = lambda: 0.15
    first = FakeEvent("u1", "group:1", "第一段", wake=True)
    second = FakeEvent("u1", "group:1", "第二段", wake=False)
    third = FakeEvent("u1", "group:1", "第三段", wake=False)

    async def run():
        first_task = asyncio.create_task(optimizer.on_waiting_llm_request(first))
        await asyncio.sleep(0.05)
        await optimizer.on_message(second)  # 重置计时
        await asyncio.sleep(0.10)          # 未满静默，仍在窗口
        await optimizer.on_message(third)  # 再次重置
        await first_task                    # 静默满后收口
        return first.message_str

    merged = asyncio.run(run())

    assert merged == MergeWindowManager.format_segments(
        ["第一段", "第二段", "第三段"]
    )


def test_inflight_reply_gets_no_stop_on_new_message():
    optimizer = make_optimizer()
    old = FakeEvent("u1", "group:1", "第一段", wake=True)
    asyncio.run(optimizer.on_waiting_llm_request(old))  # 窗口已收口
    assert optimizer._get_message_merger().quiet_remaining(old, 6.0) == 0.0

    follow = FakeEvent("u1", "group:1", "@bot 补充", wake=True)
    asyncio.run(optimizer.on_message(follow))
    asyncio.run(optimizer.on_waiting_llm_request(follow))

    assert old.stopped is False
    assert follow.message_str == "@bot 补充"  # 未被合并改写
