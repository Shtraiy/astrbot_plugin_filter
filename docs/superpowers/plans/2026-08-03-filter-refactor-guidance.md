# Filter 插件结构优化修复指导

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不删除现有文本清理能力的前提下，拆分 `main.py` 的职责，统一回复生命周期和分段发送路径，降低后续修改风险。

**Architecture:** 保留 `clean_garbage`、工具痕迹处理、敏感信息过滤、Markdown 清理、分段和 Private Companion 联动等现有行为。将发送前文本处理、回复状态协调、消息发送和 Private Companion 适配分别放入独立模块；`main.py` 最终只负责 AstrBot 事件钩子编排。

**Tech Stack:** Python 3.10+、AstrBot Plugin API、`asyncio`、pytest、现有 `MessageChain` 与消息组件。

## Global Constraints

- 不删除 `clean_garbage`、`replace_tool_leakage`、`remove_tool_narration`、`deidentify_tool_names`。
- 不改变 `filter_sensitive`、`evaluate_input`、`evaluate_output` 的安全语义。
- 不改变 `multi_message`、`delay_min`、`delay_max`、`gate_seconds`、`gate_ttl_seconds` 的现有配置含义。
- Private Companion 未安装、未加载、API 不兼容或取消失败时，filter 必须继续正常工作。
- 每个重构任务都必须先补行为测试，再移动生产代码。
- 不新增第三方运行时依赖；测试继续使用现有 pytest 配置。
- 不在本计划中重写 `image_renderer.py` 或重新设计分段算法；仅在接口需要时做最小适配。

---

## 当前问题与保留范围

当前 `main.py` 的 `LanguageLogicOptimizer.on_decorating_result` 同时负责：

1. 合并和遍历消息组件。
2. 清理模型输出和工具痕迹。
3. 调用 LLM 文风/分段处理。
4. Markdown 纯文本化和敏感信息复检。
5. 内容防护判断。
6. 列表图片发送。
7. 多消息拆分和后续消息调度。
8. 回复锁、gate、冷却和 Private Companion 过时主动消息处理。

本次不以“删除旧清理功能”为目标。原因是这些函数本身位于 `pipelines.py`，规模可控，且已有针对工具泄露和安全边界的测试；真正臃肿的是它们被编排在同一个事件函数里。

## 优先级路线

| 优先级 | 目标 | 主要文件 | 完成标准 |
|---|---|---|---|
| P0 | 建立重构前行为基线 | `tests/` | 现有 133 个测试继续通过，并补齐关键发送链路测试 |
| P0 | 拆出文本处理流水线 | `outbound_pipeline.py`, `main.py` | 文本清理顺序和结果不变 |
| P1 | 统一回复状态和生命周期 | `reply_coordinator.py`, `main.py` | gate、锁、取消、冷却只由一个对象管理 |
| P1 | 统一多消息发送入口 | `message_dispatcher.py`, `segmentation.py`, `main.py` | 不再存在两套独立的后续消息发送状态 |
| P1 | 隔离 Private Companion 适配 | `private_companion_adapter.py`, `main.py` | 主流程不再直接依赖 `_private_companion_*` 字段 |
| P2 | 收敛文档、命名和低风险长函数 | `README.md`, `image_renderer.py`, `segmentation.py` | 仅在前述拆分稳定后处理，不扩大重构范围 |

---

### Task 1: 建立重构前行为基线

**Files:**
- Create: `tests/test_refactor_baseline.py`
- Modify: `tests/test_cooldown.py`
- Modify: `tests/test_security_critical.py`
- Modify: `tests/test_single_message.py`

**Interfaces:**
- Consumes: 当前 `LanguageLogicOptimizer`、`segmentation.prepare_multi_message_parts`、现有 fake AstrBot event/context。
- Produces: 可验证的清理、发送、取消和失败回退行为，供后续任务逐步迁移。

- [ ] **Step 1: 写文本清理顺序的基线测试**

```python
def test_existing_cleanup_stages_remain_in_order(monkeypatch):
    calls = []

    def mark(name, value):
        calls.append(name)
        return value

    monkeypatch.setattr(main, "clean_garbage", lambda value: mark("garbage", value))
    monkeypatch.setattr(main, "replace_user", lambda value: mark("user", value))
    monkeypatch.setattr(main, "filter_sensitive", lambda value: mark("sensitive", value))
    monkeypatch.setattr(main, "replace_tool_leakage", lambda value: mark("tool_leak", value))
    monkeypatch.setattr(main, "remove_tool_narration", lambda value: mark("tool_narration", value))
    monkeypatch.setattr(main, "deidentify_tool_names", lambda value: mark("tool_name", value))

    # 调用当前单条文本处理入口；迁移后改为调用 outbound_pipeline 的公共入口。
    # 断言应保持清理顺序，不断言具体内部实现。
    assert calls == [
        "garbage",
        "user",
        "sensitive",
        "tool_leak",
        "tool_narration",
        "tool_name",
    ]
```

