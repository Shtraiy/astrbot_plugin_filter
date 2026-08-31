"""Lightweight LLM-based reply segmentation with rule fallback."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Callable

from astrbot.api import logger

SEGMENT_PROMPT = (
    "你是聊天消息分段助手。你的唯一任务是把一段文本拆分成适合在聊天窗口逐条发送的多条消息。\n"
    "严格要求：\n"
    "1. 不增删、不改写、不润色、不翻译原文的任何文字，只决定在哪里分段；\n"
    "2. 每个分段是一个完整、自然、可独立阅读的语义块；\n"
    "3. 只输出 JSON 数组，每个元素是一条消息的完整文本；不要输出解释、前后缀或 Markdown 代码块；\n"
    "4. 若原文不适合分段，输出只含一个元素的数组。\n"
    "原文：\n{text}"
)

_FENCE_RE = re.compile(r"```")
_DEFAULT_SPLIT_CHARS = "。！？!?；;～~"
_DEFAULT_STRIP_CHARS = "。～~"


def _boundary_re(split_chars: str) -> re.Pattern:
    """Build a zero-width sentence-boundary regex for the given characters."""
    return re.compile(r"(?<=[" + re.escape(split_chars) + r"])(?=\S)")


def _compact(text: str) -> str:
    return "".join(ch for ch in (text or "") if not ch.isspace())


def _fences_balanced(text: str) -> bool:
    return len(_FENCE_RE.findall(text or "")) % 2 == 0


def parse_segment_json(raw: str) -> list[str] | None:
    """Parse an LLM JSON-array reply into a list of strings.

    Tolerates markdown fences, leading/trailing prose, single-quoted arrays
    and ``{"segments": [...]}`` wrappers so small models that ignore the
    "JSON only" instruction still work.
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw).strip()
    candidates = [raw]
    start = raw.find("[")
    end = raw.rfind("]")
    if start >= 0 and end > start:
        candidates.append(raw[start : end + 1])
    for candidate in candidates:
        data = _loads_lenient(candidate)
        if isinstance(data, dict):
            for key in ("segments", "messages", "result", "parts"):
                value = data.get(key)
                if isinstance(value, list) and value:
                    data = value
                    break
        if isinstance(data, list) and data:
            parts = [item for item in data if isinstance(item, str)]
            if len(parts) == len(data):
                return parts
    return None


def _loads_lenient(raw: str):
    """json.loads with a single-quote fallback (``ast.literal_eval``)."""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        pass
    try:
        import ast

        return ast.literal_eval(raw)
    except Exception:
        return None


def validate_segments(
    original: str,
    segments: list[str],
    max_messages: int,
) -> bool:
    """Content-preservation check: join must equal original, fences intact."""
    if not segments or len(segments) < 2 or len(segments) > max_messages:
        return False
    if any(not (seg or "").strip() for seg in segments):
        return False
    if _compact("".join(segments)) != _compact(original):
        return False
    if any(not _fences_balanced(seg) for seg in segments):
        return False
    return True


def _protect_fences(parts: list[str]) -> list[str]:
    """Merge a part into the previous one when the cut fell inside a fence."""
    merged: list[str] = []
    for part in parts:
        if merged and not _fences_balanced(merged[-1]):
            merged[-1] = merged[-1] + "\n\n" + part
        else:
            merged.append(part)
    return merged


def _cap_parts(parts: list[str], max_messages: int) -> list[str]:
    if max_messages < 2:
        max_messages = 2
    if len(parts) <= max_messages:
        return parts
    head = parts[: max_messages - 1]
    tail = "\n\n".join(parts[max_messages - 1 :])
    return head + [tail]


def strip_segment_delimiters(
    segments: list[str],
    chars: str,
) -> list[str]:
    """Strip trailing delimiter chars (e.g. ``。～~``) from each segment.

    Applies only to the mechanical fallback path so chat segments read
    naturally without trailing split punctuation; empty leftovers are
    dropped and the remaining content is otherwise untouched.
    """
    cleaned: list[str] = []
    for seg in segments:
        text = (seg or "").rstrip()
        while text and text[-1] in (chars or ""):
            text = text[:-1].rstrip()
        if text:
            cleaned.append(text)
    return cleaned


def rule_split(
    text: str,
    max_messages: int,
    split_chars: str = _DEFAULT_SPLIT_CHARS,
) -> list[str]:
    """Split on blank lines / sentence boundaries, capped at max_messages."""
    text = (text or "").strip()
    if not text:
        return []
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if len(paragraphs) < 2:
        # 零宽断句：不吞标点后的换行/空格，保证各段拼接后与原文一致
        if split_chars:
            sentences = [s for s in _boundary_re(split_chars).split(text) if s.strip()]
            paragraphs = sentences or [text]
    paragraphs = _protect_fences(paragraphs)
    return _cap_parts(paragraphs, max_messages)


