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
