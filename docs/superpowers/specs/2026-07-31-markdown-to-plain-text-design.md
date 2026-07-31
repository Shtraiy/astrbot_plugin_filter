# Markdown-to-Plain-Text Output Cleanup

## Goal

Convert common Markdown emitted by the model into readable plain text before an
outgoing message reaches QQ. The cleanup must be deterministic, require no LLM
call, and add no third-party dependency.

## Scope

Add a focused `strip_markdown` pipeline function that removes presentation
syntax while preserving the user-visible content:

- unwrap bold, italic, and strikethrough spans;
- remove heading, blockquote, and horizontal-rule markers;
- unwrap inline code and fenced code blocks while preserving their contents;
- replace Markdown links and images with their readable labels or alt text;
- convert unordered-list markers to the plain-text `•` character;
- preserve ordered-list numbers and line breaks;
- leave ordinary punctuation, mathematical multiplication signs, identifier
  underscores, and unmatched marker characters unchanged where possible.

Nested or malformed Markdown will be handled conservatively. The filter will
remove syntax only when a supported Markdown shape is recognizable rather than
deleting every `*`, `_`, `#`, or `>` character indiscriminately.

## Pipeline Placement

Run `strip_markdown` after `apply_segmentation_and_style` and before the final
sensitive-information filter and output content guard. This placement catches
Markdown from both the original model reply and any optional existing style or
segmentation pass. The Markdown cleanup itself never invokes an LLM.

The existing optional LLM features remain unchanged and independently
configurable; this change neither enables them nor depends on them.

## Failure Behavior

The function is a local, synchronous string transformation. Empty input returns
empty output. Unsupported or ambiguous text is retained instead of being
silently discarded. Existing pipeline exception handling remains the final
fallback, so a cleanup failure leaves the prior text available for subsequent
safety checks.

## Verification

Unit tests will cover each supported syntax family, the screenshot-style bold
example, multiline combinations, and false-positive cases such as `2 * 3`,
`snake_case`, and unmatched markers. An outgoing-pipeline integration test will
prove that Markdown introduced by the post-processing stage is stripped before
the `Plain` component is sent.
