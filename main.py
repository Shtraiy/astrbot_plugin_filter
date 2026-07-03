import re
from astrbot.api.star import Context, Star
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api import logger
from astrbot.api.message_components import Plain


class FilterBugPlugin(Star):
    """
    Bug修补大师 — 强行擦除 AstrBot 接入 OneBot 后，
    LLM / MCP 工具调用产生的垃圾元数据符号。
    """

    def __init__(self, context: Context):
        super().__init__(context)

    @filter.on_decorating_result()
    async def on_decorating_result(self, event: AstrMessageEvent):
        """在消息发送给 QQ 前的最后一步，把垃圾元数据强行洗干净"""
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
                    cleaned = self._clean_garbage(comp.text)
                    if cleaned != comp.text:
                        comp.text = cleaned
                        modified = True

            if modified:
                logger.info(
                    "[Bug修补大师] 已成功拦截并清洗异常元数据符号。"
                )

        except Exception as e:
            logger.error(f"[Bug修补大师] 运行时清洗出错: {e}")

    # ==================== 清洗实现 ====================

    # 匹配从 OneBot / MCP 工具调用中泄漏出来的结构符号
    _GARBAGE_RE = re.compile(
        r'\[{text='          # "[{text=" 前导标记
        r'|,\s*type\s*=\s*\\?text\s*}'  # ", type=text}" / ", type=\text}"
        r'|\]\s*}'            # "]}"
        r'|\[{'               # 孤立 "[{"
        r'|}]'                # 孤立 "}]"
    )

    # 清洗后残留在首尾的无效字符
    _STRIP_CHARS = ' \n,[]{}'

    @classmethod
    def _clean_garbage(cls, text: str) -> str:
        """移除 text 中所有已知的垃圾元数据片段"""
        cleaned = cls._GARBAGE_RE.sub('', text)
        # 去掉首尾残留
        cleaned = cleaned.strip(cls._STRIP_CHARS)
        # 压缩多余空白
        cleaned = re.sub(r'\s+', ' ', cleaned)
        return cleaned.strip()
