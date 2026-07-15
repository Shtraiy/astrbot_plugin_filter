"""
语言逻辑优化大师 — 在消息发出前全面优化输出文本：
1. 清洗 OneBot/MCP 泄漏的垃圾元数据符号
2. "用户" → 群昵称替换
3. 过滤系统路径 / 指令等敏感信息
4. 删除工具调用过程叙述句
5. 残存工具函数名 → 自然语言
6. 正则去AI味（清除公式化表达）
7. 长文本智能分段/文风优化
8. 结构化列表自动渲染为图片（避免 QQ 气泡排版错乱）
"""

import asyncio

from astrbot.api.star import Context, Star
from astrbot.api.event import filter as _event_filter, AstrMessageEvent
from astrbot.api import logger
from astrbot.api.message_components import Plain
from astrbot.api.all import MessageChain

from .pipelines import (
    clean_garbage,
    replace_user,
    filter_sensitive,
    remove_tool_narration,
    deidentify_tool_names,
    de_ai_flavor,
)
from .segmentation import (
    apply_segmentation_and_style,
    send_followups,
)
from .image_renderer import (
    should_render_image,
    text_to_image,
    cleanup_temp_file,
)


class LanguageLogicOptimizer(Star):
    """
    语言逻辑优化大师 — 在消息发出前全面优化输出文本：
    ① 垃圾符号清洗 → ② 用户→昵称替换 → ③ 敏感信息过滤 →
    ④ 删除叙述句 → ⑤ 工具名脱敏 → ⑥ 去AI味 →
    ⑦ 智能分段/文风优化 → ⑧ 图片渲染
    """

    def __init__(self, context: Context):
        super().__init__(context)
        # 追踪后台发送任务，防止异常静默丢失
        self._pending_tasks: set[asyncio.Task] = set()

    @_event_filter.on_decorating_result()
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
            pipeline_stats: dict[str, int] = {}  # 记录每条管线的修改次数

            for comp in chain:
                if isinstance(comp, Plain):
                    original = comp.text
                    text = original

                    # ============ 八道处理管线 ============
                    text, changed = _apply_pipeline("①垃圾清洗", clean_garbage, text, pipeline_stats)
                    text, changed = _apply_pipeline("②昵称替换", replace_user, text, pipeline_stats)
                    text, changed = _apply_pipeline("③敏感过滤", filter_sensitive, text, pipeline_stats)
                    text, changed = _apply_pipeline("④叙述删除", remove_tool_narration, text, pipeline_stats)
                    text, changed = _apply_pipeline("⑤工具脱敏", deidentify_tool_names, text, pipeline_stats)

                    # ⑥ 去AI味（正则清除公式化表达）
                    if self._get_config("enable_de_ai_flavor", True):
                        text, changed = _apply_pipeline("⑥去AI味", de_ai_flavor, text, pipeline_stats)

                    # ⑦ 分段/文风优化（LLM 文风 > LLM 分段 > 规则）
                    text, changed = await _apply_pipeline_async(
                        "⑦分段优化", apply_segmentation_and_style,
                        text, self.context, self._get_config,
                        stats=pipeline_stats,
                    )
                    # =====================================

                    # ⑧ 图片渲染（检测到结构化列表时触发）
                    if self._get_config("enable_image_render", False) and should_render_image(
                        text, self._get_config
                    ):
                        image_path = await text_to_image(text, self._get_config)
                        if image_path:
                            umo = event.unified_msg_origin
                            img_chain = MessageChain().file_image(image_path)
                            await self.context.send_message(umo, img_chain)
                            # 延迟清理临时图片文件（等待平台上传完成）
                            cleanup_task = asyncio.create_task(
                                cleanup_temp_file(image_path)
                            )
                            self._track_task(cleanup_task)
                            comp.text = ""
                            modified = True
                            pipeline_stats["⑧图片渲染"] = pipeline_stats.get("⑧图片渲染", 0) + 1
                            continue

                    # ============ 多消息 & 最终写入 ============
                    # 多消息模式优先——即使文本未被管线修改，也要按段落拆分
                    if self._get_config("multi_message", True):
                        paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
                        if len(paragraphs) > 1:
                            comp.text = paragraphs[0]
                            modified = True
                            delay_min = self._get_config("delay_min", 3.0)
                            delay_max = self._get_config("delay_max", 10.0)
                            umo = event.unified_msg_origin
                            task = asyncio.create_task(
                                send_followups(self.context, umo, paragraphs[1:],
                                               delay_min, delay_max)
                            )
                            self._track_task(task)
                            continue

                    # 文本未被任何管线修改 → 无需替换原文
                    if text == original:
                        continue

                    comp.text = text
                    modified = True

            if modified:
                # 汇总报告实际生效的管线
                active = [k for k, v in pipeline_stats.items() if v > 0]
                logger.info(
                    "[语言逻辑优化大师] 已优化输出文本。生效管线: %s",
                    ', '.join(active) if active else '无（文本无需修改）',
                )

        except Exception:
            logger.error("[语言逻辑优化大师] 运行时出错", exc_info=True)

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
    #  后台任务追踪
    # ============================================================

    def _track_task(self, task: asyncio.Task) -> None:
        """追踪后台任务，自动清理已完成的，防止异常静默丢失"""
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)
        task.add_done_callback(_log_task_exception)


def _log_task_exception(task: asyncio.Task) -> None:
    """记录后台任务的未捕获异常"""
    try:
        exc = task.exception()
        if exc is not None:
            logger.warning("[后台任务] 异常: %s", exc, exc_info=True)
    except asyncio.CancelledError:
        pass
    except Exception:
        pass


# ============================================================
#  管线辅助函数
# ============================================================

def _apply_pipeline(name: str, func, text: str, stats: dict[str, int]) -> tuple[str, bool]:
    """应用同步管线，记录修改"""
    result = func(text)
    if result != text:
        stats[name] = stats.get(name, 0) + 1
        return result, True
    return text, False


async def _apply_pipeline_async(name: str, func, text: str, *args,
                                stats: dict[str, int]) -> tuple[str, bool]:
    """应用异步管线，记录修改"""
    result = await func(text, *args)
    if result != text:
        stats[name] = stats.get(name, 0) + 1
        return result, True
    return text, False
