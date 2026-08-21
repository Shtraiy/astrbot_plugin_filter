import asyncio
from types import SimpleNamespace

from astrbot.api.message_components import Image, Plain

from main import LanguageLogicOptimizer


class FakeContext:
    async def send_message(self, origin, chain):
        pass


class FakeEvent:
    def __init__(
        self,
        sender,
        origin,
        text="",
        *,
        wake=True,
        request_id=None,
        chain=None,
    ):
        self.sender = sender
        self.unified_msg_origin = origin
        self.message_str = text
        self.request_id = request_id
        self._wake = wake
        self.stopped = False
        self._result = None
        self._extras = {}
        self._chain = chain if chain is not None else ([Plain(text)] if text else [])
        self.message_obj = SimpleNamespace(message=self._chain)

    def get_sender_id(self):
        return self.sender

    def get_messages(self):
        return self.message_obj.message

    def is_wake_up(self):
        return self._wake

    def stop_event(self):
        self.stopped = True

    def is_stopped(self):
        return self.stopped

    def set_extra(self, key, value):
        self._extras[key] = value

    def get_result(self):
        return self._result


def make_optimizer(**overrides):
    optimizer = object.__new__(LanguageLogicOptimizer)
    optimizer.config = {
        "enable_message_merge": True,
        "merge_window_seconds": 6.0,
        "merge_max_messages": 5,
        "merge_max_chars": 2000,
        "merge_ignore_prefixes": "/,!",
        "merge_continuation_ttl": 120.0,
        "merge_task_cancel": False,
        "enable_content_guard": False,
        "enable_llm_style": False,
        "enable_llm_segment": False,
        "enable_de_ai_flavor": False,
        "enable_image_render": False,
        "multi_message": False,
        "gate_seconds": 0.0,
        "gate_ttl_seconds": 300.0,
        "wakeup_interval_min": 1.0,
        "wakeup_interval_max": 1.0,
    }
    optimizer.config.update(overrides)
    optimizer.context = FakeContext()
    optimizer._pending_tasks = set()
    optimizer._reply_locks = {}
    optimizer._gates = {}
    optimizer._pending_send = None
    optimizer._pending_sends = {}
    optimizer._onboarding_states = {}
    optimizer._message_merger = None
    optimizer._reply_coordinator = None
    optimizer._message_dispatcher = None
    optimizer._get_merge_window_seconds = lambda: 0.05
    return optimizer


def test_two_segments_merge_into_one_wake():
    optimizer = make_optimizer()
    first = FakeEvent("u1", "group:1", "你可以发一个表情包吗", wake=True)
    second = FakeEvent("u1", "group:1", "我觉得可爱的表情包不错", wake=False)

    async def run():
        first_task = asyncio.create_task(
            optimizer.on_waiting_llm_request(first)
        )
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
        first_task = asyncio.create_task(
            optimizer.on_waiting_llm_request(first)
        )
        await asyncio.sleep(0.05)
        await optimizer.on_message(second)
        await optimizer.on_waiting_llm_request(second)
        assert second.stopped
        await first_task
        return first.message_str

    merged = asyncio.run(run())

    assert "第二段" in merged
    assert "@bot" not in merged


def test_planning_followup_supersedes_old_event():
    optimizer = make_optimizer()
    first = FakeEvent("u1", "group:1", "第一段", wake=True, request_id="first")
    second = FakeEvent(
        "u1",
        "group:1",
        "@bot 第二段",
        wake=True,
        request_id="second",
    )

    async def run():
        await optimizer.on_waiting_llm_request(first)
        await optimizer.on_waiting_llm_request(second)
        return first, second

    first, second = asyncio.run(run())

    assert first.stopped
    assert second.message_str.startswith("第一段")
    assert "第二段" in second.message_str
    assert optimizer._get_reply_coordinator().active_event is second


def test_merged_text_still_goes_through_content_guard():
    optimizer = make_optimizer(
        enable_content_guard=True,
        content_guard_mode="balanced",
        content_guard_block_terms="可爱",
    )
    first = FakeEvent("u1", "group:1", "第一段", wake=True)
    second = FakeEvent("u1", "group:1", "可爱的第二段", wake=False)

    async def run():
        first_task = asyncio.create_task(
            optimizer.on_waiting_llm_request(first)
        )
        await asyncio.sleep(0.05)
        await optimizer.on_message(second)
        await first_task
        await optimizer.on_llm_request(first, None)
        return first

    first = asyncio.run(run())

    assert first.stopped


def test_image_followup_merged_into_owner_chain():
    optimizer = make_optimizer()
    first = FakeEvent("u1", "group:1", "看看这张图", wake=True)
    img = Image("http://example.com/a.png")
    second = FakeEvent("u1", "group:1", chain=[img], wake=False)

    async def run():
        first_task = asyncio.create_task(
            optimizer.on_waiting_llm_request(first)
        )
        await asyncio.sleep(0.05)
        await optimizer.on_message(second)
        await first_task
        return first

    first = asyncio.run(run())

    assert any(
        getattr(comp, "url", "") == "http://example.com/a.png"
        for comp in first.message_obj.message
    )


def test_planning_continuation_merges_into_next_wake():
    optimizer = make_optimizer(merge_continuation_ttl=120.0)
    first = FakeEvent("u1", "group:1", "第一段", wake=True)
    supplement = FakeEvent("u1", "group:1", "补充内容", wake=False)
    next_wake = FakeEvent("u1", "group:1", "继续", wake=True)

    async def run():
        first_task = asyncio.create_task(
            optimizer.on_waiting_llm_request(first)
        )
        await asyncio.sleep(0.05)
        await first_task
        await optimizer.on_message(supplement)
        optimizer._get_message_merger().clear_owner(first)
        optimizer._release_gate(first)
        await optimizer.on_waiting_llm_request(next_wake)
        return next_wake

    next_wake = asyncio.run(run())

    assert next_wake.message_str.startswith("第一段\n补充内容")
    assert "继续" in next_wake.message_str
