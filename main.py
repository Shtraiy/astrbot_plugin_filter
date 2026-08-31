"""AstrBot plugin entry: session merge window, self-reply marking, and guard."""

from __future__ import annotations

import asyncio
import math
import random
import re
from collections.abc import Mapping
from typing import Any

from astrbot.api import logger
from astrbot.api.all import MessageChain
from astrbot.api.event import AstrMessageEvent, filter as _event_filter
from astrbot.api.message_components import Plain
from astrbot.api.star import Context, Star

from .content_guard import SAFE_REPLY, evaluate_input, parse_terms
from .event_access import event_has_content
from .interruption_guard import (
    is_interruption_placeholder_text,
    scrub_interruption_placeholders,
)
from .merge_window import MergeWindowManager
from .onboarding_guard import OnboardingGuard
from .reply_coordinator import ReplyCoordinator
from .self_reply_marker import (
    SelfReplyMarker,
    annotate_assistant_expression_claims,
    annotate_memory_media_attribution,
    append_referenced_image_note,
    append_text_only_media_note,
    append_user_media_note,
    attach_quoted_images,
    has_referenced_image,
    has_user_media,
    mark_current_prompt_media_boundary,
    mark_context_media_ownership,
    mark_recent_self_meme_context,
    strip_recent_self_meme_context,
)
from .smart_segment import split_reply
from .task_commitment_guard import inject_task_execution_instruction

MEDIA_ONLY_PROMPT = "用户发送了一张图片/文件，请识别内容并回应。"


