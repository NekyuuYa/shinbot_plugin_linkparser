"""Plugin entry wiring tests.

Runs ``setup()`` against a fake Plugin object and lightweight fake framework
modules (the real ``shinbot.core.dispatch`` package pulls in a large dependency
tree, so only the symbols ``setup()`` touches are faked). No network involved.
"""

from __future__ import annotations

import asyncio
import sys
import types
from types import SimpleNamespace

import pytest

import shinbot_plugin_linkparser as plugin

BV = "BV1xx411c7mD"


class FakeLogger:
    """Logger stand-in accepting the logging calls setup makes."""

    def info(self, *_args: object, **_kwargs: object) -> None:
        pass

    def warning(self, *_args: object, **_kwargs: object) -> None:
        pass

    def debug(self, *_args: object, **_kwargs: object) -> None:
        pass

    def exception(self, *_args: object, **_kwargs: object) -> None:
        pass


class FakePlugin:
    """Minimal Plugin stand-in exposing the registration surface setup uses."""

    def __init__(self, data_dir) -> None:
        self.plugin_id = "shinbot_plugin_linkparser"
        self.data_dir = str(data_dir)
        self.logger = FakeLogger()
        self.routes: list[tuple] = []
        self.commands: list[tuple] = []

    def on_route(self, condition, **kwargs):
        def decorator(func):
            self.routes.append((condition, kwargs, func))
            return func

        return decorator

    def on_command(self, *_args: object, **_kwargs: object):
        def decorator(func):
            self.commands.append((_args, _kwargs, func))
            return func

        return decorator


@pytest.fixture
def fake_framework(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install fake ``shinbot.core.dispatch.routing`` symbols for setup()."""
    class RouteCondition:
        def __init__(self, **kwargs) -> None:
            self.__dict__.update(kwargs)

    class RouteMatchMode:
        NORMAL = "normal"
        EXCLUSIVE = "exclusive"
        FALLBACK = "fallback"
        OBSERVE = "observe"

    routing = types.ModuleType("shinbot.core.dispatch.routing")
    routing.RouteCondition = RouteCondition
    routing.RouteMatchMode = RouteMatchMode

    dispatch = types.ModuleType("shinbot.core.dispatch")
    dispatch.routing = routing
    core = types.ModuleType("shinbot.core")
    core.dispatch = dispatch

    monkeypatch.setitem(sys.modules, "shinbot.core", core)
    monkeypatch.setitem(sys.modules, "shinbot.core.dispatch", dispatch)
    monkeypatch.setitem(sys.modules, "shinbot.core.dispatch.routing", routing)
    monkeypatch.setattr(
        plugin,
        "_load_plugin_config",
        lambda plugin_id: plugin.LinkParserPluginConfig(),
    )
    yield


def _element(element_type: str, attrs: dict | None = None) -> dict:
    return {"type": element_type, "attrs": attrs or {}, "children": []}


def test_setup_registers_normal_route_and_matcher(
    fake_framework, tmp_path
) -> None:
    fake = FakePlugin(tmp_path)
    plugin.setup(fake)

    assert len(fake.routes) == 1
    condition, kwargs, _handler = fake.routes[0]
    assert kwargs["rule_id"] == "shinbot_plugin_linkparser.parse"
    assert kwargs["match_mode"] == "normal"
    assert condition.event_types == frozenset({"message-created"})

    matcher = condition.custom_matcher
    event = SimpleNamespace(self_id="123")
    link_message = SimpleNamespace(
        elements=[_element("text", {"content": "看 https://b23.tv/abC12"})]
    )
    assert matcher(event, link_message) is True

    plain_message = SimpleNamespace(elements=[_element("text", {"content": "你好"})])
    assert matcher(event, plain_message) is False

    asyncio.run(plugin.on_disable(fake))


def test_setup_prunes_cache_dir(fake_framework, tmp_path, monkeypatch) -> None:
    videos_dir = tmp_path / "videos"
    videos_dir.mkdir()
    stale = videos_dir / "stale.mp4"
    stale.write_bytes(b"old")
    fresh = videos_dir / "fresh.mp4"
    fresh.write_bytes(b"new")
    # make fresh newest
    import os
    import time

    now = time.time()
    os.utime(stale, (now - 100, now - 100))
    os.utime(fresh, (now, now))

    monkeypatch.setattr(
        plugin,
        "_load_plugin_config",
        lambda plugin_id: plugin.LinkParserPluginConfig(cache_max_files=1),
    )
    fake = FakePlugin(tmp_path)
    plugin.setup(fake)

    remaining = sorted(path.name for path in videos_dir.glob("*.mp4"))
    assert remaining == ["fresh.mp4"]
    asyncio.run(plugin.on_disable(fake))


def test_config_defaults_keep_sent_cache() -> None:
    config = plugin.LinkParserPluginConfig()
    assert config.delete_after_send is False
    assert config.cache_max_files == 50
