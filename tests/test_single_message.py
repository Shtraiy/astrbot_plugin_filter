import asyncio
from types import SimpleNamespace

from astrbot.api.message_components import Plain

from main import LanguageLogicOptimizer


class FakeContext:
    config = {}


class FakeEvent:
    unified_msg_origin = "group:1"

    def __init__(self, result):
        self._result = result

    def get_result(self):
        return self._result


def test_paragraphs_stay_in_one_message_even_with_legacy_multi_message_config():
    optimizer = object.__new__(LanguageLogicOptimizer)
    optimizer.config = {
        "enable_content_guard": False,
        "enable_de_ai_flavor": False,
        "multi_message": True,
    }
    optimizer.context = FakeContext()
    optimizer._reply_locks = {}
    optimizer._gates = {}
    optimizer._pending_sends = {}
    optimizer._pending_send = None
    optimizer._onboarding_states = {}
    optimizer._pending_tasks = set()
    result = SimpleNamespace(chain=[Plain("first paragraph\n\nsecond paragraph")])

    asyncio.run(optimizer.on_decorating_result(FakeEvent(result)))

    assert result.chain[0].text == "first paragraph\n\nsecond paragraph"
