"""Mark the bot's own recent replies so the model attributes media correctly.

The bot's own outgoing images/files stay in the conversation history (useful
context), but memory/history text can mislead the model into thinking the user
sent them. This module records the bot's recent sent text/media per session
and injects an objective attribution block before each LLM request.
"""

from __future__ import annotations

import re
import time
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Callable

from astrbot.api.message_components import File, Image, Plain

from .event_access import (
    entry_content,
    entry_role,
    get_message_chain,
    is_media_part,
    is_reply_component,
    request_contexts,
)


MAX_MARK_ENTRIES = 8
_MAX_TEXT_SNIPPET = 200

_TEXT_ONLY_MEDIA_NOTE = (
    "<media_note>"
    "用户本条消息为纯文字，用户本轮没有发送任何图片，也没有发出表情包或可被观察的表情、眼神、神态；"
    "不要虚构或推断用户的表情、眼神、动作或神态。"
    "历史对话中的图片属于各自消息的发送者：assistant（机器人）消息中的图片是机器人自己发送的，不属于用户。"
    "任何记忆、总结或历史中声称'用户发送过图片/表情包'的内容都可能是机器人自己的误判；"
    "除非用户本轮实际发送了图片，否则不要把机器人自己的图片归为用户发送，也不要描述成用户发送的。"
    "如果用户提到'刚才那张图/表情'，指的是机器人自己之前发送的图片，"
    "回答时应明确说明'这是我（机器人）自己发送的表情包'，"
    "不要把它说成用户发送的，也不要用机器人自己发送的表情包画面来描述用户的表情或行为。"
    "</media_note>"
)
_REFERENCED_IMAGE_NOTE = (
    "<referenced_image_note>"
    "用户引用了一张历史消息中的图片，这张图就是用户当前询问的对象。"
    "请先识别这张图片的实际内容（人物/场景/文字），再基于图片本身回答；"
    "如果图片内容与最近的聊天话题不同，以图片实际内容为准，不要根据对话话题猜测。"
    "如果该图片是机器人自己之前发送的，回答时可以说明'这是我之前发的图'，"
    "但不要声称用户刚刚发送了它。"
    "</referenced_image_note>"
)
_BOT_MEME_MARK_HEADER = (
    "<bot_sent_meme>"
    "注意：下面这张表情包是机器人（assistant，也就是你自己）刚刚在上一轮发送的，"
    "不是用户发送的，用户本轮没有发送这张表情包。"
    "若用户提到'刚才的表情'、'这个表情'、'这张图'或表情包上的配字，"
    "指的就是你自己发送的这张表情包，回答时应明确说明'这是我（机器人）自己发送的表情包'；"
    "禁止把它的画面/配字当作对用户表情、眼神或行为的描述。"
    "以下内容供你识别这张表情包："
)
_BOT_MEME_FIELD_KEYWORDS = (
    "文件：",
    "分类：",
    "画面描述：",
    "情绪：",
    "图片文字：",
    "标签：",
)
_MEMORY_EXPRESSION_MARK = (
    "（疑为机器人自己发送的表情包画面，历史总结可能误判，用户并未做出该表情/眼神）"
)
_MEMORY_STICKER_MARK = (
    "（疑为机器人自己发送的表情包，历史总结可能误判，用户并未发送）"
)
_MEMORY_ROLE_DISCLAIMER = (
    "<memory_attribution_note>"
    "以下记忆由长期记忆插件对历史消息的总结生成，总结可能没有严格区分消息发送者；"
    "其中关于'用户/对方的表情、眼神、神态'以及'用户/对方发送表情包'的描述，"
    "很可能源自机器人自己发送的表情包画面，是历史总结的误判；"
    "除非有独立证据（例如用户本轮实际发送的图片/文件），"
    "不要把这些内容当作用户的真实行为。"
    "</memory_attribution_note>"
)
_ASSISTANT_CLAIM_MARK = (
    "（这是机器人自己发送的表情包画面，不是用户真实的表情/眼神）"
)
_ASSISTANT_CLAIM_FIXES = [
    re.compile(r"(?:这种|那种|这副|那副)(?:眼神|表情|神态)(?!（这是)"),
    re.compile(r"表情包里(?:的)?小人?(?:的)?(?:眼神|表情|神态)(?!（这是)"),
]
_MISATTRIBUTION_FIXES = [
    (
        re.compile(
            r"用户(?:正|又|还|在)?用[^，。；！？\n]{0,8}"
            r"(?:眼神|眼睛|表情|神态)(?!（疑为)"
        ),
        _MEMORY_EXPRESSION_MARK,
    ),
    (
        re.compile(r"用户(?:的)?(?:眼神|表情|神态)(?!（疑为)"),
        _MEMORY_EXPRESSION_MARK,
    ),
    (
        re.compile(r"用户(?:满脸|一脸|神情)[^，。；！？\n]{0,4}(?!（疑为)"),
        _MEMORY_EXPRESSION_MARK,
    ),
    (
        re.compile(r"用户(?:翻了个?)?白眼(?!（疑为)"),
        _MEMORY_EXPRESSION_MARK,
    ),
    (
        re.compile(
            r"(?<!不是)(?<!并非)用户(?:给我|向我|又|还)?"
            r"(?:发送|发了|发过|发来|发)(?:一张|一个|个|张)?(?:的)?表情包(?!（疑为)"
        ),
        _MEMORY_STICKER_MARK,
    ),
    (
        re.compile(
            r"对方(?:正|又|还|在)?用[^，。；！？\n]{0,8}"
            r"(?:眼神|眼睛|表情|神态)(?!（疑为)"
        ),
        _MEMORY_EXPRESSION_MARK,
    ),
    (
        re.compile(r"对方(?:的)?(?:眼神|表情|神态)(?!（疑为)"),
        _MEMORY_EXPRESSION_MARK,
    ),
    (
        re.compile(r"对方(?:满脸|一脸|神情)[^，。；！？\n]{0,4}(?!（疑为)"),
        _MEMORY_EXPRESSION_MARK,
    ),
    (
        re.compile(r"对方(?:翻了个?)?白眼(?!（疑为)"),
        _MEMORY_EXPRESSION_MARK,
    ),
    (
        re.compile(
            r"(?<!不是)(?<!并非)对方(?:给我|向我|又|还)?"
            r"(?:发送|发了|发过|发来|发)(?:一张|一个|个|张)?(?:的)?表情包(?!（疑为)"
        ),
        _MEMORY_STICKER_MARK,
    ),
]
_SENDER_ASSISTANT = "机器人自己发送的"
_SENDER_USER = "用户发送的"
_SENDER_USER_CURRENT = "用户本轮发送的"
_MEDIA_PART_PREFIX_ASSISTANT = "[机器人自己发送]"
_MEDIA_PART_PREFIX_USER = "[用户发送]"

