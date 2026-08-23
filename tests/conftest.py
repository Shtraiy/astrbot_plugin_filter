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
        "task_commitment_guard",
        "main",
    ):
        module = importlib.import_module(f"{package_name}.{module_name}")
        sys.modules[module_name] = module


_install_astrbot_stubs()
_import_plugin_as_package()

import asyncio
from types import SimpleNamespace

from astrbot.api.message_components import Image, Plain

from main import LanguageLogicOptimizer


class FakeContext:
    def __init__(self):
        self.sent = []

    async def send_message(self, origin, chain):
        self.sent.append((origin, chain))


class FakeEvent:
    def __init__(
        self,
        sender="u1",
        origin="group:1",
        text="",
        *,
        wake=True,
        request_id=None,
        chain=None,
        group_id="1",
    ):
        self.sender = sender
        self.unified_msg_origin = origin
        self.group_id = group_id
        self.message_str = text
        self.request_id = request_id
        self._wake = wake
        self.is_at_or_wake_command = wake
        self.stopped = False
        self._result = None
        self._extras = {}
        self._chain = chain if chain is not None else ([Plain(text)] if text else [])
        self.message_obj = SimpleNamespace(message=self._chain)

    def get_sender_id(self):
        return self.sender

    def get_messages(self):
        return self.message_obj.message

    def is_wake_up(self):
        return self._wake

    def is_private_chat(self):
        return "FriendMessage" in str(self.unified_msg_origin)

    def stop_event(self):
        self.stopped = True

    def is_stopped(self):
        return self.stopped

    def set_extra(self, key, value):
        self._extras[key] = value

    def get_extra(self, key, default=None):
        return self._extras.get(key, default)

    def set_result(self, result):
        self._result = result

    def get_result(self):
        return self._result


def make_optimizer(**overrides):
    optimizer = object.__new__(LanguageLogicOptimizer)
    optimizer.config = {
        "enable_message_merge": True,
        "merge_window_seconds": 6.0,
        "merge_max_messages": 5,
        "merge_max_chars": 2000,
        "merge_ignore_prefixes": "/,!",
        "merge_include_media": True,
        "enable_task_execution_guard": True,
        "enable_self_reply_mark": True,
        "self_reply_mark_minutes": 5.0,
        "strip_recent_self_meme_context": True,
        "guard_own_media_attribution": True,
        "enable_content_guard": True,
        "content_guard_mode": "balanced",
        "content_guard_block_terms": "",
        "onboarding_guard_minutes": 30.0,
        "onboarding_guard_messages": 20,
    }
    optimizer.config.update(overrides)
    optimizer.context = FakeContext()
    optimizer._onboarding_guard = None
    optimizer._message_merger = None
    optimizer._reply_coordinator = None
    optimizer._self_reply_marker = None
    optimizer._get_merge_window_seconds = lambda: 0.05
    return optimizer
