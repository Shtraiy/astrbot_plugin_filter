import logging
import sys

logger = logging.getLogger("astrbot")

# ==================== 超强力动态路径反射，强行捕捉 Plugin 和 register ====================
Plugin = None
register = None
Context = None
AstrMessageEvent = None

# 所有可能存放核心组件的潜在路径
search_paths = [
    "astrbot.api",
    "astrbot.api.all",
    "astrbot.api.plugin",
    "astrbot.api.event",
    "astrbot.api.platform",
    "astrbot.core.plugin"
]

for path in search_paths:
    try:
        mod = __import__(path, fromlist=["Plugin", "register", "Context", "AstrMessageEvent"])
        if not Plugin and hasattr(mod, "Plugin"):
            Plugin = getattr(mod, "Plugin")
        if not register and hasattr(mod, "register"):
            register = getattr(mod, "register")
        if not Context and hasattr(mod, "Context"):
            Context = getattr(mod, "Context")
        if not AstrMessageEvent and hasattr(mod, "AstrMessageEvent"):
            AstrMessageEvent = getattr(mod, "AstrMessageEvent")
    except Exception:
        continue

# 备用保底：如果实在没拿到这些类，通过 sys.modules 暴力检索
if not Plugin or not register:
    for mod_name, mod in list(sys.modules.items()):
        if mod_name.startswith("astrbot."):
            if not Plugin and hasattr(mod, "Plugin"): Plugin = getattr(mod, "Plugin")
            if not register and hasattr(mod, "register"): register = getattr(mod, "register")
            if not Context and hasattr(mod, "Context"): Context = getattr(mod, "Context")
            if not AstrMessageEvent and hasattr(mod, "AstrMessageEvent"): AstrMessageEvent = getattr(mod, "AstrMessageEvent")

# ====================================================================================

# 动态组装注册，确保不因为装饰器缺失而崩溃
def safe_register(*args, **kwargs):
    if register:
        return register(*args, **kwargs)
    return lambda cls: cls

@safe_register("filter_bug", "Bug修补大师", "强行擦除系统溢出的流式符号", "1.0.0")
class FilterBugPlugin(Plugin if Plugin else object):
    def __init__(self, context=None):
        if Plugin and hasattr(super(), "__init__"):
            super().__init__(context)

    async def on_decorating_result(self, event):
        """在消息发送给QQ前的最后一步，把垃圾元数据强行洗干净"""
        if not event: return
        try:
            result = event.get_result()
            if not result: return
                
            text = result.get_plain_text()
            if not text: return
                
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
                text = text.strip(" \n,[]")
                result.set_plain_text(text)
                logger.info("[Bug修补大师] 已成功拦截并清洗异常元数据符号。")
        except Exception as e:
            logger.error(f"[Bug修补大师] 运行时清洗出错: {e}")
