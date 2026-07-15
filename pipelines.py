"""
文本处理管线 ①-⑥：元数据清洗、昵称替换、敏感信息过滤、
工具叙述删除、工具名脱敏、去AI味。

所有函数均为纯文本变换，不依赖 AstrBot 运行时上下文。
"""

from __future__ import annotations

import re
import logging

logger = logging.getLogger(__name__)


# ============================================================
#  工具名共享数据：单一数据源，同时生成检测正则和替换列表
# ============================================================

# 工具名 → 自然语言别名（同时用于步骤④的检测和步骤⑤的替换）
_TOOL_NAME_TO_ALIAS: dict[str, str] = {
    # —— 搜索 ——
    'es_search': '检索',
    'rg_search': '文件检索',
    'web_search': '搜索',
    'google_search': '搜索',
    'WebFetch': '网页',
    'mikan_search': '番剧源',
    'bangumi_search': '番剧信息',
    'anime_search': '番剧信息',
    'garden_search': '资源站',
    'elasticsearch': '检索',
    # —— 订阅 / RSS ——
    'ani-rss': '订阅系统',
    'ANI-RSS': '订阅系统',
    'add_subscription': '添加订阅',
    'delete_subscription': '删除订阅',
    'list_subscription': '订阅列表',
    'list_subscriptions': '订阅列表',
    'sub_list': '订阅列表',
    'unsubscribe': '取消订阅',
    'rss_fetch': '抓取订阅',
    'fetch_feed': '获取更新',
    'rss_parse': '解析订阅',
    # —— 文件 ——
    'read_file': '读取文件',
    'write_file': '写入文件',
    'delete_file': '删除文件',
    'edit_file': '编辑文件',
    # —— Shell / 系统（仅完整工具名，不含常见英文词） ——
    'shell_exec': '终端',
    'powershell': '终端',
    'systemctl': '系统服务',
    'kubectl': '容器编排',
    'chmod': '权限',
    'chown': '权限',
    'netstat': '网络',
    'ifconfig': '网络',
    # —— API ——
    'http_get': '查询',
    'http_post': '提交',
    # —— 图片 ——
    'saucenao': '搜图',
    'ascii2d': '搜图',
    'trace_moe': '番剧识别',
    'screenshot': '截图',
    'ocr': '文字识别',
    # —— AI / LLM ——
    'embedding': '语义匹配',
    'rag_search': '知识库检索',
    # —— 数据库 ——
    'redis': '缓存',
    'mysql': '数据库',
    'psql': '数据库',
    'sqlite': '数据库',
    'mongo': '数据库',
    # —— 通用残留 ——
    'json_parse': '数据解析',
}

# 步骤④专用的额外工具名（需要检测但不需要替换为自然语言）
# —— 这些是更宽泛的模式，用于检测叙述句中的工具痕迹 ——
_EXTRA_DETECTION_PATTERNS = [
    r'api_\w+',
    r'llm_\w+',
    r'db_\w+',
    r'sub_add',
    r'sub_remove',
    r'websocket',
    # Shell 命令类（仅在步骤④检测用，步骤⑤不替换，避免误伤正常英文）
    r'shell\b',
    r'\bbash\b',
    r'\bexec\b',
    r'\bcmd\b',
    r'\bcurl\b',
    r'\bwget\b',
    r'\bdocker\b',
    r'\bsudo\b',
    r'\bssh\b',
    r'\bscp\b',
    r'\bps\b',
    r'\bkill\b',
    r'\bpip\b',
    r'\bnpm\b',
    r'\bgit\b',
    r'\bmake\b',
    r'\bgcc\b',
    r'\bg\+\+',
    r'\brm\b',
    r'\bcp\b',
    r'\bmv\b',
    r'\bmkdir\b',
    r'\bcat\b',
    r'\btail\b',
    r'\bless\b',
    r'\bmore\b',
    r'\bhead\b',
    r'\bping\b',
]


def _build_detection_regex() -> re.Pattern:
    """从共享数据 + 额外模式生成步骤④的工具名检测正则"""
    parts = [re.escape(name) for name in _TOOL_NAME_TO_ALIAS]
    parts.extend(_EXTRA_DETECTION_PATTERNS)
    return re.compile('|'.join(parts), re.IGNORECASE)