class LanguageLogicOptimizer(Star):
    """Coalesce same-user segmented messages and mark the bot's own replies."""

    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.config = config
        self._pending_segment_tasks: set[asyncio.Task] = set()
        self._onboarding_guard: OnboardingGuard | None = None
        self._message_merger: MergeWindowManager | None = None
        self._reply_coordinator: ReplyCoordinator | None = None
        self._self_reply_marker: SelfReplyMarker | None = None

    def _get_reply_coordinator(self) -> ReplyCoordinator:
        coordinator = getattr(self, "_reply_coordinator", None)
        if coordinator is None:
            coordinator = ReplyCoordinator(event_is_wake_up=self._event_is_wake_up)
            self._reply_coordinator = coordinator
        return coordinator

    def _get_message_merger(self) -> MergeWindowManager:
        merger = getattr(self, "_message_merger", None)
        if merger is None:
            merger = MergeWindowManager(get_config=self._get_config)
            self._message_merger = merger
        return merger

    def _get_self_reply_marker(self) -> SelfReplyMarker:
        marker = getattr(self, "_self_reply_marker", None)
        if marker is None:
            marker = SelfReplyMarker(get_config=self._get_config)
            self._self_reply_marker = marker
        return marker

    def _get_onboarding_guard(self) -> OnboardingGuard:
        guard = getattr(self, "_onboarding_guard", None)
        if guard is None:
            guard = OnboardingGuard(get_config=self._get_config)
            self._onboarding_guard = guard
        return guard

    @_event_filter.event_message_type(_event_filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent) -> None:
        """Capture same-user non-wake follow-ups while a window is open.

        In-flight replies are never interrupted: once the window closes and
        the reply is being generated, new messages are left to AstrBot's
        native follow-up handling.
        """
        if not self._get_config("enable_message_merge", True):
            return
        if not event_has_content(event):
            return
        try:
            merger = self._get_message_merger()
            if merger.is_window_open(event):
                merger.capture(event)
        except Exception:
            logger.warning("[消息合并] 捕获消息失败", exc_info=True)

    @_event_filter.on_waiting_llm_request(priority=1000)
    async def on_waiting_llm_request(self, event: AstrMessageEvent) -> None:
        """Open/merge the sliding window; never interrupt an in-flight reply."""
        if not event_has_content(event):
            return
        merger = self._get_message_merger()
        coordinator = self._get_reply_coordinator()
        merge_key = (
            merger.window_key(event)
            if self._get_config("enable_message_merge", True)
            else None
        )
        window_result = "none"

        if merge_key is not None:
            window_result = await self._handle_window_phase(event, merger)
            if window_result == "consumed":
                return

        if not await coordinator.admit_wakeup(event):
            return
        if merge_key is not None and not coordinator.is_session_busy(event) and window_result != "cancel_quote":
            await self._open_merge_window(event, merger)

    async def _handle_window_phase(
        self,
        event: AstrMessageEvent,
        merger: MergeWindowManager,
    ) -> str:
        """Handle a same-user follow-up while the window is open."""
        if not merger.is_window_open(event):
            return "none"
        if merger.message_has_quote(event):
            # A quoted-message wake-up (e.g. "quote my just-sent image and
            # @bot") cannot be merged; cancel the pending window so the
            # media-only first message does not also fire, and let AstrBot
            # handle the quoted image natively.
            old = merger.cancel_window(event)
            if old is not None:
                _stop_event(old)
            return "cancel_quote"
        if merger.merge_wake(event):
            event.stop_event()
            return "consumed"
        if merger.is_captured(event):
            event.stop_event()
            return "consumed"
        return "none"

    async def _open_merge_window(
        self,
        event: AstrMessageEvent,
        merger: MergeWindowManager,
    ) -> None:
        """Hold the event until the user stays silent for the full window."""
        if not merger.start_window(event):
            return
        try:
            while True:
                if _event_is_stopped(event):
                    return
                remaining = merger.quiet_remaining(
                    event, self._get_merge_window_seconds()
                )
                if remaining <= 0:
                    break
                await asyncio.sleep(min(remaining, 0.2))
        finally:
            if _event_is_stopped(event):
                merger.clear_state(event)
                return
            merged = merger.finalize_window(event)
            if (
                not _event_is_stopped(event)
                and not (merged or "").strip()
                and merger.has_media(event)
            ):
                merged = MEDIA_ONLY_PROMPT
            event.message_str = merged

    @_event_filter.on_llm_request(priority=1000)
    async def on_llm_request(self, event: AstrMessageEvent, req) -> None:
        """Guard + admission + content guard before LLM."""
        if _event_is_stopped(event):
            self._get_message_merger().clear_state(event)
            return
        if not await self._get_reply_coordinator().admit_wakeup(event):
            return
        if not self._get_config("enable_content_guard", True):
            return

        input_text = _extract_input_text(event, req)
        if not input_text:
            return

        strict = (
            self._get_onboarding_guard().touch(event)
            or self._guard_mode() == "strict"
        )
        decision = evaluate_input(input_text, self._get_guard_terms(), strict=strict)
        if not decision.blocked:
            return

        event.stop_event()
        try:
            await self._send_guard_reply(event, decision.category)
        finally:
            self._get_reply_coordinator().finish_active(event)
            self._get_message_merger().clear_state(event)

    @_event_filter.on_llm_request(priority=500)
    async def on_llm_request_task_guard(self, event: AstrMessageEvent, req) -> None:
        """Inject the task-execution instruction before the LLM call."""
        if not self._get_config("enable_task_execution_guard", True):
            return
        if _event_is_stopped(event) or req is None:
            return
        try:
            if inject_task_execution_instruction(req):
                logger.info("[任务执行] 已注入任务执行指令")
        except Exception:
            logger.debug("[任务执行] 注入任务执行指令失败", exc_info=True)

    @_event_filter.on_llm_request(priority=-1000)
    async def on_llm_request_marking(self, event: AstrMessageEvent, req) -> None:
        """Inject ownership corrections after downstream on_llm_request hooks.

        Runs last so the corrections land after livingmemory's injected
        memories instead of being outranked by them.
        """
        if _event_is_stopped(event):
            return
        await self._apply_self_reply_marking(event, req)

    async def _apply_self_reply_marking(self, event: AstrMessageEvent, req) -> None:
        if req is None:
            return
        try:
            removed = scrub_interruption_placeholders(getattr(req, "contexts", None))
            if removed:
                logger.info("[自回复标记] 已清理中断占位符 %d 条", removed)
            marked = mark_context_media_ownership(req)
            if marked:
                logger.info("[自回复标记] 已标注 %d 条历史消息的媒体归属", marked)
            if self._get_config("fix_memory_media_attribution", True):
                fixed = annotate_memory_media_attribution(req)
                if fixed:
                    logger.info(
                        "[自回复标记] 已就地纠偏记忆中的媒体/表情归属误判 %d 处",
                        fixed,
                    )
            if self._get_config("annotate_assistant_expression_claims", True):
                claims_fixed = annotate_assistant_expression_claims(req)
                if claims_fixed:
                    logger.info(
                        "[自回复标记] 已就地标注历史中机器人对用户表情/眼神的表述 %d 处",
                        claims_fixed,
                    )
            marker = self._get_self_reply_marker()
            if self._get_config("enable_self_reply_mark", True):
                if marker.mark_own_recent_replies(req, event):
                    logger.info("[自回复标记] 已注入最近自回复归属标记")
            if self._get_config("mark_recent_self_meme_context", True):
                marked = mark_recent_self_meme_context(req)
                if marked:
                    logger.info(
                        "[自回复标记] 已改写自发表情包描述为机器人归属标记 %d 处",
                        marked,
                    )
            elif self._get_config("strip_recent_self_meme_context", False):
                removed = strip_recent_self_meme_context(req)
                if removed:
                    logger.info("[自回复标记] 已移除自发表情包描述 %d 处", removed)
            if self._get_config("guard_own_media_attribution", True):
                try:
                    user_has_media = has_user_media(event)
                except Exception:
                    user_has_media = False
                if user_has_media:
                    try:
                        if mark_current_prompt_media_boundary(req, event):
                            logger.info("[自回复标记] 已标注当前消息媒体归属")
                    except Exception:
                        logger.debug(
                            "[自回复标记] 当前消息媒体标注失败", exc_info=True
                        )
                    if append_user_media_note(req, event):
                        logger.info("[自回复标记] 用户媒体消息，已注入归属提示")
                else:
                    try:
                        user_has_ref_image = has_referenced_image(event)
                    except Exception:
                        user_has_ref_image = False
                    if user_has_ref_image:
                        try:
                            attached = await attach_quoted_images(req, event)
                        except Exception:
                            attached = 0
                        if append_referenced_image_note(req):
                            logger.info(
                                "[自回复标记] 引用图片消息，已注入识图提示"
                                + (f"，兜底附加引用图 {attached} 张" if attached else "")
                            )
                    elif append_text_only_media_note(req):
                        logger.info("[自回复标记] 纯文字消息，已注入图片归属提示")
        except Exception:
            logger.debug("[自回复标记] 标记注入失败", exc_info=True)

    @_event_filter.on_llm_response(priority=1000)
    async def on_llm_response_guard(self, event: AstrMessageEvent, resp) -> None:
        """Stop AstrBot interruption placeholders from being recorded."""
        try:
            if not _event_is_stopped(event) and _resp_is_interruption_placeholder(resp):
                event.stop_event()
                logger.info("[自回复标记] 已拦截中断占位符响应，阻止写入记忆")
        except Exception:
            logger.debug("[消息合并] 响应守卫失败", exc_info=True)

    @_event_filter.on_decorating_result()
    async def on_decorating_result(self, event: AstrMessageEvent) -> None:
        if not event:
            return
        try:
            result = event.get_result()
            chain = getattr(result, "chain", None)
            origin = getattr(event, "unified_msg_origin", None)
            if origin and result is not None:
                text = _result_plain_text(result)
                if text and self._get_self_reply_marker().recently_sent_duplicate(
                    origin, text
                ):
                    logger.info("[消息合并] 检测到重复回复，丢弃避免复读")
                    if chain is not None:
                        chain[:] = []
                    self._get_message_merger().clear_state(event)
                    return
            self._get_message_merger().clear_state(event)
            # 合并窗口：让 AstrBot 的引用回复指向最后一条用户消息，
            # 而不是第一条唤醒消息。
            try:
                last_id = event.get_extra("merge_last_message_id")
                message_obj = getattr(event, "message_obj", None)
                if last_id is not None and message_obj is not None:
                    message_obj.message_id = last_id
            except Exception:
                logger.debug("[消息合并] 重定向引用失败", exc_info=True)
            if chain:
                for comp in chain:
                    if isinstance(comp, Plain) and comp.text:
                        cleaned = _strip_structure_tags(comp.text)
                        if cleaned != comp.text:
                            comp.text = cleaned
            await self._maybe_segment_reply(event)
        except Exception:
            logger.debug("[消息合并] 结果清理失败", exc_info=True)

    async def _maybe_segment_reply(self, event: AstrMessageEvent) -> None:
        """Split a long plain-text reply via the configured segment provider."""
        if not self._get_config("enable_llm_segment", False):
            return
        result = getattr(event, "get_result", lambda: None)()
        chain = getattr(result, "chain", None) if result is not None else None
        if not isinstance(chain, list) or not chain:
            return
        if not all(isinstance(comp, Plain) for comp in chain):
            return
        text = _result_plain_text(result)
        if not text:
            return
        try:
            segments = await split_reply(text, self.context, self._get_config)
        except Exception:
            logger.warning("[智能分段] 分段失败，按原文发送", exc_info=True)
            return
        if not segments or len(segments) < 2:
            return
        chain[:] = [Plain(segments[0])]
        try:
            event.set_extra("segment_followups", list(segments[1:]))
        except Exception:
            logger.debug("[智能分段] 记录补发段失败", exc_info=True)

    @_event_filter.after_message_sent(priority=1000)
    async def after_message_sent(self, event: AstrMessageEvent) -> None:
        try:
            self._get_self_reply_marker().record_sent_reply(event)
        except Exception:
            logger.debug("[自回复标记] 记录发送失败", exc_info=True)
        self._get_message_merger().clear_state(event)
        self._get_reply_coordinator().finish_active(event)
        self._send_segment_followups(event)

    def _send_segment_followups(self, event: AstrMessageEvent) -> None:
        """Queue the remaining segments for delayed follow-up sends."""
        getter = getattr(event, "get_extra", None)
        if not callable(getter):
            return
        try:
            followups = getter("segment_followups")
        except Exception:
            return
        if not followups:
            return
        origin = getattr(event, "unified_msg_origin", None)
        sender = getattr(self.context, "send_message", None)
        if not origin or not callable(sender):
            return
        delay_min = self._get_float_config("segment_delay_min", 0.8)
        delay_max = self._get_float_config("segment_delay_max", 2.0)
        if delay_min < 0.0 or delay_max < delay_min:
            delay_min, delay_max = 0.8, 2.0
        task = asyncio.create_task(
            self._send_segment_task(origin, followups, delay_min, delay_max)
        )
        self._pending_segment_tasks.add(task)
        task.add_done_callback(self._pending_segment_tasks.discard)

    async def _send_segment_task(
        self,
        origin: str,
        followups: list[str],
        delay_min: float,
        delay_max: float,
    ) -> None:
        for index, seg in enumerate(followups, start=2):
            await asyncio.sleep(random.uniform(delay_min, delay_max))
            try:
                chain = MessageChain().message(seg)
                await self.context.send_message(origin, chain)
                logger.info("[智能分段] 已发送第 %d 段", index)
                self._get_self_reply_marker().record_sent_text(origin, seg)
            except Exception:
                logger.warning("[智能分段] 补发第 %d 段失败", index, exc_info=True)

    def _get_config(self, key: str, default=None):
        context = getattr(self, "context", None)
        for source in (getattr(self, "config", None), getattr(context, "config", None)):
            if source is None:
                continue
            value = _read_config_value(source, key, _MISSING)
            if value is not _MISSING:
                return value
        return default

    def _get_float_config(self, key: str, default: float) -> float:
        value = self._get_config(key, default)
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _event_is_wake_up(self, event: AstrMessageEvent) -> bool:
        value = getattr(event, "is_at_or_wake_command", None)
        if value is not None:
            return bool(value)
        checker = getattr(event, "is_wake_up", None)
        if callable(checker):
            try:
                return bool(checker())
            except Exception:
                return True
        return True

    def _get_guard_terms(self) -> list[str]:
        return parse_terms(self._get_config("content_guard_block_terms", ""))

    def _guard_mode(self) -> str:
        value = str(
            self._get_config("content_guard_mode", "balanced") or "balanced"
        ).lower()
        return value if value in {"balanced", "strict"} else "balanced"

    async def _send_guard_reply(self, event: AstrMessageEvent, category: str) -> None:
        origin = getattr(event, "unified_msg_origin", None)
        sender = getattr(self.context, "send_message", None)
        if not origin or not callable(sender):
            return
        try:
            await sender(origin, MessageChain().message(SAFE_REPLY))
            logger.info("[content_guard] blocked category=%s", category)
        except Exception:
            logger.warning("[content_guard] failed to send safe reply", exc_info=True)

    def _get_merge_window_seconds(self) -> float:
        """Return the same-user merge window duration, bounded to 1-30 seconds."""
        value = self._get_float_config("merge_window_seconds", 6.0)
        if not math.isfinite(value):
            return 6.0
        return min(30.0, max(1.0, value))

