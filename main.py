"""AstrBot plugin entry: strip Markdown syntax from outgoing text."""

from __future__ import annotations

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter as _event_filter
from astrbot.api.message_components import Plain
from astrbot.api.star import Context, Star

from .pipelines import strip_markdown


class LanguageLogicOptimizer(Star):
    """Strip common Markdown presentation syntax from outgoing messages."""

    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.config = config

    @_event_filter.on_decorating_result()
    async def on_decorating_result(self, event: AstrMessageEvent):
        """Filter Markdown syntax out of every plain-text component."""
        if not event:
            return

        try:
            result = event.get_result()
        except Exception:
            logger.error("[markdown] 无法读取回复结果", exc_info=True)
            return
        if not result or not getattr(result, "chain", None):
            return

        chain = result.chain
        _coalesce_adjacent_plain_components(chain)

        changed = False
        for comp in chain:
            if not isinstance(comp, Plain):
                continue
            original = comp.text or ""
            if not original:
                continue
            try:
                cleaned = strip_markdown(original)
            except Exception:
                logger.error("[markdown] 清洗失败，保留原文", exc_info=True)
                continue
            if cleaned != original:
                if not cleaned:
                    # 清洗结果为空（如整条消息只有 "---"）时保留原文，避免发送空消息
                    continue
                comp.text = cleaned
                changed = True

        if changed:
            logger.info("[markdown] 已过滤输出中的 Markdown 语法")


def _coalesce_adjacent_plain_components(chain) -> None:
    """Merge adjacent Plain components so Markdown split across them is seen."""
    previous_plain = None
    for comp in chain:
        if not isinstance(comp, Plain):
            previous_plain = None
            continue
        if previous_plain is None:
            previous_plain = comp
            continue
        previous_plain.text = (previous_plain.text or "") + (comp.text or "")
        comp.text = ""