_PLACEHOLDER_KIND = {
    "图片": "图片",
    "Image": "图片",
    "image": "图片",
    "表情": "图片",
    "文件": "文件",
    "File": "文件",
    "file": "文件",
    "音频": "音频",
    "语音": "音频",
    "Audio": "音频",
    "audio": "音频",
    "视频": "视频",
    "Video": "视频",
    "video": "视频",
}
_MEDIA_PLACEHOLDER_RE = re.compile(
    r"\[(" + "|".join(re.escape(k) for k in _PLACEHOLDER_KIND) + r")\]"
)


@dataclass
class _SentEntry:
    timestamp: float
    text: str
    media: list[str] = field(default_factory=list)


class SelfReplyMarker:
    """Record the bot's own sent text/media per session with a TTL window."""

    def __init__(
        self,
        *,
        get_config: Callable[[str, Any], Any] | None = None,
        now: Callable[[], float] | None = None,
    ) -> None:
        self._entries: dict[str, deque[_SentEntry]] = {}
        self._get_config = get_config or (lambda _key, default: default)
        self._now = now or time.time

    def record_sent_reply(self, event: Any) -> None:
        origin = str(getattr(event, "unified_msg_origin", "") or "")
        if not origin:
            return
        chain = self._result_chain(event)
        if not chain:
            return
        text = " ".join(
            (getattr(comp, "text", "") or "")
            for comp in chain
            if isinstance(comp, Plain)
        ).strip()
        media = [
            self._describe_media(comp)
            for comp in chain
            if isinstance(comp, (Image, File))
        ]
        if not text and not media:
            return
        self._prune(origin)
        queue = self._entries.setdefault(origin, deque(maxlen=MAX_MARK_ENTRIES))
        queue.append(_SentEntry(timestamp=self._now(), text=text, media=media))

    def mark_own_recent_replies(self, req: Any, event: Any) -> bool:
        origin = str(getattr(event, "unified_msg_origin", "") or "")
        if not origin:
            return False
        if not self._enabled():
            return False
        self._prune(origin)
        queue = self._entries.get(origin)
        if not queue:
            return False
        block = self._build_mark_block(queue)
        return self._append_temp_part(req, block)

    def recently_sent_duplicate(
        self,
        origin: str,
        text: str,
        *,
        within_seconds: float = 15.0,
    ) -> bool:
        """True when the same plain text was just sent for this session.

        Used as a send-level dedupe guard for AstrBot 4.27 pipelines that can
        decorate/send the same reply twice.
        """
        if not origin or not text:
            return False
        self._prune(origin)
        queue = self._entries.get(origin)
        if not queue:
            return False
        now = self._now()
        needle = " ".join(text.split())
        for entry in queue:
            if now - entry.timestamp > within_seconds:
                continue
            if " ".join(entry.text.split()) == needle:
                return True
        return False

    def _enabled(self) -> bool:
        return bool(self._get_config("enable_self_reply_mark", True))

    def _window_minutes(self) -> float:
        try:
            value = float(self._get_config("self_reply_mark_minutes", 5.0))
        except (TypeError, ValueError):
            return 5.0
        return max(0.0, value)

    def _prune(self, origin: str) -> None:
        minutes = self._window_minutes()
        queue = self._entries.get(origin)
        if queue is None:
            return
        if minutes <= 0:
            self._entries.pop(origin, None)
            return
        cutoff = self._now() - minutes * 60
        while queue and queue[0].timestamp < cutoff:
            queue.popleft()
        if not queue:
            self._entries.pop(origin, None)

    def _build_mark_block(self, queue: deque[_SentEntry]) -> str:
        minutes = self._window_minutes()
        lines = [
            "<self_reply_mark>",
            f"以下内容是机器人自己在最近 {minutes:g} 分钟内发送过的（属于机器人，不是用户发送的）：",
        ]
        for entry in queue:
            if entry.text:
                snippet = entry.text.replace("\n", " ")[:_MAX_TEXT_SNIPPET]
                lines.append(f"- [文本] {snippet}")
            for desc in entry.media:
                lines.append(f"- {desc}")
        lines.append(
            "上下文历史中 assistant（机器人）消息里的图片/文件是机器人自己发送的；"
            "用户本轮消息附带的图片/文件才是用户发送的。"
        )
        lines.append(
            "如果历史对话或记忆声称'用户发送过以上这些表情包/图片'，那是机器人此前的误判，"
            "以本标记为准，不要继续坚持'是用户发送的'。"
        )
        lines.append(
            "用户引用历史消息中的图片提问时，该图片是用户当前询问的对象"
            "（即使它是机器人自己之前发送的），需要识别分析，但仍应说清它的真实发送者。"
        )
        lines.append(
            "用户本轮消息附带的图片/文件才是用户本轮发送的；机器人上面列出的内容"
            "（含表情包图片）是机器人自己发送的。禁止把机器人自己发送的表情包内容"
            "（画面、配字）当作对用户本轮图片的描述；即使最近刚出现过某张表情包，"
            "也不能假定用户本轮又发了同一张，除非用户本轮消息确实附带了该图片。"
        )
        lines.append(
            "用户本轮为纯文字消息时，禁止用机器人自己发送的表情包（画面、配字）"
            "推断或描述用户的表情、眼神或神态；机器人历史回复中关于'用户的表情/眼神'"
            "的推测也可能是误判，不要继续当作事实采信。用户询问'刚才那张图/表情'时，"
            "应明确回答那是机器人自己发送的表情包。"
        )
        lines.append("</self_reply_mark>")
        return "\n".join(lines)

    @staticmethod
    def _result_chain(event: Any) -> list[Any] | None:
        result = getattr(event, "get_result", lambda: None)()
        chain = getattr(result, "chain", None) if result is not None else None
        return chain if isinstance(chain, list) else None

    @staticmethod
    def _describe_media(comp: Any) -> str:
        return _describe_media_component(comp)

    def _append_temp_part(self, req: Any, text: str) -> bool:
        return _append_note_part(req, text)


