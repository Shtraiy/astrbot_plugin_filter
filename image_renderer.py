"""Render structured text lists into images."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile

logger = logging.getLogger(__name__)

_LIST_DETECT_RE = re.compile(r"^\s*\d+[\.\)?)]\s+\S", re.MULTILINE)


def find_cjk_font() -> str | None:
    import platform

    system = platform.system()
    if system == "Windows":
        candidates = ["C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf", "C:/Windows/Fonts/simsun.ttc"]
    elif system == "Darwin":
        candidates = ["/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/Hiragino Sans GB.ttc"]
    else:
        candidates = [
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
            "/usr/share/fonts/TTF/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
        ]
    return next((path for path in candidates if os.path.exists(path)), None)


def should_render_image(text: str, get_config) -> bool:
    threshold = int(get_config("image_min_list_items", 3))
    if len(_LIST_DETECT_RE.findall(text)) >= threshold:
        return True
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    return len(lines) >= threshold and _count_structured_lines(lines) >= threshold


def _count_structured_lines(lines: list[str]) -> int:
    count = 0
    for line in lines:
        if re.search("[\u3002\uFF01\uFF1F]$", line) and len(line) > 30:
            continue
        if re.search("\u7B2C\\s*\\d+\\s*[\u96C6\u8BDD\u8A71]", line) and re.search("(?:\u6765\u6E90|\u5B57\u5E55\u7EC4|\u72B6\u6001)[\uFF1A:]", line):
            count += 1
            continue
        if len(re.findall("\\S+[\uFF1A:]\\S+", line)) >= 2:
            count += 1
    return count


def _auto_number_lines(lines: list[str]) -> list[str]:
    numbered = sum(1 for line in lines if re.match(r"^\s*\d+[\.\)?)]", line))
    total = len([line for line in lines if line.strip()])
    if total == 0 or numbered >= total * 0.5:
        return lines
    result: list[str] = []
    counter = 1
    for line in lines:
        stripped = line.strip()
        if not stripped:
            result.append(line)
            continue
        if re.match(r"^\s*\d+[\.\)?)]", line):
            result.append(line)
            continue
        if re.search("[\u3002\uFF01\uFF1F\uFF1A:]$", stripped) and len(stripped) < 50:
            result.append(line)
            continue
        result.append(f"{counter}. {stripped}")
        counter += 1
    return result


async def text_to_image(text: str, get_config) -> str | None:
    try:
        from PIL import Image as PILImage, ImageDraw, ImageFont
    except ImportError:
        logger.warning("[????] Pillow ????????")
        return None

    font_path = find_cjk_font()
    font_size = int(get_config("image_font_size", 22))
    max_width = max(240, int(get_config("image_max_width", 600)))
    font = None
    if font_path:
        try:
            font = ImageFont.truetype(font_path, font_size)
        except Exception:
            logger.warning("[????] ?????? %s", font_path, exc_info=True)
    if font is None:
        logger.warning("[????] ??????????????")
        return None

    lines = _auto_number_lines(text.split("\n"))

    def _render():
        temp_img = PILImage.new("RGB", (1, 1))
        draw = ImageDraw.Draw(temp_img)
        padding_x = 40
        padding_y = 30
        content_width = max_width - padding_x * 2
        line_height = int(font_size * 1.6)
        wrapped_lines: list[str] = []
        for line in lines:
            wrapped_lines.extend(_wrap_line(line, draw, font, content_width))
        line_widths = []
        for line in wrapped_lines:
            bbox = draw.textbbox((0, 0), line, font=font)
            line_widths.append(bbox[2] - bbox[0])
        img_width = min(max(line_widths, default=1) + padding_x * 2, max_width)
        img_height = line_height * len(wrapped_lines) + padding_y * 2
        img = PILImage.new("RGB", (img_width, img_height), (0xF5, 0xF0, 0xE8))
        draw = ImageDraw.Draw(img)
        y = padding_y
        for line in wrapped_lines:
            draw.text((padding_x, y), line, fill=(0x2C, 0x2C, 0x2C), font=font)
            y += line_height
        return img

    try:
        img = await asyncio.to_thread(_render)
    except Exception:
        logger.warning("[????] ????", exc_info=True)
        return None

    fd, temp_path = tempfile.mkstemp(suffix=".png", prefix="astrbot_filter_")
    os.close(fd)
    try:
        img.save(temp_path, "PNG")
    except Exception:
        logger.warning("[????] ?? PNG ??", exc_info=True)
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        return None
    logger.info("[????] ????? %s (%dx%d)", temp_path, img.width, img.height)
    return temp_path


def _wrap_line(line: str, draw, font, max_width: int) -> list[str]:
    if not line:
        return [line]
    if draw.textbbox((0, 0), line, font=font)[2] <= max_width:
        return [line]
    wrapped: list[str] = []
    current = ""
    for char in line:
        candidate = current + char
        bbox = draw.textbbox((0, 0), candidate, font=font)
        if current and bbox[2] > max_width:
            wrapped.append(current)
            current = char
        else:
            current = candidate
    if current:
        wrapped.append(current)
    return wrapped


async def cleanup_temp_file(path: str, delay: float = 120.0) -> None:
    await asyncio.sleep(delay)
    try:
        os.unlink(path)
        logger.info("[????] ??????? %s", path)
    except OSError:
        pass
