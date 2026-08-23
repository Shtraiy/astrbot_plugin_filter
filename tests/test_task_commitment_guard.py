"""Tests for the task-execution instruction injection.

Covers:
- ``task_commitment_guard`` pure helper (idempotent system-prompt injection).
- ``LanguageLogicOptimizer.on_llm_request_task_guard`` hook and its config
  switch, so the LLM actually calls tools instead of replying with a
  promise like "我正在帮你看" and stopping.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from task_commitment_guard import (
    TASK_EXECUTION_INSTRUCTION,
    inject_task_execution_instruction,
)

from tests.conftest import FakeEvent, make_optimizer


def test_inject_into_empty_system_prompt():
    req = SimpleNamespace(system_prompt="", contexts=[], extra_user_content_parts=[])

    assert inject_task_execution_instruction(req) is True
    assert TASK_EXECUTION_INSTRUCTION in req.system_prompt


def test_inject_appends_and_keeps_existing_prompt():
    req = SimpleNamespace(
        system_prompt="你是真子。",
        contexts=[],
        extra_user_content_parts=[],
    )

    assert inject_task_execution_instruction(req) is True
    assert req.system_prompt.startswith("你是真子。")
    assert TASK_EXECUTION_INSTRUCTION in req.system_prompt


def test_inject_is_idempotent():
    req = SimpleNamespace(system_prompt="", contexts=[], extra_user_content_parts=[])

    assert inject_task_execution_instruction(req) is True
    assert inject_task_execution_instruction(req) is False
    assert req.system_prompt.count(TASK_EXECUTION_INSTRUCTION) == 1


def test_inject_defensive_for_none_req():
    assert inject_task_execution_instruction(None) is False


def test_on_llm_request_task_guard_injects_instruction():
    optimizer = make_optimizer()
    event = FakeEvent("u1", "group:1", "帮我搜追番列表", wake=True)
    req = SimpleNamespace(system_prompt="", contexts=[], extra_user_content_parts=[])

    asyncio.run(optimizer.on_llm_request_task_guard(event, req))

    assert TASK_EXECUTION_INSTRUCTION in req.system_prompt


def test_on_llm_request_task_guard_respects_config_switch():
    optimizer = make_optimizer(enable_task_execution_guard=False)
    event = FakeEvent("u1", "group:1", "帮我搜追番列表", wake=True)
    req = SimpleNamespace(system_prompt="", contexts=[], extra_user_content_parts=[])

    asyncio.run(optimizer.on_llm_request_task_guard(event, req))

    assert req.system_prompt == ""
