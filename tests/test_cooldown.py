import asyncio

from main import LanguageLogicOptimizer


class FakeEvent:
    def __init__(self, wake=True, origin=""):
        self.wake = wake
        self.unified_msg_origin = origin
        self.stopped = False

    def is_wake_up(self):
        return self.wake

    def stop_event(self):
        self.stopped = True


def make_optimizer(cooldown_seconds=0):
    optimizer = object.__new__(LanguageLogicOptimizer)
    optimizer.config = {"cooldown_seconds": cooldown_seconds}
    optimizer.context = None
    optimizer._gates = {}
    optimizer._pending_send = None
    optimizer._pending_sends = {}
    optimizer._reply_locks = {}
    return optimizer


def test_cooldown_config_is_non_negative():
    optimizer = make_optimizer(-5)

    assert optimizer._get_cooldown_seconds() == 0.0


def test_gate_seconds_defaults_to_zero_and_overrides_legacy_setting():
    optimizer = make_optimizer()
    assert optimizer._get_gate_seconds() == 0.0

    optimizer.config = {"gate_seconds": 2, "cooldown_seconds": 9}
    assert optimizer._get_gate_seconds() == 2.0


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
    incoming = FakeEvent()

    async def run():
        await optimizer.on_waiting_llm_request(incoming)

    asyncio.run(run())

    assert not incoming.stopped
    assert optimizer._gates["__unified_default__"].owner_event is incoming


def test_wake_ups_in_different_origins_do_not_block_each_other():
    optimizer = make_optimizer()
    first = FakeEvent(origin="group:1")
    second = FakeEvent(origin="group:2")

    async def run():
        await optimizer.on_waiting_llm_request(first)
        await optimizer.on_waiting_llm_request(second)

    asyncio.run(run())

    assert not first.stopped
    assert not second.stopped


def test_cooldown_starts_after_the_actual_message_is_sent():
    optimizer = make_optimizer(3)
    owner = FakeEvent()
    lock = asyncio.Lock()

    async def run():
        await lock.acquire()
        await optimizer.on_waiting_llm_request(owner)
        pending = ("group:1", lock, owner)
        optimizer._pending_send = pending
        optimizer._pending_sends["group:1"] = pending
        await optimizer.after_message_sent(owner)

    asyncio.run(run())

    assert not lock.locked()
    assert optimizer._gate_is_active()


def test_gate_zero_releases_when_send_callback_uses_equivalent_event():
    optimizer = make_optimizer()
    owner = FakeEvent(origin="group:1")
    callback_event = FakeEvent(origin="group:1")
    lock = asyncio.Lock()

    async def run():
        await optimizer.on_waiting_llm_request(owner)
        await lock.acquire()
        optimizer._reply_locks["group:1"] = lock
        optimizer._finish_reply("group:1", lock, callback_event)

    asyncio.run(run())

    assert not lock.locked()
    assert not optimizer._gate_is_active(callback_event)