def has_user_media(event: Any) -> bool:
    """Return True when the current user message carries media components."""
    chain = get_message_chain(event)
    if not chain:
        return False
    return any(is_media_part(comp) for comp in chain)


def has_referenced_image(event: Any) -> bool:
    """Return True when the message quotes a historical message that includes an image."""
    chain = get_message_chain(event)
    if not chain:
        return False
    for comp in chain:
        if not is_reply_component(comp):
            continue
        reply_chain = getattr(comp, "chain", None)
        if isinstance(reply_chain, list) and any(
            is_media_part(item) for item in reply_chain
        ):
            return True
        message_str = str(getattr(comp, "message_str", "") or "")
        if "[Image]" in message_str or "[图片]" in message_str:
            return True
    return False


async def attach_quoted_images(req: Any, event: Any) -> int:
    """Best-effort attach of quoted images into ``req.image_urls``.

    AstrBot's own quote extraction can fail (e.g. persisted image files are
    missing), leaving the model without the quoted image. This helper pulls
    Image components from Reply chains directly as a fallback and is idempotent
    against paths AstrBot already attached.
    """
    chain = get_message_chain(event)
    if not chain:
        return 0
    image_urls = getattr(req, "image_urls", None)
    if not isinstance(image_urls, list):
        return 0
    existing = {str(value) for value in image_urls if value}
    attached = 0
    for comp in chain:
        if not is_reply_component(comp):
            continue
        reply_chain = getattr(comp, "chain", None)
        if not isinstance(reply_chain, list):
            continue
        for img in reply_chain:
            if not isinstance(img, Image):
                continue
            ref = _image_ref(img)
            if not ref:
                continue
            target = ref
            converter = getattr(img, "convert_to_file_path", None)
            if callable(converter):
                try:
                    converted = await converter()
                except Exception:
                    converted = None
                if isinstance(converted, str) and converted.strip():
                    target = converted.strip()
            if target in existing:
                continue
            image_urls.append(target)
            existing.add(target)
            _append_note_part(
                req,
                f"[Image Attachment in quoted message: path {target}]",
            )
            attached += 1
    return attached


