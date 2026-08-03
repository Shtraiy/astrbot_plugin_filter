"""Optional integration boundary for AstrBot Private Companion."""

from __future__ import annotations

import asyncio
import importlib as module_loader
import inspect
from typing import Any, Callable

from astrbot.api import logger


class PrivateCompanionAdapter:
    """Keep Private Companion details out of the normal reply pipeline."""

    _MODULE_NAMES = (
        "data.plugins.astrbot_plugin_private_companion.main",
        "astrbot_plugin_private_companion.main",
    )
    _MARKERS = (
        "private_companion_proactive_framework",
        "_private_companion_external_proactive_source",
        "_private_companion_proactive_chat_token",
        "_private_companion_proactive_delivery_umo",
    )

    def __init__(self, track_task: Callable[[asyncio.Task], None] | None = None):
        self._track_task = track_task or (lambda _task: None)
        self.module_loader = module_loader

    @classmethod
    def is_proactive_event(cls, event: Any | None) -> bool:
        if event is None:
            return False
        for marker in cls._MARKERS:
            value = getattr(event, marker, None)
            if isinstance(value, str):
                if value.strip():
                    return True
            elif value:
                return True
        if type(event).__name__ == "SyntheticPrivateWakeEvent":
            return True
        metadata = getattr(event, "platform_meta", None)
        description = str(getattr(metadata, "description", "") or "").strip().lower()
        return description == "syntheticprivatewake"

    @staticmethod
    def proactive_request_identity(event: Any | None) -> str:
        """Return the Companion attempt identity, if the event carries one."""
        if event is None:
            return ""
        for field in (
            "_private_companion_proactive_chat_attempt_id",
            "_private_companion_proactive_chat_token",
        ):
            value = str(getattr(event, field, "") or "").strip()
            if value:
                return f"{field}:{value}"
        return ""

    def schedule_cancel(self, owner_event: Any) -> None:
        token = str(
            getattr(owner_event, "_private_companion_proactive_chat_token", "") or ""
        ).strip()
        if not token:
            return
        try:
            task = asyncio.create_task(
                self.cancel(
                    str(getattr(owner_event, "unified_msg_origin", "") or ""),
                    token,
                )
            )
        except RuntimeError:
            return
        self._track_task(task)

    async def cancel(self, session_id: str, token: str) -> bool:
        """Best-effort cancellation; missing or broken companion never propagates."""
        for module_name in self._MODULE_NAMES:
            try:
                module = self.module_loader.import_module(module_name)
                getter = getattr(module, "get_private_companion_api", None)
                api = getter() if callable(getter) else None
                cancel = getattr(api, "cancel_proactive_chat", None)
                if not callable(cancel):
                    continue
                result = cancel(session_id, token=token)
                if inspect.isawaitable(result):
                    await result
                logger.info(
                    "[Private Companion] proactive reply cancellation requested origin=%s",
                    session_id,
                )
                return True
            except Exception:
                logger.debug(
                    "[Private Companion] cancellation unavailable; continue with local invalidation",
                    exc_info=True,
                )
        return False
