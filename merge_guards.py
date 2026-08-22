"""Guard helpers for events superseded by a merge regeneration.

AstrBot's ``call_event_hook`` short-circuits after a handler stops the event,
so stopping a superseded event in ``on_llm_request`` / ``on_llm_response``
prevents downstream plugins (e.g. livingmemory) from recalling on, or
recording, stale first-part replies.

These helpers are pure and intentionally import no AstrBot Star machinery;
the hook decorators live on the Star class in ``main.py``.
"""

from __future__ import annotations

from typing import Any


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


__all__ = ["is_superseded_event", "stop_if_superseded"]
