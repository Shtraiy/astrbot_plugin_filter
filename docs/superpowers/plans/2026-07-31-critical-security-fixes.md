# Critical Security Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the three confirmed Critical output-security paths fail closed, resistant to component/zero-width bypasses, and bounded against message-fanout denial of service.

**Architecture:** Keep the existing output pipeline and add narrow boundary helpers. Coalesce adjacent visible text before scanning, replace the whole pending result on exceptions, and route paragraph splitting through one bounded preparation function before fuzzy deduplication.

**Tech Stack:** Python 3, asyncio, unittest/pytest-compatible tests, AstrBot message component API.

## Global Constraints

- Modify only Critical-scope behavior.
- Do not add runtime dependencies.
- Preserve the existing maximum layout size of 100,000 characters.
- Emit at most five total message parts and at most four follow-up sends.
- Follow red-green-refactor for every production change.

---

### Task 1: Sensitive-output boundary

**Files:**
- Modify: `pipelines.py`
- Modify: `main.py`
- Test: `tests/test_pipelines.py`
- Test: `tests/test_security_critical.py`

**Interfaces:**
- Consumes: outgoing `result.chain`, `Plain`, `SAFE_REPLY`, configured guard terms.
- Produces: `_coalesce_adjacent_plain_components(chain)`, `_replace_chain_with_safe_reply(chain)`.

- [x] **Step 1: Add failing tests**

Add tests proving that `filter_sensitive("sk-proj-\u200b1234567890abcdef")` redacts the key, adjacent `Plain("敏感")` and `Plain("词")` trigger the configured `敏感词` guard, and a forced pipeline exception leaves only `SAFE_REPLY`.

- [x] **Step 2: Run the focused tests and confirm expected failures**

Run:

```powershell
python -m unittest tests.test_security_critical -v
```

Also execute the zero-width test through the local pure-test runner because `tests/test_pipelines.py` imports pytest.

- [x] **Step 3: Implement the minimum fix**

Strip zero-width characters at the beginning of `filter_sensitive`, coalesce adjacent `Plain` components before the processing loop, and replace the result chain with one safe component in the outer exception handler.

- [x] **Step 4: Re-run focused tests**

Expected: all new sensitive-output tests pass.

### Task 2: Bounded multi-message output

**Files:**
- Modify: `segmentation.py`
- Modify: `main.py`
- Test: `tests/test_security_critical.py`

**Interfaces:**
- Produces: `prepare_multi_message_parts(text: str) -> list[str]`.
- Limits: 100,000 input characters, five total parts, four follow-up sends.

- [x] **Step 1: Add failing tests**

Add a test with 1,001 unique paragraphs and assert that preparation returns at most five parts without calling fuzzy deduplication on more than five candidates. Add a direct `send_followups` test asserting no more than four sends.

- [x] **Step 2: Run the tests and confirm expected failures**

Run:

```powershell
python -m unittest tests.test_security_critical -v
```

- [x] **Step 3: Implement the minimum fix**

Bound source length, merge paragraph overflow into the fifth part before deduplication, call the helper from `main.py`, and slice direct follow-up input to four items.

- [x] **Step 4: Re-run focused tests**

Expected: all bounded-output tests pass without a measurable long-running deduplication step.

### Task 3: Regression verification

**Files:**
- Review: all modified production and test files.

- [x] **Step 1: Run focused security tests**

```powershell
python -m unittest tests.test_security_critical -v
```

- [x] **Step 2: Run available pure-module tests**

Execute the existing local pure-test runner and record all pre-existing failures separately from regressions.

- [x] **Step 3: Run syntax and schema checks**

```powershell
python -m compileall -q .
python -m unittest tests.test_config_schema -v
git diff --check
```

- [x] **Step 4: Review the final diff**

Confirm every production change maps to one Critical finding and no Warning-scope behavior was altered.
