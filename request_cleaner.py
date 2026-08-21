"""Keep the LLM's vision focus on the user's current media.

When a human sends an image/file, plugins such as meme managers may have
injected the bot's own previously-sent meme (as text context) and the
conversation history may still contain the bot's own outgoing media. Both can
make the model "recognize" the bot's own meme instead of the new user media.
These helpers strip that noise from the request before it reaches the LLM.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


_MEDIA_TYPE_TOKENS = ("image", "file", "audio", "video", "record")
_TEXT_TYPES = {"text", "input_text", "plain"}


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
    entries are touched; anything unrecognized is left alone.
    """
    contexts = _request_contexts(req)
    if not contexts:
        return 0
    removed = 0
    for entry in contexts:
        if not isinstance(entry, Mapping):
            continue
        role = str(entry.get("role", "") or "").casefold()
        if role not in {"assistant", "bot", "ai"}:
            continue
        content = entry.get("content")
        cleaned, count = _strip_media_from_content(content)
        if count:
            entry["content"] = cleaned
            removed += count
    return removed


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


def _is_self_meme_part(part: Any) -> bool:
    if isinstance(part, Mapping):
        text = part.get("text", part.get("content", ""))
    else:
        text = getattr(part, "text", None)
    if not isinstance(text, str):
        text = ""
    return "<recent_sent_meme>" in text or "</recent_sent_meme>" in text


def _content_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple)):
        return len(value) == 0
    return False
