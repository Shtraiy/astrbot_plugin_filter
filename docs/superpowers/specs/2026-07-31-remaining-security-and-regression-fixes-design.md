# Remaining Security and Regression Fixes

## Scope

This change addresses the remaining findings approved after the critical fixes:

1. Reject LLM layout/style output that changes the source meaning or protected facts.
2. Detect common English and Unicode-confusable content-guard bypasses.
3. Prevent unrelated same-origin callbacks from releasing another request's gate.
4. Make the local pytest environment reproducible.
5. Repair the known text-cleanup and segmentation regressions.

## Design

### LLM output boundary

Segmentation output is formatting-only, so its non-whitespace content must exactly
match the input. Style output may remove filler, but it must remain substantially
similar and preserve protected tokens such as numbers, URLs, code fragments, and
ASCII identifiers. Failed validation falls back to deterministic local processing.

### Content guard normalization

Normalize Unicode with NFKC, remove zero-width characters, and translate common
Greek/Cyrillic lookalikes before matching. Extend the existing collapsed-text
patterns with English risky-target, bypass, and obfuscation vocabulary.

### Reply gate ownership

A gate may be released only by the exact owner event or by an event carrying the
same stable correlation identifier (`request_id`, `event_id`, `message_id`, or
`trace_id`). Sharing a group/source key is not sufficient.

### Tests and regressions

Provide a pytest bootstrap that supplies minimal AstrBot stubs when AstrBot is not
installed and imports the plugin as a package. Move pytest to a development
requirements file. Fix the known filler-line, academic-transition, bracket-note,
dense-entry, long-clause, and incorrect conversation-assertion regressions.

