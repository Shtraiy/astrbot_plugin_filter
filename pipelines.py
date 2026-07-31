
"""Text cleanup pipelines for outgoing AstrBot messages."""
from __future__ import annotations
import logging
import re
logger = logging.getLogger(__name__)

U_USER = "\u7528\u6237"
TOOL_ALIAS = {
    "es_search": "\u68c0\u7d22", "rg_search": "\u6587\u4ef6\u68c0\u7d22", "web_search": "\u641c\u7d22",
    "google_search": "\u641c\u7d22", "mikan_search": "\u756a\u5267\u6e90", "bangumi_search": "\u756a\u5267\u4fe1\u606f",
    "anime_search": "\u756a\u5267\u4fe1\u606f", "read_file": "\u8bfb\u53d6\u6587\u4ef6", "write_file": "\u5199\u5165\u6587\u4ef6",
    "add_subscription": "\u6dfb\u52a0\u8ba2\u9605", "delete_subscription": "\u5220\u9664\u8ba2\u9605",
    "list_subscription": "\u8ba2\u9605\u5217\u8868", "shell_exec": "\u7ec8\u7aef", "powershell": "\u7ec8\u7aef",
    "saucenao": "\u641c\u56fe", "trace_moe": "\u756a\u5267\u8bc6\u522b", "rag_search": "\u77e5\u8bc6\u5e93\u68c0\u7d22",
    "mysql": "\u6570\u636e\u5e93", "psql": "\u6570\u636e\u5e93", "sqlite": "\u6570\u636e\u5e93", "mongo": "\u6570\u636e\u5e93",
}
EXTRA_PATTERNS = [r"api_\w+", r"llm_\w+", r"db_\w+", r"shell\b", r"\bbash\b", r"\bcurl\b", r"\bwget\b", r"\bgit\b", r"\bnpm\b", r"\bpip\b"]
_TOOL_FUNCTION_NAMES = re.compile("|".join([re.escape(k) for k in TOOL_ALIAS] + EXTRA_PATTERNS), re.IGNORECASE)
_TOOL_ALIASES = [(re.compile(re.escape(k), re.IGNORECASE), v) for k, v in TOOL_ALIAS.items()] + [
    (re.compile(r"api_\w+", re.IGNORECASE), "\u63a5\u53e3"),
    (re.compile(r"llm_\w+", re.IGNORECASE), "AI \u5206\u6790"),
    (re.compile(r"db_\w+", re.IGNORECASE), "\u6570\u636e\u5e93"),
]
_TOOL_KEYWORDS = set(TOOL_ALIAS) | {"api_", "llm_", "db_"}
_GARBAGE_RE = re.compile(r"\[{text=|,\s*type\s*=\s*\\?text\s*\}|\]\s*\}|\[{|}]")


def clean_garbage(text: str) -> str:
    original = text
    text = _GARBAGE_RE.sub("", text)
    # Only strip stray artifact brackets when an artifact was actually removed;
    # otherwise a normal message ending in "]" or "}" must be preserved.
    text = text.strip(" \n,[]{}") if text != original else text.strip()
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