实现时应将上例改成针对最终公共接口的测试，避免测试绑定在 `main.py` 私有局部变量上。

- [ ] **Step 2: 增加主动回复被用户消息取代的完整链路测试**

测试必须覆盖：主动事件占用 gate、普通用户事件通过、调用 Private Companion 取消适配、旧主动结果被清空。

- [ ] **Step 3: 增加分段发送失败和取消测试**

测试必须覆盖：首段正常保留、后续段发送失败仍释放锁、发送任务异常不会产生未回收的 gate。

- [ ] **Step 4: 运行基线测试**

Run:

```bash
python -m pytest -q
```

Expected: 当前基线保持 `133 passed`；新增测试通过后，后续每个任务都重复执行受影响测试。

- [ ] **Step 5: Commit**

```bash
git add tests/test_refactor_baseline.py tests/test_cooldown.py tests/test_security_critical.py tests/test_single_message.py
git commit -m "test: lock down outbound reply behavior before refactor"
```

### Task 2: 拆出文本处理流水线

**Files:**
- Create: `outbound_pipeline.py`
- Modify: `main.py:19-41, 137-184`
- Test: `tests/test_refactor_baseline.py`
- Modify: `tests/test_pipelines.py` only when公共接口迁移需要调整导入路径

**Interfaces:**
- Consumes: 当前 `pipelines.py` 中的清理函数、`apply_segmentation_and_style`、`content_guard`。
- Produces: `OutboundTextPipeline.process(text, event) -> ProcessedText`。

建议接口：

```python
@dataclass(frozen=True)
class ProcessedText:
    text: str
    changed: bool
    guard_blocked: bool
    stats: dict[str, int]


class OutboundTextPipeline:
    async def process(self, text: str, event: AstrMessageEvent) -> ProcessedText:
        ...
```

- [ ] **Step 1: 为 `ProcessedText` 和公共处理入口补失败测试**

测试输入应至少包括：普通文本、工具流程泄露文本、敏感词文本、Markdown 文本和内容防护命中文本。

- [ ] **Step 2: 实现 `OutboundTextPipeline`，保持原顺序**

处理顺序固定为：

```text
clean_garbage
replace_user
filter_sensitive
replace_tool_leakage
remove_tool_narration
deidentify_tool_names
de_ai_flavor（按配置）
apply_segmentation_and_style
strip_markdown
filter_sensitive（二次检查）
evaluate_output
```

现有四类模型痕迹清理必须继续调用，不得因为本次拆分而删除。

- [ ] **Step 3: 将 `main.py` 的文本循环替换为 pipeline 调用**

`main.py` 只接收 `ProcessedText`，负责决定是否发送图片、是否拆分消息，以及如何更新消息组件；它不再逐个调用清理函数。

- [ ] **Step 4: 运行文本和安全测试**

```bash
python -m pytest -q tests/test_pipelines.py tests/test_segmentation.py tests/test_security_critical.py
```

Expected: 所有清理结果与重构前一致。

- [ ] **Step 5: Commit**

```bash
git add outbound_pipeline.py main.py tests/test_refactor_baseline.py tests/test_pipelines.py
git commit -m "refactor: extract outbound text pipeline"
```

### Task 3: 统一回复状态协调器

**Files:**
- Create: `reply_coordinator.py`
- Modify: `main.py:44-77, 376-585, 598-672`
- Test: `tests/test_cooldown.py`
- Test: `tests/test_reply_lock_unittest.py`

**Interfaces:**
- Consumes: `_GateState`、`_OnboardingState`、当前 gate/lock/cooldown 行为。
- Produces: `ReplyCoordinator` 和 `ReplySession`。

建议接口：

```python
@dataclass
class ReplySession:
    origin: str
    owner_event: AstrMessageEvent | None
    reply_lock: asyncio.Lock
    superseded_by_user: bool = False
    cancel_requested: bool = False
    followup_task: asyncio.Task | None = None


class ReplyCoordinator:
    async def acquire_reply(self, event: AstrMessageEvent) -> ReplySession: ...
    def claim_wakeup(self, event: AstrMessageEvent) -> bool: ...
    def mark_user_priority(self, event: AstrMessageEvent) -> bool: ...
    def discard_superseded_result(self, event: AstrMessageEvent) -> bool: ...
    def register_followup(self, session: ReplySession, task: asyncio.Task) -> None: ...
    def release(self, session: ReplySession, *, apply_cooldown: bool = False) -> None: ...
    def release_after_send(self, event: AstrMessageEvent) -> None: ...
```

