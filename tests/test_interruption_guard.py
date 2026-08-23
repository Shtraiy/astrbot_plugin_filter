"""Tests for AstrBot interruption-placeholder hygiene.

Covers:
- ``interruption_guard`` pure helpers (placeholder detection + context scrub).
- ``LanguageLogicOptimizer`` hooks that stop placeholder responses and scrub
  placeholder entries from the LLM request context, so livingmemory never
  records the ``Output stopped.`` interruption marker as real assistant text.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from interruption_guard import (
    is_interruption_placeholder_text,
    scrub_interruption_placeholders,
)

from tests.test_merge_integration import FakeEvent, make_optimizer


class FakeResp:
    def __init__(self, text, role="assistant"):
        self.completion_text = text
        self.role = role


def test_is_interruption_placeholder_text_matches_both_placeholders():
    assert is_interruption_placeholder_text("Stop output.") is True
    assert is_interruption_placeholder_text("Output stopped.") is True
    assert is_interruption_placeholder_text("  Output stopped.  ") is True


def test_is_interruption_placeholder_text_rejects_normal_and_substring():
    assert is_interruption_placeholder_text("你好") is False
    assert is_interruption_placeholder_text("Output stopped. 请继续") is False
    assert is_interruption_placeholder_text("") is False
    assert is_interruption_placeholder_text(None) is False


def test_scrub_removes_string_format_placeholders_and_keeps_others():
    contexts = [
        {"role": "user", "content": "Stop output."},
        {"role": "assistant", "content": "Output stopped."},
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "我在。"},
    ]
    removed = scrub_interruption_placeholders(contexts)

    assert removed == 2
    assert contexts == [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "我在。"},
    ]


def test_scrub_removes_list_of_parts_placeholders():
    contexts = [
        {"role": "user", "content": [{"type": "text", "text": "Stop output."}]},
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "Output stopped."}],
        },
        {"role": "user", "content": "正常消息"},
    ]
    removed = scrub_interruption_placeholders(contexts)

    assert removed == 2
    assert contexts == [{"role": "user", "content": "正常消息"}]


def test_scrub_keeps_multimodal_entries_even_with_placeholder_text():
    contexts = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Output stopped."},
                {"type": "image_url", "image_url": {"url": "http://x/img.jpg"}},
            ],
        },
        {"role": "assistant", "content": "正常回复"},
    ]
    removed = scrub_interruption_placeholders(contexts)

    assert removed == 0
    assert len(contexts) == 2


def test_scrub_returns_zero_when_no_placeholders():
    contexts = [
        {"role": "user", "content": "第一条"},
        {"role": "assistant", "content": "第二条"},
    ]
    original = list(contexts)

    removed = scrub_interruption_placeholders(contexts)

    assert removed == 0
    assert contexts == original


def test_scrub_returns_zero_for_non_list_input():
    assert scrub_interruption_placeholders(None) == 0
    assert scrub_interruption_placeholders("Output stopped.") == 0


def test_on_llm_response_guard_stops_interruption_placeholder():
    optimizer = make_optimizer()
    event = FakeEvent("u1", "friend:1", "图片", wake=True)
    resp = FakeResp("Output stopped.")

    asyncio.run(optimizer.on_llm_response_guard(event, resp))

    assert event.stopped is True


def test_on_llm_response_guard_keeps_normal_response():
    optimizer = make_optimizer()
    event = FakeEvent("u1", "friend:1", "图片", wake=True)
    resp = FakeResp("这是正常回复")

    asyncio.run(optimizer.on_llm_response_guard(event, resp))

    assert event.stopped is False


def test_on_llm_request_marking_scrubs_placeholder_contexts():
    optimizer = make_optimizer()
    event = FakeEvent("u1", "friend:1", "你好", wake=True)
    req = SimpleNamespace(
        contexts=[
            {"role": "user", "content": "Stop output."},
            {"role": "assistant", "content": "Output stopped."},
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "我在。"},
        ],
        extra_user_content_parts=[],
    )

    asyncio.run(optimizer.on_llm_request_marking(event, req))

    assert req.contexts == [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "我在。"},
    ]
