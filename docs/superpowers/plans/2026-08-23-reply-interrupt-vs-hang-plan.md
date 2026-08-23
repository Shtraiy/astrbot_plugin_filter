# 实施计划：规划期新消息「打断 vs 悬挂」（v3.0.15）

> 依据设计：[2026-08-23-reply-interrupt-vs-hang-design.md](../specs/2026-08-23-reply-interrupt-vs-hang-design.md)

## 步骤

1. **失败测试**（先写，确认失败）
   - `tests/test_merge_guards.py`：`is_correction_follow_up` 命中/否定前缀/@bot 前缀/普通补充；`should_interrupt_running_reply` 真值表。
   - `tests/test_reply_coordinator.py`：`active_event_for` 三态。
   - `tests/test_merge_integration.py`：`on_message` / `on_waiting_llm_request` 悬挂分支（不 stop、不 promote、不合并、planning state 被清理）。
2. **最小实现**
   - `main.py`：`on_llm_response_guard` 写入 `llm_output_started` 标记；`_should_interrupt_active_reply` 判定从 `provider_request` 改为 `llm_output_started`；`merge_guards.py` 参数改名。
3. **全量验证**：`pytest -q`（基线 151 passed 之上只增不减）。
4. **版本**：`metadata.yaml` 与 README badge 升 v3.0.15，更新 changelog。
5. **提交推送**：新提交 + v3.0.15 标签（escalated）。
