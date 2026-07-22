
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
    text = _GARBAGE_RE.sub("", text).strip(" \n,[]{}")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


_AT_MENTION_RE = re.compile(r"^@(.+?)(?:\n|\s{2,}|$)")


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
_SHELL_CMD_RE = re.compile(r"(?:^|[\s\u3002\uff01\uff1f])(?:shell_exec|bash\s+-c|sh\s+-c|cmd\.exe|powershell|sudo\s+|chmod\s+|chown\s+|pip\s+install|npm\s+install|python\d?\s+|node\s+|rm\s+-rf|git\s+(?:clone|push|pull)|wget\s+|curl\s+)[^\n\u3002\uff01\uff1f]{0,120}", re.IGNORECASE)
_INTERNAL_IP_RE = re.compile(r"\b(?:127\.0\.0\.\d+|192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+|172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+|localhost|0\.0\.0\.0)\b(?::\d+)?", re.IGNORECASE)
_URL_RE = re.compile(r"\b(?:https?://)?(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}(?::\d+)?(?:/[^\s\u3002\uff0c\uff01\uff1f\"'\uff09)]*)?", re.IGNORECASE)
_SYSTEM_INFO_LINE_RE = re.compile(r"(?:\u8fdb\u7a0b\u5217\u8868|\u8fd0\u884c\u8fdb\u7a0b|\u540e\u53f0\u8fdb\u7a0b|\u6570\u636e\u5e93\u8fde\u63a5|\u914d\u7f6e\u6587\u4ef6|\u73af\u5883\u53d8\u91cf|API.?key|access.?token|\u5bc6\u7801|password|secret|\.env\b|\.config\b|\.conf\b|\.ini\b)\S*", re.IGNORECASE)


def filter_sensitive(text: str) -> str:
    text = _URL_RE.sub("[link]", text)
    text = _SYSTEM_PATH_RE.sub("", text)
    text = _SHELL_CMD_RE.sub("", text)
    text = _INTERNAL_IP_RE.sub("", text)
    text = _SYSTEM_INFO_LINE_RE.sub("", text)
    return re.sub(r"[ \t]{2,}", " ", text)

_NARRATION_MARKERS = re.compile("\u6211\u5148|\u8ba9\u6211|\u6211\u6765|\u6211\u9700\u8981|\u6211\u4eec\u9700\u8981|\u68c0\u67e5|\u786e\u8ba4|\u8c03\u7528|\u6267\u884c|\u8fd0\u884c|\u4f7f\u7528|\u901a\u8fc7|\u5de5\u5177|\u63a5\u53e3|\u547d\u4ee4|\u641c\u7d22|\u67e5\u8be2|\u68c0\u7d22|\u540e\u53f0|\u8fdb\u7a0b|\u914d\u7f6e|\u6570\u636e\u5e93")


def remove_tool_narration(text: str) -> str:
    out = []
    for para in text.split("\n\n"):
        kept = []
        for sent in re.split("(?<=[\u3002\uff01\uff1f])\s*", para):
            s = sent.strip()
            if s and not (_TOOL_FUNCTION_NAMES.search(s) and _NARRATION_MARKERS.search(s)):
                kept.append(s)
        if kept:
            out.append("".join(kept))
    return "\n\n".join(out) if out else text


def deidentify_tool_names(text: str) -> str:
    if not any(k.lower() in text.lower() for k in _TOOL_KEYWORDS):
        return text
    for pat, repl in _TOOL_ALIASES:
        text = pat.sub(repl, text)
    return text

_AI_FILLER_PATTERNS = [
    re.compile("^\u6211\u8fd9\u5c31\u628a.{0,35}(?:\u6574\u7406|\u68b3\u7406|\u5217\u51fa|\u603b\u7ed3|\u5f52\u7eb3|\u5206\u4eab|\u544a\u8bc9|\u4ecb\u7ecd|\u8bf4\u660e|\u89e3\u91ca).{0,10}?[\uff1a:\u3002\uff01]?$"),
    re.compile("^\u4ee5\u4e0b\u662f.{0,10}[\uff1a:]?$"),
    re.compile("^\u4ee5\u4e0a\u5c31\u662f.{0,20}[\u3002\uff01]?$"),
    re.compile("^\u603b\u7ed3\u4e00\u4e0b[\uff1a:,\uff0c\u3002]?$"),
]
_AI_FILLER_PREFIXES = [
    re.compile("^\u6211\u8fd9\u5c31\u628a.{0,35}(?:\u6574\u7406|\u68b3\u7406|\u5217\u51fa|\u603b\u7ed3|\u5f52\u7eb3|\u5206\u4eab|\u544a\u8bc9|\u4ecb\u7ecd|\u8bf4\u660e|\u89e3\u91ca).{0,10}?[\uff1a:]\s*"),
    re.compile("^\u4ee5\u4e0b\u662f.{0,10}[\uff1a:]\s*"),
]
_ACADEMIC_TRANSITION_RE = re.compile("(?:^|[\u3002\uff01\uff1f]\s*)(?:\u503c\u5f97\u6ce8\u610f\u7684\u662f|\u9700\u8981\u63d0\u9192\u7684\u662f|\u9700\u8981\u8bf4\u660e\u7684\u662f)[\uff1a:,\uff0c]?\s*")
_STEP_PREFIX_RE = re.compile("\u7b2c?([\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341\d]+)\u6b65\u662f\s*")
_NUMS = {"\u4e00": "1", "\u4e8c": "2", "\u4e09": "3", "\u56db": "4", "\u4e94": "5", "\u516d": "6", "\u4e03": "7", "\u516b": "8", "\u4e5d": "9", "\u5341": "10"}


def de_ai_flavor(text: str) -> str:
    paras = []
    for para in text.split("\n\n"):
        kept = []
        for sent in re.split("(?<=[\u3002\uff01\uff1f])\s*", para):
            s = sent.strip()
            if not s:
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
        para = _ACADEMIC_TRANSITION_RE.sub("", para)
        para = _STEP_PREFIX_RE.sub(lambda m: _NUMS.get(m.group(1), m.group(1)) + ". ", para)
        para = re.sub("^\s*\u9996\u5148[\uff1a:,\uff0c]\s*", "", para)
        para = re.sub("([\u3002\uff01\uff1f]\s*)(?:\u5176\u6b21|\u6700\u540e)[\uff1a:,\uff0c]\s*", r"\1", para)
        if para:
            paras.append(para)
    text = "\n\n".join(paras) if paras else text
    text = re.sub(r"\n{3,}", "\n\n", text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()
