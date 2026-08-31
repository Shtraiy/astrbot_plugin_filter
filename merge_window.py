"""Coalesce same-user segmented messages into a single LLM turn.

Single-phase sliding window per ``(unified_msg_origin, sender_id)``:

- the first wake-up opens a window and is held until the user stays silent
  for ``merge_window_seconds``;
- every same-user follow-up (with or without a wake word) is appended to the
  pending buffer and resets the quiet timer;
- once the window closes, ``finalize_window`` destroys the state; an
  in-flight reply is never interrupted by later messages.
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
_MULTI_MESSAGE_NOTE = (
    "（以上是用户在同一次唤醒中连续发送的 {count} 条消息，"
    "请整体回应，不要遗漏任何一条。）"
)


@dataclass
class _MergeState:
    owner_event: Any
    pending_text: str
    segments: list[str] = field(default_factory=list)
    pending_media: list[Any] = field(default_factory=list)
    captured_events: set[Any] = field(default_factory=set)
    captured_count: int = 0
    last_captured_id: Any = None
    last_activity_at: float = 0.0


class MergeWindowManager:
    """Track a merge window per (origin, sender) for segmented messages.

    The window is a single phase: any same-user message resets the quiet
    timer, and the window closes (state destroyed) once the user stays
    silent for ``merge_window_seconds``.
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
        cleaned = cls._clean_segment(later)
        earlier = (earlier or "").strip()
        if not cleaned:
            return earlier
        return earlier + "\n" + cleaned if earlier else cleaned

    @staticmethod
    def _clean_segment(text: str) -> str:
        return _MENTION_LEAD_RE.sub("", text or "").strip()

    @classmethod
    def append_segment(cls, segments: list[str], text: str) -> list[str]:
        """Append one user-message segment, returning the updated list."""
        cleaned = cls._clean_segment(text)
        base = [seg for seg in (segments or []) if str(seg or "").strip()]
        if cleaned:
            base.append(cleaned)
        return base

    @classmethod
    def format_segments(cls, segments: list[str]) -> str:
        """Number merged user-message segments so the model answers all of them.

        A single segment stays as its raw text; multiple segments are labeled
        ``用户消息1/2/...`` and closed with a note asking the model not to miss
        any of them.
        """
        texts = [
            str(seg or "").strip()
            for seg in (segments or [])
            if str(seg or "").strip()
        ]
        if not texts:
            return ""
        if len(texts) == 1:
            return texts[0]
        lines = [f"用户消息{i + 1}：{text}" for i, text in enumerate(texts)]
        lines.append(_MULTI_MESSAGE_NOTE.format(count=len(texts)))
        return "\n".join(lines)

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
            pending_text=str(getattr(event, "message_str", "") or "").strip(),
            segments=[str(getattr(event, "message_str", "") or "").strip()],
            last_activity_at=self._now(),
        )
        return True

    def is_window_open(self, event: Any) -> bool:
        key = self.window_key(event)
        if key is None:
            return False
        state = self._states.get(key)
        return state is not None and state.owner_event is not event

    def quiet_remaining(
        self,
        event: Any,
        window_seconds: float,
        now: float | None = None,
    ) -> float:
        """Seconds until this window is quiet; 0 when no window is open."""
        key = self.window_key(event)
        if key is None:
            return 0.0
        state = self._states.get(key)
        if state is None:
            return 0.0
        current = self._now() if now is None else now
        return max(0.0, float(window_seconds) - (current - state.last_activity_at))

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
        if state is None:
            return None
        self._states.pop(key, None)
        return state.owner_event

    def capture(self, event: Any) -> bool:
        """Append a same-user non-wake follow-up while the window is open."""
        key = self.window_key(event)
        if key is None:
            return False
        state = self._states.get(key)
        if state is None:
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
            state.segments.append(self._clean_segment(text))
        state.pending_media.extend(media)
        state.captured_events.add(event)
        state.captured_count += 1
        state.last_captured_id = self._event_message_id(event)
        state.last_activity_at = self._now()
        return True

    def is_captured(self, event: Any) -> bool:
        """True when this event was already merged into the open window."""
        key = self.window_key(event)
        if key is None:
            return False
        state = self._states.get(key)
        return state is not None and event in state.captured_events

    def merge_wake(self, event: Any) -> bool:
        """Append a same-user wake follow-up while the window is still open."""
        key = self.window_key(event)
        if key is None:
            return False
        state = self._states.get(key)
        if state is None:
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
            state.segments.append(self._clean_segment(text))
        state.pending_media.extend(media)
        state.captured_events.add(event)
        state.captured_count += 1
        state.last_captured_id = self._event_message_id(event)
        state.last_activity_at = self._now()
        return True

    def finalize_window(self, event: Any) -> str:
        """Close the window, return the merged text, and destroy its state."""
        key = self.window_key(event)
        if key is None:
            return str(getattr(event, "message_str", "") or "")
        state = self._states.get(key)
        if state is None or state.owner_event is not event:
            return str(getattr(event, "message_str", "") or "")
        merged = self.format_segments(state.segments)
        self.attach_media(event, state.pending_media)
        self._record_last_message_id(event, state.last_captured_id)
        self._states.pop(key, None)
        return merged

    def clear_state(self, event: Any) -> None:
        """Drop the state once the owner's reply is decorated or sent."""
        key = self.window_key(event)
        if key is None:
            return
        state = self._states.get(key)
        if state is None or state.owner_event is not event:
            return
        self._states.pop(key, None)

    @staticmethod
    def _event_message_id(event: Any) -> Any:
        return getattr(getattr(event, "message_obj", None), "message_id", None)

    @classmethod
    def _record_last_message_id(cls, event: Any, message_id: Any) -> None:
        """Remember the last merged message id so the quote targets it."""
        if message_id is None:
            return
        setter = getattr(event, "set_extra", None)
        if callable(setter):
            try:
                setter("merge_last_message_id", message_id)
            except Exception:
                pass

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

__all__ = ["MAX_MERGE_STATES", "MergeWindowManager"]
