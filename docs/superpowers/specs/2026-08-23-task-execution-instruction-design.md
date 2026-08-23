# 设计：任务执行指令注入，防"承诺后停止"（v3.0.17）

> 日期：2026-08-23
> 状态：已确认（方案 A）
> 范围：`astrbot_plugin_filter` 增量修复，新增 1 个 invisible 配置项

## 1. 背景与根因

用户让 bot"帮我搜追番列表"，bot 回复"我正在帮你看"后没有下一步动作，需要用户再次提醒才真正搜索。

根因在 AstrBot 核心 `tool_loop_agent_runner.py` 的 `step()`：

```python
if not llm_resp.tools_call_name:
    await self._complete_with_assistant_response(llm_resp)
```

LLM 返回的回复**没有工具调用**时，agent 循环直接结束，承诺性文本被当作最终回复发出。AstrBot 自带的工具重试只覆盖"无有意义回复"（`_has_meaningful_assistant_reply`），"正在帮你看"被判定为有意义回复，不会重试。工具链路本身是通的（用户再次提醒后 agent 正常调用了 `transfer_to_tool_executor` + grep），问题在模型第一步偷懒。

## 2. 方案（已确认：方案 A，prompt 注入）

在 `on_llm_request` 钩子中向 `req.system_prompt` 追加一条任务执行指令：

> 当用户要求执行搜索、查询、获取信息、计算、总结等任务时，必须调用可用的工具（包括 MCP 工具）完成任务后再回复，不得只回复承诺性内容（如"我帮你搜""正在看""稍等"）后停止；若确实无法调用工具，请直接说明无法完成。

约束行为而非修改核心：不缝合主动 agent、不引入定时任务，让模型在第一步就直接调用工具。

## 3. 组件设计

### 3.1 新增 `task_commitment_guard.py`（纯函数）

| 常量/函数 | 职责 |
|---|---|
| `TASK_EXECUTION_INSTRUCTION` | 指令文本（幂等标记即全文） |
| `inject_task_execution_instruction(req) -> bool` | 向 `req.system_prompt` 追加指令；已包含则跳过；返回是否写入 |

规则：
- `req` 为 None、无 `system_prompt` 属性 → 返回 False；
- `system_prompt` 为空 → 直接写入；非空 → 以空行分隔追加，保留原文；
- 已包含指令全文 → 跳过（幂等）；
- 所有异常按 False 处理，不中断主流程。

### 3.2 `main.py` 变更

新增 `on_llm_request` 钩子（priority=500，介于现有 guard 1000 与 marking -1000 之间）：

- 配置 `enable_task_execution_guard`（默认 True，invisible）关闭时跳过；
- 事件已停止或 `req` 为 None → 跳过；
- `inject_task_execution_instruction(req)` 返回 True → 记录日志。

## 4. 测试（TDD）

新增 `tests/test_task_commitment_guard.py`：

1. 空 `system_prompt` 注入后包含指令；
2. 已有 `system_prompt` 追加且保留原文；
3. 幂等：重复注入不重复追加；
4. `req` 为 None → False；
5. 集成：`on_llm_request_task_guard` 注入指令；
6. 集成：`enable_task_execution_guard=False` 时不注入。

## 5. 非目标

- 不做承诺检测 + 自动续跑（方案 B，待 A 效果评估后再定）；
- 不改 AstrBot 核心；
- 不引入轻量模型判断。

版本：v3.0.17。