def _get_min_chars(get_config: Callable[[str, Any], Any]) -> int:
    try:
        value = int(get_config("segment_min_chars", 80))
    except (TypeError, ValueError):
        return 80
    return max(20, min(value, 1000))


def _get_mechanical_min_chars(get_config: Callable[[str, Any], Any]) -> int:
    try:
        value = int(get_config("segment_mechanical_min_chars", 20))
    except (TypeError, ValueError):
        return 20
    return max(1, min(value, 1000))


def _get_split_chars(get_config: Callable[[str, Any], Any]) -> str:
    return str(get_config("segment_split_chars", _DEFAULT_SPLIT_CHARS) or "")


def _get_max_messages(get_config: Callable[[str, Any], Any]) -> int:
    try:
        value = int(get_config("segment_max_messages", 3))
    except (TypeError, ValueError):
        return 3
    return max(2, min(value, 5))


def _get_timeout(get_config: Callable[[str, Any], Any]) -> float:
    try:
        value = float(get_config("segment_timeout_seconds", 10.0))
    except (TypeError, ValueError):
        return 10.0
    return max(1.0, min(value, 30.0))


async def _try_llm_segment(
    text: str,
    provider_id: str,
    context: Any,
    get_config: Callable[[str, Any], Any],
) -> list[str] | None:
    max_messages = _get_max_messages(get_config)
    timeout = _get_timeout(get_config)
    try:
        logger.info("[智能分段] 请求 provider=%s", provider_id)
        llm_resp = await asyncio.wait_for(
            context.llm_generate(
                chat_provider_id=provider_id,
                prompt=SEGMENT_PROMPT.format(text=text),
            ),
            timeout=timeout,
        )
        raw = (getattr(llm_resp, "completion_text", "") or "").strip()
        parts = parse_segment_json(raw)
        if parts is None:
            logger.warning("[智能分段] LLM 输出不是合法 JSON 数组，回退规则分段")
            logger.debug("[智能分段] LLM 原始输出: %s", (raw or "")[:200])
            return None
        if len(parts) == 1:
            # 模型认为无需分段：交给机械规则复核。零宽断句不会切碎
            # 换行连排的诗句，因此诗歌仍整条发送；有明确切分点（空行/
            # 分隔词）的文本则按机械分段，避免模型一票否决。
            logger.debug("[智能分段] 模型返回单段，交由机械分段复核")
            return None
        if not validate_segments(text, parts, max_messages):
            logger.warning(
                "[智能分段] 校验失败（内容被改动/段数超限/围栏切断），回退规则分段"
            )
            return None
        return parts
    except asyncio.TimeoutError:
        logger.warning("[智能分段] 请求超时，回退规则分段")
        return None
    except Exception:
        logger.warning("[智能分段] 请求失败，回退规则分段", exc_info=True)
        return None


async def split_reply(
    text: str,
    context: Any,
    get_config: Callable[[str, Any], Any],
) -> list[str] | None:
    """Return 2..max segments, or None to send the reply as-is.

    ``segment_min_chars`` only gates the LLM call; below it (or without a
    provider / after LLM failure) the mechanical rule split still applies
    once the text reaches ``segment_mechanical_min_chars``.
    """
    text = (text or "").strip()
    if not text:
        return None
    llm_min_chars = _get_min_chars(get_config)
    mech_min_chars = _get_mechanical_min_chars(get_config)
    if len(text) < mech_min_chars:
        logger.debug(
            "[智能分段] 回复长度 %d 低于机械分段下限 %d，单条发送",
            len(text),
            mech_min_chars,
        )
        return None
    max_messages = _get_max_messages(get_config)
    provider_id = str(get_config("segment_provider_id", "") or "").strip()
    segments: list[str] | None = None
    if provider_id:
        if len(text) >= llm_min_chars:
            segments = await _try_llm_segment(text, provider_id, context, get_config)
        else:
            logger.debug(
                "[智能分段] 回复长度 %d 未达 LLM 分段阈值 %d，走机械分段",
                len(text),
                llm_min_chars,
            )
    else:
        logger.warning("[智能分段] 未配置 segment_provider_id，仅使用规则分段")
    if segments is None:
        segments = _protect_fences(
            rule_split(text, max_messages, _get_split_chars(get_config))
        )
        segments = _cap_parts(segments, max_messages)
        strip_chars = str(
            get_config("segment_strip_chars", _DEFAULT_STRIP_CHARS) or ""
        )
        if strip_chars:
            segments = strip_segment_delimiters(segments, strip_chars)
    if len(segments) < 2:
        return None
    return segments


__all__ = [
    "SEGMENT_PROMPT",
    "parse_segment_json",
    "rule_split",
    "split_reply",
    "strip_segment_delimiters",
    "validate_segments",
]
