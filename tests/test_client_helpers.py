"""Unit tests for BilibiliClient stream-plan helpers (no network)."""

from __future__ import annotations

from shinbot_plugin_linkparser.bilibili import (
    BilibiliClient,
    BilibiliError,
    DashPlan,
    SingleFilePlan,
)


def _segment() -> dict:
    return {"initialization": "0-100", "index_range": "101-200"}


def _dash_data() -> dict:
    """Synthetic playurl response resembling a real DASH result."""
    return {
        "format": "mp4",
        "dash": {
            "duration": 100,
            "video": [
                {
                    "id": 32,
                    "base_url": "https://cdn/v480.m4s",
                    "backup_url": ["https://backup/v480.m4s"],
                    "bandwidth": 300000,
                    "codecs": "avc1.64001E",
                    "width": 854,
                    "height": 480,
                    "frame_rate": "30",
                    "sar": "1:1",
                    "mime_type": "video/mp4",
                    "segment_base": _segment(),
                },
                {
                    "id": 32,
                    "base_url": "https://cdn/v480hev.m4s",
                    "backup_url": [],
                    "bandwidth": 250000,
                    "codecs": "hev1.1.6.L120.B0",
                    "width": 854,
                    "height": 480,
                    "frame_rate": "30",
                    "sar": "1:1",
                    "mime_type": "video/mp4",
                    "segment_base": _segment(),
                },
                {
                    "id": 16,
                    "base_url": "https://cdn/v360.m4s",
                    "backup_url": [],
                    "bandwidth": 150000,
                    "codecs": "avc1.64001E",
                    "width": 640,
                    "height": 360,
                    "frame_rate": "30",
                    "sar": "1:1",
                    "mime_type": "video/mp4",
                    "segment_base": _segment(),
                },
            ],
            "audio": [
                {
                    "id": 30280,
                    "base_url": "https://cdn/a192.m4s",
                    "backup_url": [],
                    "bandwidth": 192000,
                    "codecs": "mp4a.40.2",
                    "mime_type": "audio/mp4",
                    "segment_base": _segment(),
                },
                {
                    "id": 30216,
                    "base_url": "https://cdn/a64.m4s",
                    "backup_url": [],
                    "bandwidth": 64000,
                    "codecs": "mp4a.40.2",
                    "mime_type": "audio/mp4",
                    "segment_base": _segment(),
                },
            ],
        },
    }


def test_plan_from_html5() -> None:
    client = BilibiliClient()
    plan = client._plan_from_html5(
        {"durl": [{"url": "https://cdn/a.mp4", "size": 12345, "length": 60000}]}
    )
    assert isinstance(plan, SingleFilePlan)
    assert plan.url == "https://cdn/a.mp4"
    assert plan.size == 12345
    assert plan.length_ms == 60000

def test_plan_from_html5_rejects_empty() -> None:
    client = BilibiliClient()
    assert client._plan_from_html5({}) is None
    assert client._plan_from_html5({"durl": []}) is None
    assert client._plan_from_html5({"durl": [{"url": ""}]}) is None


def test_plan_from_dash_picks_best_avc() -> None:
    client = BilibiliClient()
    plan = client._plan_from_dash(_dash_data(), max_quality=80)
    assert isinstance(plan, DashPlan)
    assert plan.video_url == "https://cdn/v480.m4s"  # AVC preferred over HEVC
    assert plan.audio_url == "https://cdn/a192.m4s"
    assert plan.quality == 32


def test_plan_from_dash_respects_quality_cap() -> None:
    client = BilibiliClient()
    plan = client._plan_from_dash(_dash_data(), max_quality=16)
    assert isinstance(plan, DashPlan)
    assert plan.video_url == "https://cdn/v360.m4s"
    assert plan.quality == 16


def test_translate_api_error_codes() -> None:
    client = BilibiliClient()

    class _FakeError(Exception):
        code = -404
        msg = "啥都木有"

    error = client._translate_api_error(_FakeError(), fallback="fallback")
    assert isinstance(error, BilibiliError)
    assert "不存在" in str(error)

    class _Fake403(Exception):
        code = -403

    assert "风控" in str(client._translate_api_error(_Fake403(), fallback="x"))

    generic = client._translate_api_error(RuntimeError("boom"), fallback="兜底消息")
    assert str(generic) == "兜底消息"
