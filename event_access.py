"""Shared, dependency-free helpers for reading AstrBot event internals.

Every module used to re-implement the same knowledge: how to get the message
chain of an event, whether a component is a reply or media, and how to read
text out of an OpenAI-format context entry. This module is the single seam
for that knowledge, so an AstrBot API change propagates in exactly one place.

The module intentionally imports no AstrBot Star machinery.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from astrbot.api.message_components import File, Image, Plain


_TEXT_TYPES = {"text", "input_text", "plain"}
_MEDIA_TYPE_TOKENS = ("image", "file", "audio", "video", "record")
_MEDIA_TEXT_MARKERS = ("[Image", "[File", "Image Attachment", "File Attachment")


def get_message_chain(event: Any) -> list[Any] | None:
    """Return the event's message components, or None when unavailable."""
    chain = getattr(getattr(event, "message_obj", None), "message", None)
    if chain is not None:
        return chain
    getter = getattr(event, "get_messages", None)
    if callable(getter):
        try:
            chain = list(getter())
        except Exception:
            return None
        return chain or None
    return None


def is_reply_component(comp: Any) -> bool:
    """Return True when a component quotes a historical message (Reply)."""
    name = type(comp).__name__.casefold()
    if "reply" in name:
        return True
    return "reply" in str(getattr(comp, "type", "") or "").casefold()


def has_reply(event: Any) -> bool:
    """Return True when the event's chain carries a reply component."""
    chain = get_message_chain(event)
    return bool(chain) and any(is_reply_component(comp) for comp in chain)


def is_media_part(part: Any) -> bool:
    """Broad media detection for OpenAI content parts and chain components."""
    if isinstance(part, str):
        return False
    if isinstance(part, Mapping):
        ptype = str(part.get("type", "") or "").casefold()
        if ptype in _TEXT_TYPES:
            return False
        return any(token in ptype for token in _MEDIA_TYPE_TOKENS)
    name = type(part).__name__.casefold()
    if any(token in name for token in _MEDIA_TYPE_TOKENS):
        return True
    for attr in ("url", "file", "path", "image", "video"):
        value = getattr(part, attr, None)
        if value is not None and str(value).strip():
            return True
    text = str(getattr(part, "text", "") or "")
    return any(marker in text for marker in _MEDIA_TEXT_MARKERS)


def is_image_or_file(comp: Any) -> bool:
    """Narrow check: only Image/File components (merge-window policy)."""
    return isinstance(comp, (Image, File))


def has_media(event: Any) -> bool:
    """Return True when the event's chain carries any media component."""
    chain = get_message_chain(event)
    return bool(chain) and any(is_media_part(comp) for comp in chain)


def media_components(event: Any) -> list[Any]:
    """Return the Image/File components on the event's own chain."""
    chain = get_message_chain(event)
    if not chain:
        return []
    return [comp for comp in chain if is_image_or_file(comp)]


def plain_text_of(chain: Any) -> str:
    """Join Plain component texts from a message chain."""
    if not chain:
        return ""
    return "".join(
        getattr(comp, "text", "") or ""
        for comp in chain
        if isinstance(comp, Plain)
    )


def event_has_content(event: Any) -> bool:
    """Return True when the event carries text, media, or a quote.

    Content-less events (empty text, no media, no Reply) must never
    participate in merging or wake-up handling: treating them as wake-ups
    can abort an in-flight reply for nothing (e.g. QQ poke/notice events
    surfaced as empty friend messages).
    """
    if str(getattr(event, "message_str", "") or "").strip():
        return True
    chain = get_message_chain(event)
    if not chain:
        return False
    if any(is_reply_component(comp) for comp in chain):
        return True
    if any(is_media_part(comp) for comp in chain):
        return True
    return bool(plain_text_of(chain).strip())


def request_contexts(req: Any) -> list[Any] | None:
    """Return the OpenAI-format context list from a request object."""
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


def entry_role(entry: Any) -> Any:
    """Read the role field of an OpenAI-format context entry."""
    if isinstance(entry, Mapping):
        return entry.get("role")
    return getattr(entry, "role", None)


def entry_content(entry: Any) -> Any:
    """Read the content field of an OpenAI-format context entry."""
    if isinstance(entry, Mapping):
        return entry.get("content")
    return getattr(entry, "content", None)


def entry_text(content: Any) -> str | None:
    """Return text content when an entry is text-only; None for multimodal."""
    if content is None:
        return None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
                continue
            if not isinstance(part, Mapping):
                return None
            part_type = str(part.get("type", "") or "").casefold()
            if part_type not in _TEXT_TYPES:
                return None
            text = part.get("text")
            if not isinstance(text, str):
                return None
            parts.append(text)
        return "".join(parts)
    return None


__all__ = [
    "entry_content",
    "entry_role",
    "entry_text",
    "event_has_content",
    "get_message_chain",
    "has_media",
    "has_reply",
    "is_image_or_file",
    "is_media_part",
    "is_reply_component",
    "media_components",
    "plain_text_of",
    "request_contexts",
]
