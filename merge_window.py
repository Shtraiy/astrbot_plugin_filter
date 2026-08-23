"""Coalesce same-user segmented messages into a single LLM turn.

State machine per ``(unified_msg_origin, sender_id)``:

- ``"window"``: the first wake-up is held for ``merge_window_seconds``;
  same-user follow-up text/images are appended to the pending buffer.
- ``"planning"``: the window closed and the reply is being generated; a new
  message from the same user (with or without a wake word) supersedes the old
  event and regenerates with the accumulated text.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from astrbot.api.message_components import Plain

from .event_access import (
    get_message_chain,
    is_image_or_file,
    is_reply_component,
    media_components,
)


MAX_MERGE_STATES = 4096
_MENTION_LEAD_RE = re.compile(r"^\s*(?:@[^\s，。！？!?；;、]+)\s*")


@dataclass
class _MergeState:
    owner_event: Any
    phase: str
    pending_text: str
    pending_media: list[Any] = field(default_factory=list)
    captured_events: set[Any] = field(default_factory=set)
    captured_count: int = 0
    planning_started_at: float = 0.0


class MergeWindowManager:
    """Track a merge window per (origin, sender) for segmented messages.

    Phases:
    - ``"window"``: the first wake-up is held; same-user follow-up messages
      are appended to ``pending_text`` / ``pending_media``.
    - ``"planning"``: the window closed and the reply is being generated; a
      new message from the same user supersedes the old event and takes the
      accumulated text along into a regenerated request.
    """

    def __init__(
        self,
        *,
        get_config: Callable[[str, Any], Any] | None = None,
        now: Callable[[], float] | None = None,
    ) -> None:
        self._states: dict[tuple[str, str], _MergeState] = {}
        self._get_config = get_config or (lambda _key, default: default)
        self._now = now or time.monotonic

    @classmethod
    def window_key(cls, event: Any) -> tuple[str, str] | None:
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
    def _is_wake_event(event: Any) -> bool:
        """True when the event itself will proceed to an LLM request."""
        return bool(getattr(event, "is_at_or_wake_command", False))

    @classmethod
    def join_text(cls, earlier: str, later: str) -> str:
        cleaned = _MENTION_LEAD_RE.sub("", later or "").strip()
        earlier = (earlier or "").strip()
        if not cleaned:
            return earlier
        return earlier + "\n" + cleaned if earlier else cleaned

    def start_window(self, event: Any) -> bool:
        key = self.window_key(event)
        if key is None:
            return False
        existing = self._states.get(key)
        if existing is not None:
            return False
        if len(self._states) >= MAX_MERGE_STATES:
            self._states.pop(next(iter(self._states)), None)
        self._states[key] = _MergeState(
            owner_event=event,
            phase="window",
            pending_text=str(getattr(event, "message_str", "") or "").strip(),
        )
        return True

    def is_window_open(self, event: Any) -> bool:
        key = self.window_key(event)
        if key is None:
            return False
        state = self._states.get(key)
        return (
            state is not None
            and state.phase == "window"
            and state.owner_event is not event
        )

    def planning_active(self, event: Any) -> bool:
        """True when this user has a fresh (unexpired) planning state."""
        key = self.window_key(event)
        if key is None:
            return False
        state = self._states.get(key)
        if state is None or state.phase != "planning":
            return False
        if self._planning_expired(state):
            self._states.pop(key, None)
            return False
        return True

    @classmethod
    def message_has_quote(cls, event: Any) -> bool:
        """Return True when the message quotes a historical message (Reply)."""
        chain = get_message_chain(event)
        if not chain:
            return False
        return any(is_reply_component(comp) for comp in chain)

    def cancel_window(self, event: Any) -> Any | None:
        """Cancel the open window for this user and return its owner event."""
        key = self.window_key(event)
        if key is None:
            return None
        state = self._states.get(key)
        if state is None or state.phase != "window":
            return None
        self._states.pop(key, None)
        return state.owner_event

    def capture(self, event: Any) -> bool:
        """Append a same-user non-wake follow-up while the window is open."""
        key = self.window_key(event)
        if key is None:
            return False
        state = self._states.get(key)
        if state is None or state.phase != "window":
            return False
        if state.owner_event is event or event in state.captured_events:
            return False
        if self._is_wake_event(event):
            return False
        payload = self._extract_merge_payload(event)
        if payload is None:
            return False
        text, media = payload
        if text and not self._mergeable(text):
            return False
        if not text and not media:
            return False
        if not self._within_limits(state, text, media):
            return False
        if text:
            state.pending_text = self.join_text(state.pending_text, text)
        state.pending_media.extend(media)
        state.captured_events.add(event)
        state.captured_count += 1
        return True

    def merge_wake(self, event: Any) -> bool:
        """Append a same-user wake follow-up while the window is still open."""
        key = self.window_key(event)
        if key is None:
            return False
        state = self._states.get(key)
        if state is None or state.phase != "window":
            return False
        if state.owner_event is event or event in state.captured_events:
            return False
        payload = self._extract_merge_payload(event)
        if payload is None:
            return False
        text, media = payload
        if text and not self._mergeable(text):
            return False
        if not text and not media:
            return False
        if not self._within_limits(state, text, media):
            return False
        if text:
            state.pending_text = self.join_text(state.pending_text, text)
        state.pending_media.extend(media)
        state.captured_events.add(event)
        state.captured_count += 1
        return True

    def promote_planning(self, event: Any) -> bool:
        """Let a same-user non-wake supplement proceed to the LLM pipeline.

        Called from ``on_message`` while the owner reply is being generated.
        The event text/media stay on the event; ``take_planning`` merges them
        into the regenerated request.
        """
        key = self.window_key(event)
        if key is None:
            return False
        state = self._states.get(key)
        if state is None or state.phase != "planning":
            return False
        if self._planning_expired(state):
            self._states.pop(key, None)
            return False
        if state.owner_event is event or event in state.captured_events:
            return False
        if self._is_wake_event(event):
            return False
        payload = self._extract_merge_payload(event)
        if payload is None:
            return False
        text, media = payload
        if text and not self._mergeable(text):
            return False
        if not text and not media:
            return False
        if not self._within_limits(state, text, media):
            return False
        try:
            event.is_at_or_wake_command = True
        except Exception:
            return False
        return True

    def finalize_window(self, event: Any) -> str:
        """Close the window, return the merged text, and move to planning."""
        key = self.window_key(event)
        if key is None:
            return str(getattr(event, "message_str", "") or "")
        state = self._states.get(key)
        if state is None or state.owner_event is not event:
            return str(getattr(event, "message_str", "") or "")
        merged = state.pending_text
        self.attach_media(event, state.pending_media)
        state.pending_media = []
        state.phase = "planning"
        state.planning_started_at = self._now()
        state.captured_events.clear()
        return merged

    def take_planning(self, event: Any) -> tuple[Any, str, list[Any], Any] | None:
        """Consume the planning state; return (old_event, text, media, task)."""
        key = self.window_key(event)
        if key is None:
            return None
        state = self._states.get(key)
        if state is None or state.phase != "planning":
            return None
        if self._planning_expired(state):
            self._states.pop(key, None)
            return None
        self._states.pop(key, None)
        media = self._event_media(state.owner_event)
        media.extend(state.pending_media)
        return (state.owner_event, state.pending_text, media)

    def rearm_planning(
        self,
        event: Any,
        merged_text: str,
    ) -> bool:
        """Re-create a planning state for a regenerated event."""
        key = self.window_key(event)
        if key is None:
            return False
        if len(self._states) >= MAX_MERGE_STATES:
            self._states.pop(next(iter(self._states)), None)
        self._states[key] = _MergeState(
            owner_event=event,
            phase="planning",
            pending_text=str(merged_text or "").strip(),
            planning_started_at=self._now(),
        )
        return True

    def sync_pending_text(self, event: Any, text: str) -> None:
        """Sync a post-finalize rewrite (e.g. media-only placeholder) back into
        the planning state so later take_planning merges see the new text."""
        key = self.window_key(event)
        if key is None:
            return
        state = self._states.get(key)
        if state is None or state.owner_event is not event:
            return
        state.pending_text = str(text or "").strip()

    def clear_state(self, event: Any) -> None:
        """Drop the state once the owner's reply is decorated or sent."""
        key = self.window_key(event)
        if key is None:
            return
        state = self._states.get(key)
        if state is None or state.owner_event is not event:
            return
        self._states.pop(key, None)

    @classmethod
    def has_media(cls, event: Any) -> bool:
        """Return True when the event's message chain carries image/file media."""
        return bool(cls._event_media(event))

    def _extract_merge_payload(self, event: Any) -> tuple[str, list[Any]] | None:
        """Return (text, media_components) when the message is mergeable."""
        chain = get_message_chain(event)
        if chain is None:
            text = str(getattr(event, "message_str", "") or "").strip()
            return (text, []) if text else None
        text_parts: list[str] = []
        media: list[Any] = []
        for comp in chain:
            if isinstance(comp, Plain):
                text_parts.append(getattr(comp, "text", "") or "")
            elif self._is_mergeable_media(comp):
                media.append(comp)
            else:
                return None
        text = "".join(text_parts).strip()
        if not text and not media:
            return None
        return (text, media)

    def _is_mergeable_media(self, comp: Any) -> bool:
        if not self._get_config("merge_include_media", True):
            return False
        return is_image_or_file(comp)

    @staticmethod
    def attach_media(event: Any, media: list[Any]) -> None:
        if not media:
            return
        chain = getattr(getattr(event, "message_obj", None), "message", None)
        if chain is None:
            return
        chain.extend(media)

    @classmethod
    def _event_media(cls, event: Any) -> list[Any]:
        return media_components(event)

    def _mergeable(self, text: str) -> bool:
        raw = self._get_config("merge_ignore_prefixes", "/,!")
        for item in str(raw).replace("，", ",").split(","):
            prefix = item.strip()
            if prefix and text.startswith(prefix):
                return False
        return True

    def _within_limits(
        self,
        state: _MergeState,
        text: str,
        media: list[Any],
    ) -> bool:
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

    def _planning_ttl(self) -> float:
        try:
            value = float(self._get_config("merge_planning_ttl", 60.0))
        except (TypeError, ValueError):
            return 60.0
        return max(0.0, value)

    def _planning_expired(self, state: _MergeState) -> bool:
        ttl = self._planning_ttl()
        return ttl > 0 and self._now() - state.planning_started_at > ttl


__all__ = ["MAX_MERGE_STATES", "MergeWindowManager"]
