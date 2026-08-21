"""AstrBot plugin entry: optimize outgoing text before it is sent."""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Mapping
from dataclasses import dataclass

from astrbot.api import logger
from astrbot.api.all import MessageChain
from astrbot.api.event import AstrMessageEvent, filter as _event_filter
from astrbot.api.message_components import Plain
from astrbot.api.star import Context, Star

from .content_guard import (
    SAFE_REPLY,
    evaluate_input,
    is_group_origin,
    parse_terms,
)
from .image_renderer import cleanup_temp_file, should_render_image, text_to_image
from .message_dispatcher import DispatchPolicy, MessageDispatcher
from .merge_window import MergeWindowManager
from .segmentation import (
    apply_segmentation_and_style,
    prepare_multi_message_parts,
)
from .outbound_pipeline import OutboundTextPipeline
from .reply_coordinator import GateState as _GateState
from .reply_coordinator import ReplyCoordinator, ReplySession
from .request_cleaner import (
    append_attribution_note,
    append_image_text_note,
    asks_about_image_text,
    asks_about_own_media,
    has_user_media,
    strip_assistant_media,
    strip_recent_self_meme_context,
)


@dataclass
class _OnboardingState:
    started_at: float
    message_count: int = 0


MAX_ONBOARDING_STATES = 4096
MAX_GATE_STATES = 4096
GATE_TTL_DEFAULT = 300.0
WAKEUP_INTERVAL_MIN_DEFAULT = 1.0
WAKEUP_INTERVAL_MAX_DEFAULT = 2.0
FILTER_REPLY_LOCK_EXTRA = "astrbot_plugin_filter_reply_lock"


