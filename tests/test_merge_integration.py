import asyncio
from types import SimpleNamespace

from astrbot.api.message_components import Image, Plain

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

    assert merged.startswith("你可以发一个表情包吗")
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

    assert merged == "第一段\n第二段"
    assert stopped is True


def test_planning_supplement_supersedes_and_regenerates():
    optimizer = make_optimizer()
    old = FakeEvent("u1", "group:1", "第一段", wake=True)
    new = FakeEvent("u1", "group:1", "补充", wake=True)

    async def run():
        await optimizer.on_waiting_llm_request(old)  # sleep 打桩为 0，直接 planning
        await optimizer.on_waiting_llm_request(new)
        return new.message_str, old.stopped

    merged, stopped = asyncio.run(run())

    assert merged == "第一段\n补充"
    assert stopped is True


def test_group_non_wake_supplement_not_promoted_during_planning():
    optimizer = make_optimizer()
    old = FakeEvent("u1", "group:1", "第一段", wake=True)
    follow = FakeEvent("u1", "group:1", "补充", wake=False)

    async def run():
        await optimizer.on_waiting_llm_request(old)
        await optimizer.on_message(follow)
        return follow.is_at_or_wake_command

    assert asyncio.run(run()) is False


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


def test_on_llm_response_guard_stops_superseded():
    optimizer = make_optimizer()
    event = FakeEvent("u1", "group:1", "旧", wake=True)
    asyncio.run(optimizer.on_waiting_llm_request(event))
    optimizer._get_reply_coordinator().supersede_active_event(event)

    async def run():
        await optimizer.on_llm_response_guard(event, object())
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


def test_media_only_supplement_gets_recognition_prompt_during_planning():
    optimizer = make_optimizer()
    old = FakeEvent(
        "u1",
        "group:1",
        text="",
        chain=[Image("file:///old.png")],
        wake=True,
    )
    new = FakeEvent(
        "u1",
        "group:1",
        "补充",
        wake=True,
    )

    async def run():
        await optimizer.on_waiting_llm_request(old)
        await optimizer.on_waiting_llm_request(new)
        return new.message_str

    merged = asyncio.run(run())

    assert merged == f"{MEDIA_ONLY_PROMPT}\n补充"


def test_media_supplement_keeps_existing_text_without_placeholder():
    optimizer = make_optimizer()
    old = FakeEvent("u1", "group:1", "第一段", wake=True)
    new = FakeEvent(
        "u1",
        "group:1",
        text="",
        chain=[Image("file:///new.png")],
        wake=True,
    )

    async def run():
        await optimizer.on_waiting_llm_request(old)
        await optimizer.on_waiting_llm_request(new)
        return new.message_str

    merged = asyncio.run(run())

    assert merged == "第一段"


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

    assert merged == "第一段\n补充"
    assert stopped is True


def test_expired_planning_does_not_promote_later_media_message():
    optimizer = make_optimizer()
    old = FakeEvent("u1", "group:1", "第一段", wake=True)
    asyncio.run(optimizer.on_waiting_llm_request(old))  # 窗口 sleep 打桩 0 → planning
    merger = optimizer._get_message_merger()
    key = merger.window_key(old)
    merger._states[key].planning_started_at = 0.0  # 强制过期

    img = FakeEvent(
        "u1",
        "group:1",
        text="",
        chain=[Image("file:///x.png")],
        wake=False,
    )
    asyncio.run(optimizer.on_message(img))

    assert img.is_at_or_wake_command is False


def test_same_sender_wakeup_requests_agent_stop_even_without_merge_state():
    optimizer = make_optimizer()
    first = FakeEvent("u1", "group:1", "第一次唤醒", wake=True)
    asyncio.run(optimizer.on_waiting_llm_request(first))
    optimizer._get_message_merger().clear_state(first)  # 模拟状态已被清理

    calls = []
    optimizer._request_agent_stop = lambda event: calls.append(event)
    second = FakeEvent("u1", "group:1", "第二次唤醒", wake=True)
    asyncio.run(optimizer.on_message(second))

    assert len(calls) == 1


def test_other_sender_wakeup_does_not_request_agent_stop():
    optimizer = make_optimizer()
    first = FakeEvent("u1", "group:1", "第一次唤醒", wake=True)
    asyncio.run(optimizer.on_waiting_llm_request(first))

    calls = []
    optimizer._request_agent_stop = lambda event: calls.append(event)
    other = FakeEvent("u2", "group:1", "别人说话", wake=True)
    asyncio.run(optimizer.on_message(other))

    assert calls == []


