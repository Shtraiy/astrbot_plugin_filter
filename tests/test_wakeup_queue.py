import asyncio

import pytest

from _astrbot_plugin_filter_test.reply_coordinator import ReplyCoordinator


class FakeEvent:
    def __init__(self, name, *, wake=True, origin=None):
        self.name = name
        self.wake = wake
        self.unified_msg_origin = origin or name
        self.stopped = False

    def is_wake_up(self):
        return self.wake

    def stop_event(self):
        self.stopped = True


def make_coordinator(*, gate_seconds=0.0, ttl=300.0, delay=(0.0, 0.0), random_delay=None, sleep=None, now=None):
    return ReplyCoordinator(
        get_gate_seconds=lambda: gate_seconds,
        get_gate_ttl_seconds=lambda: ttl,
        get_wakeup_interval=lambda: delay,
        event_is_wake_up=lambda event: event.is_wake_up(),
        is_proactive_event=lambda _event: False,
        now=now,
        sleep=sleep,
        random_delay=random_delay,
    )


def test_global_fifo_keeps_three_pending_and_drops_newest():
    async def scenario():
        coordinator = make_coordinator()
        events = [FakeEvent(f"event-{index}") for index in range(5)]

        assert await coordinator.admit_wakeup(events[0])
        tasks = [asyncio.create_task(coordinator.admit_wakeup(event)) for event in events[1:]]
        await asyncio.sleep(0)

        assert coordinator.pending_wakeup_count == 3
        assert events[4].stopped
        assert not tasks[0].done()
        assert not tasks[1].done()
        assert not tasks[2].done()
        assert tasks[3].done()
        assert tasks[3].result() is False

    asyncio.run(scenario())


def test_global_fifo_promotes_oldest_event_across_origins():
    async def scenario():
        coordinator = make_coordinator()
        events = [
            FakeEvent("first", origin="group:1"),
            FakeEvent("second", origin="group:2"),
            FakeEvent("third", origin="group:3"),
        ]

        assert await coordinator.admit_wakeup(events[0])
        tasks = [asyncio.create_task(coordinator.admit_wakeup(event)) for event in events[1:]]
        await asyncio.sleep(0)

        coordinator.finish_active(events[0])
        await tasks[0]
        assert coordinator.active_event is events[1]
        assert not tasks[1].done()

        coordinator.finish_active(events[1])
        await tasks[1]
        assert coordinator.active_event is events[2]

    asyncio.run(scenario())


def test_queued_wakeup_is_not_admitted_before_active_finishes():
    async def scenario():
        coordinator = make_coordinator()
        active = FakeEvent("active")
        queued = FakeEvent("queued")

        assert await coordinator.admit_wakeup(active)
        task = asyncio.create_task(coordinator.admit_wakeup(queued))
        await asyncio.sleep(0)

        assert not task.done()
        assert coordinator.active_event is active

        coordinator.finish_active(active)
        assert await task
        assert coordinator.active_event is queued

    asyncio.run(scenario())


def test_cancelled_queued_wakeup_does_not_consume_capacity():
    async def scenario():
        coordinator = make_coordinator()
        active = FakeEvent("active")
        cancelled = FakeEvent("cancelled")
        replacement = FakeEvent("replacement")

        assert await coordinator.admit_wakeup(active)
        cancelled_task = asyncio.create_task(coordinator.admit_wakeup(cancelled))
        await asyncio.sleep(0)
        cancelled_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled_task

        replacement_task = asyncio.create_task(coordinator.admit_wakeup(replacement))
        await asyncio.sleep(0)
        assert coordinator.pending_wakeup_count == 1

        coordinator.finish_active(active)
        assert await replacement_task
        assert coordinator.active_event is replacement

    asyncio.run(scenario())


def test_completion_waits_for_mechanical_interval_before_next_wakeup():
    async def scenario():
        delays = []

        async def record_sleep(delay):
            delays.append(delay)

        coordinator = make_coordinator(
            gate_seconds=1.8,
            delay=(1.0, 2.0),
            random_delay=lambda _minimum, _maximum: 1.25,
            sleep=record_sleep,
        )
        active = FakeEvent("active")
        queued = FakeEvent("queued")

        assert await coordinator.admit_wakeup(active)
        queued_task = asyncio.create_task(coordinator.admit_wakeup(queued))
        await asyncio.sleep(0)

        coordinator.finish_active(active)
        assert await queued_task
        assert delays == [1.8]

    asyncio.run(scenario())


def test_non_wakeup_does_not_enter_global_queue():
    async def scenario():
        coordinator = make_coordinator()
        event = FakeEvent("ordinary", wake=False)

        assert await coordinator.admit_wakeup(event)
        assert coordinator.active_event is None
        assert coordinator.pending_wakeup_count == 0

    asyncio.run(scenario())


def test_ttl_expiry_promotes_waiting_wakeup():
    async def scenario():
        current_time = [100.0]
        coordinator = make_coordinator(
            ttl=1.0,
            now=lambda: current_time[0],
        )
        active = FakeEvent("active")
        queued = FakeEvent("queued")

        assert await coordinator.admit_wakeup(active)
        queued_task = asyncio.create_task(coordinator.admit_wakeup(queued))
        await asyncio.sleep(0)

        current_time[0] = 102.0
        assert coordinator.pending_wakeup_count == 1
        assert await queued_task
        assert coordinator.active_event is queued
        assert active.stopped

    asyncio.run(scenario())
