"""Local test bootstrap for environments without AstrBot installed."""

from __future__ import annotations

import importlib
import logging
import sys
import types
from pathlib import Path


def _install_astrbot_stubs() -> None:
    try:
        importlib.import_module("astrbot.api.message_components")
        return
    except ModuleNotFoundError:
        pass

    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    api.logger = logging.getLogger("astrbot-test")

    components_mod = types.ModuleType("astrbot.api.message_components")

    class Plain:
        def __init__(self, text):
            self.text = text

    components_mod.Plain = Plain

    class Image:
        def __init__(self, url=""):
            self.url = url

    components_mod.Image = Image

    class File:
        def __init__(self, name=""):
            self.name = name

    components_mod.File = File

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

    class EventMessageType:
        ALL = "all"
        PRIVATE_MESSAGE = "private"
        GROUP_MESSAGE = "group"

    class Filter:
        _DECORATORS = {
            "event_message_type",
            "on_waiting_llm_request",
            "on_llm_request",
            "on_llm_response",
            "on_decorating_result",
            "after_message_sent",
        }

        def __getattr__(self, name):
            if name not in self._DECORATORS:
                raise AttributeError(
                    f"astrbot.api.event.filter has no attribute {name!r}"
                )
            return lambda *args, **kwargs: (lambda function: function)

    Filter.EventMessageType = EventMessageType
    event_mod.AstrMessageEvent = AstrMessageEvent
    event_mod.filter = Filter()

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


def _import_plugin_as_package() -> None:
    root = Path(__file__).resolve().parents[1]
    package_name = "_astrbot_plugin_filter_test"
    package = sys.modules.get(package_name)
    if package is None:
        package = types.ModuleType(package_name)
        package.__path__ = [str(root)]
        sys.modules[package_name] = package

    for module_name in (
        "content_guard",
        "event_access",
        "interruption_guard",
        "merge_guards",
        "merge_window",
        "onboarding_guard",
        "reply_coordinator",
        "self_reply_marker",
        "main",
    ):
        module = importlib.import_module(f"{package_name}.{module_name}")
        sys.modules[module_name] = module


_install_astrbot_stubs()
_import_plugin_as_package()
