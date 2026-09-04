"""_handle_message reply-path robustness tests (no network, no framework)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

import shinbot_plugin_linkparser as plugin
import shinbot_plugin_linkparser.parsers as parsers
from shinbot_plugin_linkparser.debounce import Debouncer
from shinbot_plugin_linkparser.models import ParseOutcome, VideoMeta
from shinbot_plugin_linkparser.session_state import SessionStateStore

BV = "BV1xx411c7mD"
LINK_URL = f"https://www.bilibili.com/video/{BV}"
LINK_TEXT = f"看 {LINK_URL}"


class FakeLogger:
    def info(self, *_a, **_k) -> None:
        pass

    def warning(self, *_a, **_k) -> None:
        pass

    def debug(self, *_a, **_k) -> None:
        pass

    def exception(self, *_a, **_k) -> None:
        pass


class FakePlugin:
    plugin_id = "shinbot_plugin_linkparser"

    def __init__(self, tmp_path) -> None:
        self.data_dir = str(tmp_path)
        self.logger = FakeLogger()
        self.database = None


class MessageContext:
    """Fake RouteDispatchContext + MessageContext hybrid for the handler."""

    def __init__(self, *, fail_video: bool = False, fail_all: bool = False) -> None:
        self.session_id = "g:1"
        self.event = SimpleNamespace(self_id="123")
        self.message = SimpleNamespace(
            elements=[{"type": "text", "attrs": {"content": LINK_TEXT}, "children": []}]
        )
        self.sent: list[object] = []
        self.fail_video = fail_video
        self.fail_all = fail_all

    def require_message_context(self):
        return self

    async def send(self, content: object):
        if self.fail_all:
            raise RuntimeError("Adapter is not connected")
        if self.fail_video and isinstance(content, list):
            self.fail_video = False
            raise TimeoutError("request timeout")
        self.sent.append(content)
        return SimpleNamespace(message_id="m1")


@pytest.fixture
def handler_env(tmp_path, monkeypatch):
    state = SessionStateStore(tmp_path / "state.json", default_mode="always")
    debouncer = Debouncer(window_seconds=300)
    plg = FakePlugin(tmp_path)
    config = plugin.LinkParserPluginConfig()
    client = object()

    async def fake_parse_video(*_args, **_kwargs) -> ParseOutcome:
        data_dir = Path(_kwargs["data_dir"])
        dest = data_dir / "videos" / f"{BV.lower()}_p1.mp4"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"fake-video-bytes")
        meta = VideoMeta(
            bvid=BV,
            avid=None,
            cid=1,
            page=1,
            page_index=0,
            title="标题",
            part_title=None,
            author="u",
            author_mid=None,
            duration_seconds=10,
            pic="",
            pubdate=0,
            desc="",
            stat={},
            page_url=LINK_URL,
        )
        return ParseOutcome(path=dest, meta=meta)

    monkeypatch.setattr(parsers, "parse_video", fake_parse_video)
    return plg, config, state, debouncer, client


def _run(plg, config, state, debouncer, client, ctx) -> None:
    asyncio.run(
        plugin._handle_message(plg, config, state, client, debouncer, ctx)
    )


def test_success_sends_video_and_remembers(handler_env) -> None:
    plg, config, state, debouncer, client = handler_env
    ctx = MessageContext()
    _run(plg, config, state, debouncer, client, ctx)

    assert len(ctx.sent) == 1
    assert isinstance(ctx.sent[0], list)  # video element list
    resource_key = f"bilibili:video:{BV}:p1"
    assert debouncer.hit("g:1", resource_key) is True


def test_send_timeout_falls_back_to_text_and_allows_retry(handler_env) -> None:
    plg, config, state, debouncer, client = handler_env
    ctx = MessageContext(fail_video=True)
    _run(plg, config, state, debouncer, client, ctx)  # must not raise

    assert len(ctx.sent) == 1
    assert isinstance(ctx.sent[0], str)
    assert LINK_URL in str(ctx.sent[0])
    # debounce cleared → re-share may retry
    assert debouncer.hit("g:1", LINK_URL) is False


def test_adapter_down_never_raises(handler_env) -> None:
    """Video send and fallback both fail — handler must stay silent, no raise."""
    plg, config, state, debouncer, client = handler_env
    ctx = MessageContext(fail_all=True)
    _run(plg, config, state, debouncer, client, ctx)  # must not raise
    assert ctx.sent == []
    assert debouncer.hit("g:1", LINK_URL) is False


def test_oversize_video_skips_send_and_replies_link(handler_env, monkeypatch) -> None:
    plg, config, state, debouncer, client = handler_env
    monkeypatch.setattr(plugin, "_file_size_mb", lambda path: 999)
    ctx = MessageContext()
    _run(plg, config, state, debouncer, client, ctx)

    assert len(ctx.sent) == 1
    text = str(ctx.sent[0])
    assert "超过直发上限" in text
    assert LINK_URL in text
    # not the video element list
    assert isinstance(ctx.sent[0], str)
