import asyncio
import json
from types import SimpleNamespace

from _astrbot_plugin_filter_test.smart_segment import (
    SEGMENT_PROMPT,
    parse_segment_json,
    rule_split,
    split_reply,
    validate_segments,
)


def test_prompt_contains_text_placeholder():
    assert "{text}" in SEGMENT_PROMPT


def test_parse_segment_json_accepts_plain_and_fenced_json():
    assert parse_segment_json('["a", "b"]') == ["a", "b"]
    assert parse_segment_json('```json\n["a", "b"]\n```') == ["a", "b"]
    assert parse_segment_json("") is None
    assert parse_segment_json("not json") is None
    assert parse_segment_json('{"a": 1}') is None
    assert parse_segment_json("[1, 2]") is None  # 非字符串元素


def test_validate_segments_enforces_zero_rewrite():
    assert validate_segments("你好世界", ["你好", "世界"], 3) is True
    assert validate_segments("你好世界", ["你好", "世界！"], 3) is False  # 改动
    assert validate_segments("你好世界", ["你好世界"], 3) is False  # 只有 1 段
    assert validate_segments("你好世界", ["你好", "世界", "多", "四段"], 3) is False
    assert validate_segments("你好世界", ["你好", "  "], 3) is False  # 空段
    assert validate_segments("```\ncode\n```", ["```\ncode\n```"], 3) is False
    assert validate_segments("```\ncode\n```", ["```\ncode", "\n```"], 3) is False


def test_rule_split_caps_and_preserves_content():
    text = "第一句。第二句！第三句？"
    parts = rule_split(text, 3)
    assert 2 <= len(parts) <= 3
    assert "".join(parts) == text

    long = "\n\n".join(f"第{i}段" for i in range(5))
    capped = rule_split(long, 3)
    assert len(capped) == 3
    assert "".join(part.replace("\n\n", "") for part in capped) == long.replace(
        "\n\n", ""
    )


def test_split_reply_skips_short_text():
    context = SimpleNamespace()
    assert asyncio.run(split_reply("太短", context, lambda k, d: d)) is None


def test_split_reply_llm_ok_with_validation():
    text = "这是第一段比较长的内容。这里是第二段比较长的内容。"
    context = SimpleNamespace(
        llm_generate=async_stub('["这是第一段比较长的内容。", "这里是第二段比较长的内容。"]')
    )
    config = {
        "segment_provider_id": "p1",
        "segment_min_chars": 20,
        "segment_max_messages": 3,
        "segment_timeout_seconds": 5,
    }
    result = asyncio.run(
        split_reply(text, context, lambda k, d: config.get(k, d))
    )
    assert result == ["这是第一段比较长的内容。", "这里是第二段比较长的内容。"]


def test_split_reply_llm_invalid_falls_back_to_rules():
    text = "这是第一段比较长的内容。这里是第二段比较长的内容。"
    context = SimpleNamespace(llm_generate=async_stub("['改了原文']"))
    config = {
        "segment_provider_id": "p1",
        "segment_min_chars": 20,
        "segment_max_messages": 3,
        "segment_timeout_seconds": 5,
    }
    result = asyncio.run(
        split_reply(text, context, lambda k, d: config.get(k, d))
    )
    assert result is not None
    assert "".join(result) == text


def test_split_reply_without_provider_uses_rules():
    context = SimpleNamespace()
    text = "这是第一段比较长的内容。这里是第二段比较长的内容。"
    config = {
        "segment_provider_id": "",
        "segment_min_chars": 20,
        "segment_max_messages": 3,
        "segment_timeout_seconds": 5,
    }
    result = asyncio.run(
        split_reply(text, context, lambda k, d: config.get(k, d))
    )
    assert result is not None
    assert "".join(result) == text


def test_split_reply_respects_llm_single_segment_no_split():
    """模型明确判断无需分段时保持单条发送，不被规则按标点切分（诗歌场景）。"""
    text = "静夜思\n李白\n床前明月光，疑是地上霜。\n举头望明月，低头思故乡。"
    context = SimpleNamespace(
        llm_generate=async_stub(json.dumps([text], ensure_ascii=False))
    )
    config = {
        "segment_provider_id": "p1",
        "segment_min_chars": 20,
        "segment_max_messages": 3,
        "segment_timeout_seconds": 5,
    }

    result = asyncio.run(split_reply(text, context, lambda k, d: config.get(k, d)))

    assert result is None


def test_split_reply_llm_single_segment_with_rewrite_falls_back():
    """模型改写内容的单段结果仍视为失败，回退规则分段保证内容不丢。"""
    text = "这是第一句比较长的内容。这是第二句比较长的内容。"
    context = SimpleNamespace(llm_generate=async_stub('["改写了原文的单段"]'))
    config = {
        "segment_provider_id": "p1",
        "segment_min_chars": 20,
        "segment_max_messages": 3,
        "segment_timeout_seconds": 5,
    }

    result = asyncio.run(split_reply(text, context, lambda k, d: config.get(k, d)))

    assert result is not None
    assert "".join(result) == text


def test_rule_split_keeps_newline_separated_poem_lines_together():
    """规则回退不按句号逐行切分换行分隔的诗句，且拼接后与原文一致。"""
    text = "静夜思\n李白\n床前明月光，疑是地上霜。\n举头望明月，低头思故乡。"

    parts = rule_split(text, 3)

    assert parts == [text]


def async_stub(raw: str):
    async def _stub(*args, **kwargs):
        return SimpleNamespace(completion_text=raw)

    return _stub
