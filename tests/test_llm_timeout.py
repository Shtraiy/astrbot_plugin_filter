import asyncio
from types import SimpleNamespace

from segmentation import apply_segmentation_and_style


def test_llm_style_timeout_falls_back_to_original_text():
    class SlowContext:
        async def llm_generate(self, **kwargs):
            await asyncio.sleep(0.05)
            return SimpleNamespace(completion_text="never returned")

    text = "这是一条在模型超时后也必须发出的消息"
    config = {
        "enable_llm_style": True,
        "llm_provider_id": "slow-provider",
        "llm_timeout_seconds": 0.01,
    }

    result = asyncio.run(
        apply_segmentation_and_style(
            text,
            SlowContext(),
            lambda key, default: config.get(key, default),
        )
    )

    assert result == text
