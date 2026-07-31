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

    event_mod = types.ModuleType("astrbot.api.event")

    class AstrMessageEvent:
        pass

    class Filter:
        def __getattr__(self, _name):
            return lambda *args, **kwargs: (lambda function: function)

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
        "pipelines",
        "main",
    ):
        module = importlib.import_module(f"{package_name}.{module_name}")
        sys.modules[module_name] = module


_install_astrbot_stubs()
_import_plugin_as_package()
