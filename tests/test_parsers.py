"""Parse orchestration tests using a fake Bilibili client (no network)."""

from __future__ import annotations

import asyncio

import pytest

import shinbot_plugin_linkparser.parsers as parsers
from shinbot_plugin_linkparser.bilibili import BilibiliError
from shinbot_plugin_linkparser.bilibili.client import SingleFilePlan
from shinbot_plugin_linkparser.models import LinkCandidate, VideoMeta
from shinbot_plugin_linkparser.parsers import VideoTooLongError, parse_video

BV = "BV1xx411c7mD"


def _candidate(
    bvid=BV,
    avid=None,
    page=1,
    matched=f"https://bilibili.com/video/{BV}",
    short_code=None,
):
    return LinkCandidate(
        platform="bilibili",
        kind="video",
        bvid=bvid,
        avid=avid,
        page=page,
        matched=matched,
        short_code=short_code,
    )


def _meta(*, duration_seconds: int = 60, bvid: str = BV, page: int = 1) -> VideoMeta:
    return VideoMeta(
        bvid=bvid,
        avid=2,
        cid=62131,
        page=page,
        page_index=page - 1,
        title="测试视频",
        part_title=None,
        author="UP主",
        author_mid=2,
        duration_seconds=duration_seconds,
        pic="https://example.com/pic.jpg",
        pubdate=0,
        desc="",
        stat={},
        page_url=f"https://www.bilibili.com/video/{bvid}",
    )


class FakeClient:
    """Minimal stand-in for BilibiliClient supporting only what a test needs."""

    def __init__(self, *, meta=None, canonical=None, raise_meta=None):
        self._meta = meta
        self.canonical = canonical
        self.raise_meta = raise_meta
        self.http = object()

    async def resolve_short_url(self, short_url: str) -> str:
        return self.canonical or short_url

    async def fetch_meta(self, *, bvid=None, avid=None, page=1):
        if self.raise_meta is not None:
            raise self.raise_meta
        return self._meta or _meta(page=page)

    async def resolve_stream(self, **kwargs):
        return SingleFilePlan(url="https://upos.example/v.mp4", size=1000)


def test_unknown_candidate_raises(tmp_path) -> None:
    candidate = _candidate(bvid=None, avid=None, short_code="AbC123")
    with pytest.raises(BilibiliError):
        # needs_redirect True → resolves short url to itself → still unknown
        client = FakeClient(canonical="https://b23.tv/AbC123")
        import asyncio

        asyncio.run(parse_video(client, candidate, data_dir=tmp_path))


def test_short_link_is_expanded_before_meta(tmp_path, monkeypatch) -> None:
    candidate = _candidate(bvid=None, avid=None, short_code="AbC123", matched="https://b23.tv/AbC123")
    client = FakeClient(
        meta=_meta(),
        canonical=f"https://www.bilibili.com/video/{BV}",
    )

    async def fake_download(http, plan, dest, *, max_bytes):
        dest.write_bytes(b"fake")
        return dest

    monkeypatch.setattr(parsers, "download_plan_to_file", fake_download)

    async def run() -> None:
        outcome = await parse_video(
            client,
            candidate,
            data_dir=tmp_path,
            max_duration_seconds=0,
        )
        assert outcome.meta.bvid == BV


    asyncio.run(run())


def test_duration_guard(tmp_path, monkeypatch) -> None:
    candidate = _candidate()
    client = FakeClient(meta=_meta(duration_seconds=3600))

    async def fake_download(http, plan, dest, *, max_bytes):
        dest.write_bytes(b"fake")
        return dest

    monkeypatch.setattr(parsers, "download_plan_to_file", fake_download)


    with pytest.raises(VideoTooLongError):
        asyncio.run(
            parse_video(client, candidate, data_dir=tmp_path, max_duration_seconds=100)
        )


def test_successful_parse_writes_file(tmp_path, monkeypatch) -> None:
    candidate = _candidate()
    client = FakeClient(meta=_meta(duration_seconds=60))

    async def fake_download(http, plan, dest, *, max_bytes):
        dest.write_bytes(b"\x00\x00\x00\x18ftypmp42")
        return dest

    monkeypatch.setattr(parsers, "download_plan_to_file", fake_download)


    outcome = asyncio.run(
        parse_video(client, candidate, data_dir=tmp_path, max_size_mb=50)
    )
    assert outcome.path.exists()
    assert outcome.path.read_bytes().startswith(b"\x00\x00\x00\x18ftypmp42")
    assert outcome.path.name == f"{BV.lower()}_p1.mp4"
    assert (tmp_path / "videos").is_dir()


def test_size_config_is_passed_through(tmp_path, monkeypatch) -> None:
    candidate = _candidate()
    client = FakeClient(meta=_meta())

    captured: dict = {}

    async def fake_download(http, plan, dest, *, max_bytes):
        captured["max_bytes"] = max_bytes
        dest.write_bytes(b"x")
        return dest

    monkeypatch.setattr(parsers, "download_plan_to_file", fake_download)


    asyncio.run(parse_video(client, candidate, data_dir=tmp_path, max_size_mb=37))
    assert captured["max_bytes"] == 37 * 1024 * 1024
