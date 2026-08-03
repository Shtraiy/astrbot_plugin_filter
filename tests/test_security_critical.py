import asyncio
import importlib
import logging
import os
import sys
import types
import unittest
from unittest.mock import patch


def _install_astrbot_stubs() -> type:
    try:
        from astrbot.api.message_components import Plain

        return Plain
    except ModuleNotFoundError:
        pass

    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    api.logger = logging.getLogger("astrbot-test")

    all_mod = types.ModuleType("astrbot.api.all")

    class MessageChain:
        def __init__(self):
            self.chain = []

        def message(self, value):
            self.chain.append(Plain(value))
            return self

        def file_image(self, value):
            self.chain.append(value)
            return self

    all_mod.MessageChain = MessageChain

    event_mod = types.ModuleType("astrbot.api.event")

    class AstrMessageEvent:
        pass

    class Filter:
        def __getattr__(self, _name):
            return lambda *args, **kwargs: (lambda function: function)

    event_mod.AstrMessageEvent = AstrMessageEvent
    event_mod.filter = Filter()

    components_mod = types.ModuleType("astrbot.api.message_components")

    class Plain:
        def __init__(self, text):
            self.text = text

    components_mod.Plain = Plain

    star_mod = types.ModuleType("astrbot.api.star")

    class Star:
        def __init__(self, context):
            self.context = context

    star_mod.Context = object
    star_mod.Star = Star

    sys.modules.update(
        {
            "astrbot": astrbot,
            "astrbot.api": api,
            "astrbot.api.all": all_mod,
            "astrbot.api.event": event_mod,
            "astrbot.api.message_components": components_mod,
            "astrbot.api.star": star_mod,
        }
    )
    return Plain


Plain = _install_astrbot_stubs()

_PACKAGE_NAME = "_critical_security_plugin"
if _PACKAGE_NAME not in sys.modules:
    package = types.ModuleType(_PACKAGE_NAME)
    package.__path__ = [os.path.dirname(os.path.dirname(__file__))]
    sys.modules[_PACKAGE_NAME] = package

main = importlib.import_module(f"{_PACKAGE_NAME}.main")
segmentation = importlib.import_module(f"{_PACKAGE_NAME}.segmentation")
outbound_pipeline = importlib.import_module(f"{_PACKAGE_NAME}.outbound_pipeline")


class FakeContext:
    config = {}

    def __init__(self):
        self.sent = []

    async def send_message(self, origin, chain):
        self.sent.append((origin, chain))


class FakeEvent:
    unified_msg_origin = "group:1"
    group_id = "1"

    def __init__(self, result):
        self._result = result

    def get_result(self):
        return self._result


def make_optimizer(**config):
    optimizer = object.__new__(main.LanguageLogicOptimizer)
    optimizer.context = FakeContext()
    optimizer.config = {
        "enable_llm_style": False,
        "enable_llm_segment": False,
        "enable_de_ai_flavor": False,
        "enable_image_render": False,
        "multi_message": False,
        **config,
    }
    optimizer._pending_tasks = set()
    optimizer._reply_locks = {}
    optimizer._gates = {}
    optimizer._pending_send = None
    optimizer._pending_sends = {}
    optimizer._onboarding_states = {}
    return optimizer


class CriticalOutputSecurityTests(unittest.TestCase):
    def test_adjacent_plain_components_are_guarded_as_one_visible_message(self):
        result = types.SimpleNamespace(chain=[Plain("敏感"), Plain("词")])
        optimizer = make_optimizer(
            enable_content_guard=True,
            content_guard_block_terms="敏感词",
        )

        asyncio.run(optimizer.on_decorating_result(FakeEvent(result)))

        visible_text = "".join(
            component.text for component in result.chain if isinstance(component, Plain)
        )
        self.assertEqual(main.SAFE_REPLY, visible_text)

    def test_pipeline_exception_replaces_complete_chain_with_safe_reply(self):
        leaked_secret = "sk-proj-1234567890abcdef"
        result = types.SimpleNamespace(chain=[Plain(leaked_secret)])
        optimizer = make_optimizer()

        with patch.object(main.logger, "error"), patch.object(
            outbound_pipeline,
            "clean_garbage",
            side_effect=RuntimeError("forced pipeline failure"),
        ):
            asyncio.run(optimizer.on_decorating_result(FakeEvent(result)))

        self.assertEqual(1, len(result.chain))
        self.assertIsInstance(result.chain[0], Plain)
        self.assertEqual(main.SAFE_REPLY, result.chain[0].text)
        self.assertNotIn(leaked_secret, result.chain[0].text)

    def test_multi_message_preparation_bounds_candidates_before_fuzzy_dedupe(self):
        prepare = getattr(segmentation, "prepare_multi_message_parts", None)
        self.assertIsNotNone(
            prepare,
            "prepare_multi_message_parts must own the bounded split/dedupe boundary",
        )
        text = "\n\n".join(f"paragraph-{index}" for index in range(1001))
        observed_candidate_counts = []

        def observe_candidates(paragraphs):
            observed_candidate_counts.append(len(paragraphs))
            return paragraphs

        with patch.object(
            segmentation,
            "dedupe_similar_paragraphs",
            side_effect=observe_candidates,
        ):
            parts = prepare(text)

        self.assertLessEqual(len(parts), 5)
        self.assertEqual([5], observed_candidate_counts)

    def test_multi_message_preparation_caps_total_source_characters(self):
        text = "x" * (segmentation.MAX_LAYOUT_CHARS + 1)

        parts = segmentation.prepare_multi_message_parts(text)

        self.assertEqual(1, len(parts))
        self.assertEqual(segmentation.MAX_LAYOUT_CHARS, len(parts[0]))

    def test_send_followups_never_sends_more_than_four_messages(self):
        context = FakeContext()
        paragraphs = [f"part-{index}" for index in range(20)]

        asyncio.run(
            segmentation.send_followups(
                context,
                "group:1",
                paragraphs,
                0,
                0,
            )
        )

        self.assertEqual(4, len(context.sent))


if __name__ == "__main__":
    unittest.main()