_FENCED_CODE_RE = re.compile(
    r"^[ \t]*(?P<fence>`{3,}|~{3,})[^\n]*\n"
    r"(?P<code>.*?)\n?^[ \t]*(?P=fence)[ \t]*$",
    re.MULTILINE | re.DOTALL,
)
_INLINE_CODE_RE = re.compile(
    r"(?<!`)(?P<ticks>`+)(?!`)(?P<code>[^\n]*?)(?P=ticks)(?!`)"
)
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
_TASK_LIST_RE = re.compile(
    r"^(?P<indent>[ \t]*)[-+*][ \t]+\[[ xX]\][ \t]+"
)
_UNORDERED_LIST_RE = re.compile(r"^(?P<indent>[ \t]*)[-+*][ \t]+")
_MARKDOWN_ESCAPE_RE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!>|~])")
_MARKDOWN_CHARS_RE = re.compile(r"[\\`*_#+\-\[\]>|~]")


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
    """Remove common Markdown presentation syntax while preserving content."""
    if not text:
        return text

    # Fast path: no Markdown syntax characters at all, only normalize spacing.
    if not _MARKDOWN_CHARS_RE.search(text):
        return re.sub(r"\n{3,}", "\n\n", text).strip()

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

    lines: list[str] = []
    for line in text.splitlines():
        if _HORIZONTAL_RULE_RE.fullmatch(line):
            continue
        line = _HEADING_RE.sub("", line)
        line = _BLOCKQUOTE_RE.sub("", line)
        line = _TASK_LIST_RE.sub(
            lambda match: f'{match.group("indent")}• ',
            line,
        )
        line = _UNORDERED_LIST_RE.sub(
            lambda match: f'{match.group("indent")}• ',
            line,
        )
        lines.append(line)
    text = "\n".join(lines)
    text = _MARKDOWN_ESCAPE_RE.sub(r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    for token, value in protected.items():
        text = text.replace(token, value)
    return text


_AT_MENTION_RE = re.compile(r"^@([^\s，,。！？!?；;：:、]+)")


def replace_user(text: str) -> str:
    m = _AT_MENTION_RE.search(text)
    if not m:
        return text
    name = m.group(1).strip()
    patterns = [
        U_USER + "\u521a\u521a\u53d1\u9001\u4e86\u65b0\u6307\u4ee4[\"\u201c\u300c\u300e]?",
        U_USER + "\u521a\u521a\u53d1\u9001\u4e86\u65b0\u6d88\u606f[\"\u201c\u300c\u300e]?",
        U_USER + "\u521a\u521a\u53d1\u9001\u4e86[\"\u201c\u300c\u300e]?",
        U_USER + "\u521a\u521a\u8bf4[\"\u201c\u300c\u300e]?",
    ]
    for pat in patterns:
        text = re.sub(pat, lambda _m: name + "\u521a\u521a\u8bf4", text)
    text = re.sub(U_USER + "\u7684\u8981\u6c42\u662f[\"\u201c\u300c\u300e]?", lambda _m: name + "\u60f3\u8981", text)
    text = re.sub(U_USER + r"(?:\u7684)?\u6307\u4ee4[\"\u201c\u300c\u300e]?", lambda _m: name + "\u7684\u6307\u4ee4", text)
    text = re.sub(U_USER + "\u8bf4[\"\u201c\u300c\u300e]?", lambda _m: name + "\u8bf4", text)
    return re.sub(U_USER, lambda _m: name, text)

_SYSTEM_PATH_RE = re.compile(r"(?:/AstrBot|/etc/|/var/|/root/|/tmp/|/opt/|/usr/|/proc/|/sys/|/dev/|/mnt/|/NAS/|/data/|[A-Za-z]:[\\/])[^\s\u3002\uff0c\uff01\uff1f\n]*", re.IGNORECASE)
_SHELL_CMD_RE = re.compile(r"(?:^|[\s\u3002\uff01\uff1f])(?:shell_exec|bash\s+-c|sh\s+-c|cmd\.exe|powershell|sudo\s+|chmod\s+|chown\s+|pip\s+install|npm\s+install|python\d?\s+[A-Za-z0-9_./\\-]+|node\s+[A-Za-z0-9_./\\-]+|rm\s+-rf|git\s+(?:clone|push|pull)|wget\s+|curl\s+)[^\n\u3002\uff01\uff1f]{0,120}", re.IGNORECASE)
_INTERNAL_IP_RE = re.compile(r"\b(?:127\.0\.0\.\d+|192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+|172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+|localhost|0\.0\.0\.0)\b(?::\d+)?", re.IGNORECASE)
_URL_RE = re.compile(r"\b(?:https?://)?(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}(?::\d+)?(?:/[^\s\u3002\uff0c\uff01\uff1f\"'\uff09)]*)?", re.IGNORECASE)
_SYSTEM_INFO_LINE_RE = re.compile(r"(?:\u8fdb\u7a0b\u5217\u8868|\u8fd0\u884c\u8fdb\u7a0b|\u540e\u53f0\u8fdb\u7a0b|\u6570\u636e\u5e93\u8fde\u63a5|\u914d\u7f6e\u6587\u4ef6|\u73af\u5883\u53d8\u91cf|API.?key|access.?token|password|secret|\.env\b|\.config\b|\.conf\b|\.ini\b)\S*", re.IGNORECASE)
_FILE_EXT_RE = re.compile(
    r"\.(?:py|pyw|pyc|js|ts|jsx|tsx|json|txt|md|markdown|yml|yaml|toml|ini|conf|cfg|log|csv|tsv|xml|html?|css|png|jpe?g|gif|webp|svg|ico|bmp|pdf|docx?|xlsx?|pptx?|zip|tar|gz|7z|rar|exe|msi|dll|so|dylib|sh|bat|ps1|sql|db|sqlite|lock|env|example)$",
    re.IGNORECASE,
)
_PRIVATE_KEY_BLOCK_RE = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.IGNORECASE | re.DOTALL,
)
_BEARER_TOKEN_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}", re.IGNORECASE)
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_ASSIGNED_SECRET_RE = re.compile(
    r"(?P<prefix>(?:\b(?:api[_ -]?key|access[_ -]?token|authorization|bearer|password|secret|private[_ -]?key|token)\b|\u5bc6\u7801)\s*[:：=]\s*)"
    r"[\"']?[^\s,;\"']{8,}",
    re.IGNORECASE,
)
_PREFIXED_SECRET_RE = re.compile(
    r"\b(?:sk|pk|rk|ghp|github_pat|xox[baprs])[-_][A-Za-z0-9_-]{8,}\b|\bAKIA[0-9A-Z]{16}\b",
    re.IGNORECASE,
)
_SENSITIVE_ZERO_WIDTH_RE = re.compile(r"[\u200b-\u200f\u2060\ufeff]")


