import asyncio
import logging

from _astrbot_plugin_filter_test.message_dispatcher import (
    DispatchPolicy,
    MessageDispatcher,
)
from _astrbot_plugin_filter_test.reply_coordinator import ReplyCoordinator


class FakeEvent:
    unified_msg_origin = "group:1"

    def is_wake_up(self):
        return True


class FakeContext:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    async def send_message(self, origin, chain):
        if self.fail:
            raise RuntimeError("send failed")
        self.sent.append((origin, chain.chain[0].text))


def make_coordinator():
    return ReplyCoordinator(
        get_gate_seconds=lambda: 0,
        get_gate_ttl_seconds=lambda: 300,
        get_wakeup_interval=lambda: (0, 0),
        event_is_wake_up=lambda event: True,
    )


def test_dispatcher_caps_followups_and_releases_session(monkeypatch):
    context = FakeContext()
    coordinator = make_coordinator()
    event = FakeEvent()

    async def no_delay(_seconds):
        return None

    monkeypatch.setattr("_astrbot_plugin_filter_test.message_dispatcher.asyncio.sleep", no_delay)

    async def scenario():
        await coordinator.admit_wakeup(event)
        session = await coordinator.acquire_reply(event)
        dispatcher = MessageDispatcher(context, coordinator)
        await dispatcher.send_followups(
            event.unified_msg_origin,
            [f"part-{index}" for index in range(10)],
            policy=DispatchPolicy(0, 0, max_followups=4),
            session=session,
        )
        return session.reply_lock.locked()

    assert not asyncio.run(scenario())
    assert [text for _, text in context.sent] == [
        "part-0",
        "part-1",
        "part-2",
        "part-3",
    ]


def test_dispatcher_releases_session_when_send_fails(monkeypatch):
    context = FakeContext(fail=True)
    coordinator = make_coordinator()
    event = FakeEvent()

    async def no_delay(_seconds):
        return None

    monkeypatch.setattr("_astrbot_plugin_filter_test.message_dispatcher.asyncio.sleep", no_delay)

    async def scenario():
        await coordinator.admit_wakeup(event)
        session = await coordinator.acquire_reply(event)
        dispatcher = MessageDispatcher(context, coordinator)
        await dispatcher.send_followups(
            event.unified_msg_origin,
            ["part-0"],
            policy=DispatchPolicy(0, 0),
            session=session,
        )
        return session.reply_lock.locked(), coordinator.gates

    locked, gates = asyncio.run(scenario())
    assert not locked
    assert not gates


def test_dispatcher_sends_all_followups_through_process_text(monkeypatch):
    context = FakeContext()
    coordinator = make_coordinator()
    owner = FakeEvent()

    async def no_delay(_seconds):
        return None

    monkeypatch.setattr(
        "_astrbot_plugin_filter_test.message_dispatcher.asyncio.sleep",
        no_delay,
    )

    async def scenario():
        await coordinator.admit_wakeup(owner)
        session = await coordinator.acquire_reply(owner)
        dispatcher = MessageDispatcher(context, coordinator)
        calls = 0

        async def process(text):
            nonlocal calls
            calls += 1
            return text

        await dispatcher.send_followups(
            owner.unified_msg_origin,
            ["part-0", "part-1"],
            policy=DispatchPolicy(0, 0),
            session=session,
            process_text=process,
        )

    asyncio.run(scenario())
    assert [text for _, text in context.sent] == ["part-0", "part-1"]


def test_dispatcher_logs_processing_failure_and_continues(caplog, monkeypatch):
    context = FakeContext()
    coordinator = make_coordinator()
    event = FakeEvent()

    async def no_delay(_seconds):
        return None

    monkeypatch.setattr(
        "_astrbot_plugin_filter_test.message_dispatcher.asyncio.sleep",
        no_delay,
    )

    async def scenario():
        await coordinator.admit_wakeup(event)
        session = await coordinator.acquire_reply(event)
        dispatcher = MessageDispatcher(context, coordinator)

        async def process(_text):
            raise RuntimeError("boom")

        await dispatcher.send_followups(
            event.unified_msg_origin,
            ["part-0", "part-1"],
            policy=DispatchPolicy(0, 0),
            session=session,
            process_text=process,
        )

    with caplog.at_level(logging.WARNING, logger="astrbot-test"):
        asyncio.run(scenario())

    assert "后续段处理失败" in caplog.text
    assert [text for _, text in context.sent] == []