def _build_alias_list() -> list[tuple[re.Pattern, str]]:
    """从共享数据生成步骤⑤的 (pattern, replacement) 列表"""
    aliases: list[tuple[re.Pattern, str]] = []
    for name, alias in _TOOL_NAME_TO_ALIAS.items():
        pattern = re.compile(re.escape(name), re.IGNORECASE)
        aliases.append((pattern, alias))
    # 通配模式
    aliases.append((re.compile(r'api_\w+', re.IGNORECASE), '接口'))
    aliases.append((re.compile(r'llm_\w+', re.IGNORECASE), 'AI分析'))
    aliases.append((re.compile(r'db_\w+', re.IGNORECASE), '数据库'))
    return aliases


_TOOL_FUNCTION_NAMES = _build_detection_regex()
_TOOL_ALIASES = _build_alias_list()

# 快速预检：工具名关键词集合，用于 _deidentify_tool_names 提前判断是否需要遍历
_TOOL_KEYWORDS = set(_TOOL_NAME_TO_ALIAS.keys()) | {
    'api_', 'llm_', 'db_', 'sub_add', 'sub_remove',
}


# ============================================================
#  ① 垃圾符号清洗
# ============================================================

_GARBAGE_RE = re.compile(
    r'\[{text='
    r'|,\s*type\s*=\s*\\?text\s*\}'
    r'|\]\s*\}'
    r'|\[{'
    r'|}]'
)

_STRIP_CHARS = ' \n,[]{}'


def clean_garbage(text: str) -> str:
    """移除 OneBot / MCP 工具调用泄漏的元数据符号"""
    cleaned = _GARBAGE_RE.sub('', text)
    cleaned = cleaned.strip(_STRIP_CHARS)
    # 只压缩水平空白（空格/Tab），保留换行结构，否则后续分段管线无结构可依
    cleaned = re.sub(r'[ \t]+', ' ', cleaned)
    # 清理行首行尾空格，规范化连续空行（≥3个 → 2个）
    cleaned = re.sub(r'[ \t]*\n[ \t]*', '\n', cleaned)
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    return cleaned.strip()


# ============================================================
#  ② "用户" → 群昵称
# ============================================================

# 从回复开头的 @ 中提取群昵称（支持带空格的 QQ 昵称）
# @ 后到换行符 / 连续两个空格 / 行尾 为止
_AT_MENTION_RE = re.compile(r'^@(.+?)(?:\n|\s{2,}|$)')


def _escape_repl(s: str) -> str:
    """转义字符串以便安全用作 re.sub 的 replacement 参数"""
    return s.replace('\\', '\\\\')


def replace_user(text: str) -> str:
    """将 '用户' 替换为从 @ 中提取的实际昵称"""
    m = _AT_MENTION_RE.search(text)
    if not m:
        return text
    name_raw = m.group(1).strip()
    name_safe = _escape_repl(name_raw)
    # 替换各种 "用户" 的变体（按从具体到泛化的顺序）
    text = re.sub(r'用户刚刚发送了新指令[""』」]?', f'{name_safe}刚刚说"', text)
    text = re.sub(r'用户刚刚发送了新消息[""』」]?', f'{name_safe}刚刚说"', text)
    text = re.sub(r'用户刚刚发送了[""』」]?', f'{name_safe}刚刚说"', text)
    text = re.sub(r'用户刚刚说[""』」]?', f'{name_safe}刚刚说"', text)
    text = re.sub(r'用户的要求是[""』」]?', f'{name_safe}想要', text)
    text = re.sub(r'用户(?:的)?指令[""』」]?', f'{name_safe}的指令', text)
    text = re.sub(r'用户说[""』」]?', f'{name_safe}说"', text)
    # 最后的泛化替换用 lambda 避免 replacement 中的反斜杠被误解析
    text = re.sub(r'用户', lambda _: name_raw, text)
    return text


# ============================================================
#  ③ 过滤系统敏感信息
# ============================================================

