import asyncio

from main import LanguageLogicOptimizer


class FakeEvent:
    def __init__(self, wake=True):
        self.wake = wake
        self.stopped = False

    def is_wake_up(self):
        return self.wake

    def stop_event(self):
        self.stopped = True


def make_optimizer(cooldown_seconds=0):
    optimizer = object.__new__(LanguageLogicOptimizer)
    optimizer.config = {"cooldown_seconds": cooldown_seconds}
    optimizer.context = None
    optimizer._response_in_progress = False
    optimizer._gate_owner_event = None
    optimizer._cooldown_until = 0.0
    optimizer._pending_send = None
    optimizer._reply_locks = {}
    return optimizer


def test_cooldown_config_is_non_negative():
    optimizer = make_optimizer(-5)

    assert optimizer._get_cooldown_seconds() == 0.0


def test_new_wake_up_is_discarded_while_reply_is_in_progress():
    optimizer = make_optimizer()
    first = FakeEvent()
    second = FakeEvent()

    async def run():
        await optimizer.on_waiting_llm_request(first)
        await optimizer.on_llm_request(second, None)

    asyncio.run(run())

    assert not first.stopped
    assert second.stopped


def test_new_wake_up_is_discarded_during_cooldown():
    optimizer = make_optimizer(3)
    owner = FakeEvent()
    incoming = FakeEvent()

    async def run():
        await optimizer.on_waiting_llm_request(owner)
        optimizer._release_gate(owner, apply_cooldown=True)
        await optimizer.on_llm_request(incoming, None)

    asyncio.run(run())

    assert incoming.stopped
    assert optimizer._gate_is_active()


def test_wake_up_is_accepted_after_cooldown_expires():
    optimizer = make_optimizer(3)
    optimizer._cooldown_until = -1.0
    incoming = FakeEvent()

    async def run():
        await optimizer.on_waiting_llm_request(incoming)

    asyncio.run(run())

    assert not incoming.stopped
    assert optimizer._gate_owner_event is incoming


def test_cooldown_starts_after_the_actual_message_is_sent():
    optimizer = make_optimizer(3)
    owner = FakeEvent()
    lock = asyncio.Lock()

    async def run():
        await lock.acquire()
        await optimizer.on_waiting_llm_request(owner)
        optimizer._pending_send = ("group:1", lock, owner)
        await optimizer.after_message_sent(owner)

    asyncio.run(run())

    assert not lock.locked()
    assert optimizer._gate_is_active()
