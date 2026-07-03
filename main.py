try:
    from astrbot.api.event import filter, AstrMessageEvent
    from astrbot.api.plugin import Plugin, register
except ImportError:
    try:
        from astrbot.api.all import filter, AstrMessageEvent, Plugin, register
    except ImportError:
        # 最后的保底兼容
        from astrbot.api import filter, AstrMessageEvent, Plugin, register

@register("filter_bug", "Bug修补大师", "强行擦除系统溢出的流式符号", "1.0.0")
class FilterBugPlugin(Plugin):
    def __init__(self, context):
        super().__init__(context)

    @filter.on_decorating_result()
    async def remove_metadata_bug(self, event: AstrMessageEvent):
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
        
        for garbage in garbage_list:
            text = text.replace(garbage, "")
            
        # 顺便把开头和结尾可能残留的空方括号、空换行修剪干净
        text = text.strip(" \n")
        
        # 使用 AstrBot 官方推荐的 API 重新塞回发送队列
        result.set_plain_text(text)
