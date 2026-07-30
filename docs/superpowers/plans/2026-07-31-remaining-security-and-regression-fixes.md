# Remaining Security and Regression Fixes Plan

1. Add failing tests for unrelated LLM rewrites, protected-token loss, English
   prompt injection, confusable configured terms, and unrelated gate callbacks.
2. Tighten LLM result validation and prompt data boundaries until those tests pass.
3. Extend content normalization and English guard patterns until those tests pass.
4. Correlate gate releases by stable request identity and update equivalent-event
   coverage to carry the same identifier.
5. Add a local pytest bootstrap and development requirements.
6. Repair the four confirmed production regressions and the two invalid tests.
7. Run focused tests, the complete pytest suite, unittest compatibility checks,
   compile checks, and review the final diff.