def filter_sensitive(text: str) -> str:
    text = _SENSITIVE_ZERO_WIDTH_RE.sub("", text)
    text = _PRIVATE_KEY_BLOCK_RE.sub("[REDACTED]", text)
    text = _BEARER_TOKEN_RE.sub("Bearer [REDACTED]", text)
    text = _JWT_RE.sub("[REDACTED]", text)
    text = _ASSIGNED_SECRET_RE.sub(
        lambda match: f"{match.group('prefix')}[REDACTED]",
        text,
    )
    text = _PREFIXED_SECRET_RE.sub("[REDACTED]", text)
    text = _URL_RE.sub(_redact_url, text)
    text = _SYSTEM_PATH_RE.sub("", text)
    text = _SHELL_CMD_RE.sub("", text)
    text = _INTERNAL_IP_RE.sub("", text)
    text = _SYSTEM_INFO_LINE_RE.sub("", text)
    return re.sub(r"[ \t]{2,}", " ", text)


def _redact_url(match: re.Match) -> str:
    """Replace URLs with [link] but keep bare filenames with common extensions."""
    candidate = match.group(0)
    if "://" not in candidate and "/" not in candidate and _FILE_EXT_RE.search(candidate):
        return candidate
    return "[link]"


_NARRATION_MARKERS = re.compile("\u6211\u5148|\u8ba9\u6211|\u6211\u6765|\u6211\u9700\u8981|\u6211\u4eec\u9700\u8981|\u8c03\u7528|\u6267\u884c|\u8fd0\u884c|\u5de5\u5177|\u63a5\u53e3|\u547d\u4ee4|\u540e\u53f0|\u8fdb\u7a0b|\u914d\u7f6e|\u6570\u636e\u5e93")


TOOL_LEAK_REPLACEMENT = "\u6211\u6b63\u5728\u5904\u7406\u8fd9\u4e2a\u8bf7\u6c42\uff0c\u8bf7\u7a0d\u7b49\u3002"

