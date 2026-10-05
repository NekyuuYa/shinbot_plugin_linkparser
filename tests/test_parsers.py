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


def test_existing_cache_file_is_reused(tmp_path, monkeypatch) -> None:
    candidate = _candidate()
    client = FakeClient(meta=_meta())

    dest = tmp_path / "videos" / f"{BV.lower()}_p1.mp4"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"cached-content")

    calls = {"download": 0, "resolve_stream": 0}

    async def fake_download(http, plan, dest, *, max_bytes):
        calls["download"] += 1
        return dest

    async def fake_resolve_stream(**kwargs):
        calls["resolve_stream"] += 1
        return SingleFilePlan(url="https://upos.example/v.mp4")

    monkeypatch.setattr(parsers, "download_plan_to_file", fake_download)
    client.resolve_stream = fake_resolve_stream

    outcome = asyncio.run(
        parse_video(client, candidate, data_dir=tmp_path, cache_max_files=10)
    )
    assert outcome.path.read_bytes() == b"cached-content"
    assert calls["download"] == 0
    assert calls["resolve_stream"] == 0


def test_prune_video_cache_keeps_newest(tmp_path) -> None:
    import os
    import time

    videos_dir = tmp_path / "videos"
    videos_dir.mkdir()
    now = time.time()
    for index, name in enumerate(["a.mp4", "b.mp4", "c.mp4", "d.mp4"]):
        path = videos_dir / name
        path.write_bytes(b"x")
        os.utime(path, (now + index, now + index))

    removed = parsers.prune_video_cache(videos_dir, keep=2)
    assert removed == 2
    remaining = sorted(path.name for path in videos_dir.glob("*.mp4"))
    assert remaining == ["c.mp4", "d.mp4"]


def test_prune_video_cache_keeps_everything_when_disabled(tmp_path) -> None:
    videos_dir = tmp_path / "videos"
    videos_dir.mkdir()
    (videos_dir / "a.mp4").write_bytes(b"x")
    (videos_dir / "b.mp4").write_bytes(b"x")
    assert parsers.prune_video_cache(videos_dir, keep=0) == 0
    assert len(list(videos_dir.glob("*.mp4"))) == 2


def test_delete_cached_file(tmp_path) -> None:
    path = tmp_path / "a.mp4"
    path.write_bytes(b"x")
    assert parsers.delete_cached_file(path) is True
    assert not path.exists()
    # missing file → False, no error
    assert parsers.delete_cached_file(path) is False


def _write_megabytes(path, mb):
    path.write_bytes(b"0" * (mb * 1024 * 1024))


def test_oversize_triggers_compression(tmp_path, monkeypatch) -> None:
    candidate = _candidate()
    client = FakeClient(meta=_meta(duration_seconds=120))
    dest = tmp_path / "videos" / f"{BV.lower()}_p1.mp4"

    async def fake_download(http, plan, target, *, max_bytes):
        dest.parent.mkdir(parents=True, exist_ok=True)
        _write_megabytes(dest, 3)
        return dest

    calls = {"compress": 0}

    async def fake_compress(src, dst, *, target_bytes, duration_seconds, max_height):
        calls["compress"] += 1
        assert duration_seconds == 120
        assert target_bytes == 1 * 1024 * 1024
        assert max_height == 720
        dst.write_bytes(b"small")
        return True

    monkeypatch.setattr(parsers, "download_plan_to_file", fake_download)
    monkeypatch.setattr(parsers, "compress_to_target", fake_compress)

    outcome = asyncio.run(
        parse_video(
            client,
            candidate,
            data_dir=tmp_path,
            max_size_mb=10,
            max_send_mb=1,
            compress=True,
            compress_max_height=720,
        )
    )
    assert calls["compress"] == 1
    assert outcome.path.read_bytes() == b"small"


def test_under_cap_skips_compression(tmp_path, monkeypatch) -> None:
    candidate = _candidate()
    client = FakeClient(meta=_meta(duration_seconds=60))
    dest = tmp_path / "videos" / f"{BV.lower()}_p1.mp4"

    async def fake_download(http, plan, target, *, max_bytes):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"tiny")
        return dest

    calls = {"compress": 0}

    async def fake_compress(*_a, **_k):
        calls["compress"] += 1
        return True

    monkeypatch.setattr(parsers, "download_plan_to_file", fake_download)
    monkeypatch.setattr(parsers, "compress_to_target", fake_compress)

    asyncio.run(
        parse_video(client, candidate, data_dir=tmp_path, max_send_mb=50, compress=True)
    )
    assert calls["compress"] == 0


def test_compression_can_be_disabled(tmp_path, monkeypatch) -> None:
    candidate = _candidate()
    client = FakeClient(meta=_meta(duration_seconds=60))
    dest = tmp_path / "videos" / f"{BV.lower()}_p1.mp4"

    async def fake_download(http, plan, target, *, max_bytes):
        dest.parent.mkdir(parents=True, exist_ok=True)
        _write_megabytes(dest, 3)
        return dest

    async def fake_compress(*_a, **_k):
        raise AssertionError("should not compress")

    monkeypatch.setattr(parsers, "download_plan_to_file", fake_download)
    monkeypatch.setattr(parsers, "compress_to_target", fake_compress)

    outcome = asyncio.run(
        parse_video(
            client, candidate, data_dir=tmp_path, max_size_mb=10, max_send_mb=1, compress=False
        )
    )
    assert outcome.path.stat().st_size > 1024 * 1024


def test_parse_x_post_mixed_media_keeps_order(tmp_path, monkeypatch) -> None:
    """A post with photos and a video keeps every attachment in tweet order."""
    from pathlib import Path

    from shinbot_plugin_linkparser import twitter as twitter_mod
    from shinbot_plugin_linkparser.models import XMedia, XTweetInfo

    info = XTweetInfo(
        status_id="2040059740848283920",
        url="https://x.com/i/status/2040059740848283920",
        text="mixed",
        author_name="NASA",
        author_handle="NASA",
        media=[
            XMedia(kind="photo", url="https://pbs.twimg.com/a.jpg?name=large"),
            XMedia(
                kind="video",
                variants=[
                    {
                        "url": "https://v/vid/avc1/640x360/a.mp4",
                        "content_type": "video/mp4",
                        "bitrate": 832000,
                        "height": 360,
                    }
                ],
                duration=5.0,
            ),
            XMedia(kind="photo", url="https://pbs.twimg.com/b.jpg?name=large"),
        ],
    )

    class FakeXClient:
        http = object()

        async def fetch_tweet(self, status_id: str) -> XTweetInfo:
            return info

    async def fake_download_photo(http, url, dest, **_kwargs):
        Path(dest).write_bytes(b"jpg")

    async def fake_download_video(http, variants, dest, **_kwargs):
        Path(dest).write_bytes(b"mp4")
        return True, 360

    monkeypatch.setattr(twitter_mod, "download_photo", fake_download_photo)
    monkeypatch.setattr(twitter_mod, "download_video", fake_download_video)

    candidate = LinkCandidate(
        platform="x",
        kind="post",
        matched="https://x.com/NASA/status/2040059740848283920",
        status_id="2040059740848283920",
    )
    outcome = asyncio.run(
        parsers.parse_x_post(
            FakeXClient(),
            candidate,
            data_dir=tmp_path,
            image_mode="raw",
            max_send_mb=0,
        )
    )
    assert outcome.kind == "mixed"
    assert [item.kind for item in outcome.media] == ["image", "video", "image"]
    for item in outcome.media:
        assert Path(item.path).exists()
