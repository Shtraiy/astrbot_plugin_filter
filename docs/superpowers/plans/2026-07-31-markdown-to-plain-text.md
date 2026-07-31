# Markdown-to-Plain-Text Output Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert common Markdown in outgoing AstrBot replies into readable QQ plain text without an LLM call or a new dependency.

**Architecture:** Add one deterministic `strip_markdown(text: str) -> str` function to the existing text-cleanup module. Protect code contents with temporary tokens, unwrap inline Markdown, normalize line-level Markdown, then restore code verbatim. Invoke it after optional style/segmentation processing and before the final sensitive-information filter and content guard.

**Tech Stack:** Python 3.10+, standard-library `re`, pytest, existing AstrBot test stubs.

## Global Constraints

- Markdown cleanup must be deterministic and must never call an LLM.
- Add no third-party dependency.
- Preserve readable content while removing recognized presentation syntax.
- Preserve ordinary punctuation, `2 * 3`, `snake_case`, and unmatched markers.
- Keep existing optional LLM configuration and behavior unchanged.

---

### Task 1: Inline Markdown and code preservation

**Files:**
- Modify: `pipelines.py:20-40`
- Modify: `tests/test_pipelines.py:1-15`
- Test: `tests/test_pipelines.py`

**Interfaces:**
- Consumes: an arbitrary outgoing `str`.
- Produces: `strip_markdown(text: str) -> str`, a synchronous pure transformation used by Task 3.

- [ ] **Step 1: Add failing tests for inline syntax**

Add `strip_markdown` to the import list in `tests/test_pipelines.py`, then add:

```python
class TestStripMarkdown:
    def test_unwraps_screenshot_style_bold_text(self):
        text = "7月底到8月初正在打 **BLAST Bounty Summer 2026**（BLAST 赏金赛夏季赛）。"

        assert strip_markdown(text) == (
            "7月底到8月初正在打 BLAST Bounty Summer 2026（BLAST 赏金赛夏季赛）。"
        )

    def test_unwraps_common_inline_emphasis(self):
        assert strip_markdown("**粗体**、*斜体*、__重点__、~~旧内容~~") == (
            "粗体、斜体、重点、旧内容"
        )

    def test_keeps_link_labels_and_image_alt_text(self):
        assert strip_markdown("看[赛程](https://example.com)和![海报](poster.png)") == (
            "看赛程和海报"
        )

    def test_removes_code_delimiters_but_preserves_code_contents(self):
        text = "运行 `value = **raw**`：\n```python\n# title\nprint('*')\n```"

        assert strip_markdown(text) == "运行 value = **raw**：\n# title\nprint('*')"
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
python -m pytest tests/test_pipelines.py::TestStripMarkdown -q
```

Expected: collection fails because `strip_markdown` is not defined/exported.

- [ ] **Step 3: Implement the minimal inline transformer**

Add compiled expressions and private code-stashing helpers near `clean_garbage` in `pipelines.py`. The implementation must follow this shape:

```python
_FENCED_CODE_RE = re.compile(
    r"^[ \t]*(?P<fence>`{3,}|~{3,})[^\n]*\n"
    r"(?P<code>.*?)\n?^[ \t]*(?P=fence)[ \t]*$",
    re.MULTILINE | re.DOTALL,
)
_INLINE_CODE_RE = re.compile(r"(?<!`)(?P<ticks>`+)(?!`)(?P<code>[^\n]*?)(?P=ticks)(?!`)")
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^\n)]*\)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\([^\n)]*\)")
_STRONG_STAR_RE = re.compile(r"(?<!\\)\*\*(?=\S)(.+?)(?<=\S)\*\*")
_STRONG_UNDERSCORE_RE = re.compile(
    r"(?<![\w\\])__(?=\S)(.+?)(?<=\S)__(?!\w)"
)
_STRIKE_RE = re.compile(r"(?<!\\)~~(?=\S)(.+?)(?<=\S)~~")
_EM_STAR_RE = re.compile(r"(?<![\*\\])\*(?![\s*])(.+?)(?<![\s*])\*(?!\*)")
_EM_UNDERSCORE_RE = re.compile(
    r"(?<![\w\\])_(?![\s_])(.+?)(?<![\s_])_(?!\w)"
)


