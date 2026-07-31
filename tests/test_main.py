"""插件入口集成测试：回复中的 Markdown 语法在发送前被过滤。"""

import asyncio
from types import SimpleNamespace

from astrbot.api.message_components import Plain

from main import LanguageLogicOptimizer


class FakeContext:
    config = {}


class FakeEvent:
    def __init__(self, result):
        self._result = result

    def get_result(self):
        return self._result


def make_optimizer():
    optimizer = object.__new__(LanguageLogicOptimizer)
    optimizer.config = {}
    optimizer.context = FakeContext()
    return optimizer


def test_strips_markdown_from_reply():
    optimizer = make_optimizer()
    result = SimpleNamespace(chain=[Plain("看 **赛程**：[链接](https://e.com)")])

    asyncio.run(optimizer.on_decorating_result(FakeEvent(result)))

    assert result.chain[0].text == "看 赛程：链接"


def test_preserves_code_content():
    optimizer = make_optimizer()
    result = SimpleNamespace(chain=[Plain("运行 `value = **raw**`")])

    asyncio.run(optimizer.on_decorating_result(FakeEvent(result)))

    assert result.chain[0].text == "运行 value = **raw**"


def test_escaped_backticks_become_literal_backticks():
    optimizer = make_optimizer()
    result = SimpleNamespace(chain=[Plain(r"\`code\`")])

    asyncio.run(optimizer.on_decorating_result(FakeEvent(result)))

    assert result.chain[0].text == "`code`"


def test_coalesces_adjacent_plain_components():
    optimizer = make_optimizer()
    result = SimpleNamespace(chain=[Plain("**粗"), Plain("体**")])

    asyncio.run(optimizer.on_decorating_result(FakeEvent(result)))

    assert result.chain[0].text == "粗体"
    assert result.chain[1].text == ""


def test_leaves_non_plain_components_alone():
    optimizer = make_optimizer()
    other = object()
    result = SimpleNamespace(chain=[Plain("**x**"), other])

    asyncio.run(optimizer.on_decorating_result(FakeEvent(result)))

    assert result.chain[0].text == "x"
    assert result.chain[1] is other


def test_plain_text_reply_is_unchanged():
    optimizer = make_optimizer()
    result = SimpleNamespace(chain=[Plain("你好，这是一条普通消息。")])

    asyncio.run(optimizer.on_decorating_result(FakeEvent(result)))

    assert result.chain[0].text == "你好，这是一条普通消息。"


def test_all_markdown_message_keeps_original_text():
    optimizer = make_optimizer()
    result = SimpleNamespace(chain=[Plain("---")])

    asyncio.run(optimizer.on_decorating_result(FakeEvent(result)))

    assert result.chain[0].text == "---"


def test_missing_result_is_ignored():
    optimizer = make_optimizer()

    asyncio.run(optimizer.on_decorating_result(FakeEvent(None)))
