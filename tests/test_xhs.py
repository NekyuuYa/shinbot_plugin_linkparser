"""Xiaohongshu unit tests: URL scanning, state extraction, long-image stitch."""

from __future__ import annotations

import json

import pytest

from shinbot_plugin_linkparser.urls import (
    ark_extract_url,
    find_supported_candidates,
    find_xhs_candidates,
)
from shinbot_plugin_linkparser.xiaohongshu import extract_note_info, stitch_to_long_image

NOTE_ID = "68e8e3fa00000000030342ec"


def test_direct_note_url_scanned() -> None:
    url = (
        f"https://www.xiaohongshu.com/discovery/item/{NOTE_ID}"
        "?xsec_source=app_share&xsec_token=CBW9rwIV2qhcCD-JsQAOSHd2tTW9jXA"
    )
    candidates = find_xhs_candidates(f"看看 {url}")
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.platform == "xiaohongshu"
    assert candidate.note_id == NOTE_ID
    assert candidate.note_url is not None
    assert candidate.note_url.startswith("https://www.xiaohongshu.com/discovery/item/")
    assert "xsec_token=" in candidate.note_url


def test_explore_url_scanned() -> None:
    url = f"https://www.xiaohongshu.com/explore/{NOTE_ID}?xsec_source=pc_search"
    candidates = find_xhs_candidates(url)
    assert len(candidates) == 1
    assert candidates[0].note_id == NOTE_ID


def test_short_link_scanned() -> None:
    candidates = find_xhs_candidates("https://xhslink.com/a/AbC123")
    assert len(candidates) == 1
    assert candidates[0].short_code == "AbC123"
    assert candidates[0].needs_redirect is True


def test_xhslink_cn_scanned() -> None:
    candidates = find_xhs_candidates("xhslink.cn/a/xYz98")
    assert len(candidates) == 1
    assert candidates[0].short_code == "xYz98"


@pytest.mark.parametrize(
    "text",
    [
        "今天天气不错",
        "xiaohongshu.com 只是一句话",
        "myxiaohongshu.com/explore/abc123 域名不对",
    ],
)
def test_noise_rejected(text: str) -> None:
    assert find_xhs_candidates(text) == []


def test_supported_finds_both_platforms() -> None:
    text = (
        f"https://www.xiaohongshu.com/explore/{NOTE_ID}?xsec_token=abc "
        "和 https://www.bilibili.com/video/BV1xx411c7mD"
    )
    candidates = find_supported_candidates(text)
    platforms = {candidate.platform for candidate in candidates}
    assert platforms == {"xiaohongshu", "bilibili"}


def test_ark_with_xhslink_extracted() -> None:
    payload = {"meta": {"detail_1": {"qqdocurl": "https://xhslink.com/a/AbC123"}}}
    url = ark_extract_url(json.dumps(payload))
    assert url is not None
    assert find_xhs_candidates(url)


# ── HTML state extraction ────────────────────────────────────────────────


def _state_html(state: dict) -> str:
    serialized = json.dumps(state, ensure_ascii=False)
    return (
        '<script>window.__INITIAL_STATE__='
        + serialized.replace("null", "undefined")
        + "</script></body></html>"
    )


def test_extract_explore_layout() -> None:
    note = {
        "type": "normal",
        "title": "好看",
        "desc": "描述",
        "user": {"nickname": "博主"},
        "imageList": [
            {"urlDefault": "https://sns-webpic.xhscdn.com/1.jpg"},
            {"urlDefault": "https://sns-webpic.xhscdn.com/2.jpg"},
        ],
    }
    state = {"note": {"noteDetailMap": {NOTE_ID: {"note": note}}}}
    info = extract_note_info(_state_html(state), note_id=NOTE_ID, page_url="https://x")
    assert info is not None
    assert info.note_type == "normal"
    assert info.author == "博主"
    assert len(info.image_urls) == 2
    assert info.video_master_url is None


def test_extract_video_layout() -> None:
    note = {
        "type": "video",
        "title": "视频",
        "desc": "",
        "user": {"nickname": "up"},
        "imageList": [{"urlDefault": "https://sns-webpic.xhscdn.com/c.jpg"}],
        "video": {
            "media": {
                "stream": {
                    "h264": [{"masterUrl": "https://sns-video.xhscdn.com/a.m3u8"}]
                }
            }
        },
    }
    state = {"note": {"noteDetailMap": {NOTE_ID: {"note": note}}}}
    info = extract_note_info(_state_html(state), note_id=NOTE_ID, page_url="https://x")
    assert info is not None
    assert info.note_type == "video"
    assert info.video_master_url == "https://sns-video.xhscdn.com/a.m3u8"


def test_extract_prefers_h265_over_h264() -> None:
    note = {
        "type": "video",
        "video": {
            "media": {
                "stream": {
                    "h264": [{"masterUrl": "https://cdn/h264.m3u8"}],
                    "h265": [{"masterUrl": "https://cdn/h265.m3u8"}],
                }
            }
        },
    }
    state = {"note": {"noteDetailMap": {NOTE_ID: {"note": note}}}}
    info = extract_note_info(_state_html(state), note_id=NOTE_ID, page_url="https://x")
    assert info.video_master_url == "https://cdn/h265.m3u8"


