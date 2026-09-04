"""URL / card extraction unit tests (pure, no network)."""

from __future__ import annotations

import json

import pytest

from shinbot_plugin_linkparser.urls import (
    ark_extract_url,
    collect_bilibili_candidates,
    find_bilibili_candidates,
)

BV = "BV1xx411c7mD"


@pytest.mark.parametrize(
    ("text", "bvid", "avid", "page", "short"),
    [
        (f"https://www.bilibili.com/video/{BV}", BV, None, 1, None),
        (f"http://bilibili.com/video/{BV}", BV, None, 1, None),
        (f"https://m.bilibili.com/video/{BV}", BV, None, 1, None),
        (f"https://www.bilibili.com/video/{BV}?p=3", BV, None, 3, None),
        (f"https://www.bilibili.com/video/{BV}?vd_source=abc&p=2", BV, None, 2, None),
        ("https://www.bilibili.com/video/av170001", None, 170001, 1, None),
        ("https://www.bilibili.com/video/av170001?p=5", None, 170001, 5, None),
        (BV, BV, None, 1, None),
        ("av170001", None, 170001, 1, None),
        (f"{BV} 2", BV, None, 2, None),
        (f"bm{BV}", BV, None, 1, None),
        ("https://b23.tv/AbC123x", None, None, 1, "AbC123x"),
        (f"看看这个 https://www.bilibili.com/video/{BV}。", BV, None, 1, None),
    ],
)
def test_find_candidates(text, bvid, avid, page, short) -> None:
    candidates = find_bilibili_candidates(text)
    assert len(candidates) == 1, f"expected 1 candidate for {text!r}: {candidates}"
    candidate = candidates[0]
    assert candidate.bvid == bvid
    assert candidate.avid == avid
    assert candidate.page == page
    assert candidate.short_code == short


@pytest.mark.parametrize(
    "text",
    [
        "今天天气不错",
        "BVoid 不是视频号",
        "av 后面没数字",
        "xBV1xx411c7mD 前有字母不是合法裸号",
        "aav123456789 前多一个字母",
        "av1234567abc 后接字母不识别",
        "xb23.tv/abc 紧贴字母不算",
    ],
)
def test_find_candidates_rejects_noise(text: str) -> None:
    assert find_bilibili_candidates(text) == []


def test_bare_bv_inside_pasted_text_is_still_found() -> None:
    # A stray full URL pasted after an odd prefix still exposes its BV number,
    # which is a legitimate video link — bare BV tokens always count.
    assert find_bilibili_candidates("mybilibili.com/video/BV1xx411c7mD")[0].bvid == BV


def test_multi_candidates_dedupe_and_order() -> None:
    text = (
        f"https://www.bilibili.com/video/{BV} "
        "和 https://b23.tv/xyz 和 av170001 都是视频"
    )
    candidates = find_bilibili_candidates(text)
    assert len(candidates) == 3
    # Processing order: full URL, short link, then bare av.
    assert candidates[0].bvid == BV
    assert candidates[1].short_code == "xyz"
    assert candidates[2].avid == 170001


def test_full_url_does_not_duplicate_bare_bv() -> None:
    text = f"https://www.bilibili.com/video/{BV}"
    assert len(find_bilibili_candidates(text)) == 1


def test_ark_extract_bilibili_qqdocurl() -> None:
    payload = {
        "app": "com.tencent.miniapp",
        "meta": {
            "detail_1": {
                "qqdocurl": f"https://www.bilibili.com/video/{BV}",
                "title": "标题",
            }
        },
    }
    assert ark_extract_url(json.dumps(payload)) == f"https://www.bilibili.com/video/{BV}"


def test_ark_extract_miniapp_legacy_url() -> None:
    payload = {
        "meta": {"miniapp": {"legacyUrl": "https://b23.tv/abcdEF"}},
    }
    assert ark_extract_url(json.dumps(payload)) == "https://b23.tv/abcdEF"


def test_ark_extract_falls_back_to_any_url() -> None:
    payload = {"meta": {"news": {"jumpUrl": "https://example.com/abc"}}}
    assert ark_extract_url(json.dumps(payload)) == "https://example.com/abc"


def test_ark_extract_structmsg_news_card() -> None:
    """QQ 'com.tencent.structmsg' share card nests the URL in meta.news."""
    payload = {
        "app": "com.tencent.structmsg",
        "view": "news",
        "meta": {
            "news": {
                "title": "标题",
                "qqdocurl": "https://www.bilibili.com/video/BV1xx411c7mD",
            }
        },
    }
    assert ark_extract_url(json.dumps(payload)) == (
        "https://www.bilibili.com/video/BV1xx411c7mD"
    )


def test_ark_extract_meta_detail_family() -> None:
    """Some cards use meta.detail (no suffix) with shareUrl."""
    payload = {
        "meta": {
            "detail": {
                "shareUrl": "https://b23.tv/abCdEf",
            }
        },
    }
    assert ark_extract_url(json.dumps(payload)) == "https://b23.tv/abCdEf"


def test_ark_extract_double_encoded_onebot_payload() -> None:
    """The OneBot adapter json.dumps the already-serialized card string once
    more, so sb:ark attrs.data arrives double-encoded. Must still resolve."""
    card = {
        "app": "com.tencent.miniapp",
        "meta": {
            "detail_1": {
                "qqdocurl": "https://www.bilibili.com/video/BV1xx411c7mD",
                "title": "标题",
            }
        },
    }
    double_encoded = json.dumps(json.dumps(card))
    assert ark_extract_url(double_encoded) == (
        "https://www.bilibili.com/video/BV1xx411c7mD"
    )


def test_ark_extract_nested_fallback() -> None:
    """URLs in arbitrary nesting are found even without known field names."""
    payload = {
        "app": "bili",
        "meta": {"detail_1": {"host": {"uin": "1", "nick": "up"}}},
        "extra": {"player": {"target": "https://www.bilibili.com/video/av170001"}},
    }
    assert ark_extract_url(json.dumps(payload)) == (
        "https://www.bilibili.com/video/av170001"
    )


@pytest.mark.parametrize(
    "payload",
    [
        None,
        "not json",
        json.dumps({"no": "url"}),
        json.dumps({"meta": "string"}),
        json.dumps(["list"]),
    ],
)
def test_ark_extract_returns_none_for_junk(payload) -> None:
    assert ark_extract_url(payload) is None


def _element(element_type: str, attrs: dict | None = None, children: list | None = None):
    return {"type": element_type, "attrs": attrs or {}, "children": children or []}


def test_collect_candidates_from_text_and_ark() -> None:
    elements = [
        _element("text", {"content": f"正文 https://www.bilibili.com/video/{BV}"}),
        _element(
            "sb:ark",
            {"data": json.dumps({"meta": {"miniapp": {"url": "https://b23.tv/yyy"}}})},
        ),
    ]
    candidates = collect_bilibili_candidates(elements)
    assert len(candidates) == 2


def test_collect_candidates_dedupes_text_and_ark_same_video() -> None:
    elements = [
        _element("text", {"content": BV}),
        _element(
            "sb:ark",
            {"data": json.dumps({"meta": {"miniapp": {"legacyUrl": "https://b23.tv/zzz"}}})},
        ),
        _element("text", {"content": "无链接内容"}),
    ]
    candidates = collect_bilibili_candidates(elements)
    # text BV + ark b23 → two different sources, both kept (b23 is not the same key)
    assert len(candidates) == 2
