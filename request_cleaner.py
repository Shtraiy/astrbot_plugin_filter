"""Keep the LLM's vision focus on the user's current media.

When a human sends an image/file, plugins such as meme managers may have
injected the bot's own previously-sent meme (as text context) and the
conversation history may still contain the bot's own outgoing media. Both can
make the model "recognize" the bot's own meme instead of the new user media.
These helpers strip that noise from the request before it reaches the LLM.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any


_MEDIA_TYPE_TOKENS = ("image", "file", "audio", "video", "record")
_TEXT_TYPES = {"text", "input_text", "plain"}
_OWN_MEDIA_QUESTION_HINTS = (
    "表情",
    "表情包",
    "图",
    "图片",
    "照片",
    "文件",
    "视频",
    "音频",
    "语音",
)
_IMAGE_REFERENT_HINTS = (
    "这",
    "图",
    "图片",
    "表情包",
    "上面",
    "里面",
    "图上",
    "图里",
    "它",
    "那张",
    "这张",
)
_IMAGE_TEXT_HINTS = ("字", "写", "文字")
_ATTRIBUTION_NOTE = (
    "<attribution_note>"
    "用户正在询问自己发送过的内容。请严格按消息角色区分归属："
    "role=user（人类）的消息才是用户发送的；role=assistant（机器人）的消息——包括其中的图片和文字——属于机器人自己。"
    "若用户从未发送过表情包或图片，请如实回答没有发送过，不要描述机器人自己发送的内容。"
    "只有确认用户确实发送过时，才能描述用户发送的那一张。"
    "</attribution_note>"
)
_IMAGE_TEXT_NOTE = (
    "<media_note>"
    "用户本轮发送的是纯文字消息，不包含任何图片。"
    "如果用户询问某张图片或图片上的文字，请先确认用户指的是哪一张图；"
    "不要根据历史消息中机器人自己发送的表情包来回答图片内容，也不要假设用户正在查看某张图片。"
    "用户未指认具体图片时，请如实说明或请用户发送/指认图片。"
    "</media_note>"
)


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


def strip_assistant_media(req: Any) -> int:
    """Remove media parts from assistant-role history entries.

    Returns the number of removed media blocks. Only in-place mutable dict
    entries (or objects with a settable ``content`` attribute) are touched;
    anything unrecognized is left alone.
    """
    contexts = _request_contexts(req)
    if not contexts:
        return 0
    removed = 0
    for entry in contexts:
        role = str(_entry_role(entry) or "").casefold()
        if role not in {"assistant", "bot", "ai"}:
            continue
        content = _entry_content(entry)
        cleaned, count = _strip_media_from_content(content)
        if count:
            _set_entry_content(entry, cleaned)
            removed += count
    return removed


def count_assistant_media(req: Any) -> int:
    """Count media blocks inside assistant history entries (read-only)."""
    contexts = _request_contexts(req)
    if not contexts:
        return 0
    total = 0
    for entry in contexts:
        role = str(_entry_role(entry) or "").casefold()
        if role not in {"assistant", "bot", "ai"}:
            continue
        total += _count_media(_entry_content(entry))
    return total


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


def asks_about_own_media(text: str) -> bool:
    """Return True when the user asks about media they themselves sent."""
    if "我发" not in (text or ""):
        return False
    return any(hint in text for hint in _OWN_MEDIA_QUESTION_HINTS)


def asks_about_image_text(text: str) -> bool:
    """Return True for text-only questions like "这上面有字吗" / "写了什么"."""
    if not (text or ""):
        return False
    if "有字" in text:
        return True
    has_referent = any(hint in text for hint in _IMAGE_REFERENT_HINTS)
    has_text_word = any(hint in text for hint in _IMAGE_TEXT_HINTS)
    return has_referent and has_text_word


def append_attribution_note(req: Any) -> bool:
    """Append a role-attribution note to the user message content."""
    return _append_note_part(req, _ATTRIBUTION_NOTE)


def append_image_text_note(req: Any) -> bool:
    """Append a note clarifying the user sent no image this round."""
    return _append_note_part(req, _IMAGE_TEXT_NOTE)


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


def _strip_media_from_content(content: Any) -> tuple[Any, int]:
    if content is None or isinstance(content, str):
        return content, 0
    if isinstance(content, list):
        kept: list[Any] = []
        removed = 0
        for item in content:
            if _is_media_part(item):
                removed += 1
                continue
            if isinstance(item, Mapping) and "content" in item:
                cleaned, count = _strip_media_from_content(item["content"])
                if count:
                    item["content"] = cleaned
                    removed += count
                if _content_empty(cleaned):
                    removed += 1
                    continue
            kept.append(item)
        if removed and not kept:
            return "", removed
        return kept, removed
    if isinstance(content, Mapping):
        if _is_media_part(content):
            return "", 1
        if "content" in content:
            cleaned, count = _strip_media_from_content(content["content"])
            if count:
                content["content"] = cleaned
            return content, count
        return content, 0
    nested = getattr(content, "content", None)
    if nested is not None:
        cleaned, count = _strip_media_from_content(nested)
        if count:
            try:
                content.content = cleaned
            except Exception:
                pass
        return content, count
    if _is_media_part(content):
        return "", 1
    return content, 0


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


def _set_entry_content(entry: Any, value: Any) -> None:
    if isinstance(entry, Mapping):
        entry["content"] = value
        return
    try:
        entry.content = value
    except Exception:
        pass


def _count_media(content: Any) -> int:
    if content is None or isinstance(content, str):
        return 0
    if isinstance(content, list):
        return sum(_count_media(item) for item in content)
    if isinstance(content, Mapping):
        if _is_media_part(content):
            return 1
        if "content" in content:
            return _count_media(content["content"])
        return 0
    nested = getattr(content, "content", None)
    if nested is not None:
        return _count_media(nested)
    return 1 if _is_media_part(content) else 0


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


def _content_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple)):
        return len(value) == 0
    return False
