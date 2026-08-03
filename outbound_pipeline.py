"""Ordered outbound text processing for the filter plugin."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from astrbot.api.event import AstrMessageEvent

from .content_guard import SAFE_REPLY, evaluate_output
from .pipelines import (
    clean_garbage,
    de_ai_flavor,
    deidentify_tool_names,
    filter_sensitive,
    remove_tool_narration,
    replace_tool_leakage,
    replace_user,
    strip_markdown,
)
from .segmentation import apply_segmentation_and_style


@dataclass(frozen=True)
class ProcessedText:
    """The result of processing one visible Plain component."""

    text: str
    changed: bool
    guard_blocked: bool
    stats: dict[str, int]


class OutboundTextPipeline:
    """Apply the existing outbound transformations in one testable unit."""

    def __init__(
        self,
        *,
        context: Any,
        get_config: Callable[[str, Any], Any],
        get_guard_terms: Callable[[], list[str]],
        segmentation_and_style=apply_segmentation_and_style,
    ) -> None:
        self.context = context
        self.get_config = get_config
        self.get_guard_terms = get_guard_terms
        self.segmentation_and_style = segmentation_and_style

    async def process(
        self,
        text: str,
        event: AstrMessageEvent,
        *,
        strict_guard: bool = False,
    ) -> ProcessedText:
        original = text or ""
        value = original
        stats: dict[str, int] = {}

        value = self._apply("清理元数据", clean_garbage, value, stats)
        value = self._apply("替换用户称呼", replace_user, value, stats)
        value = self._apply("过滤敏感信息", filter_sensitive, value, stats)
        value = self._apply("拦截工具流程泄露", replace_tool_leakage, value, stats)
        value = self._apply("清理工具叙述", remove_tool_narration, value, stats)
        value = self._apply("工具名称脱敏", deidentify_tool_names, value, stats)

        if self.get_config("enable_de_ai_flavor", True):
            value = self._apply("去除 AI 味", de_ai_flavor, value, stats)

        value = await self._apply_async(
            "分段与文风优化",
            self.segmentation_and_style,
            value,
            self.context,
            self.get_config,
            stats=stats,
        )
        value = self._apply("清理 Markdown", strip_markdown, value, stats)
        value = self._apply("再次过滤敏感信息", filter_sensitive, value, stats)

        guard_blocked = False
        if self.get_config("enable_content_guard", True):
            decision = evaluate_output(
                value,
                self.get_guard_terms(),
                strict=strict_guard,
            )
            if decision.blocked:
                value = SAFE_REPLY
                guard_blocked = True
                stats["content_guard"] = stats.get("content_guard", 0) + 1

        return ProcessedText(
            text=value,
            changed=value != original,
            guard_blocked=guard_blocked,
            stats=stats,
        )

    @staticmethod
    def _apply(name: str, function, text: str, stats: dict[str, int]) -> str:
        value = function(text)
        if value != text:
            stats[name] = stats.get(name, 0) + 1
        return value

    @staticmethod
    async def _apply_async(
        name: str,
        function,
        text: str,
        *args,
        stats: dict[str, int],
    ) -> str:
        value = await function(text, *args)
        if value != text:
            stats[name] = stats.get(name, 0) + 1
        return value
