# 设计：AstrBot 中断占位符清洗（v3.0.11）

> 日期：2026-08-23
> 状态：已确认（方案 B）
> 范围：`astrbot_plugin_filter` 增量修复，无破坏性变更

## 1. 背景与目标

日志中出现记忆召回查询被 `Output stopped.` 污染：

```
查询='[图片] | Output stopped. | [图片] | 啊……还真的是我自己发的吗 ...'
```

根因在 AstrBot 核心（`tool_loop_agent_runner.py`）：

- `USER_INTERRUPTION_REQUEST = "Stop output."`（user 角色占位符）
- `USER_INTERRUPTION_MESSAGE = "Output stopped."`（assistant 角色占位符）
- agent 被终止（`/stop`、同用户再次唤醒触发本插件 `_request_agent_stop`、规划期补充重生成）时，核心把这对占位符写入 run context，最终以 `completion_text="Output stopped."` 落进会话历史；
- 核心在 `on_llm_response` 钩子链广播该占位符响应；
- livingmemory 的 `handle_memory_reflection` 不检查 `event.is_stopped()`，把占位符当作真实助手回复写入它自己的会话库，后续召回查询原样引用。

本插件现有 `on_llm_response_guard` 只拦截"被合并逻辑标记为 superseded"的事件，拦不住用户 `/stop` 等其他来源的中断。

目标：

1. 新产生的中断占位符不再进入 livingmemory 会话库（召回查询恢复干净）；
2. 模型请求上下文中不再出现 `Stop output.` / `Output stopped.` 占位符对；
3. 不修改 AstrBot 核心、不修改 livingmemory 数据。

## 2. 方案（已确认：方案 B）

### 2.1 响应侧拦截（主修复）

`on_llm_response`（priority=1000）中，在现有 `stop_if_superseded` 之后增加：

- 若 `resp.completion_text` 去除空白后精确等于中断占位符（`Output stopped.`），调用 `event.stop_event()` 并记录日志。
- `call_event_hook` 检测到事件被 stop 后短路，livingmemory 的 `handle_memory_reflection` 不再执行 → 占位符不再写入其会话库。

### 2.2 请求侧清洗（防御）

`on_llm_request_marking`（priority=-1000，livingmemory 召回之后、真正发请求之前）中：

- 遍历 `req.contexts`，删除 content 精确等于 `Stop output.` / `Output stopped.` 的纯文本条目（兼容 content 为字符串或 `[{type:text}]` 两种格式）；
- 多模态条目（含图片等非文本 part）一律保留，绝不误删；
- 只做精确匹配，长文本中出现的子串不删。

## 3. 组件设计

### 3.1 新增 `interruption_guard.py`（纯函数，无 AstrBot Star 依赖）

| 函数 | 职责 |
|---|---|
| `INTERRUPTION_PLACEHOLDERS` | `("Stop output.", "Output stopped.")` 常量 |
| `is_interruption_placeholder_text(text) -> bool` | 精确匹配占位符（strip 后比对） |
| `scrub_interruption_placeholders(contexts) -> int` | 原地删除占位符条目，返回删除条数 |

`scrub_interruption_placeholders` 规则：

- 条目为 Mapping（或带 `role`/`content` 属性的对象）时读取 content；
- content 为字符串 → 直接精确比对；
- content 为 list → 仅当列表为"纯文本 part"（全部为 `type=text` 且拼接后精确匹配）才删除；含媒体 part 的条目保留；
- 删除时保留列表其余条目顺序。

### 3.2 `main.py` 变更

- `on_llm_response_guard`：增加占位符检测 + stop + 日志；
- `_apply_self_reply_marking`（或前置）：调用 `scrub_interruption_placeholders(req)`，返回删除数 > 0 时记录日志。

## 4. 数据流

```
agent 被终止
  -> 核心广播 on_llm_response(completion_text="Output stopped.")
  -> [本插件 priority=1000] event.stop_event()
  -> livingmemory handle_memory_reflection 被短路，不写入会话库
  -> 核心 _save_to_history 仍写入 AstrBot 历史（无法拦截）

下一次 LLM 请求
  -> livingmemory 召回（其会话库已干净）
  -> [本插件 priority=-1000] scrub req.contexts 删除占位符对
  -> 模型上下文不含占位符
```

## 5. 错误处理

- `resp.completion_text` 为 None/非字符串 → 不匹配、不 stop；
- `req` 或 `req.contexts` 缺失/非 list → 清洗跳过，返回 0；
- 单个条目解析异常 → 跳过该条目，不中断整体清洗（try/except 粒度到条目）。

## 6. 测试（TDD）

新增 `tests/test_interruption_guard.py`：

1. `is_interruption_placeholder_text` 匹配两种占位符（含空白差异）；
2. 不匹配普通文本、占位符子串（长文本包含）；
3. `scrub_interruption_placeholders` 删除字符串格式占位符对、保留正常消息；
4. 删除 list-of-parts 格式占位符对；
5. 含媒体 part 的条目保留；
6. 无可删内容返回 0；
7. 集成：`on_llm_response_guard` 对占位符响应 stop 事件、对正常响应不 stop；
8. 集成：`on_llm_request_marking` 后 `req.contexts` 不再含占位符。

## 7. 非目标

- 不修改 AstrBot 核心（上游根治：占位符打 `_no_save`、中断响应不广播钩子，另行提 issue）；
- 不清洗 livingmemory 已污染的历史数据（可后续用 `/lmem` 相关命令手动清理）；
- 不处理白名单、响应延迟、Steam 日志等其它问题。