def test_non_wake_same_sender_message_does_not_stop_agent():
    optimizer = make_optimizer()
    first = FakeEvent("u1", "group:1", "第一次唤醒", wake=True)
    asyncio.run(optimizer.on_waiting_llm_request(first))
    optimizer._get_message_merger().clear_state(first)  # 无规划状态

    calls = []
    optimizer._request_agent_stop = lambda event: calls.append(event)
    casual = FakeEvent("u1", "group:1", "普通消息不唤醒", wake=False)
    asyncio.run(optimizer.on_message(casual))

    assert calls == []


def test_superseded_result_discarded_on_decoration():
    optimizer = make_optimizer()
    event = FakeEvent("u1", "group:1", "旧", wake=True)
    asyncio.run(optimizer.on_waiting_llm_request(event))
    optimizer._get_reply_coordinator().supersede_active_event(event)
    event.set_result(SimpleNamespace(chain=[Plain("旧回复")]))

    asyncio.run(optimizer.on_decorating_result(event))

    assert event.get_result().chain == []


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


def test_empty_event_during_planning_does_not_request_agent_stop():
    optimizer = make_optimizer()
    old = FakeEvent("u1", "group:1", "第一段", wake=True)
    asyncio.run(optimizer.on_waiting_llm_request(old))  # -> planning
    empty = FakeEvent("u1", "group:1", text="", wake=True)
    calls = []
    optimizer._request_agent_stop = lambda event: calls.append(event)

    asyncio.run(optimizer.on_message(empty))

    assert calls == []


def test_non_wake_text_event_during_planning_does_not_request_agent_stop():
    optimizer = make_optimizer()
    old = FakeEvent("u1", "group:1", "第一段", wake=True)
    asyncio.run(optimizer.on_waiting_llm_request(old))  # -> planning
    follow = FakeEvent("u1", "group:1", "补充", wake=False)
    calls = []
    optimizer._request_agent_stop = lambda event: calls.append(event)

    asyncio.run(optimizer.on_message(follow))

    assert calls == []


def test_llm_output_started_supplement_hangs_without_stop_or_promote():
    optimizer = make_optimizer()
    old = FakeEvent("u1", "group:1", "第一段", wake=True)
    asyncio.run(optimizer.on_waiting_llm_request(old))  # -> planning
    old.set_extra("llm_output_started", True)  # 第一条已产出 LLM 响应
    follow = FakeEvent("u1", "group:1", "补充", wake=False)
    calls = []
    optimizer._request_agent_stop = lambda event: calls.append(event)

    asyncio.run(optimizer.on_message(follow))

    assert calls == []
    assert follow.is_at_or_wake_command is False  # 未提升为唤醒
    assert old.stopped is False  # 未 supersede


def test_llm_output_started_supplement_hangs_on_waiting_and_clears_planning_state():
    optimizer = make_optimizer()
    old = FakeEvent("u1", "group:1", "第一段", wake=True)
    asyncio.run(optimizer.on_waiting_llm_request(old))  # -> planning
    old.set_extra("llm_output_started", True)
    follow = FakeEvent("u1", "group:1", "补充", wake=False)
    calls = []
    optimizer._request_agent_stop = lambda event: calls.append(event)

    asyncio.run(optimizer.on_waiting_llm_request(follow))

    assert calls == []
    assert old.stopped is False
    assert follow.message_str == "补充"  # 未被合并改写
    assert optimizer._get_message_merger().planning_active(follow) is False


def test_provider_request_without_llm_output_still_interrupts_and_regenerates():
    optimizer = make_optimizer()
    old = FakeEvent("u1", "group:1", "第一段", wake=True)
    asyncio.run(optimizer.on_waiting_llm_request(old))  # -> planning
    # v3.0.14 曾把 provider_request 存在误判为"已开始"而悬挂；
    # 实际未产出 LLM 响应，必须仍打断合并。
    old.set_extra("provider_request", object())
    new = FakeEvent("u1", "group:1", "补充", wake=True)

    asyncio.run(optimizer.on_waiting_llm_request(new))

    assert old.stopped is True  # 被打断
    assert new.message_str == "第一段\n补充"  # 合并重生成


def test_correction_interrupts_even_after_llm_output_started():
    optimizer = make_optimizer()
    old = FakeEvent("u1", "group:1", "第一段", wake=True)
    asyncio.run(optimizer.on_waiting_llm_request(old))  # -> planning
    old.set_extra("llm_output_started", True)
    new = FakeEvent("u1", "group:1", "再想想", wake=True)

    asyncio.run(optimizer.on_waiting_llm_request(new))

    assert old.stopped is True
    assert new.message_str == "第一段\n再想想"


