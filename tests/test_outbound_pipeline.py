import asyncio
from types import SimpleNamespace

from _astrbot_plugin_filter_test.outbound_pipeline import OutboundTextPipeline


def make_pipeline(**config):
    values = {
        "enable_de_ai_flavor": False,
        "enable_llm_style": False,
        "enable_llm_segment": False,
    }
    values.update(config)
    return OutboundTextPipeline(
        context=SimpleNamespace(),
        get_config=lambda key, default=None: values.get(key, default),
        get_guard_terms=lambda: ["内部词"],
    )


def test_pipeline_keeps_existing_cleanup_stages_without_main_orchestration():
    pipeline = make_pipeline()

    result = asyncio.run(
        pipeline.process(
            "调用 web_search 搜索一下。\n\n**结果如下**：密码：12345678",
            SimpleNamespace(),
        )
    )

    assert "web_search" not in result.text
    assert "**" not in result.text
    assert "[REDACTED]" in result.text
    assert result.changed is True


def test_pipeline_marks_content_guard_block_without_throwing():
    pipeline = make_pipeline(enable_content_guard=True)

    result = asyncio.run(pipeline.process("这里包含内部词", SimpleNamespace()))

    assert result.guard_blocked is True
    assert result.text
