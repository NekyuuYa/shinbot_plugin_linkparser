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
