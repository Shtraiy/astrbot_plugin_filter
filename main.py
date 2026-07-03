import logging
from astrbot.api.plugin import Plugin, register
from astrbot.api.event import AstrMessageEvent

logger = logging.getLogger("astrbot")

@register("filter_bug", "Bug修补大师", "强行擦除系统溢出的流式符号", "1.0.0")
class FilterBugPlugin(Plugin):
    def __init__(self, context):
        super().__init__(context)

    # 4.x 版本标准事件拦截器，在消息发送前（decorating 阶段）触发
    async def on_decorating_result(self, event: AstrMessageEvent):
        """在消息发送给QQ前的最后一步，把垃圾元数据强行洗干净"""
        result = event.get_result()
        if not result:
            return
            
        # 提取当前准备发送的文本
        text = result.get_plain_text()
        if not text:
            return
            
        # 强行替换和擦除所有变异的系统牛皮癣符号
        garbage_list = [
            "[{text=",
            ", type=text}",
            ", type=\\text}",
            "type=text",
            "}]",
            "[{",
            "}]"
        ]
        
        has_garbage = False
        for garbage in garbage_list:
            if garbage in text:
                text = text.replace(garbage, "")
                has_garbage = True
            
        if has_garbage:
            # 顺便把开头和结尾可能残留的空方括号、空换行修剪干净
            text = text.strip(" \n,[]")
            # 使用 v4.x 推荐的 API 重新塞回发送队列
            result.set_plain_text(text)
            logger.info(f"[Bug修补大师] 已成功拦截并清洗异常元数据符号。")
