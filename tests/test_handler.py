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


def _run(plg, config, state, debouncer, client, ctx, xhs_client=None, x_client=None) -> None:
    asyncio.run(
        plugin._handle_message(
            plg,
            config,
            state,
            client,
            xhs_client or object(),
            x_client or object(),
            debouncer,
            ctx,
        )
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


def _xhs_outcome(kind: str, files: list, tmp_path):
    from shinbot_plugin_linkparser.models import XHSNoteInfo, XHSOutcome

    info = XHSNoteInfo(
        note_id="n1",
        note_type="video" if kind == "video" else "normal",
        title="笔记标题",
        desc="笔记正文 描述",
        author="博主",
        image_urls=[],
        page_url="https://www.xiaohongshu.com/explore/n1",
    )
    return XHSOutcome(kind=kind, files=files, info=info)


def _xhs_context():
    ctx = MessageContext()
    ctx.message = SimpleNamespace(
        elements=[
            {
                "type": "text",
                "attrs": {"content": "https://www.xiaohongshu.com/explore/n1?xsec_token=a"},
                "children": [],
            }
        ]
    )
    return ctx


def test_xhs_image_note_sends_caption_then_images(handler_env, monkeypatch, tmp_path) -> None:
    """XHS galleries follow the X layout: caption text plus media."""
    plg, config, state, debouncer, client = handler_env
    image_path = tmp_path / "note_long.jpg"
    image_path.write_bytes(b"image-bytes")

    async def fake_parse_xhs(*_args, **_kwargs):
        return _xhs_outcome("images", [image_path], tmp_path)

    monkeypatch.setattr(parsers, "parse_xhs_note", fake_parse_xhs)

    ctx = _xhs_context()  # forward unsupported → caption then media
    _run(plg, config, state, debouncer, client, ctx)

    assert len(ctx.sent) == 2
    assert isinstance(ctx.sent[0], str)
    assert "博主" in ctx.sent[0] and "笔记标题" in ctx.sent[0]
    assert ctx.sent[1][0]["type"] == "img"
    assert debouncer.hit("g:1", "xiaohongshu:post:n1") is True


def test_xhs_media_folded_into_chat_record(handler_env, monkeypatch, tmp_path) -> None:
    plg, config, state, debouncer, client = handler_env
    image_path = tmp_path / "note_long2.jpg"
    image_path.write_bytes(b"image-bytes")

    async def fake_parse_xhs(*_args, **_kwargs):
        return _xhs_outcome("images", [image_path], tmp_path)

    monkeypatch.setattr(parsers, "parse_xhs_note", fake_parse_xhs)
    ctx = _xhs_context()
    ctx.adapter = SimpleNamespace(platform="onebot_v11")
    _run(plg, config, state, debouncer, client, ctx)

    assert len(ctx.sent) == 1
    root = ctx.sent[0][0]
    assert root["attrs"].get("forward") == "true"
    assert any("笔记标题" in str(node) for node in root["children"])
    assert any(
        child.get("type") == "img"
        for node in root["children"]
        for child in node.get("children", [])
    )


def test_xhs_video_folded_with_caption(handler_env, monkeypatch, tmp_path) -> None:
    plg, config, state, debouncer, client = handler_env
    video = tmp_path / "note_video.mp4"
    video.write_bytes(b"video")

    async def fake_parse_xhs(*_args, **_kwargs):
        return _xhs_outcome("video", [video], tmp_path)

    monkeypatch.setattr(parsers, "parse_xhs_note", fake_parse_xhs)
    ctx = _xhs_context()
    ctx.adapter = SimpleNamespace(platform="onebot_v11")
    _run(plg, config, state, debouncer, client, ctx)

    assert len(ctx.sent) == 1
    root = ctx.sent[0][0]
    assert any(
        child.get("type") == "video"
        for node in root["children"]
        for child in node.get("children", [])
    )


def test_caption_can_be_disabled(handler_env, monkeypatch, tmp_path) -> None:
    plg, config, state, debouncer, client = handler_env
    config.send_text = False
    image_path = tmp_path / "note_long3.jpg"
    image_path.write_bytes(b"image")

    async def fake_parse_xhs(*_args, **_kwargs):
        return _xhs_outcome("images", [image_path], tmp_path)

    monkeypatch.setattr(parsers, "parse_xhs_note", fake_parse_xhs)
    ctx = _xhs_context()
    _run(plg, config, state, debouncer, client, ctx)

    assert len(ctx.sent) == 1
    assert isinstance(ctx.sent[0], list) and ctx.sent[0][0]["type"] == "img"


X_STATUS = "2040059740848283920"
X_LINK = f"https://x.com/NASA/status/{X_STATUS}"


def _x_outcome(kind: str, files: list, tmp_path):
    from shinbot_plugin_linkparser.models import XOutcome, XTweetInfo

    info = XTweetInfo(
        status_id=X_STATUS,
        url=f"https://x.com/i/status/{X_STATUS}",
        text="Good morning, world!",
        author_name="NASA",
        author_handle="NASA",
    )
    return XOutcome(kind=kind, files=files, info=info)


def _x_context(elements: list | None = None):
    ctx = MessageContext()
    ctx.message = SimpleNamespace(
        elements=elements
        or [{"type": "text", "attrs": {"content": X_LINK}, "children": []}]
    )
    return ctx


def test_x_media_folded_into_chat_record(handler_env, monkeypatch, tmp_path) -> None:
    plg, config, state, debouncer, client = handler_env
    image = tmp_path / "x1.jpg"
    image.write_bytes(b"img")

    async def fake_parse_x(*_a, **_k):
        return _x_outcome("images", [image], tmp_path)

    monkeypatch.setattr(parsers, "parse_x_post", fake_parse_x)
    ctx = _x_context()
    ctx.adapter = SimpleNamespace(platform="onebot_v11")
    _run(plg, config, state, debouncer, client, ctx)

    assert len(ctx.sent) == 1
    payload = ctx.sent[0]
    assert isinstance(payload, list)
    root = payload[0]
    assert root["type"] == "message" and root["attrs"].get("forward") == "true"
    nodes = root["children"]
    assert nodes and nodes[0]["type"] == "message"
    # caption node carries the tweet text; media node carries the image
    assert any("Good morning" in str(node) for node in nodes)
    assert any(
        child.get("type") == "img" for node in nodes for child in node.get("children", [])
    )
    assert debouncer.hit("g:1", f"x:post:{X_STATUS}") is True


def test_x_media_falls_back_when_forward_unsupported(
    handler_env, monkeypatch, tmp_path
) -> None:
    plg, config, state, debouncer, client = handler_env
    image = tmp_path / "x2.jpg"
    image.write_bytes(b"img")

    async def fake_parse_x(*_a, **_k):
        return _x_outcome("images", [image], tmp_path)

    monkeypatch.setattr(parsers, "parse_x_post", fake_parse_x)
    ctx = _x_context()  # no adapter attribute → forward unsupported
    _run(plg, config, state, debouncer, client, ctx)

    assert len(ctx.sent) == 2
    assert isinstance(ctx.sent[0], str) and "Good morning" in ctx.sent[0]
    assert isinstance(ctx.sent[1], list) and ctx.sent[1][0]["type"] == "img"


def test_x_text_only_post_sends_caption(handler_env, monkeypatch, tmp_path) -> None:
    plg, config, state, debouncer, client = handler_env

    async def fake_parse_x(*_a, **_k):
        return _x_outcome("text", [], tmp_path)

    monkeypatch.setattr(parsers, "parse_x_post", fake_parse_x)
    ctx = _x_context()
    _run(plg, config, state, debouncer, client, ctx)

    assert len(ctx.sent) == 1
    assert isinstance(ctx.sent[0], str)
    assert "NASA" in ctx.sent[0] and "Good morning" in ctx.sent[0]
    assert debouncer.hit("g:1", f"x:post:{X_STATUS}") is True


def test_x_video_sent_as_video_element(handler_env, monkeypatch, tmp_path) -> None:
    plg, config, state, debouncer, client = handler_env
    video = tmp_path / "x_video.mp4"
    video.write_bytes(b"video")

    async def fake_parse_x(*_a, **_k):
        return _x_outcome("video", [video], tmp_path)

    monkeypatch.setattr(parsers, "parse_x_post", fake_parse_x)
    ctx = _x_context()
    _run(plg, config, state, debouncer, client, ctx)

    # forward unsupported here → caption text first, then the video element
    assert len(ctx.sent) == 2
    assert isinstance(ctx.sent[0], str)
    assert ctx.sent[1][0]["type"] == "video"


def test_x_video_folded_with_forward_adapter(handler_env, monkeypatch, tmp_path) -> None:
    plg, config, state, debouncer, client = handler_env
    video = tmp_path / "x_video2.mp4"
    video.write_bytes(b"video")

    async def fake_parse_x(*_a, **_k):
        return _x_outcome("video", [video], tmp_path)

    monkeypatch.setattr(parsers, "parse_x_post", fake_parse_x)
    ctx = _x_context()
    ctx.adapter = SimpleNamespace(platform="onebot_v11")
    _run(plg, config, state, debouncer, client, ctx)

    assert len(ctx.sent) == 1
    root = ctx.sent[0][0]
    assert root["attrs"].get("forward") == "true"
    assert any(
        child.get("type") == "video"
        for node in root["children"]
        for child in node.get("children", [])
    )
