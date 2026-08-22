"""AstrBot plugin entry: session merge window, self-reply marking, and guard."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Mapping
from dataclasses import dataclass

from astrbot.api import logger
from astrbot.api.all import MessageChain
from astrbot.api.event import AstrMessageEvent, filter as _event_filter
from astrbot.api.star import Context, Star

from .content_guard import SAFE_REPLY, evaluate_input, is_group_origin, parse_terms
from .merge_guards import stop_if_superseded
from .merge_window import MergeWindowManager
from .reply_coordinator import ReplyCoordinator
from .self_reply_marker import (
    SelfReplyMarker,
    append_referenced_image_note,
    append_text_only_media_note,
    append_user_media_note,
    has_referenced_image,
    has_user_media,
    strip_recent_self_meme_context,
)


MAX_ONBOARDING_STATES = 4096
MEDIA_ONLY_PROMPT = "用户发送了一张图片/文件，请识别内容并回应。"


@dataclass
class _OnboardingState:
    started_at: float
    message_count: int = 0


class LanguageLogicOptimizer(Star):
    """Coalesce same-user segmented messages and mark the bot's own replies."""

    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.config = config
        self._pending_tasks: set[asyncio.Task] = set()
        self._onboarding_states: dict[str, _OnboardingState] = {}
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

    @_event_filter.event_message_type(_event_filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent) -> None:
        """Capture window-phase follow-ups; promote planning-phase supplements."""
        if not self._get_config("enable_message_merge", True):
            return
        try:
            merger = self._get_message_merger()
            if merger.is_window_open(event):
                merger.capture(event)
                return
            if merger.promote_planning(event):
                logger.info("[消息合并] 规划期补充消息已提升为唤醒，将合并重生成")
        except Exception:
            logger.debug("[消息合并] 捕获消息失败", exc_info=True)

    @_event_filter.on_waiting_llm_request(priority=1000)
    async def on_waiting_llm_request(self, event: AstrMessageEvent) -> None:
        """Merge same-user segments; supersede old planning when supplemented."""
        merger = self._get_message_merger()
        coordinator = self._get_reply_coordinator()
        merge_key = (
            merger.user_key(event)
            if self._get_config("enable_message_merge", True)
            else None
        )
        skip_window = False

        if merge_key is not None:
            if merger.is_window_open(event):
                if merger.message_has_quote(event):
                    # A quoted-message wake-up (e.g. "quote my just-sent image
                    # and @bot") cannot be merged; cancel the pending window so
                    # the media-only first message does not also fire, and let
                    # AstrBot handle the quoted image natively.
                    old = merger.cancel_window(event)
                    if old is not None:
                        coordinator.supersede_active_event(old)
                    skip_window = True
                else:
                    merger.merge_wake(event)
                    event.stop_event()
                    return

            pending = merger.take_planning(event)
            if pending is not None:
                old_event, earlier_text, earlier_media, pipeline_task = pending
                if old_event is not None:
                    coordinator.supersede_active_event(old_event)
                    if (
                        self._get_config("merge_task_cancel", False)
                        and pipeline_task is not None
                        and not pipeline_task.done()
                    ):
                        try:
                            pipeline_task.cancel()
                        except Exception:
                            logger.debug("[消息合并] 旧任务取消失败", exc_info=True)
                event.message_str = merger.join_text(
                    earlier_text,
                    str(getattr(event, "message_str", "") or ""),
                )
                merger.attach_media(event, earlier_media)
                if not (event.message_str or "").strip() and merger.has_media(event):
                    event.message_str = MEDIA_ONLY_PROMPT
                if not await coordinator.admit_wakeup(event):
                    return
                merger.rearm_planning(
                    event,
                    event.message_str,
                    pipeline_task=asyncio.current_task(),
                )
                return

        if not await coordinator.admit_wakeup(event):
            return
        if (
            merge_key is not None
            and not coordinator.is_session_busy(event)
            and not skip_window
        ):
            pipeline_task = asyncio.current_task()
            if merger.start_window(event, pipeline_task=pipeline_task):
                try:
                    await asyncio.sleep(self._get_merge_window_seconds())
                finally:
                    merged = merger.finalize_window(event)
                    if (
                        not _event_is_stopped(event)
                        and not (merged or "").strip()
                        and merger.has_media(event)
                    ):
                        merged = MEDIA_ONLY_PROMPT
                    event.message_str = merged
                    merger.sync_pending_text(event, merged)

    @_event_filter.on_llm_request(priority=1000)
    async def on_llm_request(self, event: AstrMessageEvent, req) -> None:
        """Guard + admission + self-reply marking + content guard before LLM."""
        if _event_is_stopped(event):
            self._get_message_merger().clear_owner(event)
            return
        if not await self._get_reply_coordinator().admit_wakeup(event):
            return
        self._apply_self_reply_marking(event, req)
        if not self._get_config("enable_content_guard", True):
            return

        input_text = _extract_input_text(event, req)
        if not input_text:
            return

        strict = self._touch_onboarding_state(event) or self._guard_mode() == "strict"
        decision = evaluate_input(input_text, self._get_guard_terms(), strict=strict)
        if not decision.blocked:
            return

        event.stop_event()
        try:
            await self._send_guard_reply(event, decision.category)
        finally:
            self._get_reply_coordinator().finish_active(event)
            self._get_message_merger().clear_owner(event)

    def _apply_self_reply_marking(self, event: AstrMessageEvent, req) -> None:
        if req is None:
            return
        try:
            marker = self._get_self_reply_marker()
            if self._get_config("enable_self_reply_mark", True):
                if marker.mark_own_recent_replies(req, event):
                    logger.info("[自回复标记] 已注入最近自回复归属标记")
            if self._get_config("strip_recent_self_meme_context", True):
                removed = strip_recent_self_meme_context(req)
                if removed:
                    logger.info("[自回复标记] 已移除自发表情包描述 %d 处", removed)
            if self._get_config("guard_own_media_attribution", True):
                try:
                    user_has_media = has_user_media(event)
                except Exception:
                    user_has_media = False
                if user_has_media:
                    if append_user_media_note(req):
                        logger.info("[自回复标记] 用户媒体消息，已注入归属提示")
                else:
                    try:
                        user_has_ref_image = has_referenced_image(event)
                    except Exception:
                        user_has_ref_image = False
                    if user_has_ref_image and append_referenced_image_note(req):
                        logger.info("[自回复标记] 引用图片消息，已注入识图提示")
                    elif append_text_only_media_note(req):
                        logger.info("[自回复标记] 纯文字消息，已注入图片归属提示")
        except Exception:
            logger.debug("[自回复标记] 标记注入失败", exc_info=True)

    @_event_filter.on_llm_response(priority=1000)
    async def on_llm_response_guard(self, event: AstrMessageEvent, resp) -> None:
        """Stop superseded events before downstream hooks (e.g. livingmemory)."""
        try:
            stop_if_superseded(self._get_reply_coordinator(), event)
        except Exception:
            logger.debug("[消息合并] 响应守卫失败", exc_info=True)

    @_event_filter.on_decorating_result()
    async def on_decorating_result(self, event: AstrMessageEvent) -> None:
        if not event:
            return
        try:
            if self._get_reply_coordinator().discard_superseded_result(event):
                return
            self._get_message_merger().clear_owner(event)
        except Exception:
            logger.debug("[消息合并] 结果清理失败", exc_info=True)

    @_event_filter.after_message_sent(priority=1000)
    async def after_message_sent(self, event: AstrMessageEvent) -> None:
        try:
            self._get_self_reply_marker().record_sent_reply(event)
        except Exception:
            logger.debug("[自回复标记] 记录发送失败", exc_info=True)
        self._get_message_merger().clear_owner(event)
        self._get_reply_coordinator().finish_active(event)

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

    def _is_group_event(self, event: AstrMessageEvent) -> bool:
        if is_group_origin(getattr(event, "unified_msg_origin", None)):
            return True
        return bool(getattr(event, "group_id", None))

    def _touch_onboarding_state(self, event: AstrMessageEvent) -> bool:
        if not self._is_group_event(event):
            return False
        origin = str(getattr(event, "unified_msg_origin", "") or "")
        if not origin:
            return False
        now = asyncio.get_running_loop().time()
        self._prune_onboarding_states(now)
        state = self._onboarding_states.get(origin)
        if state is None:
            if len(self._onboarding_states) >= MAX_ONBOARDING_STATES:
                oldest = min(
                    self._onboarding_states.items(),
                    key=lambda item: item[1].started_at,
                )[0]
                self._onboarding_states.pop(oldest, None)
            state = _OnboardingState(started_at=now)
            self._onboarding_states[origin] = state
        state.message_count += 1
        return self._onboarding_active(event)

    def _onboarding_duration_seconds(self) -> float:
        value = self._get_float_config("onboarding_guard_minutes", 30.0)
        return max(0.0, value * 60) if math.isfinite(value) else 0.0

    def _onboarding_message_limit(self) -> int:
        value = self._get_float_config("onboarding_guard_messages", 20)
        return max(0, int(value)) if math.isfinite(value) else 0

    def _prune_onboarding_states(self, now: float) -> None:
        duration = self._onboarding_duration_seconds()
        message_limit = self._onboarding_message_limit()
        for origin, state in list(self._onboarding_states.items()):
            elapsed_active = duration > 0 and now - state.started_at < duration
            count_active = message_limit > 0 and state.message_count <= message_limit
            if not (elapsed_active or count_active):
                self._onboarding_states.pop(origin, None)

    def _onboarding_active(self, event: AstrMessageEvent) -> bool:
        if not self._is_group_event(event):
            return False
        origin = str(getattr(event, "unified_msg_origin", "") or "")
        state = self._onboarding_states.get(origin)
        if state is None:
            return False
        duration = self._onboarding_duration_seconds()
        message_limit = self._onboarding_message_limit()
        elapsed_active = (
            duration > 0
            and asyncio.get_running_loop().time() - state.started_at < duration
        )
        count_active = message_limit > 0 and state.message_count <= message_limit
        return elapsed_active or count_active

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

    def _track_task(self, task: asyncio.Task) -> None:
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)
        task.add_done_callback(_log_task_exception)


_MISSING = object()


def _event_is_stopped(event) -> bool:
    checker = getattr(event, "is_stopped", None)
    if callable(checker):
        try:
            return bool(checker())
        except Exception:
            return False
    return bool(getattr(event, "stopped", False))


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


def _log_task_exception(task: asyncio.Task) -> None:
    try:
        exc = task.exception()
        if exc is not None:
            logger.warning("[任务] 后台任务异常：%s", exc, exc_info=True)
    except asyncio.CancelledError:
        pass
    except Exception:
        pass