def _stash_code(text: str) -> tuple[str, dict[str, str]]:
    protected: dict[str, str] = {}

    def stash(value: str) -> str:
        token = f"\ue000MD_CODE_{len(protected)}\ue001"
        protected[token] = value
        return token

    text = _FENCED_CODE_RE.sub(lambda match: stash(match.group("code")), text)
    text = _INLINE_CODE_RE.sub(lambda match: stash(match.group("code")), text)
    return text, protected


def strip_markdown(text: str) -> str:
    if not text:
        return text
    text, protected = _stash_code(text)
    text = _IMAGE_RE.sub(r"\1", text)
    text = _LINK_RE.sub(r"\1", text)
    for pattern in (
        _STRONG_STAR_RE,
        _STRONG_UNDERSCORE_RE,
        _STRIKE_RE,
        _EM_STAR_RE,
        _EM_UNDERSCORE_RE,
    ):
        text = pattern.sub(r"\1", text)
    for token, value in protected.items():
        text = text.replace(token, value)
    return text
```

If the focused code test exposes a fence-regex edge case, adjust only the fence expression or stash callback; do not add a parser dependency.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run:

```bash
python -m pytest tests/test_pipelines.py::TestStripMarkdown -q
```

Expected: all four tests pass.

- [ ] **Step 5: Commit Task 1**

```bash
git add pipelines.py tests/test_pipelines.py
git commit -m "feat: strip inline markdown from replies"
```

---

### Task 2: Line-level Markdown and false-positive protection

**Files:**
- Modify: `pipelines.py`
- Modify: `tests/test_pipelines.py`

**Interfaces:**
- Consumes: `strip_markdown(text: str) -> str` from Task 1.
- Produces: the complete rule transformer for headings, quotes, rules, task items, and unordered lists.

- [ ] **Step 1: Add failing tests for line syntax and ordinary text**

Append to `TestStripMarkdown`:

```python
    def test_normalizes_common_line_level_markdown(self):
        text = (
            "## 比赛信息\n"
            "> 今晚开赛\n"
            "---\n"
            "- 第一场\n"
            "* 第二场\n"
            "+ [x] 已确认"
        )

        assert strip_markdown(text) == (
            "比赛信息\n今晚开赛\n• 第一场\n• 第二场\n• 已确认"
        )

    def test_preserves_ordered_lists(self):
        assert strip_markdown("1. 第一项\n2) 第二项") == "1. 第一项\n2) 第二项"

    def test_preserves_non_markdown_asterisks_and_underscores(self):
        text = "2 * 3 = 6，变量 snake_case，未闭合的 *星号"

        assert strip_markdown(text) == text

    def test_unescapes_markdown_punctuation_after_processing(self):
        assert strip_markdown(r"\*不是斜体\* 和 \#普通井号") == "*不是斜体* 和 #普通井号"

    def test_empty_text_is_unchanged(self):
        assert strip_markdown("") == ""