_MISSING = object()


def _event_is_stopped(event) -> bool:
    checker = getattr(event, "is_stopped", None)
    if callable(checker):
        try:
            return bool(checker())
        except Exception:
            return False
    return bool(getattr(event, "stopped", False))


def _stop_event(event) -> None:
    """Best-effort stop of an event (e.g. a cancelled window owner)."""
    stopper = getattr(event, "stop_event", None)
    if callable(stopper):
        try:
            stopper()
        except Exception:
            logger.debug("[消息合并] 停止事件失败", exc_info=True)


def _resp_is_interruption_placeholder(resp) -> bool:
    """True when the LLM response is AstrBot's agent-interruption marker."""
    if resp is None:
        return False
    return is_interruption_placeholder_text(getattr(resp, "completion_text", None))


def _strip_structure_tags(text: str) -> str:
    """Remove stray quote/blockquote structure tags echoed by the model."""
    return re.sub(
        r"</?(?:blockquote|quote|p|div|br|span|code|pre)\s*/?>",
        "",
        text or "",
    ).strip()


def _result_plain_text(result) -> str:
    """Best-effort plain-text extraction from a MessageEventResult-like object."""
    getter = getattr(result, "get_plain_text", None)
    if callable(getter):
        try:
            text = getter()
            if isinstance(text, str):
                return text.strip()
        except Exception:
            pass
    chain = getattr(result, "chain", None)
    if isinstance(chain, list):
        parts = [
            getattr(comp, "text", "") or ""
            for comp in chain
            if isinstance(comp, Plain)
        ]
        return " ".join(parts).strip()
    return ""


def _read_config_value(source, key: str, default):
    if isinstance(source, Mapping):
        return source.get(key, default)
    getter = getattr(source, "get", None)
    if callable(getter):
        try:
            return getter(key, default)
        except TypeError:
            try:
                value = getter(key)
            except Exception:
                return default
            return default if value is None else value
        except Exception:
            return default
    return getattr(source, key, default)


def _extract_input_text(event, req) -> str:
    """Best-effort extraction of the user's raw message without reading system prompts."""
    for source in (event, req):
        if source is None:
            continue
        if isinstance(source, Mapping):
            values = [
                source.get(name)
                for name in ("message_str", "user_message", "message", "raw_message")
            ]
        else:
            values = []
            for name in ("message_str", "user_message", "message", "raw_message"):
                value = getattr(source, name, None)
                if callable(value):
                    try:
                        value = value()
                    except TypeError:
                        value = None
                    except Exception:
                        value = None
                values.append(value)
            getter = getattr(source, "get_message_str", None)
            if callable(getter):
                try:
                    values.insert(0, getter())
                except Exception:
                    pass
        for value in values:
            if isinstance(value, str) and value.strip():
                return value
    return ""
