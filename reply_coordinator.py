"""Coordinate reply locks, wake-up gates, and reply completion."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Callable

from astrbot.api import logger


@dataclass
class GateState:
    owner_event: Any | None
    created_at: float = 0.0
    cooldown_until: float = 0.0
    superseded_by_user: bool = False
    cancel_requested: bool = False


@dataclass
class ReplySession:
    origin: str
    owner_event: Any | None
    reply_lock: asyncio.Lock
    superseded_by_user: bool = False
    cancel_requested: bool = False
    followup_task: asyncio.Task | None = None


class ReplyCoordinator:
    """Own the state transitions shared by all reply-related hooks."""

    def __init__(
        self,
        *,
        get_gate_seconds: Callable[[], float],
        get_gate_ttl_seconds: Callable[[], float],
        event_is_wake_up: Callable[[Any], bool],
        is_proactive_event: Callable[[Any | None], bool],
        schedule_cancel: Callable[[Any], None] | None = None,
        proactive_identity: Callable[[Any | None], str] | None = None,
        gates: dict[str, GateState] | None = None,
        reply_locks: dict[str, asyncio.Lock] | None = None,
        max_gate_states: int = 4096,
        now: Callable[[], float] | None = None,
    ):
        self._get_gate_seconds = get_gate_seconds
        self._get_gate_ttl_seconds = get_gate_ttl_seconds
        self._event_is_wake_up = event_is_wake_up
        self._is_proactive_event = is_proactive_event
        self._schedule_cancel = schedule_cancel or (lambda _event: None)
        self._proactive_identity = proactive_identity or self._default_proactive_identity
        self._max_gate_states = max_gate_states
        self._now = now or time.monotonic
        self.gates = gates if gates is not None else {}
        self.reply_locks = reply_locks if reply_locks is not None else {}
        self.pending_sends: dict[str, tuple[str, asyncio.Lock, Any]] = {}
        self.pending_send: tuple[str, asyncio.Lock, Any] | None = None
        self._superseded_event_ids: dict[int, Any] = {}

    def claim_wakeup(self, event: Any) -> bool:
        if not event or not self._event_is_wake_up(event):
            return True

        self._cleanup_expired()
        if not self._is_proactive_event(event):
            self.mark_user_priority(event)
            return True

        key = self._gate_key(event)
        if self._gate_is_active(event):
            state = self.gates.get(key)
            if state is None or not self.events_correlate(state.owner_event, event):
                if state is not None and self._is_new_proactive_attempt(
                    state.owner_event,
                    event,
                ):
                    self._supersede_owner(state.owner_event, replace_gate=True)
                    self.gates[key] = GateState(
                        owner_event=event,
                        created_at=self._now(),
                    )
                    return True
                logger.info("[语言优化] 丢弃唤醒：同一来源仍有请求在途或处于冷却 origin=%s", key)
                self._stop_event(event)
                return False
            return True

        if len(self.gates) >= self._max_gate_states:
            self._stop_event(event)
            return False
        self.gates[key] = GateState(owner_event=event, created_at=self._now())
        return True

    def mark_user_priority(self, event: Any) -> bool:
        """Mark an older proactive gate stale without blocking the user event."""
        key = self._gate_key(event)
        state = self.gates.get(key)
        if state is None or state.owner_event is None:
            return True
        if not self._is_proactive_event(state.owner_event):
            return True
        if self.events_correlate(state.owner_event, event):
            return True

        self._supersede_owner(state.owner_event, state)
        logger.info("[语言优化] 用户消息优先：标记同源主动请求失效 origin=%s", key)
        return True

    def discard_superseded_result(self, event: Any) -> bool:
        if not self._is_proactive_event(event):
            return False
        state = self.gates.get(self._gate_key(event))
        superseded = id(event) in self._superseded_event_ids
        if not superseded and (state is None or not state.superseded_by_user):
            return False
        if not superseded and not self.events_correlate(state.owner_event, event):
            return False

        result = getattr(event, "get_result", lambda: None)()
        chain = getattr(result, "chain", None) if result is not None else None
        if chain is not None:
            result.chain = []
        self._stop_event(event)
        if not superseded:
            self._release_gate(event, apply_cooldown=False)
        self._superseded_event_ids.pop(id(event), None)
        logger.info("[语言优化] 已丢弃被用户消息取代的主动回复 origin=%s", self._gate_key(event))
        return True

    async def acquire_reply(self, event: Any) -> ReplySession:
        origin = self._gate_key(event)
        reply_lock = self.reply_locks.setdefault(origin, asyncio.Lock())
        await reply_lock.acquire()
        state = self.gates.get(origin)
        return ReplySession(
            origin=origin,
            owner_event=event,
            reply_lock=reply_lock,
            superseded_by_user=bool(state and state.superseded_by_user),
            cancel_requested=bool(state and state.cancel_requested),
        )

    def register_followup(self, session: ReplySession, task: asyncio.Task) -> None:
        session.followup_task = task

    def register_pending_send(self, session: ReplySession) -> None:
        pending = (session.origin, session.reply_lock, session.owner_event)
        self.pending_sends[session.origin] = pending
        self.pending_send = pending

    def release(self, session: ReplySession, *, apply_cooldown: bool = False) -> None:
        if session.reply_lock.locked():
            session.reply_lock.release()
        if self.reply_locks.get(session.origin) is session.reply_lock:
            self.reply_locks.pop(session.origin, None)
        if session.owner_event is not None:
            self._release_gate(session.owner_event, apply_cooldown=apply_cooldown)

    def release_after_send(self, event: Any) -> None:
        origin = self._gate_key(event)
        pending = self.pending_sends.get(origin)
        if pending is None and isinstance(self.pending_send, tuple):
            pending = self.pending_send
        if pending is not None and self.events_correlate(pending[2], event):
            self.pending_sends.pop(pending[0], None)
            if self.pending_send is pending:
                self.pending_send = None
            session = ReplySession(pending[0], pending[2], pending[1])
            self.release(session, apply_cooldown=True)
            return

        if origin not in self.reply_locks:
            self._release_gate(event)

    def gate_is_active(self, event: Any | None = None) -> bool:
        self._cleanup_expired()
        if event is None:
            return bool(self.gates)
        return self._gate_key(event) in self.gates

    def release_gate(self, owner_event: Any, apply_cooldown: bool = False) -> None:
        self._release_gate(owner_event, apply_cooldown=apply_cooldown)

    def _release_gate(self, owner_event: Any, *, apply_cooldown: bool = False) -> None:
        key = self._gate_key(owner_event)
        state = self.gates.get(key)
        if state is None:
            return
        if state.owner_event is None or not self.events_correlate(state.owner_event, owner_event):
            return
        if apply_cooldown:
            cooldown = self._get_gate_seconds()
            if cooldown > 0:
                state.owner_event = None
                state.cooldown_until = self._now() + cooldown
                return
        self.gates.pop(key, None)

    def _cleanup_expired(self) -> None:
        now = self._now()
        ttl = self._get_gate_ttl_seconds()
        expired = [
            key
            for key, state in self.gates.items()
            if (state.owner_event is None and state.cooldown_until <= now)
            or (
                ttl > 0
                and state.owner_event is not None
                and state.created_at > 0
                and now - state.created_at > ttl
            )
        ]
        for key in expired:
            state = self.gates.pop(key, None)
            if state is not None and state.owner_event is not None:
                logger.warning("[语言优化] 唤醒闸门超时自动释放 origin=%s", key)
        if len(self._superseded_event_ids) > self._max_gate_states:
            stale_ids = list(self._superseded_event_ids)[: len(self._superseded_event_ids) - self._max_gate_states]
            for stale_id in stale_ids:
                self._superseded_event_ids.pop(stale_id, None)

    def _supersede_owner(
        self,
        owner_event: Any,
        state: GateState | None = None,
        *,
        replace_gate: bool = False,
    ) -> None:
        if owner_event is None:
            return
        if state is not None:
            state.superseded_by_user = True
        if replace_gate:
            self._superseded_event_ids[id(owner_event)] = owner_event
        if state is None or not state.cancel_requested:
            if state is not None:
                state.cancel_requested = True
            self._schedule_cancel(owner_event)

    def _is_new_proactive_attempt(self, owner: Any | None, candidate: Any) -> bool:
        owner_identity = self._proactive_identity(owner)
        candidate_identity = self._proactive_identity(candidate)
        return bool(owner_identity and candidate_identity and owner_identity != candidate_identity)

    @staticmethod
    def _default_proactive_identity(event: Any | None) -> str:
        if event is None:
            return ""
        for field in (
            "_private_companion_proactive_chat_attempt_id",
            "_private_companion_proactive_chat_token",
        ):
            value = str(getattr(event, field, "") or "").strip()
            if value:
                return f"{field}:{value}"
        return ""

    def _gate_is_active(self, event: Any) -> bool:
        self._cleanup_expired()
        return self._gate_key(event) in self.gates

    @staticmethod
    def _gate_key(event: Any) -> str:
        origin = str(getattr(event, "unified_msg_origin", "") or "")
        return origin or "__unified_default__"

    @staticmethod
    def _stop_event(event: Any) -> None:
        stopper = getattr(event, "stop_event", None)
        if callable(stopper):
            stopper()

    @staticmethod
    def _event_correlation_ids(event: Any | None) -> set[str]:
        if event is None:
            return set()
        identifiers: set[str] = set()
        for field in ("request_id", "event_id", "message_id", "trace_id"):
            value = getattr(event, field, None)
            if value is not None and str(value).strip():
                identifiers.add(f"{field}:{value}")
        return identifiers

    @classmethod
    def events_correlate(cls, owner: Any | None, candidate: Any | None) -> bool:
        if owner is candidate and owner is not None:
            return True
        return bool(cls._event_correlation_ids(owner) & cls._event_correlation_ids(candidate))