```

- [ ] **Step 2: Run the new focused tests and verify RED**

Run:

```bash
python -m pytest tests/test_pipelines.py::TestStripMarkdown -q
```

Expected: the line-level normalization test fails because markers remain.

- [ ] **Step 3: Add conservative line transformations**

Add these expressions in `pipelines.py`:

```python
_HORIZONTAL_RULE_RE = re.compile(
    r"^[ \t]{0,3}(?:(?:\*[ \t]*){3,}|(?:-[ \t]*){3,}|(?:_[ \t]*){3,})$"
)
_HEADING_RE = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+")
_BLOCKQUOTE_RE = re.compile(r"^[ \t]{0,3}(?:>[ \t]?)+")
_TASK_LIST_RE = re.compile(r"^(?P<indent>[ \t]*)[-+*][ \t]+\[[ xX]\][ \t]+")
_UNORDERED_LIST_RE = re.compile(r"^(?P<indent>[ \t]*)[-+*][ \t]+")
_MARKDOWN_ESCAPE_RE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!>|~])")
```

Before restoring protected code in `strip_markdown`, process lines:

```python
    lines: list[str] = []
    for line in text.splitlines():
        if _HORIZONTAL_RULE_RE.fullmatch(line):
            continue
        line = _HEADING_RE.sub("", line)
        line = _BLOCKQUOTE_RE.sub("", line)
        line = _TASK_LIST_RE.sub(lambda match: f'{match.group("indent")}• ', line)
        line = _UNORDERED_LIST_RE.sub(lambda match: f'{match.group("indent")}• ', line)
        lines.append(line)
    text = "\n".join(lines)
    text = _MARKDOWN_ESCAPE_RE.sub(r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
```

Keep code restoration after these steps so Markdown-looking code is never modified.

- [ ] **Step 4: Run the full pipeline unit-test file**

Run:

```bash
python -m pytest tests/test_pipelines.py -q
```

Expected: all tests in the file pass with no warnings.

- [ ] **Step 5: Commit Task 2**

```bash
git add pipelines.py tests/test_pipelines.py
git commit -m "feat: normalize markdown block syntax"
```

---

### Task 3: Outgoing pipeline integration and documentation

**Files:**
- Modify: `main.py:18-31`
- Modify: `main.py:150-165`
- Modify: `tests/test_single_message.py`
- Modify: `README.md:5-34`

**Interfaces:**
- Consumes: `strip_markdown(text: str) -> str` from Tasks 1-2.
- Produces: every outgoing `Plain` component passes through Markdown cleanup after `apply_segmentation_and_style` and before final safety checks.

- [ ] **Step 1: Add a failing outgoing-pipeline integration test**

Update `tests/test_single_message.py` to import the module and add:

```python
import main as filter_main


def test_markdown_from_post_processing_is_removed(monkeypatch):
    optimizer = object.__new__(LanguageLogicOptimizer)
    optimizer.config = {
        "enable_content_guard": False,
        "enable_de_ai_flavor": False,
        "enable_image_render": False,
        "multi_message": False,
    }
    optimizer.context = FakeContext()
    optimizer._reply_locks = {}
    optimizer._gates = {}
    optimizer._pending_sends = {}
    optimizer._pending_send = None
    optimizer._onboarding_states = {}
    optimizer._pending_tasks = set()
    result = SimpleNamespace(chain=[Plain("original")])

    async def add_markdown(*_args, **_kwargs):
        return "赛程：**BLAST Bounty Summer 2026**"

    monkeypatch.setattr(filter_main, "apply_segmentation_and_style", add_markdown)

    asyncio.run(optimizer.on_decorating_result(FakeEvent(result)))

    assert result.chain[0].text == "赛程：BLAST Bounty Summer 2026"
```

- [ ] **Step 2: Run the integration test and verify RED**

Run:

```bash
python -m pytest tests/test_single_message.py::test_markdown_from_post_processing_is_removed -q
```

Expected: FAIL because the outgoing text still contains `**`.

- [ ] **Step 3: Wire the rule into the final output path**

Import `strip_markdown` from `.pipelines` in `main.py`. Immediately after the awaited `apply_segmentation_and_style` call, add:

```python
                text, _ = _apply_pipeline("清理 Markdown", strip_markdown, text, pipeline_stats)
```

Keep the existing final `filter_sensitive` and `evaluate_output` calls after this line.

- [ ] **Step 4: Update user-facing documentation**

In `README.md`:

- add a feature bullet stating that common Markdown is converted to QQ-friendly plain text;
- insert “Markdown 纯文本化” in the documented pipeline after LLM/rule formatting and before serial sending;
- do not add a configuration option because cleanup is required for the target QQ transport.

- [ ] **Step 5: Run focused and full verification**

Run:

```bash
python -m pytest tests/test_single_message.py tests/test_pipelines.py -q
python -m py_compile main.py content_guard.py pipelines.py segmentation.py image_renderer.py
python -m pytest -q
```

Expected: every command exits 0, the complete suite has zero failures, and no new warnings are emitted.

- [ ] **Step 6: Inspect the final diff**

Run:

```bash
git diff --check
git diff -- pipelines.py main.py tests/test_pipelines.py tests/test_single_message.py README.md
```

Confirm the implementation contains no dependency or configuration changes and that Markdown cleanup occurs after post-processing.

- [ ] **Step 7: Commit Task 3**

```bash
git add main.py pipelines.py tests/test_pipelines.py tests/test_single_message.py README.md
git commit -m "feat: clean markdown before QQ delivery"
```
