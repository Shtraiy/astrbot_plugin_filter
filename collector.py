"""Pure helpers for the meme-stealing pipeline.

This module intentionally has no AstrBot imports so its safety rules can be
tested on a normal Python installation.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any


CATEGORY_ALIASES = {
    "生气": "angry",
    "愤怒": "angry",
    "开心": "happy",
    "高兴": "happy",
    "快乐": "happy",
    "难过": "sad",
    "伤心": "sad",
    "惊讶": "surprised",
    "震惊": "surprised",
    "困惑": "confused",
    "疑惑": "confused",
    "暧昧": "color",
    "卡顿": "cpu",
    "自嘲": "fool",
    "要钱": "givemoney",
    "喜欢": "like",
    "偷看": "see",
    "害羞": "shy",
    "工作": "work",
    "回复": "reply",
    "卖萌": "meow",
    "笨蛋": "baka",
    "早安": "morning",
    "睡觉": "sleep",
    "无奈": "sigh",
    "叹气": "sigh",
}


def configured_provider_id(config: Mapping[str, Any], key: str, fallback_key: str = "") -> str:
    """Read a provider override and optionally fall back to another setting."""
    primary = str(config.get(key, "") or "").strip()
    if primary:
        return primary
    return str(config.get(fallback_key, "") or "").strip() if fallback_key else ""


def strip_meme_markers(text: str) -> str:
    """Remove meme_manager's inline markers before its sender sees them."""
    return re.sub(r"&&[A-Za-z0-9_-]+&&", "", str(text or "")).strip()


def _read_value(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def group_id_from_event(event: Any) -> str:
    group_id = _read_value(event, "group_id", "")
    if not group_id:
        message_obj = _read_value(event, "message_obj")
        group_id = _read_value(message_obj, "group_id", "")
    return str(group_id or "").strip()


def whitelist_allows(event: Any, whitelist: Sequence[str] | None) -> bool:
    """Return whether a group event matches an empty-or-explicit whitelist."""
    entries = {str(item).strip() for item in (whitelist or []) if str(item).strip()}
    if not entries:
        return True
    group_id = group_id_from_event(event)
    umo = str(_read_value(event, "unified_msg_origin", "") or "").strip()
    return bool(group_id and (group_id in entries or umo in entries))


def extract_image_sources(components: Sequence[Any]) -> list[str]:
    """Extract image locators from AstrBot message components or test mappings."""
    sources: list[str] = []
    for component in components:
        component_type = str(
            _read_value(component, "type", "")
            or component.__class__.__name__
        ).lower()
        if "image" not in component_type:
            continue
        for field in ("url", "file", "path", "src", "data", "base64"):
            value = _read_value(component, field)
            if isinstance(value, str) and value.strip():
                sources.append(value.strip())
                break
    return sources


def parse_model_json(text: str) -> dict[str, Any]:
    """Parse a JSON object from a model response, including fenced output."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("model response is empty")
    candidate = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", candidate, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        candidate = fenced.group(1).strip()
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        start, end = candidate.find("{"), candidate.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("model response does not contain a JSON object") from None
        try:
            parsed = json.loads(candidate[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError("model response contains invalid JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError("model response is not a JSON object")
    return parsed


def normalize_category(
    raw_category: Any,
    allowed_categories: set[str],
    fallback: str = "confused",
) -> str:
    """Map model output to a safe existing category, never to a path."""
    allowed = {
        str(item).strip()
        for item in allowed_categories
        if _is_safe_category(str(item).strip())
    }
    fallback = fallback if _is_safe_category(fallback) and fallback in allowed else (
        sorted(allowed)[0] if allowed else "confused"
    )
    raw = str(raw_category or "").strip().strip("`'\"").lower()
    normalized = CATEGORY_ALIASES.get(raw, raw)
    if normalized in allowed and "/" not in normalized and "\\" not in normalized:
        return normalized
    return fallback


def _is_safe_category(value: str) -> bool:
    return bool(value and re.fullmatch(r"[A-Za-z0-9_-]+", value))
