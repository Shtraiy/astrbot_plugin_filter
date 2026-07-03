import logging
# v4.x 唯一标准核心入口，把 Plugin, register, AstrMessageEvent 统统收拢在这里
from astrbot.api.all import Plugin, register, Context, AstrMessageEvent

logger = logging.getLogger("astrbot")

@register("filter_bug", "Bug修补大师", "强行擦除系统溢出的流式符号", "1.0.0")
class FilterBugPlugin(Plugin):
    def __init__(self, context: Context):
        super().__init__(context)

    # 4.x 版本标准事件拦截器：在消息最终渲染、装饰完毕、准备发给QQ的前一刻触发
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
            # 使用 v4.x 官方标准的 API 重新将洗干净的文本塞回发送队列
            result.set_plain_text(text)
            logger.info("[Bug修补大师] 已成功拦截并清洗异常元数据符号。")
