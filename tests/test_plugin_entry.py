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
from shinbot_plugin_linkparser.session_state import SessionStateStore

BV = "BV1xx411c7mD"
SELF = "123"


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
        self.database = None
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


def _element(element_type: str, attrs: dict | None = None, children: list | None = None):
    return {"type": element_type, "attrs": attrs or {}, "children": children or []}


def _link_message() -> SimpleNamespace:
    return SimpleNamespace(
        elements=[_element("text", {"content": "看 https://b23.tv/abC12"})]
    )


def _context(session_id: str = "group:1") -> SimpleNamespace:
    return SimpleNamespace(session=SimpleNamespace(id=session_id))


def test_setup_registers_route_and_command(fake_framework, tmp_path) -> None:
    fake = FakePlugin(tmp_path)
    plugin.setup(fake)

    assert len(fake.routes) == 1
    assert len(fake.commands) == 1

    condition, route_kwargs, _handler = fake.routes[0]
    assert route_kwargs["rule_id"] == "shinbot_plugin_linkparser.parse"
    assert route_kwargs["match_mode"] == "normal"
    assert condition.event_types == frozenset({"message-created"})

    command_args, command_kwargs, _command_handler = fake.commands[0]
    assert command_args[0] == "parser"
    assert "linkparser" in command_kwargs["aliases"]
    assert command_kwargs["permission"] == "cmd.linkparser"

    # default_mode off → link messages do not match
    matcher = condition.custom_matcher
    assert matcher(SimpleNamespace(self_id=SELF), _link_message(), _context()) is False

    asyncio.run(plugin.on_disable(fake))


def test_setup_matcher_mode_always(fake_framework, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        plugin,
        "_load_plugin_config",
        lambda plugin_id: plugin.LinkParserPluginConfig(default_mode="always"),
    )
    fake = FakePlugin(tmp_path)
    plugin.setup(fake)

    matcher = fake.routes[0][0].custom_matcher
    event = SimpleNamespace(self_id=SELF)
    assert matcher(event, _link_message(), _context()) is True

    plain = SimpleNamespace(elements=[_element("text", {"content": "你好"})])
    assert matcher(event, plain, _context()) is False

    asyncio.run(plugin.on_disable(fake))


def test_setup_matcher_mode_at_needs_mention(fake_framework, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        plugin,
        "_load_plugin_config",
        lambda plugin_id: plugin.LinkParserPluginConfig(default_mode="at"),
    )
    fake = FakePlugin(tmp_path)
    plugin.setup(fake)

    matcher = fake.routes[0][0].custom_matcher
    event = SimpleNamespace(self_id=SELF)
    # link without @bot → left to the agent
    assert matcher(event, _link_message(), _context()) is False

    mentioned = SimpleNamespace(
        elements=[
            _element("at", {"id": SELF}),
            _element("text", {"content": "看 https://b23.tv/abC12"}),
        ]
    )
    assert matcher(event, mentioned, _context()) is True

    # @bot without any link/quote → not matched (not swallowed)
    question = SimpleNamespace(
        elements=[_element("at", {"id": SELF}), _element("text", {"content": "在吗"})]
    )
    assert matcher(event, question, _context()) is False

    asyncio.run(plugin.on_disable(fake))


def test_parser_command_sets_levels(fake_framework, tmp_path) -> None:
    state = SessionStateStore(tmp_path / "session_state.json", default_mode="off")

    class Ctx:
        session_id = "group:1"

        def __init__(self) -> None:
            self.sent: list[str] = []

        async def send(self, content: str) -> None:
            self.sent.append(content)

    async def run() -> None:
        ctx = Ctx()
        await plugin._handle_parser_command(ctx, "at", state=state, logger=FakeLogger())
        assert state.mode("group:1") == "at"
        assert "at" in ctx.sent[-1]

        await plugin._handle_parser_command(ctx, "always", state=state, logger=FakeLogger())
        assert state.mode("group:1") == "always"
        assert "always" in ctx.sent[-1]

        # "on" is an alias for always
        await plugin._handle_parser_command(ctx, "on", state=state, logger=FakeLogger())
        assert state.mode("group:1") == "always"

        await plugin._handle_parser_command(ctx, "status", state=state, logger=FakeLogger())
        assert "always" in ctx.sent[-1]
        assert "off" in ctx.sent[-1]  # default shown

        await plugin._handle_parser_command(ctx, "off", state=state, logger=FakeLogger())
        assert state.mode("group:1") == "off"
        assert "off" in ctx.sent[-1]

        await plugin._handle_parser_command(ctx, "bogus", state=state, logger=FakeLogger())
        assert "off|at|always" in ctx.sent[-1]

    asyncio.run(run())
    assert (tmp_path / "session_state.json").is_file()


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


def test_config_defaults() -> None:
    config = plugin.LinkParserPluginConfig()
    assert config.default_mode == "off"
    assert config.parse_reply is False
    assert config.delete_after_send is False
    assert config.cache_max_files == 50
    assert config.x_backend == "auto"
    assert config.send_text is True
    assert config.send_forward is True
    assert config.x_image_mode == "raw"
    assert config.xhs_image_mode == "raw"
    assert config.x_video_max_height == 720
