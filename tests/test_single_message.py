import asyncio
import re
from types import SimpleNamespace

from astrbot.api.message_components import Plain

import main as filter_main
from main import LanguageLogicOptimizer


class FakeContext:
    config = {}

    def __init__(self):
        self.sent = []

    async def send_message(self, origin, chain):
        self.sent.append((origin, chain))


class FakeEvent:
    unified_msg_origin = "group:1"

    def __init__(self, result):
        self._result = result

    def get_result(self):
        return self._result


def test_paragraphs_are_sent_as_separate_messages():
    optimizer = object.__new__(LanguageLogicOptimizer)
    optimizer.config = {
        "enable_content_guard": False,
        "enable_de_ai_flavor": False,
        "multi_message": True,
    }
    context = FakeContext()
    optimizer.context = context
    optimizer._reply_locks = {}
    optimizer._gates = {}
    optimizer._pending_sends = {}
    optimizer._pending_send = None
    optimizer._onboarding_states = {}
    optimizer._pending_tasks = set()
    result = SimpleNamespace(chain=[Plain("first paragraph\n\nsecond paragraph")])

    optimizer._get_delay_range = lambda: (0.0, 0.0)

    async def run():
        await optimizer.on_decorating_result(FakeEvent(result))
        await asyncio.sleep(0.01)

    asyncio.run(run())

    assert result.chain[0].text == "first paragraph"
    assert len(context.sent) == 1


def test_markdown_from_post_processing_is_removed(monkeypatch):
    optimizer = object.__new__(LanguageLogicOptimizer)
    optimizer.config = {
        "enable_content_guard": False,
        "enable_de_ai_flavor": False,
        "enable_image_render": False,
        "multi_message": False,
    }
    optimizer.context = FakeContext()
    optimizer._reply_locks = {}
    optimizer._gates = {}
    optimizer._pending_sends = {}
    optimizer._pending_send = None
    optimizer._onboarding_states = {}
    optimizer._pending_tasks = set()
    result = SimpleNamespace(chain=[Plain("original")])

    async def add_markdown(*_args, **_kwargs):
        return "赛程：**BLAST Bounty Summer 2026**"

    monkeypatch.setattr(filter_main, "apply_segmentation_and_style", add_markdown)

    asyncio.run(optimizer.on_decorating_result(FakeEvent(result)))

    assert result.chain[0].text == "赛程：BLAST Bounty Summer 2026"

def test_followups_reuse_outbound_pipeline(monkeypatch):
    optimizer = object.__new__(LanguageLogicOptimizer)
    optimizer.config = {
        "enable_content_guard": False,
        "enable_de_ai_flavor": False,
        "enable_image_render": False,
        "multi_message": True,
    }
    context = FakeContext()
    optimizer.context = context
    optimizer._reply_locks = {}
    optimizer._gates = {}
    optimizer._pending_sends = {}
    optimizer._pending_send = None
    optimizer._onboarding_states = {}
    optimizer._pending_tasks = set()
    optimizer._get_delay_range = lambda: (0.0, 0.0)
    pipeline_calls = []

    class FakePipeline:
        def __init__(self, **_kwargs):
            pass

        async def process(
            self,
            text,
            _event,
            *,
            strict_guard=False,
            skip_llm_layout=False,
        ):
            pipeline_calls.append((text, skip_llm_layout))
            return SimpleNamespace(
                text=f"clean:{text}",
                changed=True,
                guard_blocked=False,
                stats={},
            )

    monkeypatch.setattr(filter_main, "OutboundTextPipeline", FakePipeline)
    result = SimpleNamespace(chain=[Plain("first paragraph\n\nsecond paragraph")])

    async def run():
        await optimizer.on_decorating_result(FakeEvent(result))
        if optimizer._pending_tasks:
            await asyncio.gather(*tuple(optimizer._pending_tasks))

    asyncio.run(run())

    assert pipeline_calls == [
        ("first paragraph\n\nsecond paragraph", False),
        ("second paragraph", True),
    ]
    assert context.sent[0][1].chain[0].text == "clean:second paragraph"


def test_followups_do_not_trigger_second_llm_pass():
    optimizer = object.__new__(LanguageLogicOptimizer)
    optimizer.config = {
        "enable_content_guard": False,
        "enable_de_ai_flavor": False,
        "enable_image_render": False,
        "multi_message": True,
        "enable_llm_style": True,
        "enable_llm_segment": False,
        "llm_provider_id": "test-provider",
        "llm_timeout_seconds": 5.0,
    }
    calls = []

    class LlmContext(FakeContext):
        async def llm_generate(self, **kwargs):
            calls.append(kwargs["chat_provider_id"])
            prompt = kwargs["prompt"]
            match = re.search(
                r"<untrusted_original>\n(.*?)\n</untrusted_original>",
                prompt,
                re.S,
            )
            if match:
                return SimpleNamespace(completion_text=match.group(1))
            match = re.search(r"原文：\n(.*)", prompt, re.S)
            return SimpleNamespace(
                completion_text=match.group(1) if match else "",
            )

    context = LlmContext()
    optimizer.context = context
    optimizer._reply_locks = {}
    optimizer._gates = {}
    optimizer._pending_sends = {}
    optimizer._pending_send = None
    optimizer._onboarding_states = {}
    optimizer._pending_tasks = set()
    optimizer._get_delay_range = lambda: (0.0, 0.0)
    result = SimpleNamespace(chain=[Plain("苹果很好吃。\n\n香蕉也很好。")])

    async def run():
        await optimizer.on_decorating_result(FakeEvent(result))
        if optimizer._pending_tasks:
            await asyncio.gather(*tuple(optimizer._pending_tasks))

    asyncio.run(run())

    assert calls == ["test-provider"]
    assert len(context.sent) == 1
    assert context.sent[0][1].chain[0].text == "香蕉也很好。"