# 系统级 & 本地路径 (系统目录 / 媒体库 / NAS 挂载点 / Windows盘符等)
_SYSTEM_PATH_RE = re.compile(
    r'(?:/AstrBot|/etc/|/var/|/root/|/tmp/|/opt/|/usr/|/proc/|/sys/|'
    r'/dev/|/boot/|/run/|/srv/|/Media/|/media/|/mnt/|/NAS/|/nas/|'
    r'/volume\d*|/downloads/|/share/|/storage/|/data/|'
    r'[A-Za-z]:[\\\\/])[^\s，,。！？\n]*',
    re.IGNORECASE,
)

# Shell 命令行片段 (可执行命令 + 参数)
_SHELL_CMD_RE = re.compile(
    r'(?:^|[。！？\s])'
    r'(?:shell_exec|bash\s+-c|sh\s+-c|cmd\.exe|powershell|'
    r'ps\s+(?:aux|ef)|kill\s+-9|systemctl|docker\s+|kubectl\s+|'
    r'ssh\s+|scp\s+|sudo\s+|chmod\s+|chown\s+|'
    r'pip\s+install|npm\s+install|apt\s+get|yum\s+|brew\s+|'
    r'python\s+-m|python\d?\s+|node\s+|'
    r'rm\s+-rf|rmdir|del\s+/[fq]|'
    r'git\s+clone|git\s+push|git\s+pull|'
    r'mysql\s+|psql\s+|sqlite\d*\s+|mongod?\s+|'
    r'netstat|ifconfig|ip\s+addr|ping\s+|traceroute|'
    r'cat\s+|tail\s+-f|less\s+|more\s+|head\s+|'
    r'grep\s+|find\s+|locate\s+|which\s+|whereis\s+|'
    r'wget\s+|curl\s+)'
    r'[^\n。！？]{0,80}',
    re.IGNORECASE,
)

# 内部 IP / localhost
_INTERNAL_IP_RE = re.compile(
    r'\b(?:127\.0\.0\.\d+|192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+|'
    r'172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+|localhost|0\.0\.0\.0)\b'
    r'(?::\d+)?',
    re.IGNORECASE,
)

# 进程列表 / 数据库 等系统信息行
_SYSTEM_INFO_LINE_RE = re.compile(
    r'(?:进程列表|运行进程|后台进程|数据库连接|配置文件|'
    r'环境变量|API.?key|access.?token|密码|password|secret|'
    r'\.env\b|\.config\b|\.conf\b|\.ini\b)\S*',
    re.IGNORECASE,
)


def filter_sensitive(text: str) -> str:
    """过滤系统路径、命令行、内部 IP 等敏感信息"""
    text = _SYSTEM_PATH_RE.sub('', text)
    text = _SHELL_CMD_RE.sub('', text)
    text = _INTERNAL_IP_RE.sub('', text)
    text = _SYSTEM_INFO_LINE_RE.sub('', text)
    # 清理路径删除后的残余碎片（仅匹配水平空白，保留换行结构）
    text = re.sub(r'里[ \t]+的[ \t]*(?:文件夹|目录|路径)?', '', text)
    text = re.sub(r'[下中][ \t]+的[ \t]*(?:文件夹|目录|路径)?', '', text)
    text = re.sub(r'[ \t]{2,}', ' ', text)
    return text


# ============================================================
#  ④ 删除工具调用过程叙述句
# ============================================================

# 过程叙述关键词
_NARRATION_MARKERS = re.compile(
    # 自我对话 / 思考
    r'我先|让我|我来|我需要|我们需要|我们最好|我们先|我们先来|我最好|'
    r'我等下|等一下|我看看|我看下|我查下|我搜下|我找下|'
    r'看看有没有|检查.?下|确认.?下|'
    # 工具操作描述
    r'(?:用|使用|通过|调用|执行|运行)\s*.{0,8}(?:工具|API|接口|命令|函数|脚本|搜索|查询|查找|检索)|'
    r'执行.*操作|调用.*(?:工具|API|接口|命令)|'
    r'或者.*搜|或者.*查|或者.*找|或者.*运行|'
    # 内部计划 / 路线规划
    r'先处理|先删除|先清理|先查|先找|'
    r'接下来需要|下一步|'
    r'找寻.*(?:脚本|数据库|程序|配置)|'
    r'查找.*(?:后台|程序|数据库|命令|配置)|'
    r'运行进程|进程列表|后台.*程序|'
    # 自我规划
    r'我需要知道|我需要了解|我得知道|我得先|我需要先|'
    r'我们可以用|我们可以通过|我们能够|'
    r'找.?一下|搜.?一下|查.?一下|'
    r'通常.*(?:目录|服务|数据库)',
)


