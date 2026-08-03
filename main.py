"""AstrBot plugin entry: optimize outgoing text before it is sent."""

from __future__ import annotations

import asyncio
import importlib
import inspect
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
from .segmentation import (
    apply_segmentation_and_style,
    prepare_multi_message_parts,
    send_followups,
)
from .outbound_pipeline import OutboundTextPipeline
from .private_companion_adapter import PrivateCompanionAdapter
from .reply_coordinator import GateState as _GateState
from .reply_coordinator import ReplyCoordinator, ReplySession


@dataclass
class _OnboardingState:
    started_at: float
    message_count: int = 0


MAX_ONBOARDING_STATES = 4096
MAX_GATE_STATES = 4096
GATE_TTL_DEFAULT = 300.0
_EVENT_CORRELATION_FIELDS = ("request_id", "event_id", "message_id", "trace_id")
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
        self._private_companion_adapter = PrivateCompanionAdapter(
            track_task=self._track_task,
        )
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
            is_proactive_event=self._is_proactive_event,
            schedule_cancel=self._schedule_private_companion_cancel,
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

    def _get_private_companion_adapter(self) -> PrivateCompanionAdapter:
        adapter = getattr(self, "_private_companion_adapter", None)
        if adapter is None:
            adapter = PrivateCompanionAdapter(track_task=self._track_task)
            self._private_companion_adapter = adapter
        return adapter

    @_event_filter.on_waiting_llm_request()
    async def on_waiting_llm_request(self, event: AstrMessageEvent) -> None:
        """Discard a wake-up before it waits for AstrBot's session lock."""
        self._get_reply_coordinator().claim_wakeup(event)

    @_event_filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req) -> None:
        """Fallback gate check immediately before the LLM request."""
        if not self._get_reply_coordinator().claim_wakeup(event):
            return
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
        self._release_gate(event)
        await self._send_guard_reply(event, decision.category)

    @_event_filter.on_decorating_result()
    async def on_decorating_result(self, event: AstrMessageEvent):
        if not event:
            return

        if self._get_reply_coordinator().discard_superseded_result(event):
            return

        result = None
        reply_key = None
        reply_lock = None
        lock_owned = False
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
            guard_blocked = False
            pipeline_stats: dict[str, int] = {}

            _coalesce_adjacent_plain_components(result.chain)
            text_pipeline = OutboundTextPipeline(
                context=self.context,
                get_config=self._get_config,
                get_guard_terms=self._get_guard_terms,
                segmentation_and_style=apply_segmentation_and_style,
            )
            prepared_plain: list[tuple[Plain, str, str]] = []
            for comp in result.chain:
                if not isinstance(comp, Plain):
                    continue

                original = comp.text or ""
                processed = await text_pipeline.process(
                    original,
                    event,
                    strict_guard=self._guard_mode() == "strict" or self._onboarding_active(event),
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
                        modified = True
                        delay_min, delay_max = self._get_delay_range()
                        task = asyncio.create_task(
                            self._send_followups_and_release(
                                session,
                                paragraphs[1:],
                                delay_min,
                                delay_max,
                            )
                        )
                        self._track_task(task)
                        self._get_reply_coordinator().register_followup(session, task)
                        lock_owned = False
                        followups_scheduled = True
                        continue
                    if len(paragraphs) == 1 and paragraphs[0] != original:
                        comp.text = paragraphs[0]
                        modified = True
                        continue

                if text != original:
                    comp.text = text
                    modified = True

            if followups_scheduled:
                pass
            elif direct_send_completed and not _has_pending_message(result.chain):
                self._finish_reply(reply_key, reply_lock, event)
                lock_owned = False
            elif _has_pending_message(result.chain):
                self._get_reply_coordinator().register_pending_send(session)
                self._pending_sends = self._get_reply_coordinator().pending_sends
                self._pending_send = self._get_reply_coordinator().pending_send
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

    async def _send_followups_and_release(
        self,
        session: ReplySession,
        paragraphs: list[str],
        delay_min: float,
        delay_max: float,
    ) -> None:
        await self._get_message_dispatcher().send_followups(
            session.origin,
            paragraphs,
            policy=DispatchPolicy(delay_min, delay_max),
            session=session,
        )

    # Run before plugins such as meme_manager that may stop hook propagation.
    # This callback owns the response gate cleanup, so it must not be skipped.
    @_event_filter.after_message_sent(priority=1000)
    async def after_message_sent(self, event: AstrMessageEvent) -> None:
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

    def _mark_proactive_gate_superseded(self, event: AstrMessageEvent) -> None:
        """Let a real user message pass and invalidate the older proactive owner."""
        key = self._gate_key(event)
        state = getattr(self, "_gates", {}).get(key)
        if state is None or state.owner_event is None:
            return
        if not self._is_proactive_event(state.owner_event):
            return
        if self._events_correlate(state.owner_event, event):
            return
        state.superseded_by_user = True
        logger.info(
            "[语言优化] 用户消息优先：标记同源主动请求失效 origin=%s",
            key,
        )
        if not state.cancel_requested:
            state.cancel_requested = True
            self._schedule_private_companion_cancel(state.owner_event)

    def _discard_superseded_proactive_result(self, event: AstrMessageEvent) -> bool:
        """Prevent a stale proactive response from being sent after user input."""
        if not self._is_proactive_event(event):
            return False
        state = getattr(self, "_gates", {}).get(self._gate_key(event))
        if state is None or not state.superseded_by_user:
            return False
        if not self._events_correlate(state.owner_event, event):
            return False
        result = getattr(event, "get_result", lambda: None)()
        chain = getattr(result, "chain", None) if result is not None else None
        if chain is not None:
            result.chain = []
        stopper = getattr(event, "stop_event", None)
        if callable(stopper):
            stopper()
        self._release_gate(event, apply_cooldown=False)
        logger.info(
            "[语言优化] 已丢弃被用户消息取代的主动回复 origin=%s",
            self._gate_key(event),
        )
        return True

    def _schedule_private_companion_cancel(self, owner_event: AstrMessageEvent) -> None:
        token = str(
            getattr(owner_event, "_private_companion_proactive_chat_token", "") or ""
        ).strip()
        if not token:
            return
        try:
            task = asyncio.create_task(
                self._cancel_private_companion_proactive(
                    str(getattr(owner_event, "unified_msg_origin", "") or ""),
                    token,
                )
            )
        except RuntimeError:
            return
        self._track_task(task)

    async def _cancel_private_companion_proactive(self, session_id: str, token: str) -> None:
        """Best-effort optional cancellation; never make it a hard dependency."""
        module_names = (
            "data.plugins.astrbot_plugin_private_companion.main",
            "astrbot_plugin_private_companion.main",
        )
        for module_name in module_names:
            try:
                module = importlib.import_module(module_name)
                getter = getattr(module, "get_private_companion_api", None)
                api = getter() if callable(getter) else None
                cancel = getattr(api, "cancel_proactive_chat", None)
                if not callable(cancel):
                    continue
                result = cancel(session_id, token=token)
                if inspect.isawaitable(result):
                    await result
                logger.info(
                    "[语言优化] 已请求 Private Companion 取消过时主动回复 origin=%s",
                    session_id,
                )
                return
            except Exception:
                logger.debug(
                    "[语言优化] Private Companion 主动回复取消失败，继续按本地失效标记处理",
                    exc_info=True,
                )

    def _claim_or_stop_wake_up(self, event: AstrMessageEvent) -> bool:
        if not event or not self._event_is_wake_up(event):
            return True
        if not self._is_proactive_event(event):
            self._mark_proactive_gate_superseded(event)
            return True
        if not hasattr(self, "_gates"):
            self._gates = {}
        key = self._gate_key(event)
        if self._gate_is_active(event):
            state = self._gates.get(key)
            if state is None or not self._events_correlate(state.owner_event, event):
                logger.info(
                    "[语言优化] 丢弃唤醒：同一来源仍有请求在途或处于冷却 origin=%s",
                    key,
                )
                event.stop_event()
                return False
        if key not in self._gates:
            if len(self._gates) >= MAX_GATE_STATES:
                event.stop_event()
                return False
            self._gates[key] = _GateState(
                owner_event=event,
                created_at=time.monotonic(),
            )
        return True

    def _gate_key(self, event: AstrMessageEvent) -> str:
        origin = str(getattr(event, "unified_msg_origin", "") or "")
        return origin or "__unified_default__"

    def _gate_is_active(self, event: AstrMessageEvent | None = None) -> bool:
        if not hasattr(self, "_gates"):
            self._gates = {}
        now = time.monotonic()
        ttl = self._get_gate_ttl_seconds()
        expired = [
            key
            for key, state in self._gates.items()
            if state.owner_event is None and state.cooldown_until <= now
            or (
                ttl > 0
                and state.owner_event is not None
                and state.created_at > 0
                and now - state.created_at > ttl
            )
        ]
        for key in expired:
            state = self._gates.pop(key, None)
            if state is not None and state.owner_event is not None:
                logger.warning(
                    "[语言优化] 唤醒闸门超时自动释放 origin=%s 已持续=%.1f秒",
                    key,
                    now - state.created_at,
                )
        if event is None:
            return bool(self._gates)
        state = self._gates.get(self._gate_key(event))
        return state is not None

    def _release_gate(self, owner_event: AstrMessageEvent, apply_cooldown: bool = False) -> None:
        if not hasattr(self, "_gates"):
            self._gates = {}
        key = self._gate_key(owner_event)
        state = self._gates.get(key)
        if state is None:
            return
        if state.owner_event is None or not self._events_correlate(
            state.owner_event,
            owner_event,
        ):
            return
        if apply_cooldown:
            cooldown = self._get_gate_seconds()
            if cooldown > 0:
                state.owner_event = None
                state.cooldown_until = time.monotonic() + cooldown
                return
        self._gates.pop(key, None)

    @staticmethod
    def _event_correlation_ids(event: AstrMessageEvent | None) -> set[str]:
        if event is None:
            return set()
        identifiers: set[str] = set()
        for field in _EVENT_CORRELATION_FIELDS:
            value = getattr(event, field, None)
            if value is not None and str(value).strip():
                identifiers.add(f"{field}:{value}")
        return identifiers

    @classmethod
    def _events_correlate(
        cls,
        owner: AstrMessageEvent | None,
        candidate: AstrMessageEvent | None,
    ) -> bool:
        if owner is candidate and owner is not None:
            return True
        return bool(
            cls._event_correlation_ids(owner)
            & cls._event_correlation_ids(candidate)
        )

    @classmethod
    def _is_pending_send_event(cls, event: AstrMessageEvent, pending) -> bool:
        return cls._events_correlate(pending[2], event)

    def _finish_reply(
        self,
        reply_key: str,
        reply_lock: asyncio.Lock,
        owner_event: AstrMessageEvent | None = None,
        apply_cooldown: bool = True,
    ) -> None:
        if reply_lock.locked():
            reply_lock.release()
        if self._reply_locks.get(reply_key) is reply_lock:
            self._reply_locks.pop(reply_key, None)
        if owner_event is not None:
            self._release_gate(owner_event, apply_cooldown=apply_cooldown)

    # Compatibility shims for older integrations that called these private
    # helpers directly. The state transitions themselves live in the
    # coordinator above.
    def _mark_proactive_gate_superseded(self, event: AstrMessageEvent) -> None:
        self._get_reply_coordinator().mark_user_priority(event)

    def _discard_superseded_proactive_result(self, event: AstrMessageEvent) -> bool:
        return self._get_reply_coordinator().discard_superseded_result(event)

    def _claim_or_stop_wake_up(self, event: AstrMessageEvent) -> bool:
        return self._get_reply_coordinator().claim_wakeup(event)

    def _gate_is_active(self, event: AstrMessageEvent | None = None) -> bool:
        return self._get_reply_coordinator().gate_is_active(event)

    def _release_gate(self, owner_event: AstrMessageEvent, apply_cooldown: bool = False) -> None:
        self._get_reply_coordinator().release_gate(owner_event, apply_cooldown)

    @classmethod
    def _events_correlate(
        cls,
        owner: AstrMessageEvent | None,
        candidate: AstrMessageEvent | None,
    ) -> bool:
        return ReplyCoordinator.events_correlate(owner, candidate)

    def _finish_reply(
        self,
        reply_key: str,
        reply_lock: asyncio.Lock,
        owner_event: AstrMessageEvent | None = None,
        apply_cooldown: bool = True,
    ) -> None:
        session = ReplySession(reply_key, owner_event, reply_lock)
        self._get_reply_coordinator().release(session, apply_cooldown=apply_cooldown)

    def _is_proactive_event(self, event: AstrMessageEvent | None) -> bool:
        return self._get_private_companion_adapter().is_proactive_event(event)

    def _schedule_private_companion_cancel(self, owner_event: AstrMessageEvent) -> None:
        self._get_private_companion_adapter().schedule_cancel(owner_event)

    async def _cancel_private_companion_proactive(self, session_id: str, token: str) -> None:
        await self._get_private_companion_adapter().cancel(session_id, token)

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

    def _track_task(self, task: asyncio.Task) -> None:
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)
        task.add_done_callback(_log_task_exception)


_MISSING = object()


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
