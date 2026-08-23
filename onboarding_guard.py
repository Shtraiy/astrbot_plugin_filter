"""New-group onboarding strict-mode guard.

Keeps the content guard strict for a bounded window (time or message count)
after a group first speaks, so new groups are protected before they are known
to be safe. The guard only tracks group events and evicts expired states.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any, Callable

from .content_guard import is_group_origin


MAX_ONBOARDING_STATES = 4096


@dataclass
class _OnboardingState:
    started_at: float
    message_count: int = 0


class OnboardingGuard:
    """Track recent messages in new groups to enable strict content guard."""

    def __init__(
        self,
        *,
        get_config: Callable[[str, Any], Any] | None = None,
        now: Callable[[], float] | None = None,
    ) -> None:
        self._get_config = get_config or (lambda _key, default: default)
        self._now = now or time.monotonic
        self._states: dict[str, _OnboardingState] = {}

    def touch(self, event: Any) -> bool:
        """Record a group message and report whether onboarding is active."""
        if not self._is_group_event(event):
            return False
        origin = str(getattr(event, "unified_msg_origin", "") or "")
        if not origin:
            return False
        now = self._now()
        self._prune(now)
        state = self._states.get(origin)
        if state is None:
            if len(self._states) >= MAX_ONBOARDING_STATES:
                oldest = min(
                    self._states.items(),
                    key=lambda item: item[1].started_at,
                )[0]
                self._states.pop(oldest, None)
            state = _OnboardingState(started_at=now)
            self._states[origin] = state
        state.message_count += 1
        return self._onboarding_active(origin)

    def is_active(self, event: Any) -> bool:
        """Return True when the group is still within its onboarding window."""
        if not self._is_group_event(event):
            return False
        origin = str(getattr(event, "unified_msg_origin", "") or "")
        if not origin:
            return False
        self._prune(self._now())
        return self._onboarding_active(origin)

    def _onboarding_active(self, origin: str) -> bool:
        state = self._states.get(origin)
        if state is None:
            return False
        now = self._now()
        duration = self._duration_seconds()
        message_limit = self._message_limit()
        elapsed_active = duration > 0 and now - state.started_at < duration
        count_active = message_limit > 0 and state.message_count <= message_limit
        return elapsed_active or count_active

    def _duration_seconds(self) -> float:
        value = self._get_config("onboarding_guard_minutes", 30.0)
        if not isinstance(value, (int, float)):
            return 0.0
        if not math.isfinite(value):
            return 0.0
        return max(0.0, value * 60)

    def _message_limit(self) -> int:
        value = self._get_config("onboarding_guard_messages", 20)
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return 0
        return max(0, parsed)

    def _prune(self, now: float) -> None:
        duration = self._duration_seconds()
        message_limit = self._message_limit()
        for origin, state in list(self._states.items()):
            elapsed_active = duration > 0 and now - state.started_at < duration
            count_active = message_limit > 0 and state.message_count <= message_limit
            if not (elapsed_active or count_active):
                self._states.pop(origin, None)

    @staticmethod
    def _is_group_event(event: Any) -> bool:
        if is_group_origin(getattr(event, "unified_msg_origin", None)):
            return True
        return bool(getattr(event, "group_id", None))


__all__ = ["MAX_ONBOARDING_STATES", "OnboardingGuard"]