- [ ] **Step 1: 为 `ReplyCoordinator` 补 gate、锁、冷却和用户优先测试**

把现有 `tests/test_cooldown.py` 的断言迁移到新对象，确保普通用户消息不会被主动事件误杀。

- [ ] **Step 2: 移动 `_GateState` 和会话清理逻辑**

`MAX_GATE_STATES`、TTL、相关事件 ID 判断和 gate 释放逻辑只保留在 `reply_coordinator.py`。

- [ ] **Step 3: 让 `main.py` 委托所有回复状态操作**

删除 `main.py` 中重复的 `_gates`、`_reply_locks`、`_pending_send` 和 `_pending_sends` 直接操作；`main.py` 只能调用协调器接口。

- [ ] **Step 4: 运行生命周期测试**

```bash
python -m pytest -q tests/test_cooldown.py tests/test_reply_lock_unittest.py tests/test_single_message.py
```

Expected: gate、锁、冷却、事件回调和失败释放行为全部保持不变。

- [ ] **Step 5: Commit**

```bash
git add reply_coordinator.py main.py tests/test_cooldown.py tests/test_reply_lock_unittest.py tests/test_single_message.py
git commit -m "refactor: centralize reply lifecycle coordination"
```

### Task 4: 统一多消息发送入口

**Files:**
- Create: `message_dispatcher.py`
- Modify: `segmentation.py:252-280`
- Modify: `main.py:194-247, 268-303`
- Test: `tests/test_reply_lock_unittest.py`
- Test: `tests/test_security_critical.py`

**Interfaces:**
- Consumes: `prepare_multi_message_parts`、`send_followups`、`Context.send_message`、`ReplyCoordinator`。
- Produces: `MessageDispatcher.send_followups(...)`，所有后续消息统一经过此入口。

建议接口：

```python
@dataclass(frozen=True)
class DispatchPolicy:
    delay_min: float
    delay_max: float
    max_followups: int = 4


class MessageDispatcher:
    async def send_followups(
        self,
        origin: str,
        paragraphs: list[str],
        *,
        policy: DispatchPolicy,
        session: ReplySession,
        process_text: Callable[[str], Awaitable[str | None]] | None = None,
    ) -> None:
        ...
```

- [ ] **Step 1: 补充发送顺序、延迟上限、失败释放和取消测试**

测试必须断言：最多补发 4 条、每条按顺序发送、异常后继续释放会话、取消后不再发送后续段。

- [ ] **Step 2: 将 `send_followups` 的发送实现移动到 `MessageDispatcher`**

`segmentation.py` 只保留分段、合并和去重算法；不再持有消息发送状态。

- [ ] **Step 3: 让 dispatcher 在发送前支持文本处理回调**

后续段必须能复用 `OutboundTextPipeline`，避免只有首段经过安全过滤。

- [ ] **Step 4: 删除 `main.py` 中重复的后续发送分支**

图片直发仍由现有图片渲染功能负责；文本后续段统一交给 dispatcher。

- [ ] **Step 5: 运行发送测试**

```bash
python -m pytest -q tests/test_security_critical.py tests/test_reply_lock_unittest.py tests/test_single_message.py
```

- [ ] **Step 6: Commit**

```bash
git add message_dispatcher.py segmentation.py main.py tests/test_security_critical.py tests/test_reply_lock_unittest.py tests/test_single_message.py
git commit -m "refactor: unify follow-up message dispatch"
```

### Task 5: 隔离 Private Companion 适配层

**Files:**
- Create: `private_companion_adapter.py`
- Modify: `main.py:351-461`
- Modify: `tests/test_cooldown.py`
- Create: `tests/test_private_companion_adapter.py`
- Modify: `README.md` only if适配行为或支持范围发生变化

**Interfaces:**
- Consumes: AstrBot event、Private Companion 可选 API。
- Produces: `PrivateCompanionAdapter.is_proactive_event`、`schedule_cancel`、`cancel`。

建议接口：

```python
class PrivateCompanionAdapter:
    @staticmethod
    def is_proactive_event(event: AstrMessageEvent | None) -> bool: ...

    def schedule_cancel(self, event: AstrMessageEvent) -> None: ...

    async def cancel(self, session_id: str, token: str) -> bool: ...
```

- [ ] **Step 1: 为适配器补 API 缺失、导入失败、取消异常和重复取消测试**

所有失败都必须返回 `False` 或记录日志，不能抛出到普通用户消息处理流程。

- [ ] **Step 2: 移动主动事件标记识别和动态导入逻辑**

`main.py` 不再直接出现 `_private_companion_*` 字段名和模块路径；这些细节只能存在适配器中。

- [ ] **Step 3: 接入 `ReplyCoordinator` 的用户优先流程**

