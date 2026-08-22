"""Per-session wake-up admission and merge supersede tracking.

The old global FIFO queue, gates, and cooldowns are gone: different sessions
are fully parallel, and within one session AstrBot's own session lock
serializes LLM calls while this coordinator decides which events get
superseded by a merge regeneration.
"""

from __future__ import annotations

from typing import Any, Callable

from astrbot.api import logger


MAX_CANCELLED_IDS = 4096


class ReplyCoordinator:
    """Track one active LLM reply per session for supersede/merge decisions."""

    def __init__(
        self,
        *,
        event_is_wake_up: Callable[[Any], bool] | None = None,
        max_cancelled_ids: int = MAX_CANCELLED_IDS,
    ) -> None:
        self._event_is_wake_up = event_is_wake_up or (lambda _event: True)
        self._max_cancelled_ids = max_cancelled_ids
        self._active_by_session: dict[str, Any] = {}
        self._cancelled_event_ids: set[int] = set()

    @property
    def active_by_session(self) -> dict[str, Any]:
        return dict(self._active_by_session)

    async def admit_wakeup(self, event: Any) -> bool:
        """Admit a wake event, tracking it as the session's active reply."""
        if not event:
            return False
        if not self._event_is_wake_up(event):
            return True
        if id(event) in self._cancelled_event_ids:
            return False
        session = self._session_key(event)
        if session not in self._active_by_session:
            self._active_by_session[session] = event
        return True

    def is_session_busy(self, event: Any) -> bool:
        """True when another event is still the active reply of this session."""
        session = self._session_key(event)
        active = self._active_by_session.get(session)
        return active is not None and active is not event

    def active_same_sender(self, event: Any) -> bool:
        """True when the session's active reply belongs to the same sender.

        Same-sender wake-ups must supersede and re-plan; different senders are
        left for the next turn (AstrBot's session lock serializes them).
        """
        session = self._session_key(event)
        active = self._active_by_session.get(session)
        if active is None or active is event:
            return False
        try:
            return str(active.get_sender_id()) == str(event.get_sender_id())
        except Exception:
            return False

    def supersede_active_event(self, event: Any) -> bool:
        """Cancel an active event's reply so a merged follow-up can regenerate."""
        if event is None:
            return False
        session = self._session_key(event)
        if self._active_by_session.get(session) is not event:
            return False
        self._remember_cancelled(event)
        self._stop_event(event)
        self._active_by_session.pop(session, None)
        logger.info("[语言优化] 已终止旧规划，等待合并重生成")
        return True

    def is_superseded(self, event: Any) -> bool:
        """Return True when the event was superseded by a merge regeneration."""
        return event is not None and id(event) in self._cancelled_event_ids

    def finish_active(self, event: Any) -> bool:
        """Finish an active reply normally and clear the session slot."""
        if event is None:
            return False
        session = self._session_key(event)
        if self._active_by_session.get(session) is not event:
            return False
        self._active_by_session.pop(session, None)
        return True

    def discard_superseded_result(self, event: Any) -> bool:
        """Drop a superseded event's late result so it is never sent."""
        if not self.is_superseded(event):
            return False
        result = getattr(event, "get_result", lambda: None)()
        chain = getattr(result, "chain", None) if result is not None else None
        if chain is not None:
            result.chain = []
        self._stop_event(event)
        self._cancelled_event_ids.discard(id(event))
        return True

    def _remember_cancelled(self, event: Any) -> None:
        """Track an event whose late result must be discarded, bounded to avoid leaks."""
        self._cancelled_event_ids.add(id(event))
        while len(self._cancelled_event_ids) > self._max_cancelled_ids:
            self._cancelled_event_ids.pop()

    @staticmethod
    def _session_key(event: Any) -> str:
        return str(getattr(event, "unified_msg_origin", "") or "__unified_default__")

    @staticmethod
    def _stop_event(event: Any) -> None:
        stopper = getattr(event, "stop_event", None)
        if callable(stopper):
            stopper()


__all__ = ["MAX_CANCELLED_IDS", "ReplyCoordinator"]
