"""Per-session wake-up admission and active-reply tracking.

No merge supersede machinery: an in-flight reply is never interrupted; the
coordinator only admits wake-ups, reports whether the session is busy, and
clears the active slot after a reply is sent. Different sessions stay fully
parallel; within one session AstrBot's session lock serializes LLM calls.
"""

from __future__ import annotations

from typing import Any, Callable


class ReplyCoordinator:
    """Track one active LLM reply per session for busy/admission decisions."""

    def __init__(
        self,
        *,
        event_is_wake_up: Callable[[Any], bool] | None = None,
    ) -> None:
        self._event_is_wake_up = event_is_wake_up or (lambda _event: True)
        self._active_by_session: dict[str, Any] = {}

    @property
    def active_by_session(self) -> dict[str, Any]:
        return dict(self._active_by_session)

    async def admit_wakeup(self, event: Any) -> bool:
        """Admit a wake event, tracking it as the session's active reply."""
        if not event:
            return False
        if self._event_is_wake_up(event):
            if self._is_stopped(event):
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

    def finish_active(self, event: Any) -> bool:
        """Finish an active reply normally and clear the session slot."""
        if event is None:
            return False
        session = self._session_key(event)
        if self._active_by_session.get(session) is not event:
            return False
        self._active_by_session.pop(session, None)
        return True

    @staticmethod
    def _session_key(event: Any) -> str:
        return str(getattr(event, "unified_msg_origin", "") or "__unified_default__")

    @staticmethod
    def _is_stopped(event: Any) -> bool:
        checker = getattr(event, "is_stopped", None)
        if callable(checker):
            try:
                return bool(checker())
            except Exception:
                return False
        return False


__all__ = ["ReplyCoordinator"]
