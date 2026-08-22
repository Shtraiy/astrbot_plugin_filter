"""Mark the bot's own recent replies so the model attributes media correctly.

The bot's own outgoing images/files stay in the conversation history (useful
context), but memory/history text can mislead the model into thinking the user
sent them. This module records the bot's recent sent text/media per session
and injects an objective attribution block before each LLM request.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Callable

from astrbot.api.message_components import File, Image, Plain


MAX_MARK_STATES = 4096
MAX_MARK_ENTRIES = 8
_MAX_TEXT_SNIPPET = 200

_MEDIA_TYPE_TOKENS = ("image", "file", "audio", "video", "record")
_TEXT_TYPES = {"text", "input_text", "plain"}
_TEXT_ONLY_MEDIA_NOTE = (
    "<media_note>"
    "用户本条消息为纯文字，用户本轮没有发送任何图片。"
    "历史对话中的图片属于各自消息的发送者：assistant（机器人）消息中的图片是机器人自己发送的，不属于用户。"
    "任何记忆、总结或历史中声称'用户发送过图片/表情包'的内容都可能是机器人自己的误判；"
    "除非用户本轮实际发送了图片，否则不要把机器人自己的图片归为用户发送，也不要描述成用户发送的。"
    "</media_note>"
)
_REFERENCED_IMAGE_NOTE = (
    "<referenced_image_note>"
    "用户引用了一张历史消息中的图片，这是用户当前询问的对象。"
    "请识别并分析这张图片的内容，用自然语言回答用户的问题。"
    "如果该图片是机器人自己之前发送的，回答时可以说明'这是我之前发的图'，"
    "但不要声称用户刚刚发送了它。"
    "</referenced_image_note>"
)


@dataclass
class _SentEntry:
    timestamp: float
    text: str
    media: list[str] = field(default_factory=list)


class SelfReplyMarker:
    """Record the bot's own sent text/media per session with a TTL window."""

    def __init__(
        self,
        *,
        get_config: Callable[[str, Any], Any] | None = None,
        now: Callable[[], float] | None = None,
    ) -> None:
        self._entries: dict[str, deque[_SentEntry]] = {}
        self._get_config = get_config or (lambda _key, default: default)
        self._now = now or time.time

    def record_sent_reply(self, event: Any) -> None:
        origin = str(getattr(event, "unified_msg_origin", "") or "")
        if not origin:
            return
        chain = self._result_chain(event)
        if not chain:
            return
        text = " ".join(
            (getattr(comp, "text", "") or "")
            for comp in chain
            if isinstance(comp, Plain)
        ).strip()
        media = [
            self._describe_media(comp)
            for comp in chain
            if isinstance(comp, (Image, File))
        ]
        if not text and not media:
            return
        self._prune(origin)
        queue = self._entries.setdefault(origin, deque(maxlen=MAX_MARK_ENTRIES))
        queue.append(_SentEntry(timestamp=self._now(), text=text, media=media))

    def mark_own_recent_replies(self, req: Any, event: Any) -> bool:
        origin = str(getattr(event, "unified_msg_origin", "") or "")
        if not origin:
            return False
        if not self._enabled():
            return False
        self._prune(origin)
        queue = self._entries.get(origin)
        if not queue:
            return False
        block = self._build_mark_block(queue)
        return self._append_temp_part(req, block)

    def _enabled(self) -> bool:
        return bool(self._get_config("enable_self_reply_mark", True))

    def _window_minutes(self) -> float:
        try:
            value = float(self._get_config("self_reply_mark_minutes", 5.0))
        except (TypeError, ValueError):
            return 5.0
        return max(0.0, value)

    def _prune(self, origin: str) -> None:
        minutes = self._window_minutes()
        queue = self._entries.get(origin)
        if queue is None:
            return
        if minutes <= 0:
            self._entries.pop(origin, None)
            return
        cutoff = self._now() - minutes * 60
        while queue and queue[0].timestamp < cutoff:
            queue.popleft()
        if not queue:
            self._entries.pop(origin, None)

    def _build_mark_block(self, queue: deque[_SentEntry]) -> str:
        minutes = self._window_minutes()
        lines = [
            "<self_reply_mark>",
            f"以下内容是机器人自己在最近 {minutes:g} 分钟内发送过的（属于机器人，不是用户发送的）：",
        ]
        for entry in queue:
            if entry.text:
                snippet = entry.text.replace("\n", " ")[:_MAX_TEXT_SNIPPET]
                lines.append(f"- [文本] {snippet}")
            for desc in entry.media:
                lines.append(f"- {desc}")
        lines.append("用户本轮消息中出现的媒体/文件才属于用户。")
        lines.append(
            "任何记忆、总结或历史中声称'用户发送过图片/表情包'的内容若与本标记冲突，以本标记为准。"
        )
        lines.append(
            "用户引用历史消息中的图片提问时，该图片是用户当前询问的对象"
            "（即使它是机器人自己之前发送的），需要识别分析。"
        )
        lines.append("</self_reply_mark>")
        return "\n".join(lines)

    @staticmethod
    def _result_chain(event: Any) -> list[Any] | None:
        result = getattr(event, "get_result", lambda: None)()
        chain = getattr(result, "chain", None) if result is not None else None
        return chain if isinstance(chain, list) else None

    @staticmethod
    def _describe_media(comp: Any) -> str:
        name = ""
        for attr in ("name", "file", "url", "path"):
            value = getattr(comp, attr, None)
            if isinstance(value, str) and value.strip():
                name = value.strip()
                break
        kind = "图片" if isinstance(comp, Image) else "文件"
        if name:
            basename = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
            return f"[{kind}] {basename}"
        return f"[{kind}]"

    def _append_temp_part(self, req: Any, text: str) -> bool:
        return _append_note_part(req, text)


