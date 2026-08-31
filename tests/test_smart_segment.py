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


def test_split_reply_mechanical_below_llm_threshold_without_llm_call():
    """低于 LLM 阈值但达到机械下限时走机械分段，且不调用 LLM。"""
    text = "这是第一句比较长的内容。这是第二句比较长的内容。"  # 22 字 < 80
    calls = []

    async def record_stub(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(completion_text='["不应被调用"]')

    config = {
        "segment_provider_id": "p1",
        "segment_min_chars": 80,
        "segment_mechanical_min_chars": 20,
        "segment_max_messages": 3,
        "segment_timeout_seconds": 5,
        "segment_strip_chars": "。～~",
    }

    result = asyncio.run(
        split_reply(text, SimpleNamespace(llm_generate=record_stub), lambda k, d: config.get(k, d))
    )

    assert calls == []
    assert result == ["这是第一句比较长的内容", "这是第二句比较长的内容"]


def test_split_reply_below_mechanical_floor_single_message():
    """低于机械下限时不调 LLM、不机械切分，单条发送。"""
    calls = []

    async def record_stub(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(completion_text="")

    config = {
        "segment_provider_id": "p1",
        "segment_min_chars": 80,
        "segment_mechanical_min_chars": 20,
        "segment_max_messages": 3,
        "segment_timeout_seconds": 5,
        "segment_strip_chars": "。～~",
    }

    result = asyncio.run(
        split_reply("好的。", SimpleNamespace(llm_generate=record_stub), lambda k, d: config.get(k, d))
    )

    assert result is None
    assert calls == []


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
    # 机械回退默认删除分段分隔符
    assert result == ["这是第一段比较长的内容", "这里是第二段比较长的内容"]


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
    assert result == ["这是第一段比较长的内容", "这里是第二段比较长的内容"]


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
    assert result == ["这是第一句比较长的内容", "这是第二句比较长的内容"]


def test_rule_split_keeps_newline_separated_poem_lines_together():
    """规则回退不按句号逐行切分换行分隔的诗句，且拼接后与原文一致。"""
    text = "静夜思\n李白\n床前明月光，疑是地上霜。\n举头望明月，低头思故乡。"

    parts = rule_split(text, 3)

    assert parts == [text]


def test_mechanical_fallback_strips_delimiters():
    text = "这是第一句比较长的内容。这是第二句比较长的内容~这是第三句比较长的内容"
    context = SimpleNamespace()
    config = {
        "segment_provider_id": "",
        "segment_min_chars": 20,
        "segment_max_messages": 3,
        "segment_timeout_seconds": 5,
        "segment_strip_chars": "。～~",
    }

    result = asyncio.run(split_reply(text, context, lambda k, d: config.get(k, d)))

    assert result == [
        "这是第一句比较长的内容",
        "这是第二句比较长的内容",
        "这是第三句比较长的内容",
    ]


def test_mechanical_fallback_strip_disabled_preserves_content():
    text = "这是第一句比较长的内容。这是第二句比较长的内容。"
    context = SimpleNamespace()
    config = {
        "segment_provider_id": "",
        "segment_min_chars": 20,
        "segment_max_messages": 3,
        "segment_timeout_seconds": 5,
        "segment_strip_chars": "",
    }

    result = asyncio.run(split_reply(text, context, lambda k, d: config.get(k, d)))

    assert result is not None
    assert "".join(result) == text


def test_llm_path_not_stripped():
    """LLM 分段路径保持零改动，不删除分段符号。"""
    first = "这是第一段比较长的内容。"
    second = "这里是第二段比较长的内容。"
    text = first + second
    context = SimpleNamespace(
        llm_generate=async_stub(
            json.dumps([first, second], ensure_ascii=False)
        )
    )
    config = {
        "segment_provider_id": "p1",
        "segment_min_chars": 20,
        "segment_max_messages": 3,
        "segment_timeout_seconds": 5,
        "segment_strip_chars": "。～~",
    }

    result = asyncio.run(split_reply(text, context, lambda k, d: config.get(k, d)))

    assert result == [first, second]


def async_stub(raw: str):
    async def _stub(*args, **kwargs):
        return SimpleNamespace(completion_text=raw)

    return _stub
