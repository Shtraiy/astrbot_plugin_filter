import asyncio
from types import SimpleNamespace

from astrbot.api.message_components import Plain

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