# These patterns target leaked orchestration text rather than ordinary mentions
# of tools in an answer. A hit requires at least two independent signals so a
# legitimate explanation such as "web_search \u662f\u4e00\u4e2a\u641c\u7d22\u5de5\u5177" is preserved.
_TOOL_PROTOCOL_RE = re.compile(
    r"(?:calling\s+tool|call\s+the\s+tool|use\s+`?\w+|"
    r"follow\s+the\s+tool\s+schema|after\s+execution|"
    r"tool\s+(?:call|schema|execution)|\u8c03\u7528\u5de5\u5177|\u5de5\u5177\u8c03\u7528|\u5de5\u5177\u534f\u8bae)",
    re.IGNORECASE,
)
_EXPLICIT_TOOL_PROTOCOL_RE = re.compile(
    r"(?:calling\s+tool|call\s+the\s+tool|follow\s+the\s+tool\s+schema|"
    r"after\s+execution|tool\s+schema\s+exactly|\u8c03\u7528\u5de5\u5177\u524d|\u5de5\u5177\u8c03\u7528\u540e)",
    re.IGNORECASE,
)
_TOOL_NAME_RE = re.compile(
    r"(?:astrbot_execute_shell|angel_recall|"
    r"(?:es|rg|web|google|mikan|bangumi|anime)_search|"
    r"(?:read|write)_file|(?:shell_exec|powershell|rag_search))",
    re.IGNORECASE,
)
_COMMAND_FRAGMENT_RE = re.compile(
    r"(?:`\s*(?:find|ls|rg|grep|dir|Get-ChildItem|python|powershell|bash|cmd)\b[^`]*`|"
    r"\b(?:ls\s+-[alR]+|find\s+[^\n]{1,100}|rg\s+[^\n]{1,100})\b)",
    re.IGNORECASE,
)
_PROCESS_NARRATION_RE = re.compile(
    r"(?:\u6211(?:\u5148|\u6765|\u8fd9\u5c31|\u9700\u8981|\u4eec\u5148)|\u8ba9\u6211|\u7a0d\u7b49|\u7b49\u6211).{0,40}"
    r"(?:\u540e\u53f0|\u6587\u4ef6\u5939|\u76ee\u5f55|\u6587\u4ef6|\u7d22\u5f15|\u547d\u4ee4|\u5de5\u5177|\u641c\u7d22|\u67e5\u770b|\u68c0\u67e5|\u8bfb\u53d6|\u6267\u884c)",
    re.IGNORECASE,
)
_INTERNAL_INSTRUCTION_RE = re.compile(
    r"(?:the\s+instruction\s+says|tool\s+schema\s+exactly|"
    r"final\s+(?:text\s+)?response|plain\s+text|markdown\s+syntax|"
    r"\u8c03\u7528\u5de5\u5177\u524d|\u5de5\u5177\u8c03\u7528\u540e|\u4e0d\u8981\u8fd4\u56de\u7a7a\u56de\u590d)",
    re.IGNORECASE,
)


def is_tool_call_leak(text: str) -> bool:
    """Return whether text is an exposed tool orchestration draft."""
    if not text or len(text.strip()) < 12:
        return False

    protocol = bool(_TOOL_PROTOCOL_RE.search(text))
    tool_name = bool(_TOOL_NAME_RE.search(text))
    command = bool(_COMMAND_FRAGMENT_RE.search(text))
    narration = bool(_PROCESS_NARRATION_RE.search(text))
    internal_instruction = bool(_INTERNAL_INSTRUCTION_RE.search(text))

    # Protocol/meta text is strong evidence only when paired with a concrete
    # tool or command. A Chinese process narration also needs a concrete
    # execution signal; this avoids replacing normal status messages.
    if _EXPLICIT_TOOL_PROTOCOL_RE.search(text):
        return True
    if protocol and (tool_name or command or internal_instruction):
        return True
    if tool_name and narration:
        return True
    if command and narration:
        return True
    return internal_instruction and tool_name


def replace_tool_leakage(text: str) -> str:
    """Replace a leaked tool workflow with a safe, user-facing status."""
    return TOOL_LEAK_REPLACEMENT if is_tool_call_leak(text) else text


def remove_tool_narration(text: str) -> str:
    out = []
    for para in text.split("\n\n"):
        kept = []
        for sent in re.split(r"(?<=[\u3002\uff01\uff1f])\s*", para):
            s = sent.strip()
            if s and not (_TOOL_FUNCTION_NAMES.search(s) and _NARRATION_MARKERS.search(s)):
                kept.append(s)
        if kept:
            out.append("".join(kept))
    return "\n\n".join(out)


def deidentify_tool_names(text: str) -> str:
    if not any(k.lower() in text.lower() for k in _TOOL_KEYWORDS):
        return text
    for pat, repl in _TOOL_ALIASES:
        text = pat.sub(repl, text)
    return text

