import asyncio
import random
import re
from astrbot.api.star import Context, Star
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api import logger
from astrbot.api.message_components import Plain
from astrbot.api.all import MessageChain


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
                    # ⑥ 分段/文风优化（LLM 文风 > LLM 分段 > 规则）
                    text = await self._apply_segmentation_and_style(text)
                    # =====================================

                    if text == original:
                        continue

                    # 多消息模式：按段落逐条发送，模拟真人节奏
                    if self._get_config("multi_message", True):
                        paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
                        if len(paragraphs) > 1:
                            comp.text = paragraphs[0]
                            modified = True
                            delay_min = self._get_config("delay_min", 3.0)
                            delay_max = self._get_config("delay_max", 10.0)
                            umo = event.unified_msg_origin
                            asyncio.create_task(
                                self._send_followups(umo, paragraphs[1:], delay_min, delay_max)
                            )
                            continue

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
        name = m.group(1).strip()
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
    #  ⑥ 智能分段（LLM 优先 → 规则降级）
    # ============================================================

    _SENTENCE_SPLIT = re.compile(r'(?<=[。！？])\s*')
    _SEGMENT_THRESHOLD = 150       # 短于此值不处理
    _CHARS_PER_PARA = 300          # 目标每段字数
    _MAX_PARAS = 5                 # 最多段数

    # 列表行检测：编号 / 圆圈数字 / 符号前缀
    _LIST_LINE_RE = re.compile(
        r'^\s*(?:\d+[\.\)、]|[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳]|[-•·▪▸►●○➤✓✅])\s'
    )

    # ---- LLM 分段提示词（精炼版 ~80 tokens）----

    _SEGMENT_PROMPT = (
        "按语义将文本分段，用\\n\\n分隔后输出：\n"
        "- 开头寒暄/回应 → 首段\n"
        "- 正文按话题自然分组：独立话题块各自成段，相关内容归为一段\n"
        "- 收尾/关怀 → 末段\n"
        "通常3~5段，原文>600字可扩至6段。若含无关话题用\\n---\\n分隔。\n"
        "严禁修改任何一个字、标点、语气词、emoji——仅调整分段和换行，不改原文内容。只输出结果。\n\n"
        "原文：\n{text}"
    )

    # ---- LLM 文风优化提示词 ----

    _STYLE_PROMPT = (
        "优化以下文本的结构和可读性。\n\n"
        "【硬约束——必须严格遵守】\n"
        "1. 人设不变：语气、口头禅、emoji、称呼方式完全保留，不改动任何一个体现性格的词\n"
        "2. 内容不变：原文所有信息必须保留，不添加原文没有的事实或建议\n"
        "3. 语义不变：每句话的原意不能有任何偏差\n\n"
        "【输出结构】\n"
        "- 第1段（开场）：寒暄/回应/引入话题\n"
        "- 中间1~3段（正文）：核心内容。如有操作步骤/方法/要点，用序号分行：\n"
        "  1. 第一步内容\n"
        "  2. 第二步内容\n"
        "  同类步骤放在同一段内，不同主题拆到不同段\n"
        "- 最后1段（收尾）：总结/关怀/温馨收尾\n"
        "- 总共不超过5段，段与段之间用空行分隔\n\n"
        "【润色范围——仅在不违背硬约束时执行】\n"
        "- 过长句子适当拆短，让阅读更轻松\n"
        "- 去除重复啰嗦的表述\n"
        "- 段落内信息密度尽量均衡\n\n"
        "【输出格式】\n"
        "只输出优化后的文本。不要加任何前缀、后缀或解释。\n\n"
        "原文：\n{text}"
    )

    # ============================================================
    #  配置读取
    # ============================================================

    def _get_config(self, key: str, default=None):
        """读取插件配置（兼容多种 AstrBot 版本）"""
        if hasattr(self, 'config') and isinstance(self.config, dict):
            return self.config.get(key, default)
        if hasattr(self.context, 'config') and isinstance(self.context.config, dict):
            return self.context.config.get(key, default)
        return default

    # ============================================================
    #  LLM 语义分段
    # ============================================================

    async def _try_llm_segment(self, text: str) -> str | None:
        """
        尝试用 LLM 做语义分段。
        成功返回分段后文本，失败返回 None（触发规则降级）。
        """
        if not self._get_config("enable_llm_segment"):
            return None

        provider_id = self._get_config("llm_provider_id", "")
        if not provider_id:
            return None

        # 仅对较长文本使用 LLM，短文本无需分段
        if len(text) <= self._SEGMENT_THRESHOLD:
            return None

        try:
            logger.info(f"[LLM分段] 正在调用 LLM（provider={provider_id}）...")
            llm_resp = await self.context.llm_generate(
                chat_provider_id=provider_id,
                prompt=self._SEGMENT_PROMPT.format(text=text),
            )
            result = llm_resp.completion_text.strip()

            # 验证：结果不应为空，不应对原文改动过大
            if not result:
                logger.warning("[LLM分段] 返回空结果，降级到规则分段")
                return None
            if len(result) < len(text) * 0.3:
                logger.warning("[LLM分段] 输出过短（可能截断），降级到规则分段")
                return None

            # 验证：严禁 OOC——中文字数不得明显增减（允许 ±5% 含空格差异）
            orig_han = len(re.findall(r'[一-鿿]', text))
            result_han = len(re.findall(r'[一-鿿]', result))
            if orig_han > 0 and abs(orig_han - result_han) > orig_han * 0.05:
                logger.warning(
                    f"[LLM分段] 原文内容被篡改（中文字数 {orig_han} → {result_han}），降级到规则分段"
                )
                return None

            # 检测是否存在多个无关话题混入（内部标记，不暴露给用户）
            if '\n---\n' in result:
                topic_count = result.count('\n---\n') + 1
                logger.warning(
                    f"[LLM分段] 检测到 {topic_count} 个无关话题混入。"
                    f"建议检查 LLM 回复质量，避免无关内容混入用户回复。"
                )
                # 去掉 --- 分隔符，替换为正常段落间距，保持活人感
                result = result.replace('\n---\n', '\n\n')

            logger.info("[LLM分段] 语义分段完成")
            return result

        except Exception as e:
            logger.warning(f"[LLM分段] 调用失败，降级到规则分段: {e}")
            return None

    # ============================================================
    #  LLM 文风优化
    # ============================================================

    async def _try_llm_style_optimize(self, text: str) -> str | None:
        """
        尝试用 LLM 做结构化重组 + 文风润色。
        成功返回优化后文本，失败返回 None（触发降级）。
        """
        enable = self._get_config("enable_llm_style", False)
        provider_id = self._get_config("llm_provider_id", "")
        logger.info(
            f"[LLM文风] 诊断: enable={enable}, "
            f"provider={'SET' if provider_id else 'EMPTY'}, "
            f"text_len={len(text)}"
        )

        if not enable:
            logger.info("[LLM文风] 跳过——enable_llm_style=false，请在插件配置中打开")
            return None

        if not provider_id:
            logger.warning("[LLM文风] 跳过——llm_provider_id 为空，请在插件配置中选择 LLM 模型")
            return None

        if len(text) <= self._SEGMENT_THRESHOLD:
            logger.info(f"[LLM文风] 跳过——文本过短（{len(text)} ≤ {self._SEGMENT_THRESHOLD}）")
            return None

        try:
            logger.info(f"[LLM文风] 正在调用 LLM（provider={provider_id}）...")
            llm_resp = await self.context.llm_generate(
                chat_provider_id=provider_id,
                prompt=self._STYLE_PROMPT.format(text=text),
            )
            result = llm_resp.completion_text.strip()
            logger.info(f"[LLM文风] LLM 返回 {len(result)} 字符")

            if not result:
                logger.warning("[LLM文风] 返回空结果，降级到下一级")
                return None
            if len(result) < len(text) * 0.3:
                logger.warning(
                    f"[LLM文风] 输出过短（{len(result)} < {int(len(text) * 0.3)}），降级到下一级"
                )
                return None

            # 文风优化允许 ±10% 中文字数浮动（轻量润色可能微调措辞）
            orig_han = len(re.findall(r'[一-鿿]', text))
            result_han = len(re.findall(r'[一-鿿]', result))
            if orig_han > 0 and abs(orig_han - result_han) > orig_han * 0.10:
                logger.warning(
                    f"[LLM文风] 内容偏差过大（中文字数 {orig_han} → {result_han}），降级到下一级"
                )
                return None

            # 验证段数：不应超过 _MAX_PARAS + 1（给 LLM 一点弹性）
            para_count = len([p for p in result.split('\n\n') if p.strip()])
            if para_count > self._MAX_PARAS + 1:
                logger.warning(
                    f"[LLM文风] 段数过多（{para_count} > {self._MAX_PARAS + 1}），降级到下一级"
                )
                return None

            logger.info(f"[LLM文风] ✓ 结构化重组完成，{para_count} 段")
            return result

        except Exception as e:
            logger.warning(f"[LLM文风] LLM 调用异常，降级到下一级: {e}")
            return None

    # ============================================================
    #  分段/文风统一入口
    # ============================================================

    async def _apply_segmentation_and_style(self, text: str) -> str:
        """优先级：LLM 文风优化 > LLM 语义分段 > 规则分段"""
        if len(text) <= self._SEGMENT_THRESHOLD:
            return text

        # 1) LLM 文风优化（含结构重组）
        result = await self._try_llm_style_optimize(text)
        if result:
            logger.info("[分段/文风] 使用 LLM 文风优化")
            return result

        # 2) LLM 语义分段
        result = await self._try_llm_segment(text)
        if result:
            logger.info("[分段/文风] 使用 LLM 语义分段")
            return result

        # 3) 规则分段
        logger.info("[分段/文风] 使用规则分段")
        return self._segment_text(text)

    # ============================================================
    #  多消息逐段发送
    # ============================================================

    async def _send_followups(self, umo, paragraphs: list, delay_min: float, delay_max: float):
        """逐段发送后续消息，段间随机延迟 3~10 秒，模拟真人打字节奏"""
        for i, para in enumerate(paragraphs):
            delay = random.uniform(delay_min, delay_max)
            await asyncio.sleep(delay)
            try:
                chain = MessageChain().message(para)
                await self.context.send_message(umo, chain)
                logger.info(f"[多消息发送] 第 {i + 2}/{len(paragraphs) + 1} 段已发送（延迟 {delay:.1f}s）")
            except Exception as e:
                logger.warning(f"[多消息发送] 第 {i + 2} 段发送失败: {e}")

    # ============================================================
    #  规则分段（fallback）
    # ============================================================

    @classmethod
    def _is_list_block(cls, text: str) -> bool:
        """检测文本是否为列表结构（≥2 行带编号/符号前缀）"""
        lines = text.split('\n')
        list_count = sum(1 for ln in lines if cls._LIST_LINE_RE.match(ln))
        return list_count >= 2

    @classmethod
    def _split_long_para(cls, text: str) -> list:
        """将单个长段落按句子均分成 ~200 字的子段落"""
        sentences = [s.strip() for s in cls._SENTENCE_SPLIT.split(text) if s.strip()]
        if len(sentences) <= 2:
            return [text]

        total = sum(len(s) for s in sentences)
        n = max(2, min(cls._MAX_PARAS, total // cls._CHARS_PER_PARA))
        size = max(1, len(sentences) // n)

        paras = []
        for i in range(n):
            start = i * size
            end = len(sentences) if i == n - 1 else start + size
            para = ''.join(sentences[start:end])
            if para:
                paras.append(para)
        return paras

    @classmethod
    def _segment_text(cls, text: str) -> str:
        """
        改进的规则分段：
        1. 短文本不处理
        2. 按已有空行先拆段，尊重 LLM/用户手动分段
        3. 对超长段落进一步细分（列表块除外）
        4. 段数不足时拆分最长段；段数超限时合并最短段
        5. 最终目标：3~5 段
        """
        if len(text) <= cls._SEGMENT_THRESHOLD:
            return text

        # ---- 预处理：单换行规范化 ----
        # 把 "句子结束。\n下一段" 提升为 "\n\n"（真正的段落分隔）
        text = re.sub(r'([。！？])\n(?!\n)', r'\1\n\n', text)
        # 把 3 个以上连续换行折叠为双换行
        text = re.sub(r'\n{3,}', '\n\n', text)

        # 按已有空行拆段
        raw = [p.strip() for p in text.split('\n\n') if p.strip()]

        # 处理每段：超长且非列表 → 细分
        result = []
        for para in raw:
            if len(para) <= cls._CHARS_PER_PARA or cls._is_list_block(para):
                result.append(para)
            else:
                result.extend(cls._split_long_para(para))

        # 段数太少（<3）→ 拆分最长段
        if len(result) < 3 and len(result) > 0:
            longest_idx = max(range(len(result)), key=lambda i: len(result[i]))
            longest = result.pop(longest_idx)
            subs = cls._split_long_para(longest)
            result[longest_idx:longest_idx] = subs

        # 段数超限（>_MAX_PARAS）→ 合并最短段到较短邻居
        while len(result) > cls._MAX_PARAS:
            i = min(range(len(result)), key=lambda i: len(result[i]))
            # 选择较短的邻居合并
            if i == 0:
                j = 1
            elif i == len(result) - 1:
                j = i - 1
            else:
                j = i - 1 if len(result[i - 1]) <= len(result[i + 1]) else i + 1
            # 保证 left < right
            left, right = (i, j) if i < j else (j, i)
            result[left] = result[left] + '\n\n' + result[right]
            result.pop(right)

        return '\n\n'.join(result)