def remove_tool_narration(text: str) -> str:
    """
    删除同时满足两个条件的句子：
    (a) 包含工具函数名  AND  (b) 包含过程叙述标记
    逐段处理，保留原文的 \\n\\n 段落结构。
    """
    paragraphs = text.split('\n\n')
    processed = []
    for para in paragraphs:
        if not para.strip():
            continue
        sentences = re.split(r'(?<=[。！？\n])\s*', para)
        kept = []
        for sent in sentences:
            stripped = sent.strip()
            if not stripped:
                continue
            if _TOOL_FUNCTION_NAMES.search(stripped) and _NARRATION_MARKERS.search(stripped):
                continue
            kept.append(stripped)
        processed.append(''.join(kept) if kept else para)

    return '\n\n'.join(processed) if processed else text


# ============================================================
#  ⑤ 残存工具名脱敏 → 自然语言
# ============================================================


def _has_tool_keyword(text: str) -> bool:
    """快速预检：文本中是否可能包含工具名关键词"""
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in _TOOL_KEYWORDS)


def deidentify_tool_names(text: str) -> str:
    """将残存的工具函数名替换为自然语言描述"""
    # 快速预检：无工具名关键词则跳过全部正则
    if not _has_tool_keyword(text):
        return text

    result = text
    for pattern, replacement in _TOOL_ALIASES:
        result = pattern.sub(replacement, result)
    return result


# ============================================================
#  ⑥ 去AI味 — 正则清除高频AI公式化表达
# ============================================================

# —— 合并后的 AI 填充模式（同一个模式可能同时用于整句删除和前缀剥离）——

# 高置信度AI填充句——可整句安全删除（纯结构性胶水，不含信息）
_AI_FILLER_PATTERNS = [
    re.compile(r'^我这就把.{0,20}(?:整理|梳理|列出|总结|归纳).{0,10}[：:]?$'),
    re.compile(r'^我来[给帮为].{0,15}(?:梳理|整理|介绍|说明|解释).{0,10}[：:]?$'),
    re.compile(r'^接下来[我让].{0,15}(?:介绍|说明|解释|展开|讲讲).{0,10}[：:]?$'),
    re.compile(r'^以下是.{0,10}[：:]?$'),
    re.compile(r'^下面[我让].{0,10}(?:说说|讲讲|介绍)[：:]?$'),
    re.compile(r'^让我[们]?.{0,10}(?:看看|聊聊|说说|展开)[：:]?$'),
    re.compile(r'^以上就是.{0,20}[。！]?$'),
    re.compile(r'^总结一下[：:,，]?$'),
    re.compile(r'^总的[来说而言]{1,2}[：:,，]?$'),
]

# AI填充前缀——紧接内容时剥离前缀、保留正文
# 与 _AI_FILLER_PATTERNS 共享模式核心，加上 [：:]\s* 后缀
_AI_FILLER_PREFIXES = [
    (re.compile(r'^我这就把.{0,20}(?:整理|梳理|列出|总结|归纳).{0,10}[：:]\s*'), ''),
    (re.compile(r'^我来[给帮为].{0,15}(?:梳理|整理|介绍|说明|解释).{0,10}[：:]\s*'), ''),
    (re.compile(r'^接下来[我让].{0,15}(?:介绍|说明|解释|展开|讲讲).{0,10}[：:]\s*'), ''),
    (re.compile(r'^以下是.{0,10}[：:]\s*'), ''),
    (re.compile(r'^下面[我让].{0,10}(?:说说|讲讲|介绍)[：:]\s*'), ''),
    (re.compile(r'^让我[们]?.{0,10}(?:看看|聊聊|说说|展开)[：:]\s*'), ''),
]

# 论文式衔接词（句首匹配，移除后不影响语义）
_ACADEMIC_TRANSITION_RE = re.compile(
    r'(?:^|[。！？]\s*)(?:值得注意的是|需要提醒的是|需要说明的是|需要注?意的?是)[：:,，]?\s*'
)
_ALSO_TRANSITION_RE = re.compile(
    r'(?:^|[。！？]\s*)(?:此外|另外|顺便[一]?提|补充[一]?点)[：:,，]?\s*'
)