def _image_ref(img: Any) -> str | None:
    for attr in ("file", "url", "path"):
        value = getattr(img, attr, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def mark_context_media_ownership(req: Any) -> int:
    """Annotate media blocks in request history with role ownership.

    Media inside assistant-role history entries becomes
    ``[机器人自己发送]``/``[机器人自己发送的图片]``, media inside user-role
    entries becomes ``[用户发送]``/``[用户发送的图片]``. Both structured
    (OpenAI content part lists) and string-placeholder history (``[图片]``)
    are annotated in place. The request contexts are a per-request copy
    loaded from conversation history, so nothing is written back to AstrBot
    history. Returns the number of annotated messages.
    """
    contexts = request_contexts(req)
    if not contexts:
        return 0
    marked = 0
    for entry in contexts:
        role = str(entry_role(entry) or "").casefold()
        if role in {"assistant", "bot", "ai"}:
            part_prefix = _MEDIA_PART_PREFIX_ASSISTANT
            placeholder_sender = _SENDER_ASSISTANT
        elif role == "user":
            part_prefix = _MEDIA_PART_PREFIX_USER
            placeholder_sender = _SENDER_USER
        else:
            continue
        content = entry_content(entry)
        if isinstance(content, str):
            replaced = _annotate_placeholder_text(content, placeholder_sender)
            if replaced != content:
                _set_entry_content(entry, replaced)
                marked += 1
            continue
        if _annotate_media_blocks(content, part_prefix, placeholder_sender):
            marked += 1
    return marked


def mark_current_prompt_media_boundary(req: Any, event: Any) -> bool:
    """Annotate the current user prompt's media placeholders in place.

    AstrBot keeps the current turn in ``req.prompt`` while history lives in
    ``req.contexts``. When the current message actually carries media, rewrite
    ``[图片]``-style placeholders in the prompt to ``[用户本轮发送的图片]`` so
    the model can bind this turn's attachment exactly instead of guessing from
    older context. Returns True when the prompt was rewritten.
    """
    if req is None:
        return False
    chain = get_message_chain(event)
    if not chain or not any(is_media_part(comp) for comp in chain):
        return False
    prompt = getattr(req, "prompt", None)
    if not isinstance(prompt, str) or not prompt.strip():
        return False
    replaced = _annotate_placeholder_text(prompt, _SENDER_USER_CURRENT)
    if replaced == prompt:
        return False
    try:
        req.prompt = replaced
        return True
    except Exception:
        return False


def _set_entry_content(entry: Any, value: str) -> bool:
    if isinstance(entry, Mapping):
        entry["content"] = value
        return True
    try:
        entry.content = value
        return True
    except Exception:
        return False


def _annotate_placeholder_text(text: str, sender: str) -> str:
    """Rewrite ``[图片]``-style tokens to ``[<sender>图片]``-style tokens."""

    def repl(match: re.Match) -> str:
        kind = _PLACEHOLDER_KIND.get(match.group(1), match.group(1))
        return f"[{sender}{kind}]"

    return _MEDIA_PLACEHOLDER_RE.sub(repl, text)


def _annotate_media_blocks(
    content: Any,
    part_prefix: str,
    placeholder_sender: str,
) -> bool:
    """Prefix media blocks inside a message content structure. Returns True if changed."""
    changed = False
    if isinstance(content, list):
        for index, item in enumerate(content):
            if isinstance(item, str):
                replaced = _annotate_placeholder_text(item, placeholder_sender)
                if replaced != item:
                    content[index] = replaced
                    changed = True
            elif _annotate_media_block(item, part_prefix, placeholder_sender):
                changed = True
    elif isinstance(content, Mapping):
        if is_media_part(content):
            return _prefix_media_mapping(content, part_prefix)
        nested = content.get("content")
        if isinstance(nested, list):
            if _annotate_media_blocks(nested, part_prefix, placeholder_sender):
                changed = True
        elif isinstance(nested, str) and nested.strip():
            replaced = _annotate_placeholder_text(nested, placeholder_sender)
            if replaced != nested:
                content["content"] = replaced
                changed = True
        else:
            text = content.get("text")
            if isinstance(text, str) and text.strip():
                replaced = _annotate_placeholder_text(text, placeholder_sender)
                if replaced != text:
                    content["text"] = replaced
                    changed = True
    return changed


def _annotate_media_block(
    item: Any,
    part_prefix: str,
    placeholder_sender: str,
) -> bool:
    if isinstance(item, str):
        replaced = _annotate_placeholder_text(item, placeholder_sender)
        return replaced != item
    if isinstance(item, Mapping):
        if is_media_part(item):
            return _prefix_media_mapping(item, part_prefix)
        nested = item.get("content")
        if isinstance(nested, list):
            return _annotate_media_blocks(nested, part_prefix, placeholder_sender)
        if isinstance(nested, str) and nested.strip():
            replaced = _annotate_placeholder_text(nested, placeholder_sender)
            if replaced != nested:
                item["content"] = replaced
                return True
            return False
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            replaced = _annotate_placeholder_text(text, placeholder_sender)
            if replaced != text:
                item["text"] = replaced
                return True
        return False
    if is_media_part(item):
        text = str(getattr(item, "text", "") or "").strip()
        if text.startswith(part_prefix):
            return False
        try:
            if text:
                item.text = f"{part_prefix} {text}".strip()
            else:
                item.text = f"{part_prefix} {_object_media_label(item)}".strip()
            return True
        except Exception:
            return False
    text = str(getattr(item, "text", "") or "").strip()
    if not text:
        return False
    replaced = _annotate_placeholder_text(text, placeholder_sender)
    if replaced == text:
        return False
    try:
        item.text = replaced
        return True
    except Exception:
        return False


def _prefix_media_mapping(item: Mapping, prefix: str) -> bool:
    for key in ("text", "content"):
        if key in item:
            original = str(item.get(key) or "").strip()
            if original and not original.startswith(prefix):
                item[key] = f"{prefix} {original}"
                return True
            return False
    item["text"] = f"{prefix} {_mapping_media_label(item)}".strip()
    return True


def _object_media_label(item: Any) -> str:
    """Build a short ``[图片] 文件名`` style label for a media object part."""
    for attr in ("name", "file", "path"):
        value = getattr(item, attr, None)
        if isinstance(value, str) and value.strip():
            return _media_label_from_ref(value.strip())
    image_url = getattr(item, "image_url", None)
    if isinstance(image_url, Mapping):
        url = image_url.get("url")
    elif isinstance(image_url, str):
        url = image_url
    else:
        url = getattr(image_url, "url", None)
    if isinstance(url, str) and url.strip():
        return _media_label_from_ref(url.strip())
    url = getattr(item, "url", None)
    if isinstance(url, str) and url.strip():
        return _media_label_from_ref(url.strip())
    return "[图片]"


def _mapping_media_label(item: Mapping) -> str:
    """Build a short label for a dict media part (``image_url``/``audio_url``...)."""
    for key in ("image_url", "audio_url", "file", "url"):
        value = item.get(key)
        if isinstance(value, Mapping):
            url = value.get("url")
            if isinstance(url, str) and url.strip():
                return _media_label_from_ref(url.strip())
        elif isinstance(value, str) and value.strip():
            return _media_label_from_ref(value.strip())
    kind = str(item.get("type", "") or "")
    if "image" in kind:
        return "[图片]"
    if "audio" in kind or "record" in kind:
        return "[音频]"
    if "video" in kind:
        return "[视频]"
    if "file" in kind:
        return "[文件]"
    return f"[{kind or '媒体'}]"


def _media_label_from_ref(ref: str) -> str:
    """Return ``[图片] 文件名`` for a URL/path reference (kept short)."""
    if ref.startswith("data:"):
        return "[图片]"
    basename = ref.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if not basename:
        return "[图片]"
    if len(basename) > 80:
        basename = basename[:80]
    return f"[图片] {basename}"


def strip_recent_self_meme_context(req: Any) -> int:
    """Drop meme_manager-style <recent_sent_meme> text parts from the request.

    Returns the number of removed parts.
    """
    parts = getattr(req, "extra_user_content_parts", None)
    if not isinstance(parts, list):
        return 0
    kept = [part for part in parts if not _is_self_meme_part(part)]
    removed = len(parts) - len(kept)
    if removed:
        parts[:] = kept
    return removed


def mark_recent_self_meme_context(req: Any) -> int:
    """Rewrite meme_manager <recent_sent_meme> parts into explicit bot-owned marks.

    The original block is appended to the user message by the meme plugin and
    only says "本插件刚刚发送", which lets the model think the user just sent a
    sticker. This keeps the block in context (so "刚才那张图" follow-ups still
    work) but rewrites it into ``<bot_sent_meme>`` stating the assistant itself
    sent the image and the user did not. Returns the number of rewritten parts.
    """
    parts = getattr(req, "extra_user_content_parts", None)
    if not isinstance(parts, list):
        return 0
    marked = 0
    for part in parts:
        text = _part_text(part)
        if "<recent_sent_meme>" not in text or "<bot_sent_meme>" in text:
            continue
        prefix, body, tail = _split_meme_block(text)
        new_text = (
            f"{prefix}{_BOT_MEME_MARK_HEADER}\n{body}\n</bot_sent_meme>{tail}"
        )
        if _set_part_text(part, new_text):
            marked += 1
    return marked


def _part_text(part: Any) -> str:
    if isinstance(part, Mapping):
        value = part.get("text", part.get("content", ""))
    else:
        value = getattr(part, "text", None)
    return str(value or "").strip()


def _set_part_text(part: Any, text: str) -> bool:
    if isinstance(part, Mapping):
        if "text" in part:
            part["text"] = text
            return True
        if "content" in part:
            part["content"] = text
            return True
        return False
    try:
        part.text = text
        return True
    except Exception:
        return False


def annotate_memory_media_attribution(req: Any) -> int:
    """In-place fix of misattributed media/expression claims in injected memory.

    livingmemory summaries can claim the user sent the bot's own memes or made
    facial expressions visible inside them. A trailing ``<self_reply_mark>``
    correction can be outranked by that memory text, so this rewrites the
    offending phrases in place (request copy only, never persisted). Returns
    the number of phrases fixed.
    """
    parts = getattr(req, "extra_user_content_parts", None)
    if not isinstance(parts, list):
        return 0
    fixed = 0
    for part in parts:
        text = _part_text(part)
        if not text:
            continue
        if "<memory_attribution_note>" in text:
            continue
        rewritten, part_fixed = _fix_memory_attribution_text(text)
        if part_fixed:
            rewritten = _prepend_memory_role_disclaimer(rewritten)
        if part_fixed and _set_part_text(part, rewritten):
            fixed += part_fixed
    return fixed


def annotate_assistant_expression_claims(req: Any) -> int:
    """In-place fix of the bot's own historical expression/eye claims.

    The bot's past replies can assert the user made an expression that actually
    came from a meme the bot itself sent ("干嘛用这种眼神看着我"). That text is
    stored as assistant history and keeps "self-confirming" the confusion in
    every later request. This annotates such phrases in place (request copy
    only, never persisted). Returns the number of phrases fixed.
    """
    contexts = request_contexts(req)
    if not contexts:
        return 0
    fixed = 0
    for entry in contexts:
        role = str(entry_role(entry) or "").casefold()
        if role not in {"assistant", "bot", "ai"}:
            continue
        content = entry_content(entry)
        if isinstance(content, str):
            rewritten, part_fixed = _fix_assistant_claim_text(content)
            if part_fixed and _set_entry_content(entry, rewritten):
                fixed += part_fixed
            continue
        if not isinstance(content, list):
            continue
        for index, part in enumerate(content):
            if isinstance(part, str):
                rewritten, part_fixed = _fix_assistant_claim_text(part)
                if part_fixed:
                    content[index] = rewritten
                    fixed += part_fixed
            elif isinstance(part, Mapping):
                for key in ("text", "content"):
                    value = part.get(key)
                    if not isinstance(value, str):
                        continue
                    rewritten, part_fixed = _fix_assistant_claim_text(value)
                    if part_fixed:
                        part[key] = rewritten
                        fixed += part_fixed
    return fixed


def _fix_assistant_claim_text(text: str) -> tuple[str, int]:
    fixed = 0

    def _repl(match: re.Match) -> str:
        nonlocal fixed
        fixed += 1
        return match.group(0) + _ASSISTANT_CLAIM_MARK

    for pattern in _ASSISTANT_CLAIM_FIXES:
        text = pattern.sub(_repl, text)
    return text, fixed


def _prepend_memory_role_disclaimer(text: str) -> str:
    """Insert a block-level role disclaimer at the top of the memory part."""
    for marker in ("<RAG-Faiss-Memory>", "<memory_block>", "<memory>"):
        idx = text.find(marker)
        if idx != -1:
            end = idx + len(marker)
            return text[:end] + "\n" + _MEMORY_ROLE_DISCLAIMER + "\n" + text[end:]
    return _MEMORY_ROLE_DISCLAIMER + "\n" + text


def _fix_memory_attribution_text(text: str) -> tuple[str, int]:
    fixed = 0

    for pattern, mark in _MISATTRIBUTION_FIXES:

        def _repl(match: re.Match, mark=mark) -> str:
            nonlocal fixed
            fixed += 1
            return match.group(0) + mark

        text = pattern.sub(_repl, text)
    return text, fixed


def _split_meme_block(text: str) -> tuple[str, str, str]:
    """Split a <recent_sent_meme> part into (prefix, field-body, tail)."""
    start = text.find("<recent_sent_meme>")
    end = text.find("</recent_sent_meme>")
    if start == -1 or end == -1 or end <= start:
        return "", text.strip(), ""
    body = text[start + len("<recent_sent_meme>") : end]
    lines = [
        line.strip()
        for line in body.splitlines()
        if any(keyword in line for keyword in _BOT_MEME_FIELD_KEYWORDS)
    ]
    body_text = "\n".join(lines).strip() or "（无描述）"
    return text[:start].strip(), body_text, text[end + len("</recent_sent_meme>") :]


def append_text_only_media_note(req: Any) -> bool:
    """Append a note clarifying that a text-only message carries no image."""
    return _append_note_part(req, _TEXT_ONLY_MEDIA_NOTE)


def append_referenced_image_note(req: Any) -> bool:
    """Append a note telling the model that a quoted image is the question target."""
    return _append_note_part(req, _REFERENCED_IMAGE_NOTE)


def append_user_media_note(req: Any, event: Any = None) -> bool:
    """Append a note clarifying which media in the request belongs to the user.

    When ``event`` is given, the current turn's media file names are embedded
    so the model can match this turn's attachment exactly instead of guessing
    from older memes.
    """
    return _append_note_part(req, _user_media_note(event))


def _user_media_note(event: Any) -> str:
    names = _current_media_names(event)
    media_desc = f"（文件：{'、'.join(names)}）" if names else ""
    return (
        "<user_media_note>"
        "用户本轮发送了图片/文件，这些图片/文件是用户发送的"
        f"{media_desc}。"
        "上下文历史中 assistant（机器人）消息里的图片/文件是机器人自己发送的，不属于用户；"
        "禁止把机器人历史中自己发送的表情包内容（画面、配字）说成用户本轮发送的。"
        "即使最近刚出现过某张表情包，也不能假定用户本轮又发了同一张；"
        "请优先识别并回答用户本轮发送的图片。"
        "若无法看到用户本轮图片的实际内容，不要根据记忆或历史描述猜测图片内容，"
        "直接说明无法看到。"
        "</user_media_note>"
    )


def _current_media_names(event: Any) -> list[str]:
    chain = get_message_chain(event)
    if not chain:
        return []
    names: list[str] = []
    for comp in chain:
        if not is_media_part(comp):
            continue
        desc = _describe_media_component(comp)
        if desc and desc not in names:
            names.append(desc)
    return names


def _describe_media_component(comp: Any) -> str:
    name = ""
    for attr in ("name", "file", "url", "path"):
        value = getattr(comp, attr, None)
        if isinstance(value, str) and value.strip():
            name = value.strip()
            break
    kind = "图片" if isinstance(comp, Image) else "文件"
    if name:
        basename = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        return f"[{kind}] {basename}"
    return f"[{kind}]"


def _append_note_part(req: Any, note: str) -> bool:
    parts = getattr(req, "extra_user_content_parts", None)
    if not isinstance(parts, list):
        return False
    part = _make_text_part(note)
    if part is None:
        return False
    parts.append(part)
    return True


def _is_self_meme_part(part: Any) -> bool:
    if isinstance(part, Mapping):
        text = part.get("text", part.get("content", ""))
    else:
        text = getattr(part, "text", None)
    if not isinstance(text, str):
        text = ""
    return "<recent_sent_meme>" in text or "</recent_sent_meme>" in text


def _make_text_part(text: str) -> Any | None:
    try:
        from astrbot.core.agent.message import TextPart
    except Exception:
        return SimpleNamespace(text=text)
    try:
        part = TextPart(text=text)
    except Exception:
        return None
    mark_as_temp = getattr(part, "mark_as_temp", None)
    if callable(mark_as_temp):
        try:
            part = mark_as_temp()
        except Exception:
            pass
    return part


__all__ = [
    "MAX_MARK_ENTRIES",
    "SelfReplyMarker",
    "annotate_assistant_expression_claims",
    "annotate_memory_media_attribution",
    "append_text_only_media_note",
    "append_referenced_image_note",
    "append_user_media_note",
    "attach_quoted_images",
    "has_referenced_image",
    "has_user_media",
    "mark_current_prompt_media_boundary",
    "mark_context_media_ownership",
    "mark_recent_self_meme_context",
    "strip_recent_self_meme_context",
]
