import asyncio
import unittest
from types import SimpleNamespace

try:
    import conftest  # noqa: F401 - installs the local AstrBot test stubs
except ModuleNotFoundError:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import conftest  # noqa: F401 - installs the local AstrBot test stubs
from astrbot.api.message_components import Plain

import main as filter_main
from main import LanguageLogicOptimizer


class FakeContext:
    config = {}

    async def send_message(self, _origin, _chain):
        return None


class FakeEvent:
    unified_msg_origin = "group:1"

    def __init__(self, result):
        self._result = result
        self._extras = {}

    def get_result(self):
        return self._result

    def get_extra(self, key):
        return self._extras.get(key)

    def set_extra(self, key, value):
        self._extras[key] = value


def make_optimizer():
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
    optimizer._get_delay_range = lambda: (0.0, 0.0)
    return optimizer


class FilterReplyLockIntegrationTests(unittest.TestCase):
    def test_reply_lock_stays_held_until_all_followups_finish(self):
        async def scenario():
            optimizer = make_optimizer()
            event = FakeEvent(
                SimpleNamespace(
                    chain=[Plain("first paragraph\n\nsecond paragraph")]
                )
            )
            followups_started = asyncio.Event()
            release_followups = asyncio.Event()

            async def controlled_followups(*_args, **_kwargs):
                followups_started.set()
                await release_followups.wait()

            original_send_followups = filter_main.send_followups
            filter_main.send_followups = controlled_followups
            try:
                await optimizer.on_decorating_result(event)
                await followups_started.wait()
                reply_lock = event.get_extra(
                    "astrbot_plugin_filter_reply_lock"
                )
                same_lock = reply_lock is optimizer._reply_locks["group:1"]
                locked_during_followups = (
                    reply_lock.locked() if reply_lock is not None else False
                )
                release_followups.set()
                await asyncio.gather(*tuple(optimizer._pending_tasks))
                locked_after_followups = (
                    reply_lock.locked() if reply_lock is not None else True
                )
                return same_lock, locked_during_followups, locked_after_followups
            finally:
                filter_main.send_followups = original_send_followups
                release_followups.set()
                if optimizer._pending_tasks:
                    await asyncio.gather(
                        *tuple(optimizer._pending_tasks),
                        return_exceptions=True,
                    )

        same_lock, held, released = asyncio.run(scenario())

        self.assertTrue(same_lock)
        self.assertTrue(held)
        self.assertFalse(released)


if __name__ == "__main__":
    unittest.main()