用户消息只通过协调器标记主动会话失效，取消动作交给适配器异步执行；取消失败不阻塞用户回复。

- [ ] **Step 4: 运行适配器和全量测试**

```bash
python -m pytest -q tests/test_private_companion_adapter.py tests/test_cooldown.py
python -m pytest -q
```

- [ ] **Step 5: Commit**

```bash
git add private_companion_adapter.py main.py tests/test_private_companion_adapter.py tests/test_cooldown.py README.md
git commit -m "refactor: isolate Private Companion adapter"
```

### Task 6: 收敛主入口并更新维护文档

**Files:**
- Modify: `main.py`
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-08-01-private-companion-coordination-design.md` only when实际接口说明需要同步
- Test: `tests/`

**Interfaces:**
- Consumes: `OutboundTextPipeline`、`ReplyCoordinator`、`MessageDispatcher`、`PrivateCompanionAdapter`。
- Produces: 单一的 AstrBot 事件编排入口。

- [ ] **Step 1: 将 `on_decorating_result` 收敛为编排函数**

目标结构：

```python
async def on_decorating_result(self, event):
    if not event:
        return
    if self.reply_coordinator.discard_superseded_result(event):
        return
    result = self._get_result(event)
    if not result:
        self.reply_coordinator.release_after_send(event)
        return
    session = await self.reply_coordinator.acquire_reply(event)
    await self._process_and_dispatch_result(event, result, session)
```

事件函数只负责顺序控制，不再直接实现清理算法或发送细节。

- [ ] **Step 2: 统一命名和日志字段**

统一使用 `origin`、`session`、`followup`、`proactive`、`superseded` 等术语；日志中保留来源、会话和段序号，避免同一概念出现多种叫法。

- [ ] **Step 3: 更新 README 的模块职责说明**

明确说明：模型痕迹清理仍然保留；文本流水线、消息生命周期、消息发送和 Private Companion 适配已经分离。

- [ ] **Step 4: 运行编译、测试和静态检查**

```bash
python -m py_compile main.py outbound_pipeline.py reply_coordinator.py message_dispatcher.py private_companion_adapter.py content_guard.py pipelines.py segmentation.py image_renderer.py
python -m pytest -q
```

- [ ] **Step 5: Commit**

```bash
git add main.py README.md docs/superpowers/specs/2026-08-01-private-companion-coordination-design.md
git commit -m "refactor: simplify filter plugin entrypoint"
```

### Task 7: P2 低风险收尾

**Files:**
- Modify: `image_renderer.py` only if前述任务完成后仍有明确收益
- Modify: `segmentation.py` only for纯函数命名、类型标注和模块注释
- Test: `tests/test_image_renderer.py`, `tests/test_segmentation.py`

**Interfaces:**
- Consumes: 已稳定的 pipeline、coordinator 和 dispatcher 接口。
- Produces: 更清晰的低风险算法模块，不改变对外行为。

- [ ] **Step 1: 只拆出具有独立语义的纯函数**

图像模块优先按“输入规范化、布局计算、绘制、临时文件清理”分组；分段模块优先保持算法纯函数，不把发送状态重新放回去。

- [ ] **Step 2: 为每个拆出的纯函数补边界测试**

覆盖空文本、超长文本、超过最大段数、列表渲染失败和临时文件清理失败等现有边界。

- [ ] **Step 3: 运行专项测试**

```bash
python -m pytest -q tests/test_image_renderer.py tests/test_segmentation.py
```

- [ ] **Step 4: 仅在行为无变化时提交**

```bash
git add image_renderer.py segmentation.py tests/test_image_renderer.py tests/test_segmentation.py
git commit -m "refactor: clarify pure segmentation and rendering helpers"
```

## 不应做的事情

- 不要删除四类模型痕迹清理函数；它们并不是当前最大的结构问题。
- 不要把所有逻辑移动到一个新的 `utils.py`，否则只是换一个 God Module。
- 不要同时保留两套后续消息发送器。
- 不要让 Private Companion 的内部字段继续扩散到主流程和测试之外。
- 不要在没有行为测试的情况下先重写 `on_decorating_result`。
- 不要为了“看起来干净”顺便修改敏感词规则、Markdown 规则或分段算法。

## 完成验收标准

- `main.py` 的事件入口只负责编排，文本处理和发送生命周期有独立模块。
- 四类模型痕迹清理仍然存在，原有相关测试继续通过。
- 所有后续文本消息经过统一 dispatcher，并能在发送前复用安全处理。
- Private Companion 缺失或取消失败时，普通消息仍能正常发送。
- 用户消息可以取代同源主动消息，旧主动结果不会继续发送。
- 回复锁、gate、冷却和异常释放行为与重构前一致。
- 全量测试通过，且新增跨插件分段/取消测试覆盖主要竞态场景。
