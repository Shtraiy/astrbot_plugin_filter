"""Strip common Markdown presentation syntax from text."""

from __future__ import annotations

import re

_FENCED_CODE_RE = re.compile(
    r"^[ \t]*(?P<fence>`{3,}|~{3,})[^\n]*\n"
    r"(?P<code>.*?)\n?^[ \t]*(?P=fence)[ \t]*$",
    re.MULTILINE | re.DOTALL,
)
_INLINE_CODE_RE = re.compile(
    r"(?<!`)(?P<ticks>`+)(?!`)(?P<code>[^\n]*?)(?P=ticks)(?!`)"
)
_ESCAPED_BACKTICK_RE = re.compile(r"\\`")
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^\n)]*\)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\([^\n)]*\)")
_STRONG_STAR_RE = re.compile(r"(?<!\\)\*\*(?=\S)(.+?)(?<=\S)\*\*")
_STRONG_UNDERSCORE_RE = re.compile(
    r"(?<![\w\\])__(?=\S)(.+?)(?<=\S)__(?!\w)"
)
_STRIKE_RE = re.compile(r"(?<!\\)~~(?=\S)(.+?)(?<=\S)~~")
_EM_STAR_RE = re.compile(
    r"(?<![\*\\])\*(?![\s*])(.+?)(?<![\s*])\*(?!\*)"
)
_EM_UNDERSCORE_RE = re.compile(
    r"(?<![\w\\])_(?![\s_])(.+?)(?<![\s_])_(?!\w)"
)
_HORIZONTAL_RULE_RE = re.compile(
    r"^[ \t]{0,3}(?:(?:\*[ \t]*){3,}|(?:-[ \t]*){3,}|(?:_[ \t]*){3,})$"
)
_HEADING_RE = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+")
_BLOCKQUOTE_RE = re.compile(r"^[ \t]{0,3}(?:>[ \t]?)+")
_BULLET_QUOTE_RE = re.compile(r"^(?P<indent>[ \t]*•[ \t]*)(?:>[ \t]?)+")
_BULLET_HEADING_RE = re.compile(r"^(?P<indent>[ \t]*•[ \t]*)#{1,6}[ \t]+")
_TASK_LIST_RE = re.compile(
    r"^(?P<indent>[ \t]*)[-+*][ \t]+\[[ xX]\][ \t]+"
)
_UNORDERED_LIST_RE = re.compile(r"^(?P<indent>[ \t]*)[-+*][ \t]+")
_MARKDOWN_ESCAPE_RE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!>|~])")
_MARKDOWN_CHARS_RE = re.compile(r"[\\`*_#+\-\[\]>|~=]")
_SETEXT_LINE_RE = re.compile(r"^[ \t]*={2,}[ \t]*$")
_TRAILING_HASHES_RE = re.compile(r"[ \t]+#+[ \t]*$")


def _new_stasher(prefix: str, suffix: str):
    """Create a token stasher that protects content from later regex passes."""
    protected: dict[str, str] = {}
    counter = 0

    def stash(value: str) -> str:
        nonlocal counter
        token = f"{prefix}{counter}{suffix}"
        counter += 1
        protected[token] = value
        return token

    return stash, protected


def _strip_quote_and_heading(line: str) -> str:
    """Strip blockquote and heading markers, in any combination or order."""
    previous = None
    while line != previous:
        previous = line
        line = _BLOCKQUOTE_RE.sub("", line)
        line = _BULLET_QUOTE_RE.sub(lambda match: match.group("indent"), line)
        stripped = _HEADING_RE.sub("", line)
        if stripped == line:
            stripped = _BULLET_HEADING_RE.sub(
                lambda match: match.group("indent"),
                line,
            )
        if stripped != line:
            line = _TRAILING_HASHES_RE.sub("", stripped)
    return line


def _strip_leading_markers(line: str) -> str:
    """Strip line-level Markdown markers in any nesting combination."""
    previous = None
    while line != previous:
        previous = line
        line = _TASK_LIST_RE.sub(
            lambda match: f'{match.group("indent")}• ',
            line,
        )
        line = _UNORDERED_LIST_RE.sub(
            lambda match: f'{match.group("indent")}• ',
            line,
        )
        line = _strip_quote_and_heading(line)
    return line


def strip_markdown(text: str) -> str:
    """Remove common Markdown presentation syntax while preserving content."""
    if not text:
        return text

    # Fast path: no Markdown syntax characters at all, only normalize spacing.
    if not _MARKDOWN_CHARS_RE.search(text):
        return re.sub(r"\n{3,}", "\n\n", text).strip()

    # Fenced code first: backslash escapes inside code blocks are literal.
    stash, protected = _new_stasher("\ue000MD_CODE_", "\ue001")
    text = _FENCED_CODE_RE.sub(lambda match: stash(match.group("code")), text)

    # Escaped backticks outside code are literal backticks; tokenize them so
    # they cannot be misread as inline-code delimiters (e.g. `\`code\``).
    bt_stash, escaped_backticks = _new_stasher("\ue002MD_BT_", "\ue003")
    text = _ESCAPED_BACKTICK_RE.sub(lambda _match: bt_stash("`"), text)

    # Inline code spans (any `\`` inside them was already tokenized above).
    text = _INLINE_CODE_RE.sub(lambda match: stash(match.group("code")), text)

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

    lines: list[str] = []
    for line in text.splitlines():
        if _HORIZONTAL_RULE_RE.fullmatch(line) or _SETEXT_LINE_RE.fullmatch(line):
            continue
        line = _strip_leading_markers(line)
        lines.append(line)
    text = "\n".join(lines)
    text = _MARKDOWN_ESCAPE_RE.sub(r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    for token, value in protected.items():
        text = text.replace(token, value)
    for token, value in escaped_backticks.items():
        text = text.replace(token, value)
    return text
