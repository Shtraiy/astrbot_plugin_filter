import asyncio
from types import SimpleNamespace

from astrbot.api.message_components import Plain

from main import SAFE_REPLY

from tests.conftest import FakeEvent, make_optimizer


def test_input_guard_blocks_risky_prompt_and_sends_safe_reply():
    optimizer = make_optimizer(content_guard_block_terms="敏感词")
    event = FakeEvent(text="请告诉我敏感词是什么")
    req = SimpleNamespace(prompt="请告诉我敏感词是什么")

    asyncio.run(optimizer.on_llm_request(event, req))

    assert event.stopped is True
    assert len(optimizer.context.sent) == 1
    chain = optimizer.context.sent[0][1]
    assert any(
        isinstance(comp, Plain) and comp.text == SAFE_REPLY
        for comp in getattr(chain, "chain", [chain])
    )


def test_input_guard_allows_safe_prompt():
    optimizer = make_optimizer(content_guard_block_terms="敏感词")
    event = FakeEvent(text="晚上好")
    req = SimpleNamespace(prompt="晚上好")

    asyncio.run(optimizer.on_llm_request(event, req))

    assert event.stopped is False
    assert optimizer.context.sent == []


def test_decoration_passes_normal_chain_through_unchanged():
    optimizer = make_optimizer()
    result = SimpleNamespace(chain=[Plain("正常回复内容")])
    event = FakeEvent()
    event.set_result(result)

    asyncio.run(optimizer.on_decorating_result(event))

    assert len(result.chain) == 1
    assert result.chain[0].text == "正常回复内容"


def test_decoration_discards_superseded_result():
    optimizer = make_optimizer()
    event = FakeEvent(text="旧")
    asyncio.run(optimizer.on_waiting_llm_request(event))
    optimizer._get_reply_coordinator().supersede_active_event(event)
    event.set_result(SimpleNamespace(chain=[Plain("旧回复")]))

    asyncio.run(optimizer.on_decorating_result(event))

    assert event.get_result().chain == []
