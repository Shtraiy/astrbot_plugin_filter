# Critical Security Fixes Design

## Scope

Fix only the three Critical findings confirmed on 2026-07-30:

1. Outgoing filtering fails open when a pipeline step raises.
2. Secret and block-term scanning can be bypassed with zero-width characters or adjacent `Plain` component boundaries.
3. Unbounded paragraph deduplication and follow-up scheduling can block the event loop and flood a conversation.

The two Warning findings about secondary-LLM semantic validation and English prompt-injection detection are explicitly out of scope.

## Design

### Safe output transaction

`on_decorating_result` continues to prepare transformed text before mutating the original chain. If any processing or direct-send operation raises after the result is available, the complete outgoing chain is replaced with one `Plain(SAFE_REPLY)` component. This makes the security hook fail closed instead of returning the untouched model output.

### Full visible-text boundary

Adjacent `Plain` components are coalesced before filtering. Their text is concatenated into the first component and subsequent adjacent components are cleared, preserving the user-visible text while preventing a token or configured term from being split across component boundaries. `filter_sensitive` removes zero-width formatting characters before secret-pattern matching so recoverable obfuscated credentials are redacted.

### Bounded multi-message preparation

A new `prepare_multi_message_parts` function owns splitting, size bounding, paragraph-count bounding, and deduplication. It truncates the processed source to 100,000 characters, reduces it to at most five message parts before fuzzy deduplication, and preserves overflow inside the final allowed part. `send_followups` independently caps follow-up sends at four as defense in depth.

## Error Handling

- A processing exception after `event.get_result()` yields only `SAFE_REPLY`.
- A failure while sending a rendered image also replaces the still-pending result chain with `SAFE_REPLY`.
- Follow-up send failures remain isolated per message, but the number of attempted follow-ups is bounded.

## Tests

- A zero-width-obfuscated API key is redacted.
- Adjacent `Plain` components containing one configured block term are blocked as a single visible message.
- A forced pipeline exception replaces the original chain with `SAFE_REPLY`.
- More than five source paragraphs are bounded before fuzzy deduplication.
- Direct calls to `send_followups` cannot send more than four messages.

## Success Criteria

- Each regression test fails on the current implementation and passes after the fix.
- Existing tests that can run in the local environment remain green.
- Python compilation and JSON schema parsing succeed.
- The git diff contains no Warning-scope changes or unrelated refactors.