_AI_FILLER_PATTERNS = [
    re.compile("^\u6211\u8fd9\u5c31\u628a.{0,35}(?:\u6574\u7406|\u68b3\u7406|\u5217\u51fa|\u603b\u7ed3|\u5f52\u7eb3|\u5206\u4eab|\u544a\u8bc9|\u4ecb\u7ecd|\u8bf4\u660e|\u89e3\u91ca).{0,10}?[\uff1a:\u3002\uff01]?$"),
    re.compile("^\u4ee5\u4e0a\u5c31\u662f.{0,20}[\u3002\uff01]?$"),
    re.compile("^\u603b\u7ed3\u4e00\u4e0b[\uff1a:,\uff0c\u3002]?$"),
]
_AI_FILLER_PREFIXES = [
    re.compile(r"^\u6211\u8fd9\u5c31\u628a.{0,35}(?:\u6574\u7406|\u68b3\u7406|\u5217\u51fa|\u603b\u7ed3|\u5f52\u7eb3|\u5206\u4eab|\u544a\u8bc9|\u4ecb\u7ecd|\u8bf4\u660e|\u89e3\u91ca).{0,10}?[\uff1a:]\s*"),
    re.compile(r"^\u4ee5\u4e0b\u662f.{0,10}[\uff1a:]\s*"),
]
_HEADING_LIKE_RE = re.compile(r"^(?:以下|以上)[^。！？\n]{1,20}[:：]$")
_ACADEMIC_TRANSITION_RE = re.compile(
    r"(^|[\u3002\uff01\uff1f]\s*)"
    r"(?:\u503c\u5f97\u6ce8\u610f\u7684\u662f|\u9700\u8981\u63d0\u9192\u7684\u662f|"
    r"\u9700\u8981\u8bf4\u660e\u7684\u662f|\u6b64\u5916)"
    r"[\uff1a:,\uff0c]?\s*",
    re.MULTILINE,
)
_BRACKET_NOTE_RE = re.compile(
    r"[\uff08(]\s*(?:\u6ce8|\u5907\u6ce8|\u8bf4\u660e)\s*[\uff1a:]\s*"
    r"([^\uff08\uff09()\n]{1,80})[\uff09)]"
)
_STEP_PREFIX_RE = re.compile(r"\u7b2c?([\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341\d]+)\u6b65\u662f\s*")
_NUMS = {"\u4e00": "1", "\u4e8c": "2", "\u4e09": "3", "\u56db": "4", "\u4e94": "5", "\u516d": "6", "\u4e03": "7", "\u516b": "8", "\u4e5d": "9", "\u5341": "10"}


def de_ai_flavor(text: str) -> str:
    paras = []
    for para in text.split("\n\n"):
        para = "\n".join(
            line
            for line in para.splitlines()
            if not any(pattern.match(line.strip()) for pattern in _AI_FILLER_PATTERNS)
        )
        kept = []
        for sent in re.split(r"(?<=[\u3002\uff01\uff1f])\s*", para):
            s = sent.strip()
            if not s:
                continue
            if _HEADING_LIKE_RE.match(s):
                kept.append(s)
                continue
            stripped = s
            for pat in _AI_FILLER_PREFIXES:
                m = pat.match(stripped)
                if m:
                    stripped = stripped[m.end():]
                    break
            if not stripped or any(p.match(stripped) for p in _AI_FILLER_PATTERNS):
                continue
            kept.append(stripped)
        para = "".join(kept)
        para = _ACADEMIC_TRANSITION_RE.sub(lambda match: match.group(1), para)
        para = _BRACKET_NOTE_RE.sub(r"\1", para)
        para = _STEP_PREFIX_RE.sub(lambda m: _NUMS.get(m.group(1), m.group(1)) + ". ", para)
        para = re.sub(r"^\s*\u9996\u5148[\uff1a:,\uff0c]\s*", "", para)
        para = re.sub(r"([\u3002\uff01\uff1f]\s*)(?:\u5176\u6b21|\u6700\u540e)[\uff1a:,\uff0c]\s*", r"\1", para)
        if para:
            paras.append(para)
    text = "\n\n".join(paras) if paras else text
    text = re.sub(r"\n{3,}", "\n\n", text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()
