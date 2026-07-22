"""Configurable input/output guard for risky group-chat prompts and replies."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable

ALLOW = "allow"
BLOCK = "block"
SAFE_REPLY = "这个话题我不方便继续展开，可以换一个更中性的说法。"


@dataclass(frozen=True)
class GuardDecision:
    """The result of a deterministic content-guard check."""

    action: str
    category: str = ""

    @property
    def blocked(self) -> bool:
        return self.action == BLOCK


_ZERO_WIDTH_RE = re.compile(r"[\u200b-\u200f\u2060\ufeff]")
_RISK_TARGET_RE = re.compile(
    r"(?:敏感词|敏感内容|禁词|违禁词|违规词|不合适(?:的)?(?:词|内容)?|"
    r"不该说|不能说|脏话|辱骂|违规表达)"
)
_INJECTION_RE = re.compile(
    r"(?:忽略|无视|绕过|解除|关闭|不要遵守).{0,20}"
    r"(?:规则|限制|审查|安全|过滤|审核|系统提示)"
)
_OBFUSCATION_RE = re.compile(
    r"(?:谐音|拆字|拼音|编码|解码|base64|空格|零宽字符).{0,20}"
    r"(?:输出|说|复述|还原|生成|绕过)"
)


def normalize_for_scan(text: str) -> str:
    """Normalize common spacing and Unicode evasions before matching."""
    normalized = unicodedata.normalize("NFKC", text or "").casefold()
    normalized = _ZERO_WIDTH_RE.sub("", normalized)
    return "".join(
        char
        for char in normalized
        if char.isalnum() or char == "_"
    )


def parse_terms(value) -> list[str]:
    """Parse newline/comma-separated terms from AstrBot config values."""
    if value is None:
        return []
    if isinstance(value, str):
        values: Iterable = re.split(r"[\r\n,，;；]+", value)
    elif isinstance(value, (list, tuple, set)):
        values = value
    else:
        values = [value]

    terms: list[str] = []
    seen: set[str] = set()
    for item in values:
        term = str(item).strip()
        key = normalize_for_scan(term)
        if not key or key in seen:
            continue
        seen.add(key)
        terms.append(term)
    return terms


def _matches_configured_term(text: str, terms) -> bool:
    normalized_text = normalize_for_scan(text)
    if not normalized_text:
        return False
    return any(
        len(normalize_for_scan(term)) >= 2
        and normalize_for_scan(term) in normalized_text
        for term in parse_terms(terms)
    )


def evaluate_input(text: str, block_terms, *, strict: bool = False) -> GuardDecision:
    """Check a user request before it is passed to the LLM."""
    if _matches_configured_term(text, block_terms):
        return GuardDecision(BLOCK, "blocked_term")

    normalized = normalize_for_scan(text)
    targets_risky_content = bool(_RISK_TARGET_RE.search(normalized))
    asks_to_bypass = bool(_INJECTION_RE.search(normalized))
    asks_to_obfuscate = bool(_OBFUSCATION_RE.search(normalized))
    if targets_risky_content and (asks_to_bypass or asks_to_obfuscate or strict):
        return GuardDecision(BLOCK, "prompt_injection")
    return GuardDecision(ALLOW)


def evaluate_output(text: str, block_terms, *, strict: bool = False) -> GuardDecision:
    """Check final text immediately before it is sent to the group."""
    if _matches_configured_term(text, block_terms):
        return GuardDecision(BLOCK, "blocked_term")

    normalized = normalize_for_scan(text)
    if strict and _RISK_TARGET_RE.search(normalized) and (
        _INJECTION_RE.search(normalized) or _OBFUSCATION_RE.search(normalized)
    ):
        return GuardDecision(BLOCK, "prompt_injection")
    return GuardDecision(ALLOW)


def is_group_origin(origin) -> bool:
    """Recognize common AstrBot group-origin formats without assuming one adapter."""
    if not origin:
        return False
    return bool(re.search(r"(?:^|[:/])group(?:[:/]|$)", str(origin), re.IGNORECASE))
