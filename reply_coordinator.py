"""Coordinate global wake-up admission, reply locks, and completion."""

from __future__ import annotations

import asyncio
import random
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from astrbot.api import logger


MAX_PENDING_WAKEUPS = 3


@dataclass
class GateState:
    owner_event: Any | None
    created_at: float = 0.0


@dataclass
class ReplySession:
    origin: str
    owner_event: Any | None
    reply_lock: asyncio.Lock
    gate_tracked: bool = False
    followup_task: asyncio.Task | None = None


@dataclass
class _WakeupTicket:
    event: Any
    ready: asyncio.Event
    admitted: bool = False


class ReplyCoordinator:
    """Own global wake-up admission and the reply lifecycle."""

    def __init__(
        self,
        *,
        get_gate_seconds: Callable[[], float],
        get_gate_ttl_seconds: Callable[[], float],
        event_is_wake_up: Callable[[Any], bool],
        notify_dropped: Callable[[Any], None] | None = None,
        get_wakeup_interval: Callable[[], tuple[float, float]] | None = None,
        gates: dict[str, GateState] | None = None,
        reply_locks: dict[str, asyncio.Lock] | None = None,
        max_gate_states: int = 4096,
        max_pending_wakeups: int = MAX_PENDING_WAKEUPS,
        now: Callable[[], float] | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        ttl_sleep: Callable[[float], Awaitable[None]] | None = None,
        random_delay: Callable[[float, float], float] | None = None,
    ):
        self._get_gate_seconds = get_gate_seconds
        self._get_gate_ttl_seconds = get_gate_ttl_seconds
        self._event_is_wake_up = event_is_wake_up
        self._notify_dropped = notify_dropped or (lambda _event: None)
        self._get_wakeup_interval = get_wakeup_interval or (lambda: (1.0, 2.0))
        self._max_gate_states = max_gate_states
        self._max_pending_wakeups = max(0, int(max_pending_wakeups))
        self._now = now or time.monotonic
        self._sleep = sleep or asyncio.sleep
        self._ttl_sleep = ttl_sleep or asyncio.sleep
        self._random_delay = random_delay or random.uniform

        self.gates = gates if gates is not None else {}
        self.reply_locks = reply_locks if reply_locks is not None else {}
        self.pending_sends: dict[str, tuple[str, asyncio.Lock, Any]] = {}
        self.pending_send: tuple[str, asyncio.Lock, Any] | None = None

        self._active_event: Any | None = None
        self._active_started_at = 0.0
        self._active_ttl_task: asyncio.Task | None = None
        self._pending_wakeups: deque[_WakeupTicket] = deque()
        self._wakeup_tickets: dict[int, _WakeupTicket] = {}
        self._promotion_task: asyncio.Task | None = None
        self._cancelled_event_ids: set[int] = set()
        self._queue_full_notified = False

    @property
    def active_event(self) -> Any | None:
        self._cleanup_expired()
        return self._active_event

    @property
    def pending_wakeup_count(self) -> int:
        self._cleanup_expired()
        return len(self._pending_wakeups)

    async def admit_wakeup(self, event: Any) -> bool:
        """Wait for a global FIFO slot before allowing AstrBot to continue."""
        if not event or not self._event_is_wake_up(event):
            return True

        self._cleanup_expired()
        if self._event_matches(self._active_event, event):
            return True
        if id(event) in self._cancelled_event_ids:
            return False

        existing = self._find_ticket(event)
        if existing is not None:
            return await self._wait_for_ticket(existing)

        if (
            self._active_event is None
            and not self._pending_wakeups
            and self._promotion_task is None
        ):
            self._activate(event)
            return True

        if len(self._pending_wakeups) >= self._max_pending_wakeups:
            self._stop_event(event)
            logger.info(
                "[语言优化] 全局唤醒队列已满，丢弃新唤醒 pending=%d",
                len(self._pending_wakeups),
            )
            if not self._queue_full_notified:
                self._queue_full_notified = True
                try:
                    self._notify_dropped(event)
                except Exception:
                    logger.debug(
                        "[语言优化] 队列满提示发送失败",
                        exc_info=True,
                    )
            return False

        ticket = _WakeupTicket(event=event, ready=asyncio.Event())
        self._pending_wakeups.append(ticket)
        self._wakeup_tickets[id(event)] = ticket
        logger.info(
            "[语言优化] 唤醒进入全局队列 pending=%d/%d",
            len(self._pending_wakeups),
            self._max_pending_wakeups,
        )
        return await self._wait_for_ticket(ticket)

    async def _wait_for_ticket(self, ticket: _WakeupTicket) -> bool:
        try:
            await ticket.ready.wait()
            return ticket.admitted
        except asyncio.CancelledError:
            if not ticket.admitted:
                self._remove_ticket(ticket)
                logger.info("[语言优化] 排队唤醒等待被取消，已释放队列位置")
            raise

    def finish_active(self, event: Any | None, *, apply_cooldown: bool = False) -> bool:
        """Finish an active reply once, then schedule the next FIFO ticket."""
        self._cleanup_expired()
        return self._finish_active(event, apply_cooldown=apply_cooldown)

    def _finish_active(self, event: Any | None, *, apply_cooldown: bool = False) -> bool:
        if event is None or not self._event_matches(self._active_event, event):
            return False

        active = self._active_event
        self._active_event = None
        self._active_started_at = 0.0
        self._cancel_active_ttl_task()
        self._release_legacy_gate(active)

        delay_min, delay_max = self._get_wakeup_interval()
        delay_min = max(0.0, float(delay_min))
        delay_max = max(delay_min, float(delay_max))
        mechanical_delay = self._random_delay(delay_min, delay_max)
        try:
            mechanical_delay = max(0.0, float(mechanical_delay))
        except (TypeError, ValueError):
            mechanical_delay = delay_min
        try:
            gate_seconds = max(0.0, float(self._get_gate_seconds()))
        except (TypeError, ValueError):
            gate_seconds = 0.0
        delay = max(mechanical_delay, gate_seconds) if apply_cooldown else mechanical_delay
        self._schedule_promotion(delay)
        logger.info(
            "[语言优化] active 回复完成，准备推进全局队列 delay=%.2fs pending=%d",
            delay,
            len(self._pending_wakeups),
        )
        return True

    def _schedule_promotion(self, delay: float) -> None:
        existing = self._promotion_task
        if existing is not None and not existing.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._promotion_task = None
            return
        try:
            self._promotion_task = loop.create_task(
                self._promote_next_after_delay(delay)
            )
            self._promotion_task.add_done_callback(self._clear_promotion_task)
        except RuntimeError:
            self._promotion_task = None

    async def _promote_next_after_delay(self, delay: float) -> None:
        await self._sleep(delay)
        while self._pending_wakeups and self._active_event is None:
            ticket = self._pending_wakeups.popleft()
            self._wakeup_tickets.pop(id(ticket.event), None)
            self._queue_full_notified = False
            if id(ticket.event) in self._cancelled_event_ids:
                continue
            self._activate(ticket.event, ticket=ticket)
            return

    def _clear_promotion_task(self, task: asyncio.Task) -> None:
        if self._promotion_task is task:
            self._promotion_task = None
        try:
            task.exception()
        except (asyncio.CancelledError, Exception):
            return

    def _activate(self, event: Any, ticket: _WakeupTicket | None = None) -> None:
        if self._active_event is not None:
            return
        self._active_event = event
        self._active_started_at = self._now()
        self._cancelled_event_ids.discard(id(event))
        self.gates[self._gate_key(event)] = GateState(
            owner_event=event,
            created_at=self._active_started_at,
        )
        self._start_active_ttl(event)
        if ticket is not None:
            ticket.admitted = True
            ticket.ready.set()

    def _start_active_ttl(self, event: Any) -> None:
        self._cancel_active_ttl_task()
        try:
            ttl = float(self._get_gate_ttl_seconds())
        except (TypeError, ValueError):
            ttl = 0.0
        if ttl <= 0:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._active_ttl_task = None
            return
        try:
            self._active_ttl_task = loop.create_task(
                self._expire_active_after(event, ttl)
            )
        except RuntimeError:
            self._active_ttl_task = None

    async def _expire_active_after(self, event: Any, ttl: float) -> None:
        try:
            await self._ttl_sleep(ttl)
        except asyncio.CancelledError:
            return
        if self._event_matches(self._active_event, event):
            self._remember_cancelled(event)
            self._stop_event(event)
            logger.warning("[语言优化] active 回复超时，推进全局唤醒队列")
            self._finish_active(event)

    def _cancel_active_ttl_task(self) -> None:
        task = self._active_ttl_task
        self._active_ttl_task = None
        if task is None or task.done():
            return
        if task is not asyncio.current_task():
            task.cancel()

    def _find_ticket(self, event: Any) -> _WakeupTicket | None:
        ticket = self._wakeup_tickets.get(id(event))
        if ticket is not None:
            return ticket
        for candidate in self._pending_wakeups:
            if self._event_matches(candidate.event, event):
                return candidate
        return None

    def _remove_ticket(self, ticket: _WakeupTicket) -> None:
        try:
            self._pending_wakeups.remove(ticket)
        except ValueError:
            pass
        self._wakeup_tickets.pop(id(ticket.event), None)
        self._queue_full_notified = False

    def _remember_cancelled(self, event: Any) -> None:
        """Track an event whose late result must be discarded, bounded to avoid leaks."""
        self._cancelled_event_ids.add(id(event))
        while len(self._cancelled_event_ids) > self._max_gate_states:
            self._cancelled_event_ids.pop()

    def discard_superseded_result(self, event: Any) -> bool:
        self._cleanup_expired()
        if id(event) not in self._cancelled_event_ids:
            return False
        result = getattr(event, "get_result", lambda: None)()
        chain = getattr(result, "chain", None) if result is not None else None
        if chain is not None:
            result.chain = []
        self._stop_event(event)
        self._cancelled_event_ids.discard(id(event))
        return True

    def supersede_active_event(self, event: Any) -> bool:
        """Cancel an active event's reply and release its gate for a merged follow-up."""
        self._cleanup_expired()
        if event is None or not self._event_matches(self._active_event, event):
            return False
        self._remember_cancelled(event)
        self._stop_event(event)
        self._finish_active(event, apply_cooldown=False)
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
            gate_tracked=state is not None,
        )

    def register_followup(self, session: ReplySession, task: asyncio.Task) -> None:
        session.followup_task = task

    def session_cancelled(self, session: ReplySession) -> bool:
        self._cleanup_expired()
        if id(session.owner_event) in self._cancelled_event_ids:
            return True
        if self._active_event is None:
            return session.gate_tracked
        return not self._event_matches(self._active_event, session.owner_event)

    def register_pending_send(self, session: ReplySession) -> None:
        pending = (session.origin, session.reply_lock, session.owner_event)
        self.pending_sends[session.origin] = pending
        self.pending_send = pending

    def release(self, session: ReplySession, *, apply_cooldown: bool = False) -> None:
        if session.reply_lock.locked():
            session.reply_lock.release()
        if self.reply_locks.get(session.origin) is session.reply_lock:
            self.reply_locks.pop(session.origin, None)
        self.finish_active(session.owner_event, apply_cooldown=apply_cooldown)

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
            self.finish_active(event)

    def release_gate(self, owner_event: Any, apply_cooldown: bool = False) -> None:
        self.finish_active(owner_event, apply_cooldown=apply_cooldown)

    def _release_gate(self, owner_event: Any, *, apply_cooldown: bool = False) -> None:
        self.finish_active(owner_event, apply_cooldown=apply_cooldown)

    def _release_legacy_gate(self, owner_event: Any | None) -> None:
        if owner_event is None:
            return
        key = self._gate_key(owner_event)
        state = self.gates.get(key)
        if state is not None and self._event_matches(state.owner_event, owner_event):
            self.gates.pop(key, None)

    def _cleanup_expired(self) -> None:
        now = self._now()
        try:
            ttl = float(self._get_gate_ttl_seconds())
        except (TypeError, ValueError):
            ttl = 0.0
        if (
            self._active_event is not None
            and ttl > 0
            and self._active_started_at > 0
            and now - self._active_started_at > ttl
        ):
            expired = self._active_event
            self._remember_cancelled(expired)
            self._stop_event(expired)
            self._finish_active(expired)

    @staticmethod
    def _gate_key(event: Any) -> str:
        origin = str(getattr(event, "unified_msg_origin", "") or "")
        return origin or "__unified_default__"

    @staticmethod
    def _stop_event(event: Any) -> None:
        stopper = getattr(event, "stop_event", None)
        if callable(stopper):
            stopper()

    @classmethod
    def _event_matches(cls, owner: Any | None, candidate: Any | None) -> bool:
        return cls.events_correlate(owner, candidate)

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
