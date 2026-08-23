# 屎山优化实施计划（v3.0.12）

> **For agentic workers:** REQUIRED SUB-SKILL: 本计划由主代理内联执行（executing-plans 方式，带检查点），不使用子代理。步骤使用 checkbox（`- [ ]`）语法跟踪。

**Goal:** 在不改变任何对外行为的前提下，消除 main.py 上帝文件、统一 AstrBot 事件访问层、删除死代码、统一测试夹具、修正命名漂移。

**Architecture:** 新增纯函数模块 `event_access.py` 作为唯一的事件/上下文访问入口；onboarding 状态机独立成 `onboarding_guard.py`；main.py 只保留钩子路由与配置转发；测试夹具统一收口到 `conftest.py`。

**Tech Stack:** Python 3.x、pytest（120 条存量测试为回归基线）、AstrBot Star 插件 API（stub 测试）。

## Global Constraints

- 行为零变化：不改变对外日志文案（`[消息合并]` / `[自回复标记]` 等标签）、不改变配置项语义、不改变任何钩子优先级。
- 每任务独立提交；每次提交前 `python -m pytest -q` 必须全绿。
- 新代码必须 TDD：先写失败测试 → 确认失败 → 最小实现 → 确认通过。
- 不引入新的运行时依赖；不修改 AstrBot 核心。
- 保留 `merge_task_cancel` 兼容配置（有意保留的死配置）。

---

## Task 1: 事件访问层 `event_access.py`

**Files:**
- Create: `event_access.py`
- Test: `tests/test_event_access.py`

**Interfaces (Produces):**

```python
def get_message_chain(event) -> list | None        # message_obj.message，get_messages() 兜底
def is_reply_component(comp) -> bool               # 类名/type 含 "reply"
def has_reply(event) -> bool                       # 链上任意回复组件
def is_media_part(part) -> bool                    # 广义媒体判定（dict part 或组件，含 audio/video/record + 文本标记）
def is_image_or_file(comp) -> bool                 # 窄判定：Image/File 组件
def has_media(event) -> bool                       # 链上任意广义媒体
def media_components(event) -> list                # 链上 Image/File 组件（合并窗口用）
def plain_text_of(chain) -> str                    # 拼接 Plain 组件文本
def request_contexts(req) -> list | None           # req.contexts / context 列表
def entry_role(entry)                              # role 字段
def entry_content(entry)                           # content 字段
def entry_text(content) -> str | None              # 纯文本内容提取；多模态返回 None
```

- [ ] **Step 1: 写失败测试** `tests/test_event_access.py`（覆盖上述全部函数：正常链、空链、get_messages 兜底、回复组件、广义/窄媒体、dict part、多模态 content、文本拼接）。
- [ ] **Step 2: 运行确认失败** `python -m pytest tests/test_event_access.py -q` → ModuleNotFoundError。
- [ ] **Step 3: 实现 `event_access.py`**（纯函数，逻辑从 merge_window/self_reply_marker/interruption_guard 现有实现平移，不改变语义）。
- [ ] **Step 4: 运行确认通过** `python -m pytest tests/test_event_access.py -q` → 全绿。
- [ ] **Step 5: 提交** `git commit -m "feat: add shared event access layer (event_access.py)"`

## Task 2: 消费方切换到事件访问层

**Files:**
- Modify: `merge_window.py`（`message_has_quote`、`_extract_merge_payload`、`attach_media`、`_owner_media`、`has_media`、删 `_is_reply_component`）
- Modify: `self_reply_marker.py`（`has_user_media`、`has_referenced_image`、`attach_quoted_images`、`mark_context_media_ownership`、删 `_is_reply_component`/`_is_media_part`/`_request_contexts`/`_entry_role`/`_entry_content`）
- Modify: `interruption_guard.py`（`_entry_text` → 用 `event_access.entry_text`）

**Interfaces (Consumes):** Task 1 的全部函数。

- [ ] **Step 1: 写回归测试**：无新测试——120 条存量测试即回归基线；`tests/test_merge_integration.py`、`tests/test_self_reply_marker.py`、`tests/test_interruption_guard.py` 覆盖被重构函数。
- [ ] **Step 2: 逐模块替换**（merge_window → self_reply_marker → interruption_guard），每改一个模块跑一次 `python -m pytest -q`。
- [ ] **Step 3: 提交** `git commit -m "refactor: route event access through event_access layer"`

## Task 3: 拆 main.py（onboarding 迁出 + 钩子瘦身）

