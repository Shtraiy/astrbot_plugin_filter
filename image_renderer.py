"""
⑧ 图片渲染 — 结构化列表自动转图片，避免 QQ 气泡排版错乱。
"""

from __future__ import annotations

import asyncio
import os
import re
import tempfile
import logging

logger = logging.getLogger(__name__)

_LIST_DETECT_RE = re.compile(r'^\s*\d+[\.\)、]\s+\S', re.MULTILINE)


def find_cjk_font() -> str | None:
    """查找系统可用的中文字体，返回字体路径或 None"""
    import platform
    system = platform.system()
    if system == "Windows":
        candidates = [
            "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/simhei.ttf",
            "C:/Windows/Fonts/simsun.ttc",
        ]
    elif system == "Darwin":
        candidates = [
            "/System/Library/Fonts/PingFang.ttc",
            "/System/Library/Fonts/Hiragino Sans GB.ttc",
        ]
    else:
        candidates = [
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
            "/usr/share/fonts/TTF/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
        ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


def should_render_image(text: str, get_config) -> bool:
    """
    检测文本是否应渲染为图片。满足任一条件即触发：
    1. ≥N 条已编号列表行（如 "1. xxx" "2. xxx"）
    2. ≥N 条结构化数据行（如番剧列表：每行含 "第X集" / "来源：" / "字幕组：" 等字段）
    """
    threshold = get_config("image_min_list_items", 3)
    # 条件1：已编号列表
    if len(_LIST_DETECT_RE.findall(text)) >= threshold:
        return True
    # 条件2：无序号但多行结构化数据
    lines = [ln.strip() for ln in text.split('\n') if ln.strip()]
    if len(lines) >= threshold and _count_structured_lines(lines) >= threshold:
        return True
    return False


def _count_structured_lines(lines: list) -> int:
    """
    统计结构化数据行数。一行视为结构化数据，如果它：
    - 含 "第\d+集"（番剧进度）+ "来源" 或 "字幕组"（来源信息）
    - 或含 ≥2 个中文冒号键值对（如 "番剧名：xxx，来源：xxx"）
    """
    count = 0
    for line in lines:
        # 跳过明显是叙事/寒暄的句子（含句号结束的完整句子）
        if re.search(r'[。！？]', line) and len(line) > 30:
            continue
        # 番剧订阅特征：含集数 + 来源/字幕组
        if re.search(r'第\d+[集话話節]', line) and re.search(r'(?:来源|字幕组|狀態|状态)[：:]', line):
            count += 1
            continue
        # 通用键值对：一行中含 ≥2 个冒号分隔的字段
        kv_count = len(re.findall(r'\S+[：:]\S+', line))
        if kv_count >= 2:
            count += 1
            continue
    return count


def _auto_number_lines(lines: list) -> list:
    """
    如果多行结构化数据没有编号，自动补上 1. 2. 3.
    保留已有编号；跳过空行和纯叙事行。
    """
    # 统计已编号行数
    numbered = sum(1 for ln in lines if re.match(r'^\s*\d+[\.\)、]', ln))
    total = len([ln for ln in lines if ln.strip()])
    # 如果大部分行已有编号，不做处理
    if numbered >= total * 0.5:
        return lines

    result = []
    counter = 1
    for ln in lines:
        stripped = ln.strip()
        if not stripped:
            result.append(ln)
            continue
        # 已经是编号行，保持原样（不再同步计数器，避免跳跃）
        if re.match(r'^\s*\d+[\.\)、]', ln):
            result.append(ln)
            continue
        # 叙事句/标题（含句号或冒号结尾的短句）→ 不加编号
        if re.search(r'[。！？]$', stripped) and len(stripped) < 50:
            result.append(ln)
            continue
        # 结构化数据行 → 加编号
        result.append(f"{counter}. {stripped}")
        counter += 1
    return result


async def text_to_image(text: str, get_config) -> str | None:
    """将文本渲染为 PNG 图片，返回临时文件路径。失败返回 None。"""
    try:
        from PIL import Image as PILImage, ImageDraw, ImageFont
    except ImportError:
        logger.warning("[图片渲染] Pillow 未安装，跳过。请执行: pip install Pillow")
        return None

    font_path = find_cjk_font()
    font_size = get_config("image_font_size", 22)
    max_width = get_config("image_max_width", 600)

    font = None
    if font_path:
        try:
            font = ImageFont.truetype(font_path, font_size)
        except Exception:
            logger.warning("[图片渲染] 无法加载字体 %s", font_path, exc_info=True)

    if font is None:
        # load_default() 无法渲染 CJK，直接放弃
        logger.warning("[图片渲染] 未找到可用的中文字体，放弃图片渲染。"
                       "请安装中文字体（如 Noto Sans CJK 或微软雅黑）。")
        return None

    lines = text.split('\n')
    lines = _auto_number_lines(lines)

    def _render():
        # 测量阶段
        temp_img = PILImage.new("RGB", (1, 1))
        draw = ImageDraw.Draw(temp_img)
        line_widths = []
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font)
            line_widths.append(bbox[2] - bbox[0])

        padding_x = 40
        padding_y = 30
        img_width = min(max(line_widths) + padding_x * 2, max_width)
        line_height = int(font_size * 1.6)
        img_height = line_height * len(lines) + padding_y * 2

        # 绘制阶段
        bg_color = (0xF5, 0xF0, 0xE8)
        text_color = (0x2C, 0x2C, 0x2C)

        img = PILImage.new("RGB", (img_width, img_height), bg_color)
        draw = ImageDraw.Draw(img)

        y = padding_y
        for line in lines:
            draw.text((padding_x, y), line, fill=text_color, font=font)
            y += line_height

        return img

    try:
        img = await asyncio.to_thread(_render)
    except Exception:
        logger.warning("[图片渲染] 渲染失败", exc_info=True)
        return None

    fd, temp_path = tempfile.mkstemp(suffix=".png", prefix="astrbot_filter_")
    os.close(fd)
    try:
        img.save(temp_path, "PNG")
    except Exception:
        logger.warning("[图片渲染] 保存 PNG 失败", exc_info=True)
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        return None

    logger.info("[图片渲染] 已生成图片: %s (%dx%d)", temp_path, img.width, img.height)
    return temp_path


async def cleanup_temp_file(path: str, delay: float = 120.0) -> None:
    """延迟清理临时图片文件（等待 AstrBot 完成文件上传后删除）"""
    await asyncio.sleep(delay)
    try:
        os.unlink(path)
        logger.info("[图片渲染] 已清理临时文件: %s", path)
    except OSError:
        pass
