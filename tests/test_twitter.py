"""X/Twitter unit tests: link scanning, payload parsing, variant picking, client."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from shinbot_plugin_linkparser.twitter import (
    XClient,
    XError,
    parse_fxtwitter,
    parse_syndication,
    pick_video_variant,
)
from shinbot_plugin_linkparser.twitter.media import download_video
from shinbot_plugin_linkparser.urls import (
    find_supported_candidates,
    find_x_candidates,
)

STATUS_ID = "2040059740848283920"


# ── link scanning ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        f"https://x.com/NASA/status/{STATUS_ID}",
        f"https://twitter.com/NASA/status/{STATUS_ID}?s=20",
        f"https://mobile.twitter.com/NASA/statuses/{STATUS_ID}",
        f"https://x.com/i/status/{STATUS_ID}",
        f"https://x.com/i/web/status/{STATUS_ID}",
        f"看这个 https://fixupx.com/NASA/status/{STATUS_ID}",
        f"https://vxtwitter.com/NASA/status/{STATUS_ID}",
        "https://twitter.com/i/status/20",  # legacy short id
    ],
)
def test_x_links_scanned(text: str) -> None:
    candidates = find_x_candidates(text)
    assert len(candidates) == 1
    assert candidates[0].platform == "x"
    assert candidates[0].status_id


@pytest.mark.parametrize(
    "text",
    [
        "今天天气不错",
        "https://x.com/NASA 没有 status",
        "https://x.com/NASA/123 不是 status 路径",
        "https://x.com/NASA/status/abc 非数字",
        "https://example.com/NASA/status/12345",
    ],
)
def test_x_noise_rejected(text: str) -> None:
    assert find_x_candidates(text) == []


def test_x_in_supported_candidates() -> None:
    text = f"https://x.com/NASA/status/{STATUS_ID} 和 https://www.bilibili.com/video/BV1xx411c7mD"
    platforms = {candidate.platform for candidate in find_supported_candidates(text)}
    assert platforms == {"x", "bilibili"}


# ── variant picking ───────────────────────────────────────────────────────


def _variants() -> list[dict]:
    return [
        {"url": "https://v/pl/x.m3u8", "content_type": "application/x-mpegURL",
         "bitrate": None, "height": None},
        {"url": "https://v/vid/avc1/480x270/a.mp4", "content_type": "video/mp4",
         "bitrate": 256000, "height": 270},
        {"url": "https://v/vid/avc1/640x360/b.mp4", "content_type": "video/mp4",
         "bitrate": 832000, "height": 360},
        {"url": "https://v/vid/avc1/1280x720/c.mp4", "content_type": "video/mp4",
         "bitrate": 2176000, "height": 720},
        {"url": "https://v/vid/avc1/1920x1080/d.mp4", "content_type": "video/mp4",
         "bitrate": 10368000, "height": 1080},
    ]


def test_pick_variant_prefers_highest_within_cap() -> None:
    chosen = pick_video_variant(_variants(), max_height=720)
    assert chosen is not None
    assert chosen["height"] == 720


def test_pick_variant_falls_back_to_smallest_when_all_too_big() -> None:
    chosen = pick_video_variant(_variants(), max_height=200)
    assert chosen is not None
    assert chosen["height"] == 270  # smallest mp4 → least bandwidth


def test_pick_variant_uses_hls_when_no_mp4() -> None:
    hls_only = [v for v in _variants() if "mpegURL" in v["content_type"]]
    chosen = pick_video_variant(hls_only, max_height=720)
    assert chosen is not None
    assert "mpegURL" in chosen["content_type"]


def test_pick_variant_empty() -> None:
    assert pick_video_variant([], max_height=720) is None


# ── payload parsing ───────────────────────────────────────────────────────


def _syndication_payload() -> dict:
    return {
        "text": "Good morning, world!",
        "created_at": "2026-04-04T12:00:00.000Z",
        "user": {"name": "NASA", "screen_name": "NASA", "profile_image_url_https": "https://a"},
        "mediaDetails": [
            {"type": "photo", "media_url_https": "https://pbs.twimg.com/media/a.jpg"},
            {
                "type": "video",
                "media_url_https": "https://pbs.twimg.com/cover.jpg",
                "video_info": {
                    "duration_millis": 32405,
                    "variants": [
                        {"content_type": "application/x-mpegURL", "url": "https://v/pl/x.m3u8"},
                        {
                            "content_type": "video/mp4",
                            "bitrate": 2176000,
                            "url": "https://v/vid/avc1/1280x720/b.mp4",
                        },
                    ],
                },
            },
        ],
    }


def test_parse_syndication() -> None:
    info = parse_syndication(_syndication_payload(), STATUS_ID)
    assert info is not None
    assert info.status_id == STATUS_ID
    assert info.text.startswith("Good morning")
    assert info.author_name == "NASA"
    assert info.author_handle == "NASA"
    assert info.photos == ["https://pbs.twimg.com/media/a.jpg?name=large"]
    assert [item.kind for item in info.media] == ["photo", "video"]
    video = info.videos[0]
    assert video.duration == pytest.approx(32.405)
    assert video.cover_url == "https://pbs.twimg.com/cover.jpg"
    heights = [v["height"] for v in video.variants]
    assert 720 in heights and None in heights


def test_parse_syndication_handles_missing_state() -> None:
    assert parse_syndication({}, STATUS_ID) is not None  # empty text but valid shape
    assert parse_syndication("nope", STATUS_ID) is None  # type: ignore[arg-type]


def test_parse_fxtwitter() -> None:
    payload = {
        "tweet": {
            "url": f"https://x.com/NASA/status/{STATUS_ID}",
            "text": "hello",
            "created_at": "Mon Apr 06 12:00:00 +0000 2026",
            "author": {"name": "NASA", "screen_name": "NASA"},
            "media": {
                "photos": [
                    {"url": "https://pbs.twimg.com/media/x.jpg?name=orig"},
                    {"url": "https://pbs.twimg.com/media/y.jpg"},
                ],
                "videos": [
                    {
                        "url": "https://v/vid/avc1/1280x720/z.mp4",
                        "duration": 12.5,
                        "height": 720,
                        "thumbnail_url": "https://pbs.twimg.com/thumb.jpg",
                    }
                ],
            },
        }
    }
    info = parse_fxtwitter(payload, STATUS_ID)
    assert info is not None
    assert info.photos == [
        "https://pbs.twimg.com/media/x.jpg?name=large",
        "https://pbs.twimg.com/media/y.jpg?name=large",
    ]
    assert [item.kind for item in info.media] == ["photo", "photo", "video"]
    video = info.videos[0]
    assert video.duration == pytest.approx(12.5)
    assert video.variants[0]["height"] == 720
    assert video.cover_url == "https://pbs.twimg.com/thumb.jpg"


def test_parse_fxtwitter_rejects_non_tweet() -> None:
    assert parse_fxtwitter({"code": 200, "user": {}}, STATUS_ID) is None


# ── client backend chain ──────────────────────────────────────────────────


def _client_with(handler, backend="auto") -> XClient:
    return XClient(backend=backend, transport=httpx.MockTransport(handler))


def test_client_auto_falls_back_to_fxtwitter() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "syndication" in request.url.host:
            return httpx.Response(503, text="rate limited")
        return httpx.Response(200, json={"tweet": _fxtwitter_min()})

    client = _client_with(handler)
    try:
        info = asyncio.run(client.fetch_tweet(STATUS_ID))
    finally:
        asyncio.run(client.close())
    assert info.author_handle == "NASA"
    assert info.text == "from fxtwitter"


def _fxtwitter_min() -> dict:
    return {
        "url": f"https://x.com/NASA/status/{STATUS_ID}",
        "text": "from fxtwitter",
        "author": {"name": "NASA", "screen_name": "NASA"},
        "media": {},
    }


def test_client_syndication_only_does_not_fallback() -> None:
    calls = {"fxtwitter": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if "syndication" in request.url.host:
            return httpx.Response(503, text="nope")
        calls["fxtwitter"] += 1
        return httpx.Response(200, json={"tweet": _fxtwitter_min()})

    client = _client_with(handler, backend="syndication")
    try:
        with pytest.raises(XError):
            asyncio.run(client.fetch_tweet(STATUS_ID))
    finally:
        asyncio.run(client.close())
    assert calls["fxtwitter"] == 0


def test_client_both_fail_raises_clean_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = _client_with(handler)
    try:
        with pytest.raises(XError):
            asyncio.run(client.fetch_tweet(STATUS_ID))
    finally:
        asyncio.run(client.close())


# ── media download ────────────────────────────────────────────────────────


def test_download_video_direct_mp4(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"\x00\x00\x00\x18ftypmp42",
            headers={"content-type": "video/mp4"},
        )

    client = _client_with(handler)
    info = parse_syndication(_syndication_payload(), STATUS_ID)
    assert info is not None
    dest = tmp_path / "v.mp4"
    try:
        ok, height = asyncio.run(
            download_video(client.http, info.videos[0].variants, dest, max_height=720)
        )
    finally:
        asyncio.run(client.close())
    assert ok is True
    assert height == 720
    assert dest.read_bytes().startswith(b"\x00\x00\x00\x18ftypmp42")


def test_download_video_hls_requires_ffmpeg(tmp_path, monkeypatch) -> None:
    from shinbot_plugin_linkparser.twitter import media as xmedia

    monkeypatch.setattr(xmedia, "ffmpeg_available", lambda: False)
    hls_variants = [
        {
            "url": "https://v/pl/x.m3u8",
            "content_type": "application/x-mpegURL",
            "bitrate": None,
            "height": None,
        }
    ]
    client = _client_with(lambda request: httpx.Response(404))
    try:
        with pytest.raises(XError):
            asyncio.run(
                download_video(client.http, hls_variants, tmp_path / "x.mp4")
            )
    finally:
        asyncio.run(client.close())


def test_parse_syndication_mixed_media_keeps_order() -> None:
    payload = {
        "text": "mixed",
        "user": {"name": "NASA", "screen_name": "NASA"},
        "mediaDetails": [
            {"type": "photo", "media_url_https": "https://pbs.twimg.com/media/p1.jpg"},
            {
                "type": "video",
                "media_url_https": "https://pbs.twimg.com/cover.jpg",
                "video_info": {
                    "variants": [
                        {
                            "content_type": "video/mp4",
                            "bitrate": 832000,
                            "url": "https://v/vid/avc1/640x360/a.mp4",
                        }
                    ]
                },
            },
            {"type": "photo", "media_url_https": "https://pbs.twimg.com/media/p2.jpg"},
        ],
    }
    info = parse_syndication(payload, STATUS_ID)
    assert info is not None
    assert [item.kind for item in info.media] == ["photo", "video", "photo"]
    assert len(info.photos) == 2
    assert len(info.videos) == 1


# ── sensitive-content flag ────────────────────────────────────────────────


def test_parse_syndication_reads_possibly_sensitive() -> None:
    payload = _syndication_payload()
    payload["possibly_sensitive"] = True
    info = parse_syndication(payload, STATUS_ID)
    assert info is not None and info.sensitive is True


def test_parse_fxtwitter_reads_possibly_sensitive() -> None:
    payload = {
        "tweet": {
            "url": f"https://x.com/NASA/status/{STATUS_ID}",
            "text": "nsfw",
            "possibly_sensitive": True,
            "author": {"name": "NASA", "screen_name": "NASA"},
            "media": {},
        }
    }
    info = parse_fxtwitter(payload, STATUS_ID)
    assert info is not None and info.sensitive is True


def test_sensitive_defaults_false() -> None:
    info = parse_syndication(_syndication_payload(), STATUS_ID)
    assert info is not None and info.sensitive is False


# ── media availability (withheld / restricted) ────────────────────────────


def test_parse_syndication_reads_media_availability() -> None:
    payload = _syndication_payload()
    payload["mediaDetails"][0]["ext_media_availability"] = {"status": "Unavailable"}
    payload["mediaDetails"][1]["ext_media_availability"] = {"status": "Available"}
    info = parse_syndication(payload, STATUS_ID)
    assert info is not None
    assert info.media[0].available is False  # photo withheld
    assert info.media[1].available is True  # video available


def test_media_available_defaults_true_without_field() -> None:
    info = parse_syndication(_syndication_payload(), STATUS_ID)
    assert info is not None
    assert all(item.available for item in info.media)
