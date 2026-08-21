"""Coalesce same-user segmented messages into a single LLM turn."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from astrbot.api.message_components import Plain


MAX_MERGE_STATES = 4096
_MENTION_LEAD_RE = re.compile(r"^\s*(?:@[^\s，。！？!?；;、]+)\s*")


@dataclass
class _MergeState:
    owner_event: Any
    phase: str
    pending_text: str
    captured_events: set[Any] = field(default_factory=set)
    captured_count: int = 0
    pipeline_task: Any = None


class MergeWindowManager:
    """Track a short window per (origin, sender) to merge follow-up messages.

    Phases:
    - "window": the first wake-up is held; same-user plain-text messages are
      appended to ``pending_text``.
    - "planning": the window closed and the reply is being generated; a new
      wake-up from the same user can supersede the old event and take the
      accumulated text along.
    """

    def __init__(
        self,
        *,
        get_config: Callable[[str, Any], Any] | None = None,
    ) -> None:
        self._states: dict[tuple[str, str], _MergeState] = {}
        self._get_config = get_config or (lambda _key, default: default)

    @classmethod
    def user_key(cls, event: Any) -> tuple[str, str] | None:
        origin = str(getattr(event, "unified_msg_origin", "") or "")
        sender = cls._sender_id(event)
        if not origin or not sender:
            return None
        return (origin, sender)

    @staticmethod
    def _sender_id(event: Any) -> str:
        getter = getattr(event, "get_sender_id", None)
        if callable(getter):
            try:
                return str(getter() or "")
            except Exception:
                return ""
        return ""

    @staticmethod
    def _event_stopped(event: Any) -> bool:
        checker = getattr(event, "is_stopped", None)
        if callable(checker):
            try:
                return bool(checker())
            except Exception:
                return False
        return bool(getattr(event, "stopped", False))

    @classmethod
    def join_text(cls, earlier: str, later: str) -> str:
        cleaned = _MENTION_LEAD_RE.sub("", later or "").strip()
        earlier = (earlier or "").strip()
        if not cleaned:
            return earlier
        return earlier + "\n" + cleaned if earlier else cleaned

    def start_window(self, event: Any, *, pipeline_task: Any = None) -> bool:
        key = self.user_key(event)
        if key is None or key in self._states:
            return False
        if len(self._states) >= MAX_MERGE_STATES:
            self._states.pop(next(iter(self._states)), None)
        self._states[key] = _MergeState(
            owner_event=event,
            phase="window",
            pending_text=str(getattr(event, "message_str", "") or "").strip(),
            pipeline_task=pipeline_task,
        )
        return True

    def is_window_open(self, event: Any) -> bool:
        key = self.user_key(event)
        if key is None:
            return False
        state = self._states.get(key)
        return (
            state is not None
            and state.phase == "window"
            and state.owner_event is not event
        )

    def capture(self, event: Any) -> bool:
        """Append a same-user follow-up into the open window, if eligible."""
        key = self.user_key(event)
        if key is None:
            return False
        state = self._states.get(key)
        if state is None or state.phase != "window":
            return False
        if state.owner_event is event or event in state.captured_events:
            return False
        text = self._plain_text(event)
        if text is None or not self._mergeable(text):
            return False
        if not self._within_limits(state, text):
            return False
        state.pending_text = self.join_text(state.pending_text, text)
        state.captured_events.add(event)
        state.captured_count += 1
        return True

    def finalize_window(self, event: Any) -> str:
        """Close the window, return the merged text, and move to planning."""
        key = self.user_key(event)
        if key is None:
            return str(getattr(event, "message_str", "") or "")
        state = self._states.get(key)
        if state is None or state.owner_event is not event:
            return str(getattr(event, "message_str", "") or "")
        merged = state.pending_text
        state.phase = "planning"
        state.captured_events.clear()
        return merged

    def take_planning(self, event: Any) -> tuple[Any, str, Any] | None:
        """Consume the planning state; return (old_event, text, pipeline_task)."""
        key = self.user_key(event)
        if key is None:
            return None
        state = self._states.get(key)
        if state is None or state.phase != "planning":
            return None
        self._states.pop(key, None)
        if self._event_stopped(state.owner_event):
            return None
        return (state.owner_event, state.pending_text, state.pipeline_task)

    def clear_owner(self, event: Any) -> None:
        """Drop the state once the owner's reply is decorated or sent."""
        key = self.user_key(event)
        if key is None:
            return
        state = self._states.get(key)
        if state is not None and state.owner_event is event:
            self._states.pop(key, None)

    @staticmethod
    def _plain_text(event: Any) -> str | None:
        getter = getattr(event, "get_messages", None)
        if callable(getter):
            try:
                chain = list(getter())
            except Exception:
                chain = []
            if chain:
                for comp in chain:
                    if not isinstance(comp, Plain):
                        return None
                return "".join(getattr(c, "text", "") or "" for c in chain).strip() or None
            return None
        text = str(getattr(event, "message_str", "") or "").strip()
        return text or None

    def _mergeable(self, text: str) -> bool:
        raw = self._get_config("merge_ignore_prefixes", "/,!")
        for item in str(raw).replace("，", ",").split(","):
            prefix = item.strip()
            if prefix and text.startswith(prefix):
                return False
        return True

    def _within_limits(self, state: _MergeState, text: str) -> bool:
        max_messages = self._max_messages()
        if max_messages > 0 and state.captured_count >= max_messages:
            return False
        max_chars = self._max_chars()
        if max_chars > 0 and len(state.pending_text) + len(text) > max_chars:
            return False
        return True

    def _max_messages(self) -> int:
        try:
            return max(0, int(self._get_config("merge_max_messages", 5)))
        except (TypeError, ValueError):
            return 5

    def _max_chars(self) -> int:
        try:
            return max(0, int(self._get_config("merge_max_chars", 2000)))
        except (TypeError, ValueError):
            return 2000
