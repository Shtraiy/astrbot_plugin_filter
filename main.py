import re
from astrbot.api.star import Context, Star
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api import logger
from astrbot.api.message_components import Plain


class LanguageLogicOptimizer(Star):
    """
    语言逻辑优化大师 — 在消息发出前全面优化输出文本：
    1. 清洗 OneBot/MCP 泄漏的垃圾元数据符号
    2. "用户" → 群昵称替换
    3. 过滤系统路径 / 指令等敏感信息
    4. 删除工具调用过程叙述句
    5. 残存工具函数名 → 自然语言
    6. 长文本强制智能分段
    """

    def __init__(self, context: Context):
        super().__init__(context)

    @filter.on_decorating_result()
    async def on_decorating_result(self, event: AstrMessageEvent):
        """消息发送前的最后一步：全面优化输出文本"""
        if not event:
            return

        try:
            result = event.get_result()
            if not result:
                return

            chain = result.chain
            if not chain:
                return

            modified = False

            for comp in chain:
                if isinstance(comp, Plain):
                    original = comp.text
                    text = original

                    # ============ 六道处理管线 ============
                    text = self._clean_garbage(text)            # ①
                    text = self._replace_user(text)             # ②
                    text = self._filter_sensitive(text)         # ③
                    text = self._remove_tool_narration(text)    # ④
                    text = self._deidentify_tool_names(text)    # ⑤
                    text = self._segment_text(text)             # ⑥
                    # =====================================

                    if text != original:
                        comp.text = text
                        modified = True

            if modified:
                logger.info("[语言逻辑优化大师] 已优化输出文本。")

        except Exception as e:
            logger.error(f"[语言逻辑优化大师] 运行时出错: {e}")

    # ============================================================
    #  ① 垃圾符号清洗
    # ============================================================

    _GARBAGE_RE = re.compile(
        r'\[{text='
        r'|,\s*type\s*=\s*\\?text\s*}'
        r'|\]\s*}'
        r'|\[{'
        r'|}]'
    )

    _STRIP_CHARS = ' \n,[]{}'

    @classmethod
    def _clean_garbage(cls, text: str) -> str:
        """移除 OneBot / MCP 工具调用泄漏的元数据符号"""
        cleaned = cls._GARBAGE_RE.sub('', text)
        cleaned = cleaned.strip(cls._STRIP_CHARS)
        cleaned = re.sub(r'\s+', ' ', cleaned)
        return cleaned.strip()

    # ============================================================
    #  ② "用户" → 群昵称
    # ============================================================

    # 从回复开头的 @ 中提取群昵称（支持带空格的 QQ 昵称）
    # @ 后到换行符 / 连续两个空格 / 行尾 为止
    _AT_MENTION_RE = re.compile(r'^@(.+?)(?:\n|\s{2,}|$)')

    @classmethod
    def _replace_user(cls, text: str) -> str:
        """将 '用户' 替换为从 @ 中提取的实际昵称"""
        m = cls._AT_MENTION_RE.search(text)
        if not m:
            return text
        name = m.group(1)
        # 替换各种 "用户" 的变体（按从具体到泛化的顺序）
        text = re.sub(r'用户刚刚发送了新指令[""』」]?', f'{name}刚刚说"', text)
        text = re.sub(r'用户刚刚发送了新消息[""』」]?', f'{name}刚刚说"', text)
        text = re.sub(r'用户刚刚发送了[""』」]?', f'{name}刚刚说"', text)
        text = re.sub(r'用户刚刚说[""』」]?', f'{name}刚刚说"', text)
        text = re.sub(r'用户的要求是[""』」]?', f'{name}想要', text)
        text = re.sub(r'用户(?:的)?指令[""』」]?', f'{name}的指令', text)
        text = re.sub(r'用户说[""』」]?', f'{name}说"', text)
        text = re.sub(r'用户', name, text)
        return text

    # ============================================================
    #  ③ 过滤系统敏感信息
    # ============================================================

    # 系统级路径 (含 AstrBot 内部目录、Linux 系统目录等)
    _SYSTEM_PATH_RE = re.compile(
        r'(?:/AstrBot|/etc/|/var/|/root/|/tmp/|/opt/|/usr/|/proc/|/sys/|'
        r'/dev/|/boot/|/run/|/srv/)[^\s，,。！？\n]*',
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

    @classmethod
    def _filter_sensitive(cls, text: str) -> str:
        """过滤系统路径、命令行、内部 IP 等敏感信息"""
        text = cls._SYSTEM_PATH_RE.sub('[系统路径]', text)
        text = cls._SHELL_CMD_RE.sub('', text)
        text = cls._INTERNAL_IP_RE.sub('[内部地址]', text)
        text = cls._SYSTEM_INFO_LINE_RE.sub('', text)
        return text

    # ============================================================
    #  ④ 删除工具调用过程叙述句
    # ============================================================

    # 绝不应出现在用户可见文本中的工具函数名
    _TOOL_FUNCTION_NAMES = re.compile(
        r'es_search|rg_search|web_search|WebFetch|google_search|'
        r'mikan_search|bangumi_search|anime_search|garden_search|'
        r'elasticsearch|saucenao|ascii2d|trace_moe|'
        r'ani-rss|rss_fetch|rss_parse|fetch_feed|'
        r'add_subscription|delete_subscription|list_subscriptions?|'
        r'sub_list|sub_add|sub_remove|unsubscribe|'
        r'read_file|write_file|delete_file|edit_file|'
        r'api_\w+|http_get|http_post|websocket|'
        r'llm_\w+|embedding|rag_search|vector_search|'
        r'db_\w+|redis|cache_query|'
        r'jq\b|json_parse|'
        # ---- 新增 Shell / 系统工具 ----
        r'shell_exec|shell\b|bash\b|\bexec\b|cmd\b|powershell|'
        r'systemctl|docker\b|kubectl|sudo\b|'
        r'ps\b|kill\b|chmod|chown|'
        r'pip\b|npm\b|apt\b|yum\b|brew\b|'
        r'git\b|make\b|gcc\b|g\+\+|'
        r'rm\b|cp\b|mv\b|mkdir\b|'
        r'cat\b|tail\b|less\b|more\b|head\b|'
        r'ssh\b|scp\b|ping\b|netstat|ifconfig|'
        r'mysql|psql|sqlite|mongo',
        re.IGNORECASE,
    )

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

    @classmethod
    def _remove_tool_narration(cls, text: str) -> str:
        """
        删除同时满足两个条件的句子：
        (a) 包含工具函数名  AND  (b) 包含过程叙述标记
        """
        sentences = re.split(r'(?<=[。！？\n])\s*', text)
        kept = []

        for sent in sentences:
            stripped = sent.strip()
            if not stripped:
                continue
            if cls._TOOL_FUNCTION_NAMES.search(stripped) and cls._NARRATION_MARKERS.search(stripped):
                continue
            kept.append(stripped)

        return ' '.join(kept) if kept else text

    # ============================================================
    #  ⑤ 残存工具名脱敏 → 自然语言
    # ============================================================

    _TOOL_ALIASES = [
        # —— 搜索 ——
        (re.compile(r'es_search', re.I), '检索'),
        (re.compile(r'rg_search', re.I), '文件检索'),
        (re.compile(r'web_search', re.I), '搜索'),
        (re.compile(r'google_search', re.I), '搜索'),
        (re.compile(r'WebFetch', re.I), '网页'),
        (re.compile(r'mikan_search', re.I), '番剧源'),
        (re.compile(r'bangumi_search', re.I), '番剧信息'),
        (re.compile(r'anime_search', re.I), '番剧信息'),
        (re.compile(r'garden_search', re.I), '资源站'),
        (re.compile(r'elasticsearch', re.I), '检索'),
        # —— 订阅 / RSS ——
        (re.compile(r'ani-rss', re.I), '订阅系统'),
        (re.compile(r'ANI-RSS', re.I), '订阅系统'),
        (re.compile(r'add_subscription', re.I), '添加订阅'),
        (re.compile(r'delete_subscription', re.I), '删除订阅'),
        (re.compile(r'list_subscriptions?', re.I), '订阅列表'),
        (re.compile(r'sub_list', re.I), '订阅列表'),
        (re.compile(r'unsubscribe', re.I), '取消订阅'),
        (re.compile(r'rss_fetch', re.I), '抓取订阅'),
        (re.compile(r'fetch_feed', re.I), '获取更新'),
        (re.compile(r'rss_parse', re.I), '解析订阅'),
        # —— 文件 ——
        (re.compile(r'read_file', re.I), '读取文件'),
        (re.compile(r'write_file', re.I), '写入文件'),
        (re.compile(r'delete_file', re.I), '删除文件'),
        (re.compile(r'edit_file', re.I), '编辑文件'),
        # —— Shell / 系统 (逐个处理，避免 \b 边界漏掉组合词) ——
        (re.compile(r'shell_exec', re.I), '终端'),
        (re.compile(r'\bshell\b', re.I), '终端'),
        (re.compile(r'\bbash\b', re.I), '终端'),
        (re.compile(r'\bexec\b', re.I), '执行'),
        (re.compile(r'\bcmd\b', re.I), '终端'),
        (re.compile(r'powershell', re.I), '终端'),
        (re.compile(r'\bcurl\b', re.I), '网络请求'),
        (re.compile(r'\bwget\b', re.I), '下载'),
        (re.compile(r'systemctl', re.I), '系统服务'),
        (re.compile(r'docker\b', re.I), '容器'),
        (re.compile(r'kubectl', re.I), '容器编排'),
        (re.compile(r'\bsudo\b', re.I), '提权'),
        (re.compile(r'\bssh\b', re.I), '远程连接'),
        (re.compile(r'\bscp\b', re.I), '文件传输'),
        (re.compile(r'chmod\b', re.I), '权限'),
        (re.compile(r'chown\b', re.I), '权限'),
        (re.compile(r'\bps\b', re.I), '进程'),
        (re.compile(r'\bkill\b', re.I), '终止进程'),
        (re.compile(r'pip\b', re.I), '包管理'),
        (re.compile(r'npm\b', re.I), '包管理'),
        (re.compile(r'\bgit\b', re.I), '版本控制'),
        (re.compile(r'netstat', re.I), '网络'),
        (re.compile(r'ifconfig', re.I), '网络'),
        # —— API ——
        (re.compile(r'api_\w+', re.I), '接口'),
        (re.compile(r'http_get', re.I), '查询'),
        (re.compile(r'http_post', re.I), '提交'),
        # —— AI / LLM ——
        (re.compile(r'llm_\w+', re.I), 'AI分析'),
        (re.compile(r'embedding', re.I), '语义匹配'),
        (re.compile(r'rag_search', re.I), '知识库检索'),
        # —— 图片 ——
        (re.compile(r'saucenao', re.I), '搜图'),
        (re.compile(r'ascii2d', re.I), '搜图'),
        (re.compile(r'trace_moe', re.I), '番剧识别'),
        (re.compile(r'screenshot', re.I), '截图'),
        (re.compile(r'ocr', re.I), '文字识别'),
        # —— 数据库 ——
        (re.compile(r'redis', re.I), '缓存'),
        (re.compile(r'db_\w+', re.I), '数据库'),
        (re.compile(r'mysql', re.I), '数据库'),
        (re.compile(r'psql', re.I), '数据库'),
        (re.compile(r'sqlite', re.I), '数据库'),
        (re.compile(r'mongo', re.I), '数据库'),
        # —— 通用残留 ——
        (re.compile(r'\bjq\b', re.I), '数据解析'),
        (re.compile(r'json_parse', re.I), '数据解析'),
    ]

    @classmethod
    def _deidentify_tool_names(cls, text: str) -> str:
        """将残存的工具函数名替换为自然语言描述"""
        result = text
        for pattern, replacement in cls._TOOL_ALIASES:
            result = pattern.sub(replacement, result)
        return result

    # ============================================================
    #  ⑥ 通用分段
    # ============================================================

    _SENTENCE_SPLIT = re.compile(r'(?<=[。！？\n])\s*')
    _SEGMENT_THRESHOLD = 150    # 短于此值不处理
    _CHARS_PER_PARA = 200       # 目标每段字数

    @classmethod
    def _segment_text(cls, text: str) -> str:
        """
        通用分段：将长文本拆成 ~200 字/段的可读段落，段间空行分隔。

        规则极简：
        - 短文本（≤150 字）→ 不动
        - 已有空行分段    → 保留（LLM 已处理好）
        - 其余一切长文本  → 拆句 → 按 200 字/段重组 → 段间 \\n\\n
        """
        if len(text) <= cls._SEGMENT_THRESHOLD:
            return text

        # LLM 已经用空行分好段 → 信任，不动
        if '\n\n' in text:
            return text

        # 统一拆句：句号、感叹号、问号、换行 都算边界
        sentences = [s.strip() for s in cls._SENTENCE_SPLIT.split(text) if s.strip()]

        if len(sentences) <= 2:
            return text

        # 按每段 ~200 字动态算段数（最少 2，最多 6）
        total_chars = sum(len(s) for s in sentences)
        target_count = max(2, min(6, total_chars // cls._CHARS_PER_PARA))
        seg_count = min(target_count, len(sentences))
        seg_size = max(1, len(sentences) // seg_count)

        # 均分句子到各段
        paragraphs = []
        for i in range(seg_count):
            start = i * seg_size
            end = len(sentences) if i == seg_count - 1 else start + seg_size
            para = ' '.join(sentences[start:end]).strip()
            if para:
                paragraphs.append(para)

        return '\n\n'.join(paragraphs)
