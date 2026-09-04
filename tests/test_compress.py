"""Real ffmpeg compression smoke test (skipped when ffmpeg is missing).

Synthesizes a small high-bitrate clip, then verifies compress_to_target
produces a playable mp4 within the requested size budget. Fast (~seconds).
"""

from __future__ import annotations

import asyncio
import shutil
import types

import pytest

from shinbot_plugin_linkparser.bilibili.download import compress_to_target

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg not installed"
)


async def _run_ffmpeg(args: list[str]) -> None:
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    assert process.returncode == 0, stderr.decode(errors="ignore")[-500:]


def test_compress_to_target_reduces_size_and_stays_playable(tmp_path) -> None:
    asyncio.run(
        _run_ffmpeg(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=1280x720:rate=30",
                "-t",
                "6",
                "-c:v",
                "libx264",
                "-b:v",
                "2000k",
                "-pix_fmt",
                "yuv420p",
                str(tmp_path / "src.mp4"),
            ]
        )
    )
    source = tmp_path / "src.mp4"
    assert source.stat().st_size > 1_000_000

    compressed = tmp_path / "out.mp4"
    ok = asyncio.run(
        compress_to_target(
            source,
            compressed,
            target_bytes=300 * 1024,
            duration_seconds=6,
            max_height=360,
        )
    )
    assert ok is True
    assert compressed.is_file()
    assert 0 < compressed.stat().st_size < 500 * 1024
    # still an mp4 (ftyp box at offset 4)
    assert compressed.read_bytes()[4:8] == b"ftyp"


def test_compress_to_target_returns_false_without_ffmpeg(
    tmp_path, monkeypatch
) -> None:
    from shinbot_plugin_linkparser.bilibili import download

    # Patch only the module's shutil binding (not the global shutil module).
    monkeypatch.setattr(
        download, "shutil", types.SimpleNamespace(which=lambda name: None)
    )
    result = asyncio.run(
        compress_to_target(
            tmp_path / "src.mp4",
            tmp_path / "out.mp4",
            target_bytes=300 * 1024,
            duration_seconds=6,
            max_height=360,
        )
    )
    assert result is False
