import asyncio
from types import SimpleNamespace
from unittest import mock

from _astrbot_plugin_filter_test.private_companion_adapter import (
    PrivateCompanionAdapter,
)


def test_adapter_recognizes_only_explicit_proactive_markers():
    proactive = SimpleNamespace(private_companion_proactive_framework=True)
    ordinary = SimpleNamespace(private_companion_proactive_framework=False)

    assert PrivateCompanionAdapter.is_proactive_event(proactive)
    assert not PrivateCompanionAdapter.is_proactive_event(ordinary)
    assert not PrivateCompanionAdapter.is_proactive_event(None)


def test_adapter_cancel_is_optional_and_best_effort():
    calls = []

    class Api:
        async def cancel_proactive_chat(self, session_id, *, token=""):
            calls.append((session_id, token))

    module = SimpleNamespace(get_private_companion_api=lambda: Api())
    adapter = PrivateCompanionAdapter()

    def import_module(_name):
        return module

    async def scenario():
        with mock.patch.object(
            adapter.module_loader,
            "import_module",
            side_effect=import_module,
        ):
            return await adapter.cancel("group:1", "token-1")

    assert asyncio.run(scenario())
    assert calls == [("group:1", "token-1")]


def test_adapter_returns_false_when_companion_is_unavailable():
    adapter = PrivateCompanionAdapter()

    async def scenario():
        with mock.patch.object(
            adapter.module_loader,
            "import_module",
            side_effect=ImportError("not installed"),
        ):
            return await adapter.cancel("group:1", "token-1")

    assert not asyncio.run(scenario())
