"""AstrBot plugin entry: optimize outgoing text before it is sent."""

from __future__ import annotations

import asyncio
import math
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
    evaluate_output,
    is_group_origin,
    parse_terms,
)
from .image_renderer import cleanup_temp_file, should_render_image, text_to_image
from .pipelines import (
    clean_garbage,
    de_ai_flavor,
    deidentify_tool_names,
    filter_sensitive,
    remove_tool_narration,
    replace_user,
)
from .segmentation import apply_segmentation_and_style, dedupe_similar_paragraphs, send_followups


@dataclass
class _OnboardingState:
    started_at: float
    message_count: int = 0


class LanguageLogicOptimizer(Star):
    """Optimize outgoing text by cleaning metadata, tool traces, style, and layout."""

    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.config = config
        self._pending_tasks: set[asyncio.Task] = set()
        self._reply_locks: dict[str, asyncio.Lock] = {}
        self._response_in_progress = False
        self._gate_owner_event: AstrMessageEvent | None = None
        self._cooldown_until = 0.0
        self._pending_send: tuple[str, asyncio.Lock, AstrMessageEvent] | None = None
        self._onboarding_states: dict[str, _OnboardingState] = {}

    @_event_filter.on_waiting_llm_request()
    async def on_waiting_llm_request(self, event: AstrMessageEvent) -> None:
        """Discard a wake-up before it waits for AstrBot's session lock."""
        self._claim_or_stop_wake_up(event)

    @_event_filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req) -> None:
        """Fallback gate check immediately before the LLM request."""
        if not self._claim_or_stop_wake_up(event):
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

        reply_key = None
        reply_lock = None
        lock_owned = False
        try:
            result = event.get_result()
            if not result or not getattr(result, "chain", None):
                self._release_gate(event)
                return

            reply_key = event.unified_msg_origin
            reply_lock = self._reply_locks.setdefault(reply_key, asyncio.Lock())
            await reply_lock.acquire()
            lock_owned = True

            modified = False
            direct_send_completed = False
            followups_scheduled = False
            guard_blocked = False
            pipeline_stats: dict[str, int] = {}

            prepared_plain: list[tuple[Plain, str, str]] = []
            for comp in result.chain:
                if not isinstance(comp, Plain):
                    continue

                original = comp.text or ""
                text = original

                text, _ = _apply_pipeline("????", clean_garbage, text, pipeline_stats)
                text, _ = _apply_pipeline("????", replace_user, text, pipeline_stats)
                text, _ = _apply_pipeline("????", filter_sensitive, text, pipeline_stats)
                text, _ = _apply_pipeline("????", remove_tool_narration, text, pipeline_stats)
                text, _ = _apply_pipeline("????", deidentify_tool_names, text, pipeline_stats)

                if self._get_config("enable_de_ai_flavor", True):
                    text, _ = _apply_pipeline("? AI ?", de_ai_flavor, text, pipeline_stats)

                text, _ = await _apply_pipeline_async(
                    "????",
                    apply_segmentation_and_style,
                    text,
                    self.context,
                    self._get_config,
                    stats=pipeline_stats,
                )

                if self._get_config("enable_content_guard", True):
                    decision = evaluate_output(
                        text,
                        self._get_guard_terms(),
                        strict=self._guard_mode() == "strict" or self._onboarding_active(event),
                    )
                    if decision.blocked:
                        text = SAFE_REPLY
                        guard_blocked = True
                        pipeline_stats["content_guard"] = pipeline_stats.get("content_guard", 0) + 1

                prepared_plain.append((comp, original, text))

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
                        pipeline_stats["????"] = pipeline_stats.get("????", 0) + 1
                        continue

                if self._get_config("multi_message", True):
                    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
                    paragraphs = dedupe_similar_paragraphs(paragraphs)
                    if len(paragraphs) > 1:
                        comp.text = paragraphs[0]
                        modified = True
                        delay_min, delay_max = self._get_delay_range()
                        task = asyncio.create_task(
                            self._send_followups_and_release(
                                reply_key,
                                reply_lock,
                                paragraphs[1:],
                                delay_min,
                                delay_max,
                                event,
                            )
                        )
                        self._track_task(task)
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
                self._pending_send = (reply_key, reply_lock, event)
                lock_owned = False
            else:
                self._finish_reply(reply_key, reply_lock, event, apply_cooldown=False)
                lock_owned = False

            if modified:
                active = [name for name, count in pipeline_stats.items() if count > 0]
                logger.info("[????????] ?????????????%s", ", ".join(active) if active else "?")

        except Exception:
            logger.error("[????????] ?????", exc_info=True)
        finally:
            if lock_owned and reply_key is not None and reply_lock is not None:
                self._finish_reply(reply_key, reply_lock, event, apply_cooldown=False)

    # Run before plugins such as meme_manager that may stop hook propagation.
    # This callback owns the response gate cleanup, so it must not be skipped.
    @_event_filter.after_message_sent(priority=1000)
    async def after_message_sent(self, event: AstrMessageEvent) -> None:
        pending = self._pending_send
        if pending is None or not self._is_pending_send_event(event, pending):
            return
        self._pending_send = None
        self._finish_reply(pending[0], pending[1], pending[2])

    async def _send_followups_and_release(
        self,
        reply_key: str,
        reply_lock: asyncio.Lock,
        paragraphs: list[str],
        delay_min: float,
        delay_max: float,
        owner_event: AstrMessageEvent | None = None,
    ) -> None:
        try:
            await send_followups(self.context, reply_key, paragraphs, delay_min, delay_max)
        finally:
            self._finish_reply(reply_key, reply_lock, owner_event)

    def _get_config(self, key: str, default=None):
        for source in (getattr(self, "config", None), getattr(self.context, "config", None)):
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

    def _get_cooldown_seconds(self) -> float:
        value = self._get_float_config("cooldown_seconds", 0.0)
        return value if math.isfinite(value) and value > 0 else 0.0

    def _event_is_wake_up(self, event: AstrMessageEvent) -> bool:
        checker = getattr(event, "is_wake_up", None)
        if callable(checker):
            try:
                return bool(checker())
            except Exception:
                return True
        return bool(checker) if checker is not None else True

    def _claim_or_stop_wake_up(self, event: AstrMessageEvent) -> bool:
        if not event or not self._event_is_wake_up(event):
            return True
        if self._gate_is_active() and self._gate_owner_event is not event:
            event.stop_event()
            return False
        if not self._response_in_progress:
            self._response_in_progress = True
            self._gate_owner_event = event
        return True

    @staticmethod
    def _is_pending_send_event(event: AstrMessageEvent, pending) -> bool:
        if event is pending[2]:
            return True
        return getattr(event, "unified_msg_origin", None) == pending[0]

    def _gate_is_active(self) -> bool:
        if self._response_in_progress:
            return True
        if self._cooldown_until > asyncio.get_running_loop().time():
            return True
        self._cooldown_until = 0.0
        return False

    def _release_gate(self, owner_event: AstrMessageEvent, apply_cooldown: bool = False) -> None:
        if self._gate_owner_event is not owner_event:
            return
        self._response_in_progress = False
        self._gate_owner_event = None
        self._cooldown_until = (
            asyncio.get_running_loop().time() + self._get_cooldown_seconds()
            if apply_cooldown
            else 0.0
        )

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
        state = self._onboarding_states.get(origin)
        now = asyncio.get_running_loop().time()
        if state is None:
            state = _OnboardingState(started_at=now)
            self._onboarding_states[origin] = state
        state.message_count += 1
        return self._onboarding_active(event)

    def _onboarding_active(self, event: AstrMessageEvent) -> bool:
        if not self._is_group_event(event):
            return False
        origin = str(getattr(event, "unified_msg_origin", "") or "")
        state = self._onboarding_states.get(origin)
        if state is None:
            return False
        duration = max(0.0, self._get_float_config("onboarding_guard_minutes", 30.0)) * 60
        message_limit = max(0, int(self._get_float_config("onboarding_guard_messages", 20)))
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
            logger.warning("[????] ???%s", exc, exc_info=True)
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


def _apply_pipeline(name: str, func, text: str, stats: dict[str, int]) -> tuple[str, bool]:
    result = func(text)
    if result != text:
        stats[name] = stats.get(name, 0) + 1
        return result, True
    return text, False


async def _apply_pipeline_async(name: str, func, text: str, *args, stats: dict[str, int]) -> tuple[str, bool]:
    result = await func(text, *args)
    if result != text:
        stats[name] = stats.get(name, 0) + 1
        return result, True
    return text, False
