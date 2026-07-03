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

            # 提取当前消息链中的纯文本
            plain_text = result.get_plain_text()
            if not plain_text:
                return

            # ---- 清洗逻辑：移除 OneBot 工具调用产生的元数据垃圾 ----
            # 典型垃圾形态：
            #   [{text=, type=text}], type=text}][{text=[{text=
            #   柠檬鸭可是南宁当地非常经典且超级开胃的名菜！, type=text}], type=text}]
            cleaned = self._clean_garbage(plain_text)

            if cleaned == plain_text:
                return  # 没有垃圾，无需处理

            # 用清洗后的纯文本重建消息链
            result.chain = [Plain(text=cleaned)]

            logger.info(
                f"[Bug修补大师] 已成功拦截并清洗异常元数据符号。"
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
