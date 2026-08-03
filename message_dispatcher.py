"""Single outbound path for delayed follow-up messages."""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import Awaitable, Callable

from astrbot.api import logger
from astrbot.api.all import MessageChain

from .reply_coordinator import ReplyCoordinator, ReplySession


@dataclass(frozen=True)
class DispatchPolicy:
    delay_min: float
    delay_max: float
    max_followups: int = 4


class MessageDispatcher:
    """Send follow-ups and always finish the owning reply session."""

    def __init__(self, context, coordinator: ReplyCoordinator):
        self.context = context
        self.coordinator = coordinator

    async def send_followups(
        self,
        origin: str,
        paragraphs: list[str],
        *,
        policy: DispatchPolicy,
        session: ReplySession,
        process_text: Callable[[str], Awaitable[str | None]] | None = None,
    ) -> None:
        delay_min = max(0.0, float(policy.delay_min))
        delay_max = max(0.0, float(policy.delay_max))
        if delay_min > delay_max:
            delay_min, delay_max = delay_max, delay_min
        max_followups = max(0, min(4, int(policy.max_followups)))
        bounded_paragraphs = paragraphs[:max_followups]

        try:
            for index, paragraph in enumerate(bounded_paragraphs):
                if session.cancel_requested or session.superseded_by_user:
                    break
                await asyncio.sleep(random.uniform(delay_min, delay_max))
                if session.cancel_requested or session.superseded_by_user:
                    break
                text = paragraph
                if process_text is not None:
                    text = await process_text(paragraph)
                if text is None or not str(text).strip():
                    continue
                logger.info(
                    "[分段发送] 准备发送第 %d/%d 条消息",
                    index + 2,
                    len(bounded_paragraphs) + 1,
                )
                try:
                    chain = MessageChain().message(text)
                    await self.context.send_message(origin, chain)
                    logger.info(
                        "[分段发送] 已发送第 %d/%d 条消息",
                        index + 2,
                        len(bounded_paragraphs) + 1,
                    )
                except Exception:
                    logger.warning(
                        "[分段发送] 第 %d 条消息发送失败",
                        index + 2,
                        exc_info=True,
                    )
        finally:
            self.coordinator.release(session, apply_cooldown=True)