def has_user_media(event: Any) -> bool:
    """Return True when the current user message carries media components."""
    chain = getattr(getattr(event, "message_obj", None), "message", None)
    if chain is None:
        getter = getattr(event, "get_messages", None)
        if callable(getter):
            try:
                chain = getter()
            except Exception:
                chain = None
    if not chain:
        return False
    return any(_is_media_part(comp) for comp in chain)


def has_referenced_image(event: Any) -> bool:
    """Return True when the message quotes a historical message that includes an image."""
    chain = getattr(getattr(event, "message_obj", None), "message", None)
    if chain is None:
        getter = getattr(event, "get_messages", None)
        if callable(getter):
            try:
                chain = getter()
            except Exception:
                chain = None
    if not chain:
        return False
    for comp in chain:
        if not _is_reply_component(comp):
            continue
        reply_chain = getattr(comp, "chain", None)
        if isinstance(reply_chain, list) and any(
            _is_media_part(item) for item in reply_chain
        ):
            return True
        message_str = str(getattr(comp, "message_str", "") or "")
        if "图片" in message_str or "[Image]" in message_str or "[图片]" in message_str:
            return True
    return False


def _is_reply_component(comp: Any) -> bool:
    name = type(comp).__name__.casefold()
    if "reply" in name:
        return True
    return "reply" in str(getattr(comp, "type", "") or "").casefold()


def describe_contexts(req: Any) -> str:
    """Compact structural summary of the request history for diagnostics."""
    contexts = _request_contexts(req)
    if not contexts:
        return "no-contexts"
    roles: dict[str, int] = {}
    for entry in contexts:
        role = str(_entry_role(entry) or "?").casefold() or "?"
        roles[role] = roles.get(role, 0) + 1
    return f"entries={len(contexts)} roles={roles}"


def strip_recent_self_meme_context(req: Any) -> int:
    """Drop meme_manager-style <recent_sent_meme> text parts from the request.

    Returns the number of removed parts.
    """
    parts = getattr(req, "extra_user_content_parts", None)
    if not isinstance(parts, list):
        return 0
    kept = [part for part in parts if not _is_self_meme_part(part)]
    removed = len(parts) - len(kept)
    if removed:
        parts[:] = kept
    return removed


def append_text_only_media_note(req: Any) -> bool:
    """Append a note clarifying that a text-only message carries no image."""
    return _append_note_part(req, _TEXT_ONLY_MEDIA_NOTE)


def append_referenced_image_note(req: Any) -> bool:
    """Append a note telling the model that a quoted image is the question target."""
    return _append_note_part(req, _REFERENCED_IMAGE_NOTE)


def _append_note_part(req: Any, note: str) -> bool:
    parts = getattr(req, "extra_user_content_parts", None)
    if not isinstance(parts, list):
        return False
    part = _make_text_part(note)
    if part is None:
        return False
    parts.append(part)
    return True


def _request_contexts(req: Any) -> list[Any] | None:
    if req is None:
        return None
    if isinstance(req, Mapping):
        for key in ("contexts", "context", "chat_history", "history"):
            value = req.get(key)
            if isinstance(value, list):
                return value
        return None
    for attr in ("contexts", "context"):
        value = getattr(req, attr, None)
        if isinstance(value, list):
            return value
    return None


def _is_media_part(part: Any) -> bool:
    if isinstance(part, str):
        return False
    if isinstance(part, Mapping):
        ptype = str(part.get("type", "") or "").casefold()
        if ptype in _TEXT_TYPES:
            return False
        return any(token in ptype for token in _MEDIA_TYPE_TOKENS)
    name = type(part).__name__.casefold()
    return any(token in name for token in _MEDIA_TYPE_TOKENS)


def _entry_role(entry: Any) -> Any:
    if isinstance(entry, Mapping):
        return entry.get("role")
    return getattr(entry, "role", None)


def _entry_content(entry: Any) -> Any:
    if isinstance(entry, Mapping):
        return entry.get("content")
    return getattr(entry, "content", None)


def _is_self_meme_part(part: Any) -> bool:
    if isinstance(part, Mapping):
        text = part.get("text", part.get("content", ""))
    else:
        text = getattr(part, "text", None)
    if not isinstance(text, str):
        text = ""
    return "<recent_sent_meme>" in text or "</recent_sent_meme>" in text


def _make_text_part(text: str) -> Any | None:
    try:
        from astrbot.core.agent.message import TextPart
    except Exception:
        return SimpleNamespace(text=text)
    try:
        part = TextPart(text=text)
    except Exception:
        return None
    mark_as_temp = getattr(part, "mark_as_temp", None)
    if callable(mark_as_temp):
        try:
            part = mark_as_temp()
        except Exception:
            pass
    return part


__all__ = [
    "MAX_MARK_ENTRIES",
    "MAX_MARK_STATES",
    "SelfReplyMarker",
    "append_text_only_media_note",
    "append_referenced_image_note",
    "describe_contexts",
    "has_referenced_image",
    "has_user_media",
    "strip_recent_self_meme_context",
]