**Files:**
- Create: `onboarding_guard.py`（`OnboardingGuard` 类：touch / is_active / 时长与条数上限 / 有界淘汰）
- Modify: `main.py`（删 `_OnboardingState` 与 6 个 onboarding 方法；`on_llm_request` 改用 `self._get_onboarding_guard().touch(event)`；`on_waiting_llm_request` 拆出 `_handle_window_phase` / `_handle_planning_phase` 两个私有方法，钩子只做路由）
- Modify: `tests/conftest.py`（make_optimizer 兼容 `_onboarding_guard` 懒加载，无需预置）

**Interfaces (Consumes):** `content_guard.is_group_origin`。
**Interfaces (Produces):** `OnboardingGuard(get_config=...)`，方法 `touch(event) -> bool`、`is_active(event) -> bool`。

- [ ] **Step 1: 写失败测试** `tests/test_onboarding_guard.py`（直接测 OnboardingGuard：新群严格期判定、过期淘汰、非群事件跳过）。
- [ ] **Step 2: 运行确认失败** → AttributeError/ImportError。
- [ ] **Step 3: 实现 `onboarding_guard.py` + main.py 接线**（钩子行为不变；`on_waiting_llm_request` 拆方法由 `tests/test_merge_integration.py` 现有用例回归）。
- [ ] **Step 4: 全量测试** `python -m pytest -q` → 120+ 全绿。
- [ ] **Step 5: 提交** `git commit -m "refactor: extract onboarding guard and slim main.py hooks"`

## Task 4: 删除死代码

**Files:**
- Modify: `self_reply_marker.py`（删 `describe_contexts`、`MAX_MARK_STATES` 及 `__all__` 条目）
- Modify: `main.py`（删 `_track_task`、`_pending_tasks`）
- Modify: `content_guard.py`（删 `evaluate_output`）
- Modify: `tests/test_content_guard.py`（删 `evaluate_output` 相关用例与 import）
- Modify: `tests/conftest.py`（make_optimizer 不再设置 `_pending_tasks`）

- [ ] **Step 1: 确认删除目标无生产引用**：`rg -n "describe_contexts|_track_task|_pending_tasks|MAX_MARK_STATES|evaluate_output"`。
- [ ] **Step 2: 删除代码与对应测试**。
- [ ] **Step 3: 全量测试** `python -m pytest -q` → 全绿。
- [ ] **Step 4: 提交** `git commit -m "chore: remove dead code (describe_contexts, evaluate_output, _track_task, MAX_MARK_STATES)"`

## Task 5: 测试夹具收口 conftest.py

**Files:**
- Modify: `tests/conftest.py`（新增统一 `FakeContext` / `FakeEvent` / `make_optimizer`，取三份现存实现的超集）
- Modify: `tests/test_merge_integration.py`、`tests/test_self_reply_marker.py`、`tests/test_security_critical.py`（删本地夹具，改从 `tests.conftest` import）
- Modify: `tests/test_interruption_guard.py`（import 从 `tests.test_merge_integration` 改为 `tests.conftest`）

- [ ] **Step 1: 在 conftest.py 写入统一夹具**（超集：wake/request_id/chain/group_id/message_obj/set_extra/stop_event）。
- [ ] **Step 2: 更新四个测试文件删除本地副本**。
- [ ] **Step 3: 全量测试** `python -m pytest -q` → 全绿。
- [ ] **Step 4: 提交** `git commit -m "test: consolidate FakeEvent/make_optimizer into conftest"`

## Task 6: 命名修正

**Files:**
- Modify: `merge_window.py`（`_owner_media`→`_event_media`、`clear_owner`→`clear_state`、`user_key`→`window_key`、`_event_will_call_llm`→`_is_wake_event`）
- Modify: `main.py` 与全部测试中的调用点同步更新

- [ ] **Step 1: 全局替换 + 全量测试**（无行为变化，测试即验证）。
- [ ] **Step 2: 提交** `git commit -m "refactor: clarify window state naming"`

## Task 7: 收尾（版本 + 文档 + 验证）

**Files:**
- Modify: `metadata.yaml`（3.0.11 → 3.0.12）、`README.md`（badge + 更新日志 v3.0.12）

- [ ] **Step 1: 更新版本与 README 变更日志。**
- [ ] **Step 2: 全量测试 + 编译检查** `python -m pytest -q`、`python -m compileall -q *.py tests/*.py`。
- [ ] **Step 3: 提交** `git commit -m "chore: bump version to v3.0.12 and update changelog"`

## Self-Review 记录

- 方案 A 的 5 项要求 → Task 1/2（访问层）、Task 3（拆 main.py）、Task 4（死代码）、Task 5（夹具）、Task 6（命名）全覆盖。
- 无 TBD/占位符；Task 1 接口签名在后续任务中保持一致（`get_message_chain`/`entry_text` 等）。
