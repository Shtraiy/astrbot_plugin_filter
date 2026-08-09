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
    optimizer._get_wakeup_interval = lambda: (0.0, 0.0)
    return optimizer


class FilterReplyLockIntegrationTests(unittest.TestCase):
    def test_global_queue_waits_until_all_followups_finish(self):
        async def scenario():
            optimizer = make_optimizer()
            owner = FakeEvent(
                SimpleNamespace(
                    chain=[Plain("first paragraph\n\nsecond paragraph")]
                )
            )
            queued = FakeEvent(SimpleNamespace(chain=[Plain("queued")]))
            followups_started = asyncio.Event()
            release_followups = asyncio.Event()

            async def controlled_followups(dispatcher, _origin, _paragraphs, **kwargs):
                followups_started.set()
                await release_followups.wait()
                dispatcher.coordinator.release(kwargs["session"], apply_cooldown=True)

            original_send_followups = filter_main.MessageDispatcher.send_followups
            filter_main.MessageDispatcher.send_followups = controlled_followups
            try:
                await optimizer.on_waiting_llm_request(owner)
                queued_task = asyncio.create_task(
                    optimizer.on_waiting_llm_request(queued)
                )
                await asyncio.sleep(0)
                await optimizer.on_decorating_result(owner)
                await followups_started.wait()
                assert not queued_task.done()

                release_followups.set()
                await asyncio.gather(*tuple(optimizer._pending_tasks))
                await queued_task
                return optimizer._get_reply_coordinator().active_event is queued
            finally:
                filter_main.MessageDispatcher.send_followups = original_send_followups
                release_followups.set()
                if optimizer._pending_tasks:
                    await asyncio.gather(
                        *tuple(optimizer._pending_tasks),
                        return_exceptions=True,
                    )

        self.assertTrue(asyncio.run(scenario()))

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

            async def controlled_followups(dispatcher, _origin, _paragraphs, **kwargs):
                followups_started.set()
                await release_followups.wait()
                dispatcher.coordinator.release(kwargs["session"], apply_cooldown=True)

            original_send_followups = filter_main.MessageDispatcher.send_followups
            filter_main.MessageDispatcher.send_followups = controlled_followups
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
                filter_main.MessageDispatcher.send_followups = original_send_followups
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

    def test_multiple_plain_components_share_one_followup_task(self):
        async def scenario():
            optimizer = make_optimizer()
            event = FakeEvent(
                SimpleNamespace(
                    chain=[
                        Plain("first component\n\nfirst follow-up"),
                        object(),
                        Plain("second component\n\nsecond follow-up"),
                    ]
                )
            )
            calls = []
            followups_started = asyncio.Event()
            release_followups = asyncio.Event()

            async def controlled_followups(dispatcher, origin, paragraphs, **kwargs):
                calls.append((origin, list(paragraphs), kwargs["session"]))
                followups_started.set()
                await release_followups.wait()
                dispatcher.coordinator.release(kwargs["session"], apply_cooldown=True)

            original_send_followups = filter_main.MessageDispatcher.send_followups
            filter_main.MessageDispatcher.send_followups = controlled_followups
            try:
                await optimizer.on_decorating_result(event)
                await followups_started.wait()
                await asyncio.sleep(0)
                reply_lock = event.get_extra("astrbot_plugin_filter_reply_lock")
                locked_before_release = reply_lock.locked()
                release_followups.set()
                tasks = tuple(optimizer._pending_tasks)
                if tasks:
                    await asyncio.gather(*tasks)
                return calls, locked_before_release
            finally:
                filter_main.MessageDispatcher.send_followups = original_send_followups
                release_followups.set()
                if optimizer._pending_tasks:
                    await asyncio.gather(
                        *tuple(optimizer._pending_tasks),
                        return_exceptions=True,
                    )

        calls, locked_before_release = asyncio.run(scenario())

        self.assertEqual(len(calls), 1)
        self.assertEqual(
            calls[0][1],
            ["first follow-up", "second follow-up"],
        )
        self.assertTrue(locked_before_release)


if __name__ == "__main__":
    unittest.main()
