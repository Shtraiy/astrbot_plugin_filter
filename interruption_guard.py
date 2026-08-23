"""Hygiene helpers for AstrBot's agent-interruption placeholder messages.

AstrBot core writes ``Stop output.`` (user) / ``Output stopped.`` (assistant)
into the run context and final response whenever an agent run is aborted
(``/stop``, merge regeneration, or any ``request_stop``). livingmemory then
treats the assistant placeholder as real reply text and stores it, polluting
later memory recall queries.

These pure helpers let the filter plugin:

- detect the placeholder response in ``on_llm_response`` and stop the event so
  downstream hooks (livingmemory) never record it;
- scrub the placeholder pair from the per-request context so the model never
  sees the junk in its conversation history.

The module intentionally imports no AstrBot Star machinery.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


INTERRUPTION_PLACEHOLDERS = frozenset({"Stop output.", "Output stopped."})

_TEXT_TYPES = {"text", "input_text", "plain"}


def is_interruption_placeholder_text(text: Any) -> bool:
    """Return True only for exact interruption placeholder text."""
    if not isinstance(text, str):
        return False
    return text.strip() in INTERRUPTION_PLACEHOLDERS


def scrub_interruption_placeholders(contexts: Any) -> int:
    """Remove exact placeholder entries from an OpenAI-format context list.

    Entries are removed in place; multimodal entries (any non-text part) are
    always kept. Returns the number of removed entries.
    """
    if not isinstance(contexts, list):
        return 0
    kept: list[Any] = []
    removed = 0
    for entry in contexts:
        try:
            if _entry_is_placeholder(entry):
                removed += 1
                continue
        except Exception:
            pass
        kept.append(entry)
    if removed:
        contexts[:] = kept
    return removed


def _entry_is_placeholder(entry: Any) -> bool:
    if isinstance(entry, Mapping):
        content = entry.get("content")
    else:
        content = getattr(entry, "content", None)
    text = _entry_text(content)
    return text is not None and is_interruption_placeholder_text(text)


def _entry_text(content: Any) -> str | None:
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
    "INTERRUPTION_PLACEHOLDERS",
    "is_interruption_placeholder_text",
    "scrub_interruption_placeholders",
]
