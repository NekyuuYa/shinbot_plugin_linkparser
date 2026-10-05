"""Downloader merge-branch unit tests (no network, no ffmpeg execution)."""

from __future__ import annotations

from pathlib import Path

from shinbot_plugin_linkparser.bilibili import download


def test_ffmpeg_missing_returns_false(monkeypatch) -> None:
    monkeypatch.setattr(download.shutil, "which", lambda name: None)
    assert download.ffmpeg_available() is False

    async def run() -> None:
        return await download._ffmpeg_merge(
            Path("/tmp/does-not-matter.video"),
            Path("/tmp/does-not-matter.audio"),
            Path("/tmp/out.mp4"),
        )

    import asyncio

    assert asyncio.run(run()) is False


def test_hls_helper_forces_mp4_muxer(tmp_path, monkeypatch) -> None:
    """Regression: ffmpeg writes to ``*.part``; the muxer must be forced to mp4."""
    import asyncio
    import types
    from pathlib import Path

    from shinbot_plugin_linkparser import ffmpeg_media

    captured: dict = {}

    class _FakeProcess:
        returncode = 0

        async def communicate(self):
            Path(captured["cmd"][-1]).write_bytes(b"\x00\x00\x00\x18ftypisom")
            return b"", b""

    async def fake_exec(*cmd, **_kwargs):
        captured["cmd"] = list(cmd)
        return _FakeProcess()

    monkeypatch.setattr(ffmpeg_media.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(
        ffmpeg_media, "shutil", types.SimpleNamespace(which=lambda name: "/usr/bin/ffmpeg")
    )

    dest = tmp_path / "out.mp4"
    ok = asyncio.run(
        ffmpeg_media.download_hls_with_ffmpeg(
            "https://v/pl/x.m3u8", dest, headers={"Referer": "https://www.xiaohongshu.com/"}
        )
    )
    assert ok is True
    assert dest.read_bytes().startswith(b"\x00\x00\x00\x18ftypisom")
    # the partial output name ends with ".part", so "-f mp4" must be present
    cmd = captured["cmd"]
    assert cmd[-3:-1] == ["-f", "mp4"]
    assert cmd[-1].endswith(".part")
