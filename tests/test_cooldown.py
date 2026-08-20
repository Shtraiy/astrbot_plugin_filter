import asyncio

import main
from main import LanguageLogicOptimizer


class FakeEvent:
    def __init__(self, wake=True, origin="", request_id=None):
        self.wake = wake
        self.unified_msg_origin = origin
        self.request_id = request_id
        self.stopped = False

    def is_wake_up(self):
        return self.wake

    def stop_event(self):
        self.stopped = True


def make_optimizer(gate_seconds=0):
    optimizer = object.__new__(LanguageLogicOptimizer)
    optimizer.config = {
        "cooldown_seconds": gate_seconds,
        "enable_content_guard": False,
    }
    optimizer.context = None
    optimizer._gates = {}
    optimizer._pending_send = None
    optimizer._pending_sends = {}
    optimizer._reply_locks = {}
    optimizer._onboarding_states = {}
    optimizer._pending_tasks = set()
    optimizer._get_wakeup_interval = lambda: (0.0, 0.0)
    return optimizer


def test_cooldown_config_is_non_negative():
    optimizer = make_optimizer(-5)
    assert optimizer._get_cooldown_seconds() == 0.0


def test_gate_seconds_defaults_to_zero_and_overrides_legacy_setting():
    optimizer = make_optimizer()
    assert optimizer._get_gate_seconds() == 0.0

    optimizer.config = {"gate_seconds": 2, "cooldown_seconds": 9}
    assert optimizer._get_gate_seconds() == 2.0


def test_wakeup_interval_has_one_second_floor_and_ordered_bounds():
    optimizer = make_optimizer()
    del optimizer._get_wakeup_interval
    optimizer.config.update({"wakeup_interval_min": 0, "wakeup_interval_max": 0.5})
    assert optimizer._get_wakeup_interval() == (1.0, 1.0)

    optimizer.config.update({"wakeup_interval_min": 2.5, "wakeup_interval_max": 1})
    assert optimizer._get_wakeup_interval() == (2.5, 2.5)


def test_global_queue_keeps_user_wakeups_without_preemption():
    optimizer = make_optimizer()
    first = FakeEvent(origin="group:1", request_id="first")
    second = FakeEvent(origin="group:2", request_id="second")

    async def run():
        await optimizer.on_waiting_llm_request(first)
        second_task = asyncio.create_task(optimizer.on_waiting_llm_request(second))
        await asyncio.sleep(0)
        assert not second_task.done()
        assert not second.stopped

        optimizer._release_gate(first)
        await second_task

    asyncio.run(run())
    assert not second.stopped
    assert optimizer._get_reply_coordinator().active_event is second


def test_content_guard_releases_after_safe_reply_finishes():
    optimizer = make_optimizer()
    optimizer.config.update(
        {
            "enable_content_guard": True,
            "content_guard_mode": "strict",
        }
    )
    first = FakeEvent(origin="group:1", request_id="first")
    first.message_str = "敏感词"
    second = FakeEvent(origin="group:2", request_id="second")

    async def run():
        safe_reply_started = asyncio.Event()
        allow_safe_reply = asyncio.Event()

        async def controlled_safe_reply(_event, _category):
            safe_reply_started.set()
            await allow_safe_reply.wait()

        optimizer._send_guard_reply = controlled_safe_reply
        await optimizer.on_waiting_llm_request(first)
        second_task = asyncio.create_task(optimizer.on_waiting_llm_request(second))
        first_task = asyncio.create_task(optimizer.on_llm_request(first, None))
        await safe_reply_started.wait()
        await asyncio.sleep(0)
        assert not second_task.done()

        allow_safe_reply.set()
        await first_task
        await second_task
        assert optimizer._get_reply_coordinator().active_event is second

    asyncio.run(run())


def test_global_queue_drops_only_the_newest_when_full():
    optimizer = make_optimizer()
    events = [FakeEvent(origin=f"group:{index}") for index in range(5)]

    async def run():
        await optimizer.on_waiting_llm_request(events[0])
        tasks = [
            asyncio.create_task(optimizer.on_waiting_llm_request(event))
            for event in events[1:]
        ]
        await asyncio.sleep(0)
        assert optimizer._get_reply_coordinator().pending_wakeup_count == 3
        assert events[4].stopped
        for task in tasks[:3]:
            task.cancel()
        await asyncio.gather(*tasks[:3], return_exceptions=True)

    asyncio.run(run())


def test_after_message_sent_releases_active_and_allows_queued_event():
    optimizer = make_optimizer()
    owner = FakeEvent(origin="group:1", request_id="owner")
    queued = FakeEvent(origin="group:2", request_id="queued")

    async def run():
        await optimizer.on_waiting_llm_request(owner)
        queue_task = asyncio.create_task(optimizer.on_waiting_llm_request(queued))
        await asyncio.sleep(0)

        lock = asyncio.Lock()
        await lock.acquire()
        pending = (owner.unified_msg_origin, lock, owner)
        optimizer._pending_send = pending
        optimizer._pending_sends[owner.unified_msg_origin] = pending
        await optimizer.after_message_sent(owner)
        await queue_task
        assert not lock.locked()

    asyncio.run(run())
    assert not queued.stopped


def test_unrelated_same_origin_callback_does_not_release_global_active():
    optimizer = make_optimizer()
    owner = FakeEvent(origin="group:1", request_id="owner")
    unrelated = FakeEvent(origin="group:1", request_id="unrelated")
    queued = FakeEvent(origin="group:2", request_id="queued")

    async def run():
        await optimizer.on_waiting_llm_request(owner)
        await optimizer.after_message_sent(unrelated)
        queue_task = asyncio.create_task(optimizer.on_waiting_llm_request(queued))
        await asyncio.sleep(0)
        assert not queue_task.done()
        queue_task.cancel()
        await asyncio.gather(queue_task, return_exceptions=True)

    asyncio.run(run())
    assert not queued.stopped


def test_wakeup_is_not_admitted_until_gate_cooldown_expires():
    optimizer = make_optimizer(3)
    owner = FakeEvent(origin="group:1")
    incoming = FakeEvent(origin="group:2")

    async def run():
        await optimizer.on_waiting_llm_request(owner)
        task = asyncio.create_task(optimizer.on_waiting_llm_request(incoming))
        await asyncio.sleep(0)
        optimizer._release_gate(owner, apply_cooldown=True)
        await asyncio.sleep(0.01)
        assert not task.done()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())
    assert not incoming.stopped


def test_non_wakeup_does_not_consume_global_slot():
    optimizer = make_optimizer()
    event = FakeEvent(wake=False)

    async def run():
        await optimizer.on_waiting_llm_request(event)

    asyncio.run(run())
    coordinator = optimizer._get_reply_coordinator()
    assert coordinator.active_event is None
    assert coordinator.pending_wakeup_count == 0