def test_extract_missing_state_returns_none() -> None:
    info = extract_note_info("<html>no state</html>", note_id=NOTE_ID, page_url="https://x")
    assert info is None


# ── long-image stitch ────────────────────────────────────────────────────


def test_stitch_combines_images(tmp_path) -> None:
    pytest.importorskip("PIL")
    from PIL import Image

    sources = []
    for index, size in enumerate([(300, 200), (300, 100)], start=1):
        path = tmp_path / f"img{index}.png"
        Image.new("RGB", size, (index * 40, index * 40, index * 40)).save(path)
        sources.append(path)

    out = tmp_path / "long.jpg"
    result = stitch_to_long_image(sources, out, max_width=300, max_height=1000)
    assert result is not None
    assert result == out
    with Image.open(out) as image:
        assert image.height == 300  # 200 + 100
        assert image.width <= 300


def test_stitch_downscales_when_too_tall(tmp_path) -> None:
    pytest.importorskip("PIL")
    from PIL import Image

    sources = []
    for index in range(3):
        path = tmp_path / f"t{index}.png"
        Image.new("RGB", (200, 400), (10, 10, 10)).save(path)
        sources.append(path)

    out = tmp_path / "long2.jpg"
    result = stitch_to_long_image(sources, out, max_width=200, max_height=400)
    assert result is not None
    with Image.open(out) as image:
        assert image.height <= 400  # downscaled to fit max_height


def test_stitch_returns_none_without_images(tmp_path) -> None:
    assert stitch_to_long_image([], tmp_path / "x.jpg") is None


# ── video variant selection / direct mp4 download ─────────────────────────


def _note_with_stream() -> dict:
    def stream(codec, fmt, url, height, size):
        return {
            codec: [
                {
                    "format": fmt,
                    "masterUrl": url,
                    "width": int(height * 16 / 9),
                    "height": height,
                    "size": size,
                }
            ]
        }

    merged: dict = {}
    merged.update(stream("h265", "hls", "https://v/pl/h265.m3u8", 1080, 40000000))
    merged.update(
        stream("h264", "mp4", "https://v/stream/x_258.mp4?sign=abc", 720, 18500902)
    )
    merged.update(stream("av1", "mp4", "https://v/stream/y_av1.mp4", 1080, 30000000))
    return {"video": {"media": {"stream": merged}}}


def test_video_variants_of_normalizes_entries() -> None:
    from shinbot_plugin_linkparser.xiaohongshu import video_variants_of

    variants = video_variants_of(_note_with_stream())
    assert len(variants) == 3
    codecs = {variant["codec"] for variant in variants}
    assert codecs == {"h265", "h264", "av1"}
    h264 = next(v for v in variants if v["codec"] == "h264")
    assert h264["format"] == "mp4"
    assert h264["height"] == 720 and h264["size"] == 18500902


def test_pick_xhs_variant_prefers_highest_mp4_within_cap() -> None:
    from shinbot_plugin_linkparser.xiaohongshu import pick_video_variant, video_variants_of

    variants = video_variants_of(_note_with_stream())
    chosen = pick_video_variant(variants, max_height=720)
    assert chosen is not None
    assert chosen["height"] == 720  # 1080 mp4 rejected, 720 chosen
    assert chosen["format"] == "mp4"


def test_pick_xhs_variant_smallest_when_all_over_cap() -> None:
    from shinbot_plugin_linkparser.xiaohongshu import pick_video_variant

    variants = [
        {"url": "https://v/a.mp4", "format": "mp4", "height": 1080, "size": 9},
        {"url": "https://v/b.mp4", "format": "mp4", "height": 720, "size": 5},
    ]
    chosen = pick_video_variant(variants, max_height=480)
    assert chosen is not None
    assert chosen["height"] == 720


def test_pick_xhs_variant_falls_back_to_hls() -> None:
    from shinbot_plugin_linkparser.xiaohongshu import pick_video_variant

    variants = [{"url": "https://v/pl/x.m3u8", "format": "hls", "height": 720}]
    chosen = pick_video_variant(variants, max_height=720)
    assert chosen is not None and chosen["url"].endswith(".m3u8")


def test_pick_xhs_variant_empty() -> None:
    from shinbot_plugin_linkparser.xiaohongshu import pick_video_variant

    assert pick_video_variant([], max_height=720) is None


def test_download_note_video_direct_mp4(tmp_path) -> None:
    import asyncio

    import httpx

    from shinbot_plugin_linkparser.xiaohongshu import XHSClient
    from shinbot_plugin_linkparser.xiaohongshu.media import download_note_video

    def handler(request: httpx.Request) -> httpx.Response:
        assert "sns-video" in request.url.host
        return httpx.Response(
            200,
            content=b"\x00\x00\x00\x18ftypisom",
            headers={"content-type": "video/mp4"},
        )

    client = XHSClient(transport=httpx.MockTransport(handler))
    dest = tmp_path / "v.mp4"
    try:
        asyncio.run(
            download_note_video(client.http, "https://sns-video-v6.xhscdn.com/a.mp4", dest)
        )
    finally:
        asyncio.run(client.close())
    assert dest.read_bytes() == b"\x00\x00\x00\x18ftypisom"
