# 实施计划：规划期新消息「打断 vs 悬挂」（v3.0.14）

> 依据设计：[2026-08-23-reply-interrupt-vs-hang-design.md](../specs/2026-08-23-reply-interrupt-vs-hang-design.md)

## 步骤

1. **失败测试**（先写，确认失败）
   - `tests/test_merge_guards.py`：`is_correction_follow_up` 命中/否定前缀/@bot 前缀/普通补充；`should_interrupt_running_reply` 真值表。
   - `tests/test_reply_coordinator.py`：`active_event_for` 三态。
   - `tests/test_merge_integration.py`：`on_message` / `on_waiting_llm_request` 悬挂分支（不 stop、不 promote、不合并、planning state 被清理）。
2. **最小实现**
   - `merge_guards.py`：两个纯函数 + `CORRECTION_TERMS` 词表。
   - `reply_coordinator.py`：`active_event_for`。
   - `main.py`：`on_message` 与 `_handle_planning_phase` 插入悬挂判定；`_request_agent_stop` 改为接收旧事件；悬挂时 `merger.clear_state(old_event)`。
3. **全量验证**：`pytest -q`（基线 151 passed 之上只增不减）。
4. **版本**：`metadata.yaml` 与 README badge 升 v3.0.14，更新 changelog（若存在）。
5. **提交推送**：含 v3.0.13 未推提交 + 新提交 + v3.0.13/v3.0.14 标签（escalated）。