class LanguageLogicOptimizer(Star):
    """Optimize outgoing text by cleaning metadata, tool traces, style, and layout."""

    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.config = config
        self._pending_tasks: set[asyncio.Task] = set()
        self._reply_locks: dict[str, asyncio.Lock] = {}
        self._gates: dict[str, _GateState] = {}
        self._pending_send: tuple[str, asyncio.Lock, AstrMessageEvent] | None = None
        self._pending_sends: dict[str, tuple[str, asyncio.Lock, AstrMessageEvent]] = {}
        self._onboarding_states: dict[str, _OnboardingState] = {}
        self._reply_coordinator = self._build_reply_coordinator()
        self._message_dispatcher = MessageDispatcher(
            self.context,
            self._reply_coordinator,
        )

    def _build_reply_coordinator(self) -> ReplyCoordinator:
        return ReplyCoordinator(
            get_gate_seconds=self._get_gate_seconds,
            get_gate_ttl_seconds=self._get_gate_ttl_seconds,
            event_is_wake_up=self._event_is_wake_up,
            notify_dropped=self._notify_wakeup_dropped,
            get_wakeup_interval=self._get_wakeup_interval,
            gates=self._gates,
            reply_locks=self._reply_locks,
            max_gate_states=MAX_GATE_STATES,
            now=lambda: time.monotonic(),
        )

    def _get_reply_coordinator(self) -> ReplyCoordinator:
        coordinator = getattr(self, "_reply_coordinator", None)
        if coordinator is None:
            if not hasattr(self, "_gates"):
                self._gates = {}
            if not hasattr(self, "_reply_locks"):
                self._reply_locks = {}
            coordinator = self._build_reply_coordinator()
            self._reply_coordinator = coordinator
        return coordinator

    def _get_message_dispatcher(self) -> MessageDispatcher:
        dispatcher = getattr(self, "_message_dispatcher", None)
        if dispatcher is None:
            dispatcher = MessageDispatcher(self.context, self._get_reply_coordinator())
            self._message_dispatcher = dispatcher
        return dispatcher

    def _get_message_merger(self) -> MergeWindowManager:
        merger = getattr(self, "_message_merger", None)
        if merger is None:
            merger = MergeWindowManager(get_config=self._get_config)
            self._message_merger = merger
        return merger

    @_event_filter.event_message_type(_event_filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent) -> None:
        """Capture same-user follow-up text while a merge window is open."""
        if not self._get_config("enable_message_merge", True):
            return
        try:
            self._get_message_merger().capture(event)
        except Exception:
            logger.debug("[消息合并] 捕获消息失败", exc_info=True)

    @_event_filter.on_waiting_llm_request()
    async def on_waiting_llm_request(self, event: AstrMessageEvent) -> None:
        """Admit wake-ups globally; hold the first one to collect segments."""
        merger = self._get_message_merger()
        merge_key = (
            merger.user_key(event)
            if self._get_config("enable_message_merge", True)
            else None
        )
        if merge_key is not None:
            if merger.is_window_open(event):
                merger.merge_wake(event)
                event.stop_event()
                return
            pending = merger.take_planning(event)
            if pending is not None:
                old_event, earlier_text, earlier_media, pipeline_task = pending
                superseded = (
                    old_event is None
                    or self._get_reply_coordinator().supersede_active_event(
                        old_event
                    )
                )
                if superseded:
                    merger.attach_media(event, earlier_media)
                    if (
                        old_event is not None
                        and
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
        if not await self._get_reply_coordinator().admit_wakeup(event):
            return
        if merge_key is not None:
            pipeline_task = asyncio.current_task()
            if merger.start_window(event, pipeline_task=pipeline_task):
                try:
                    await asyncio.sleep(self._get_merge_window_seconds())
                finally:
                    event.message_str = merger.finalize_window(event)

    @_event_filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req) -> None:
        """Idempotent admission check immediately before the LLM request."""
        if _event_is_stopped(event):
            return
        if not await self._get_reply_coordinator().admit_wakeup(event):
            return
        self._clean_media_focus_context(event, req)
        self._guard_media_question_context(event, req)
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
            self._release_gate(event)

    def _clean_media_focus_context(self, event: AstrMessageEvent, req) -> None:
        """Keep the model from re-analyzing the bot's own previously-sent media.

        The bot's own outgoing images stay in the conversation history and are
        visible to multimodal models on every later request, which hijacks
        replies even for plain-text messages such as a greeting. Always remove
        media from assistant history and always remove other plugins'
        "recently sent self meme" text injection (it is appended into the user
        message, so the model can mistake the bot's own meme for one the user
        just sent).
        """
        if not self._get_config("protect_user_media_focus", True):
            return
        if req is None:
            return
        removed_media = 0
        removed_meme = 0
        try:
            if self._get_config("strip_self_media_from_context", True):
                removed_media = strip_assistant_media(req)
            if self._get_config("strip_recent_self_meme_context", True):
                removed_meme = strip_recent_self_meme_context(req)
        except Exception:
            logger.debug("[请求清洗] 上下文清洗失败", exc_info=True)
            return
        if removed_media or removed_meme:
            logger.info(
                "[请求清洗] 已剔除历史机器人图片/文件=%d、自发表情包描述=%d",
                removed_media,
                removed_meme,
            )

    def _guard_media_question_context(self, event: AstrMessageEvent, req) -> None:
        """Correct media attribution for text-only questions.

        Memories and history text can make the model attribute the bot's own
        memes to the user, or answer image questions from the bot's own past
        memes. For "我发了什么/我发过吗" questions append a role note; for
        text-only image-text questions ("这上面有字吗") append a note clarifying
        that the user sent no image this round.
        """
        if not self._get_config("guard_own_media_attribution", True):
            return
        if req is None:
            return
        text = str(getattr(event, "message_str", "") or "")
        try:
            if asks_about_own_media(text) and append_attribution_note(req):
                logger.info("[请求清洗] 检测到'我发了什么'类提问，已注入消息归属提示")
                return
            try:
                user_has_media = has_user_media(event)
            except Exception:
                user_has_media = False
            if (
                not user_has_media
                and asks_about_image_text(text)
                and append_image_text_note(req)
            ):
                logger.info("[请求清洗] 纯文字询问图片文字，已注入图片归属提示")
        except Exception:
            logger.debug("[请求清洗] 消息归属提示注入失败", exc_info=True)

    @_event_filter.on_decorating_result()
    async def on_decorating_result(self, event: AstrMessageEvent):
        if not event:
            return

        if self._get_reply_coordinator().discard_superseded_result(event):
            return
        self._get_message_merger().clear_owner(event)

        result = None
        reply_key = None
        reply_lock = None
        lock_owned = False
        completion_delegated = False
        try:
            result = event.get_result()
            if not result or not getattr(result, "chain", None):
                self._release_gate(event)
                return

            session = await self._get_reply_coordinator().acquire_reply(event)
            reply_key = session.origin
            reply_lock = session.reply_lock
            lock_owned = True
            set_extra = getattr(event, "set_extra", None)
            if callable(set_extra):
                try:
                    set_extra(FILTER_REPLY_LOCK_EXTRA, reply_lock)
                except Exception:
                    logger.debug(
                        "[语言优化] 无法向当前事件发布分段回复锁",
                        exc_info=True,
                    )

            modified = False
            direct_send_completed = False
            followups_scheduled = False
            followup_paragraphs: list[str] = []
            guard_blocked = False
            pipeline_stats: dict[str, int] = {}

            _coalesce_adjacent_plain_components(result.chain)
            text_pipeline = OutboundTextPipeline(
                context=self.context,
                get_config=self._get_config,
                get_guard_terms=self._get_guard_terms,
                segmentation_and_style=apply_segmentation_and_style,
            )
            strict_guard = self._guard_mode() == "strict" or self._onboarding_active(event)

            async def process_followup(text: str) -> str | None:
                nonlocal guard_blocked
                processed = await text_pipeline.process(
                    text,
                    event,
                    strict_guard=strict_guard,
                    skip_llm_layout=True,
                )
                for name, count in processed.stats.items():
                    pipeline_stats[name] = pipeline_stats.get(name, 0) + count
                guard_blocked = guard_blocked or processed.guard_blocked
                value = processed.text or ""
                return value if value.strip() else None

            prepared_plain: list[tuple[Plain, str, str]] = []
            for comp in result.chain:
                if not isinstance(comp, Plain):
                    continue

                original = comp.text or ""
                processed = await text_pipeline.process(
                    original,
                    event,
                    strict_guard=strict_guard,
                )
                for name, count in processed.stats.items():
                    pipeline_stats[name] = pipeline_stats.get(name, 0) + count
                guard_blocked = guard_blocked or processed.guard_blocked
                prepared_plain.append((comp, original, processed.text))

            fallback_written = False
            for comp, original, text in prepared_plain:
                if guard_blocked:
                    comp.text = SAFE_REPLY if not fallback_written else ""
                    fallback_written = True
                    modified = True
                    continue

                if self._get_config("enable_image_render", False) and should_render_image(text, self._get_config):
                    image_path = await text_to_image(text, self._get_config)
                    if image_path:
                        img_chain = MessageChain().file_image(image_path)
                        await self.context.send_message(event.unified_msg_origin, img_chain)
                        cleanup_task = asyncio.create_task(cleanup_temp_file(image_path))
                        self._track_task(cleanup_task)
                        comp.text = ""
                        direct_send_completed = True
                        modified = True
                        pipeline_stats["列表图片渲染"] = pipeline_stats.get("列表图片渲染", 0) + 1
                        continue

                if self._get_config("multi_message", True):
                    paragraphs = prepare_multi_message_parts(text)
                    if len(paragraphs) > 1:
                        comp.text = paragraphs[0]
                        followup_paragraphs.extend(paragraphs[1:])
                        modified = True
                        continue
                    if len(paragraphs) == 1 and paragraphs[0] != original:
                        comp.text = paragraphs[0]
                        modified = True
                        continue

                if text != original:
                    comp.text = text
                    modified = True

            if followup_paragraphs:
                delay_min, delay_max = self._get_delay_range()
                task = asyncio.create_task(
                    self._send_followups_and_release(
                        session,
                        followup_paragraphs,
                        delay_min,
                        delay_max,
                        process_text=process_followup,
                    )
                )
                self._track_task(task)
                self._get_reply_coordinator().register_followup(session, task)
                lock_owned = False
                followups_scheduled = True

            if followups_scheduled:
                completion_delegated = True
            elif direct_send_completed and not _has_pending_message(result.chain):
                self._finish_reply(reply_key, reply_lock, event)
                lock_owned = False
            elif _has_pending_message(result.chain):
                self._get_reply_coordinator().register_pending_send(session)
                self._pending_sends = self._get_reply_coordinator().pending_sends
                self._pending_send = self._get_reply_coordinator().pending_send
                completion_delegated = True
                lock_owned = False
            else:
                self._finish_reply(reply_key, reply_lock, event, apply_cooldown=False)
                lock_owned = False

            if modified:
                active = [name for name, count in pipeline_stats.items() if count > 0]
                logger.info("[语言优化] 已应用处理流程：%s", ", ".join(active) if active else "无")

        except Exception:
            logger.error("[语言优化] 输出处理失败", exc_info=True)
            if result is not None and getattr(result, "chain", None) is not None:
                try:
                    _replace_chain_with_safe_reply(result.chain)
                except Exception:
                    logger.error("[语言优化] 安全回复替换失败，停止原始结果发送", exc_info=True)
                    stopper = getattr(event, "stop_event", None)
                    if callable(stopper):
                        stopper()
        finally:
            if lock_owned and reply_key is not None and reply_lock is not None:
                self._finish_reply(reply_key, reply_lock, event, apply_cooldown=False)
            elif not completion_delegated:
                self._release_gate(event)

    async def _send_followups_and_release(
        self,
        session: ReplySession,
        paragraphs: list[str],
        delay_min: float,
        delay_max: float,
        process_text=None,
    ) -> None:
        await self._get_message_dispatcher().send_followups(
            session.origin,
            paragraphs,
            policy=DispatchPolicy(delay_min, delay_max),
            session=session,
            process_text=process_text,
        )

    # Run before plugins such as meme_manager that may stop hook propagation.
    # This callback owns the response gate cleanup, so it must not be skipped.
    @_event_filter.after_message_sent(priority=1000)
    async def after_message_sent(self, event: AstrMessageEvent) -> None:
        self._get_message_merger().clear_owner(event)
        coordinator = self._get_reply_coordinator()
        coordinator.pending_sends = getattr(self, "_pending_sends", coordinator.pending_sends)
        coordinator.pending_send = getattr(self, "_pending_send", coordinator.pending_send)
        coordinator.release_after_send(event)
        self._pending_sends = coordinator.pending_sends
        self._pending_send = coordinator.pending_send

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

    def _get_gate_seconds(self) -> float:
        configured = self._get_config("gate_seconds", _MISSING)
        if configured is _MISSING:
            configured = self._get_config("cooldown_seconds", 0.0)
        try:
            value = float(configured)
        except (TypeError, ValueError):
            return 0.0
        return value if math.isfinite(value) and value > 0 else 0.0

    def _get_gate_ttl_seconds(self) -> float:
        """Safety net: auto-release a gate whose owner reply never completes."""
        value = self._get_float_config("gate_ttl_seconds", GATE_TTL_DEFAULT)
        if not math.isfinite(value) or value <= 0:
            return 0.0
        return value

    def _get_cooldown_seconds(self) -> float:
        """Backward-compatible alias for older configurations and callers."""
        return self._get_gate_seconds()

    def _event_is_wake_up(self, event: AstrMessageEvent) -> bool:
        checker = getattr(event, "is_wake_up", None)
        if callable(checker):
            try:
                return bool(checker())
            except Exception:
                return True
        return bool(checker) if checker is not None else True

    def _release_gate(self, owner_event: AstrMessageEvent, apply_cooldown: bool = False) -> None:
        self._get_reply_coordinator().release_gate(owner_event, apply_cooldown)

    def _finish_reply(
        self,
        reply_key: str,
        reply_lock: asyncio.Lock,
        owner_event: AstrMessageEvent | None = None,
        apply_cooldown: bool = True,
    ) -> None:
        session = ReplySession(reply_key, owner_event, reply_lock)
        self._get_reply_coordinator().release(session, apply_cooldown=apply_cooldown)

    def _notify_wakeup_dropped(self, event: AstrMessageEvent) -> None:
        notice = self._get_config("queue_full_notice", "队列繁忙，请稍后再试。")
        if not notice:
            return
        origin = getattr(event, "unified_msg_origin", None)
        if not origin:
            return
        try:
            task = asyncio.create_task(
                self.context.send_message(
                    origin,
                    MessageChain().message(str(notice)),
                )
            )
        except Exception:
            logger.warning("[语言优化] 队列满提示发送失败", exc_info=True)
            return
        self._track_task(task)

    def _get_guard_terms(self) -> list[str]:
        return parse_terms(self._get_config("content_guard_block_terms", ""))

    def _guard_mode(self) -> str:
        value = str(self._get_config("content_guard_mode", "balanced") or "balanced").lower()
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
        elapsed_active = duration > 0 and asyncio.get_running_loop().time() - state.started_at < duration
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

    def _get_delay_range(self) -> tuple[float, float]:
        """Keep follow-up delays inside the requested 2-5 second window."""
        delay_min = min(5.0, max(2.0, self._get_float_config("delay_min", 2.0)))
        delay_max = min(5.0, max(2.0, self._get_float_config("delay_max", 5.0)))
        return (delay_min, delay_max) if delay_min <= delay_max else (delay_max, delay_min)

    def _get_merge_window_seconds(self) -> float:
        """Return the same-user merge window duration, bounded to 1-30 seconds."""
        value = self._get_float_config("merge_window_seconds", 6.0)
        if not math.isfinite(value):
            return 6.0
        return min(30.0, max(1.0, value))

    def _get_wakeup_interval(self) -> tuple[float, float]:
        """Return a safe global interval between completed wake-ups."""
        delay_min = max(
            1.0,
            self._get_float_config(
                "wakeup_interval_min",
                WAKEUP_INTERVAL_MIN_DEFAULT,
            ),
        )
        delay_max = max(
            delay_min,
            self._get_float_config(
                "wakeup_interval_max",
                WAKEUP_INTERVAL_MAX_DEFAULT,
            ),
        )
        return delay_min, delay_max

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
            values = [source.get(name) for name in ("message_str", "user_message", "message", "raw_message")]
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


def _has_pending_message(chain) -> bool:
    for comp in chain:
        if isinstance(comp, Plain):
            if (comp.text or "").strip():
                return True
        else:
            return True
    return False


def _coalesce_adjacent_plain_components(chain) -> None:
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


def _replace_chain_with_safe_reply(chain) -> None:
    chain.clear()
    chain.append(Plain(SAFE_REPLY))