def test_llm_response_guard_marks_output_started():
    optimizer = make_optimizer()
    event = FakeEvent("u1", "group:1", "旧", wake=True)
    resp = SimpleNamespace(completion_text="正常回复")

    asyncio.run(optimizer.on_llm_response_guard(event, resp))

    assert event.get_extra("llm_output_started") is True


def test_streaming_started_supplement_hangs_without_stop_or_promote():
    optimizer = make_optimizer()
    old = FakeEvent("u1", "group:1", "第一段", wake=True)
    asyncio.run(optimizer.on_waiting_llm_request(old))  # -> planning
    # AstrBot 流式响应在 agent 启动时就 set_result(STREAMING_RESULT)，
    # 但本轮 LLM 调用尚未完成，llm_output_started 标记还没写入。
    old.set_result(SimpleNamespace(result_content_type="streaming"))
    follow = FakeEvent("u1", "group:1", "补充", wake=False)
    calls = []
    optimizer._request_agent_stop = lambda event: calls.append(event)

    asyncio.run(optimizer.on_message(follow))

    assert calls == []
    assert follow.is_at_or_wake_command is False
    assert old.stopped is False


def test_wake_supplement_hangs_after_llm_output_started():
    optimizer = make_optimizer()
    old = FakeEvent("u1", "group:1", "第一段", wake=True)
    asyncio.run(optimizer.on_waiting_llm_request(old))  # -> planning
    old.set_extra("llm_output_started", True)
    follow = FakeEvent("u1", "group:1", "@bot 补充", wake=True)
    calls = []
    optimizer._request_agent_stop = lambda event: calls.append(event)

    asyncio.run(optimizer.on_message(follow))

    assert calls == []
    assert old.stopped is False


def test_private_supplement_interrupts_even_after_llm_output_started():
    optimizer = make_optimizer()
    old = FakeEvent("u1", "FriendMessage:2419269719", "第一段", wake=True)
    asyncio.run(optimizer.on_waiting_llm_request(old))  # -> planning
    old.set_extra("llm_output_started", True)
    follow = FakeEvent("u1", "FriendMessage:2419269719", "补充", wake=True)
    calls = []
    optimizer._request_agent_stop = lambda event: calls.append(event)

    asyncio.run(optimizer.on_message(follow))

    # 私聊：每条消息都是对 bot 说的，即使旧回复已产出 LLM 响应也一律打断，
    # 避免 follow-up 排队等旧 agent（如 LLM 超时重试）导致消息石沉大海。
    assert len(calls) == 1
    assert old.stopped is False


def test_other_sender_message_does_not_clear_planning_state():
    optimizer = make_optimizer()
    old = FakeEvent("u1", "group:1", "第一段", wake=True)
    asyncio.run(optimizer.on_waiting_llm_request(old))  # -> planning
    old.set_extra("llm_output_started", True)
    other = FakeEvent("u2", "group:1", "别人说话", wake=True)
    calls = []
    optimizer._request_agent_stop = lambda event: calls.append(event)

    asyncio.run(optimizer.on_waiting_llm_request(other))

    assert calls == []
    assert optimizer._get_message_merger().planning_active(old) is True


def test_empty_event_does_not_open_merge_window():
    optimizer = make_optimizer()
    empty = FakeEvent("u1", "group:1", text="", wake=True)

    asyncio.run(optimizer.on_waiting_llm_request(empty))

    assert optimizer._get_message_merger().planning_active(empty) is False
    assert empty.stopped is False


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

    assert merged == "第一句\n第二句"
    assert any(isinstance(comp, Image) for comp in chain)
    assert second_stopped is True
    assert third_stopped is True


def test_planning_supplements_keep_accumulating_text_and_image():
    optimizer = make_optimizer()
    first = FakeEvent(
        "u1",
        "group:1",
        text="",
        chain=[Image("file:///a.png")],
        wake=True,
    )
    second = FakeEvent("u1", "group:1", "第一句", wake=True)
    third = FakeEvent("u1", "group:1", "第二句", wake=True)

    async def run():
        await optimizer.on_waiting_llm_request(first)  # window -> planning
        await optimizer.on_waiting_llm_request(second)  # merge + regenerate
        await optimizer.on_waiting_llm_request(third)  # merge + regenerate
        return third.message_str, third.get_messages(), first.stopped, second.stopped

    merged, chain, first_stopped, second_stopped = asyncio.run(run())

    assert merged == f"{MEDIA_ONLY_PROMPT}\n第一句\n第二句"
    assert any(isinstance(comp, Image) for comp in chain)
    assert first_stopped is True
    assert second_stopped is True
