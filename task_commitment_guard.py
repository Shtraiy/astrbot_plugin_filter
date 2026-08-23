"""Inject a task-execution instruction so the LLM does not stop after a promise.

AstrBot's agent loop finishes as soon as the LLM returns a response without a
tool call (``_complete_with_assistant_response``). Some models reply "我帮你搜"
/ "正在看" instead of calling the requested tool, leaving the user without an
actual result. This module appends a short instruction to ``req.system_prompt``
so the model calls the tool (including MCP tools) before answering.

Pure helper; the hook decorator lives on the Star class in ``main.py``.
"""

from __future__ import annotations

from typing import Any

TASK_EXECUTION_INSTRUCTION = (
    "当用户要求执行搜索、查询、获取信息、计算、总结等任务时，必须调用可用的"
    "工具（包括 MCP 工具）完成任务后再回复，不得只回复承诺性内容（如“我帮你搜”"
    "“正在看”“稍等”）后停止；若确实无法调用工具，请直接说明无法完成。"
)


def inject_task_execution_instruction(req: Any) -> bool:
    """Append the instruction to ``req.system_prompt``; idempotent.

    Returns True when the instruction was written this call. Never raises:
    malformed requests simply return False.
    """
    if req is None:
        return False
    try:
        current = str(getattr(req, "system_prompt", "") or "")
    except Exception:
        return False
    if TASK_EXECUTION_INSTRUCTION in current:
        return False
    try:
        req.system_prompt = (
            current.rstrip() + "\n\n" + TASK_EXECUTION_INSTRUCTION
            if current.strip()
            else TASK_EXECUTION_INSTRUCTION
        )
    except Exception:
        return False
    return True


__all__ = ["TASK_EXECUTION_INSTRUCTION", "inject_task_execution_instruction"]
