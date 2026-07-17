"""AstrBot plugin entry: optimize outgoing text before it is sent."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping

from astrbot.api import logger
from astrbot.api.all import MessageChain
from astrbot.api.event import AstrMessageEvent, filter as _event_filter
from astrbot.api.message_components import Plain
from astrbot.api.star import Context, Star

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


class LanguageLogicOptimizer(Star):
    """Optimize outgoing text by cleaning metadata, tool traces, style, and layout."""

    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.config = config
        self._pending_tasks: set[asyncio.Task] = set()
        self._reply_locks: dict[str, asyncio.Lock] = {}

    @_event_filter.on_decorating_result()
    async def on_decorating_result(self, event: AstrMessageEvent):
        if not event:
            return

        try:
            result = event.get_result()
            if not result or not getattr(result, "chain", None):
                return

            # Keep replies from the same group/session contiguous. The lock is
            # released only after all delayed follow-up messages are sent.
            reply_key = event.unified_msg_origin
            reply_lock = self._reply_locks.setdefault(reply_key, asyncio.Lock())
            await reply_lock.acquire()
            lock_owned = True

            modified = False
            pipeline_stats: dict[str, int] = {}

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

                if self._get_config("enable_image_render", False) and should_render_image(text, self._get_config):
                    image_path = await text_to_image(text, self._get_config)
                    if image_path:
                        img_chain = MessageChain().file_image(image_path)
                        await self.context.send_message(event.unified_msg_origin, img_chain)
                        cleanup_task = asyncio.create_task(cleanup_temp_file(image_path))
                        self._track_task(cleanup_task)
                        comp.text = ""
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
                            )
                        )
                        self._track_task(task)
                        lock_owned = False
                        continue
                    if len(paragraphs) == 1 and paragraphs[0] != original:
                        comp.text = paragraphs[0]
                        modified = True
                        continue

                if text != original:
                    comp.text = text
                    modified = True

            if modified:
                active = [name for name, count in pipeline_stats.items() if count > 0]
                logger.info("[????????] ?????????????%s", ", ".join(active) if active else "?")

        except Exception:
            logger.error("[????????] ?????", exc_info=True)
        finally:
            if "lock_owned" in locals() and lock_owned:
                reply_lock.release()
                if self._reply_locks.get(reply_key) is reply_lock:
                    self._reply_locks.pop(reply_key, None)

    async def _send_followups_and_release(
        self,
        reply_key: str,
        reply_lock: asyncio.Lock,
        paragraphs: list[str],
        delay_min: float,
        delay_max: float,
    ) -> None:
        try:
            await send_followups(self.context, reply_key, paragraphs, delay_min, delay_max)
        finally:
            if reply_lock.locked():
                reply_lock.release()
            if self._reply_locks.get(reply_key) is reply_lock:
                self._reply_locks.pop(reply_key, None)

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


def _log_task_exception(task: asyncio.Task) -> None:
    try:
        exc = task.exception()
        if exc is not None:
            logger.warning("[????] ???%s", exc, exc_info=True)
    except asyncio.CancelledError:
        pass
    except Exception:
        pass


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
