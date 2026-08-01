import asyncio
import time
from unittest import mock

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


def test_gate_ttl_default_is_positive_and_override_applies():
    optimizer = make_optimizer()
    assert optimizer._get_gate_ttl_seconds() == main.GATE_TTL_DEFAULT

    optimizer.config["gate_ttl_seconds"] = 60
    assert optimizer._get_gate_ttl_seconds() == 60.0

    optimizer.config["gate_ttl_seconds"] = 0
    assert optimizer._get_gate_ttl_seconds() == 0.0


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
        return optimizer._gate_is_active()

    gate_is_active = asyncio.run(run())

    assert incoming.stopped
    assert gate_is_active


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


def test_recent_owner_gate_still_discards_new_wake_up():
    optimizer = make_optimizer()
    owner = FakeEvent(origin="group:1", request_id="request-1")
    incoming = FakeEvent(origin="group:1", request_id="request-2")

    async def run():
        await optimizer.on_waiting_llm_request(owner)
        await optimizer.on_waiting_llm_request(incoming)
        return incoming.stopped

    assert asyncio.run(run())


def test_stale_owner_gate_is_expired_and_new_wake_up_accepted():
    optimizer = make_optimizer()
    owner = FakeEvent(origin="group:1", request_id="request-1")
    incoming = FakeEvent(origin="group:1", request_id="request-2")

    async def run():
        await optimizer.on_waiting_llm_request(owner)
        future = time.monotonic() + main.GATE_TTL_DEFAULT * 2
        with mock.patch("main.time.monotonic", return_value=future):
            await optimizer.on_waiting_llm_request(incoming)
        return incoming.stopped, optimizer._gates.get("group:1") is not None

    stopped, has_gate = asyncio.run(run())

    assert not stopped
    assert has_gate


def test_gate_ttl_zero_disables_stale_expiry():
    optimizer = make_optimizer()
    optimizer.config["gate_ttl_seconds"] = 0
    owner = FakeEvent(origin="group:1", request_id="request-1")
    incoming = FakeEvent(origin="group:1", request_id="request-2")

    async def run():
        await optimizer.on_waiting_llm_request(owner)
        future = time.monotonic() + main.GATE_TTL_DEFAULT * 2
        with mock.patch("main.time.monotonic", return_value=future):
            await optimizer.on_waiting_llm_request(incoming)
        return incoming.stopped

    assert asyncio.run(run())


def test_cooldown_starts_after_the_actual_message_is_sent():
    optimizer = make_optimizer(3)
    owner = FakeEvent()

    async def run():
        lock = asyncio.Lock()
        await lock.acquire()
        await optimizer.on_waiting_llm_request(owner)
        pending = ("group:1", lock, owner)
        optimizer._pending_send = pending
        optimizer._pending_sends["group:1"] = pending
        await optimizer.after_message_sent(owner)
        return lock.locked(), optimizer._gate_is_active()

    lock_is_locked, gate_is_active = asyncio.run(run())

    assert not lock_is_locked
    assert gate_is_active


def test_gate_zero_releases_when_send_callback_uses_equivalent_event():
    optimizer = make_optimizer()
    owner = FakeEvent(origin="group:1", request_id="request-1")
    callback_event = FakeEvent(origin="group:1", request_id="request-1")

    async def run():
        lock = asyncio.Lock()
        await optimizer.on_waiting_llm_request(owner)
        await lock.acquire()
        optimizer._reply_locks["group:1"] = lock
        optimizer._finish_reply("group:1", lock, callback_event)
        return lock.locked(), optimizer._gate_is_active(callback_event)

    lock_is_locked, gate_is_active = asyncio.run(run())

    assert not lock_is_locked
    assert not gate_is_active


def test_after_message_sent_releases_gate_when_decorator_was_skipped():
    optimizer = make_optimizer()
    owner = FakeEvent(origin="group:1", request_id="request-1")
    callback_event = FakeEvent(origin="group:1", request_id="request-1")
    incoming = FakeEvent(origin="group:1")

    async def run():
        await optimizer.on_waiting_llm_request(owner)
        await optimizer.after_message_sent(callback_event)
        await optimizer.on_waiting_llm_request(incoming)

    asyncio.run(run())

    assert not incoming.stopped


def test_unrelated_same_origin_callback_does_not_release_gate():
    optimizer = make_optimizer()
    owner = FakeEvent(origin="group:1", request_id="request-1")
    unrelated = FakeEvent(origin="group:1", request_id="request-2")
    incoming = FakeEvent(origin="group:1", request_id="request-3")

    async def run():
        await optimizer.on_waiting_llm_request(owner)
        await optimizer.after_message_sent(unrelated)
        await optimizer.on_waiting_llm_request(incoming)

    asyncio.run(run())

    assert incoming.stopped