# "第X步是" 前缀
_STEP_PREFIX_RE = re.compile(r'第([一二三四五六七八九十\d]+)步是\s*')


def _is_ai_filler(sentence: str) -> bool:
    """判断句子是否为纯AI填充句（可安全删除，不影响信息完整性）"""
    stripped = sentence.strip()
    for pat in _AI_FILLER_PATTERNS:
        if pat.match(stripped):
            return True
    return False


def _strip_ai_prefix(sentence: str) -> str:
    """剥离句首AI填充前缀，保留后续正文"""
    for pat, _ in _AI_FILLER_PREFIXES:
        m = pat.match(sentence)
        if m:
            return sentence[m.end():]
    return sentence


def de_ai_flavor(text: str) -> str:
    """
    ⑥ 去AI味 —— 正则清除高频AI公式化表达。
    逐段处理，保留原文的 \\n\\n 段落结构。
    三层策略：
      第一层：逐句处理（整句删除纯填充句 / 剥离前缀保留正文）
      第二层：模式替换（去括号、去论文衔接词、去"第X步是"前缀）
      第三层：清理多余空白
    """
    # 先按段落拆分，处理完后再拼回去，保护分段结构
    paragraphs = text.split('\n\n')
    processed_paras = []
    total_removed = 0

    for para in paragraphs:
        if not para.strip():
            continue

        # === 第一层：逐句处理 ===
        sentences = re.split(r'(?<=[。！？\n])\s*', para)
        kept = []
        for sent in sentences:
            stripped = sent.strip()
            if not stripped:
                continue
            # 1a. 纯填充句 → 整句删除
            if _is_ai_filler(stripped):
                total_removed += 1
                continue
            # 1b. 填充前缀 → 剥离前缀，保留正文
            stripped = _strip_ai_prefix(stripped)
            kept.append(stripped)

        para = ''.join(kept) if kept else para

        # === 第二层：模式替换 ===

        # 2a. "（注/提示/注意/ps：...）" → 去括号保留内容
        para = re.sub(
            r'([。！？\n])\s*[（(]\s*(?:注|提示|注意|ps|p\.s\.)[：:]\s*([^）)]*)[）)]',
            r'\1\2', para, flags=re.IGNORECASE,
        )
        para = re.sub(
            r'([^。！？\n\s])[（(]\s*(?:注|提示|注意|ps|p\.s\.)[：:]\s*([^）)]*)[）)]',
            r'\1，\2', para, flags=re.IGNORECASE,
        )

        # 2b. 普通内容括号 → 去括号融入句子（仅处理 6 字以上信息备注，保留短标签）
        para = re.sub(
            r'([。！？\n])\s*[（(]([^（）()]{6,80})[）)]',
            r'\1\2', para,
        )
        para = re.sub(
            r'([^。！？\n\s])[（(]([^（）()]{6,80})[）)]',
            r'\1，\2', para,
        )

        # 2c. 论文式衔接词
        para = _ACADEMIC_TRANSITION_RE.sub('', para)
        para = _ALSO_TRANSITION_RE.sub('', para)

        # 2d. "第X步是" → "X. "
        para = _STEP_PREFIX_RE.sub(r'\1. ', para)

        # 2e. 段落级"首先，"/"其次，"/"最后，" → 移除
        para = re.sub(r'^\s*首先[，,]\s*', '', para)
        para = re.sub(r'([。！？]\s*)(?:其次|最后)[，,]\s*', r'\1', para)

        processed_paras.append(para)

    if total_removed > 0:
        logger.info("[去AI味] 移除了 %d 句AI填充句", total_removed)

    text = '\n\n'.join(processed_paras) if processed_paras else text

    # === 第三层：清理多余空白 ===
    text = re.sub(r'([。！？])\1+', r'\1', text)      # 连续标点去重
    text = re.sub(r'\n{3,}', '\n\n', text)             # 多余空行折叠
    text = re.sub(r'[ \t]{2,}', ' ', text)             # 多余水平空格（不触碰换行）

    return text.strip()
