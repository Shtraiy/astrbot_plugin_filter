"""Guard helpers for events superseded by a merge regeneration.

AstrBot's ``call_event_hook`` short-circuits after a handler stops the event,
so stopping a superseded event in ``on_llm_request`` / ``on_llm_response``
prevents downstream plugins (e.g. livingmemory) from recalling on, or
recording, stale first-part replies.

These helpers are pure and intentionally import no AstrBot Star machinery;
the hook decorators live on the Star class in ``main.py``.
"""

from __future__ import annotations

import re
from typing import Any

_MENTION_LEAD_RE = re.compile(r"^\s*(?:@[^\s，。！？!?；;、]+)\s*")

# 修正词：用户对正在生成中的回复表达"推翻/换一个/重来"。
# 命中时即使 provider 已开始调用也打断并合并重生成。
CORRECTION_TERMS = (
    "再想想",
    "不对",
    "等一下",
    "换一个",
    "重新",
    "忘了",
    "不是这个",
)

# 修正词前的否定前缀：出现则不算修正（"不用再想想了"）。
_CORRECTION_NEGATIONS = ("不", "别", "没", "不要", "不用")


def is_correction_follow_up(text: str | None) -> bool:
    """Return True when the follow-up text reads like a correction.

    Rules:
    - leading ``@bot`` mention is stripped;
    - the text must be short-ish (no long explanations) and contain a
      correction term;
    - a negation immediately before the term cancels the match.
    """
    if not text:
        return False
    cleaned = _MENTION_LEAD_RE.sub("", str(text)).strip()
    if not cleaned or len(cleaned) > 32:
        return False
    for term in CORRECTION_TERMS:
        idx = cleaned.find(term)
        if idx < 0:
            continue
        prefix = cleaned[:idx].strip()
        if prefix and any(prefix.endswith(neg) for neg in _CORRECTION_NEGATIONS):
            continue
        return True
    return False


def should_interrupt_running_reply(
    provider_call_started: bool,
    is_correction: bool,
) -> bool:
    """Decide whether an in-flight reply should be interrupted.

    - provider not started yet -> interrupting is cheap, merge and regenerate;
    - provider already started -> let AstrBot's native follow-up take over,
      unless the user is explicitly correcting the in-flight reply.
    """
    return is_correction or not provider_call_started


def is_superseded_event(coordinator: Any, event: Any) -> bool:
    """Return True when the event was superseded by a merge regeneration."""
    checker = getattr(coordinator, "is_superseded", None)
    if callable(checker):
        try:
            return bool(checker(event))
        except Exception:
            return False
    return False


def stop_if_superseded(coordinator: Any, event: Any) -> bool:
    """Stop a superseded event so ``call_event_hook`` short-circuits.

    Idempotent: calling it again on the same event is harmless.
    """
    if not is_superseded_event(coordinator, event):
        return False
    stopper = getattr(event, "stop_event", None)
    if callable(stopper):
        stopper()
    return True


__all__ = [
    "CORRECTION_TERMS",
    "is_correction_follow_up",
    "is_superseded_event",
    "should_interrupt_running_reply",
    "stop_if_superseded",
]
