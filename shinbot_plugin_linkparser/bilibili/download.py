"""Streamed media download and optional ffmpeg merge for Bilibili.

The Bilibili CDN requires a browser User-Agent plus a ``Referer`` header
(``https://www.bilibili.com/``), otherwise downloads return 403. Single-file
HTML5 mp4 plans download directly; DASH plans need their two streams merged
with ``ffmpeg -c copy``.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

import httpx

from ..models import DashPlan, SingleFilePlan
from .client import REFERER, USER_AGENT, BilibiliError

_CHUNK_BYTES = 1 << 20  # 1 MiB read chunks

logger = logging.getLogger("shinbot_plugin_linkparser.download")


def ffmpeg_available() -> bool:
    """Return True when an ``ffmpeg`` binary is available on PATH."""
    return shutil.which("ffmpeg") is not None


async def download_plan_to_file(
    http: httpx.AsyncClient,
    plan: SingleFilePlan | DashPlan,
    dest: Path,
    *,
    max_bytes: int = 0,
) -> Path:
    """Download a stream plan into *dest* (single file or ffmpeg-merged mp4).

    Args:
        http: Shared HTTP client (owned by the BilibiliClient).
        plan: The resolved stream plan.
        dest: Destination file path.
        max_bytes: Optional download size cap (0 disables the cap).

    Returns:
        The produced file path.

    Raises:
        BilibiliError: On HTTP failure or when size limits are exceeded.
    """
    if isinstance(plan, SingleFilePlan):
        await _download_stream(http, plan.url, dest, max_bytes=max_bytes, label="视频")
        return dest

    video_path = dest.with_name(f"{dest.name}.video.part")
    audio_path = dest.with_name(f"{dest.name}.audio.part")
    try:
        await _download_stream(
            http, plan.video_url, video_path, max_bytes=max_bytes, label="视频流"
        )
        await _download_stream(
            http, plan.audio_url, audio_path, max_bytes=max_bytes, label="音频流"
        )
        if not await _ffmpeg_merge(video_path, audio_path, dest):
            raise BilibiliError("音视频合并失败（ffmpeg 不可用或出错）。")
    finally:
        video_path.unlink(missing_ok=True)
        audio_path.unlink(missing_ok=True)
    return dest


async def _download_stream(
    http: httpx.AsyncClient,
    url: str,
    dest: Path,
    *,
    max_bytes: int,
    label: str,
) -> None:
    """Stream *url* into *dest* with Bilibili headers and a size cap."""
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": REFERER,
        "Accept": "*/*",
    }
    try:
        async with http.stream("GET", url, headers=headers) as response:
            if response.status_code not in (200, 206):
                raise BilibiliError(f"{label}下载失败（HTTP {response.status_code}）。")
            content_length = int(response.headers.get("content-length") or 0)
            size_limit_mb = max_bytes // (1024 * 1024)
            if max_bytes > 0 and content_length > max_bytes:
                raise BilibiliError(f"{label}体积超过设定上限（{size_limit_mb}MB）。")

            dest.parent.mkdir(parents=True, exist_ok=True)
            partial = dest.with_name(f"{dest.name}.part")
            total = 0
            try:
                with open(partial, "wb") as file_obj:
                    async for chunk in response.aiter_bytes(_CHUNK_BYTES):
                        total += len(chunk)
                        if max_bytes > 0 and total > max_bytes:
                            raise BilibiliError(
                                f"{label}体积超过设定上限（{size_limit_mb}MB）。"
                            )
                        file_obj.write(chunk)
            except BaseException:
                partial.unlink(missing_ok=True)
                raise
            partial.replace(dest)
    except httpx.HTTPError as exc:
        raise BilibiliError(f"{label}网络下载失败，请稍后再试。") from exc


async def _ffmpeg_merge(video_path: Path, audio_path: Path, dest: Path) -> bool:
    """Merge separate DASH streams into a single mp4 with ``ffmpeg -c copy``."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(f"{dest.name}.part")
    command = [
        ffmpeg,
        "-nostdin",
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(video_path),
        "-i",
        str(audio_path),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c",
        "copy",
        "-f",
        "mp4",
        str(partial),
    ]
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
        if process.returncode != 0:
            logger.warning("ffmpeg merge failed: %s", stderr.decode(errors="ignore")[:500])
            partial.unlink(missing_ok=True)
            return False
        partial.replace(dest)
        return True
    except (OSError, asyncio.CancelledError):
        partial.unlink(missing_ok=True)
        if asyncio.current_task() is not None and asyncio.current_task().cancelling():
            raise
        return False


async def compress_to_target(
    src: Path,
    dest: Path,
    *,
    target_bytes: int,
    duration_seconds: int,
    max_height: int = 720,
) -> bool:
    """Re-encode *src* toward *target_bytes* with ffmpeg (H.264 + AAC).

    Used to shrink videos that exceed the platform send cap: no need to upload
    the source's best quality. Resolution is capped at *max_height* and the
    video bitrate is derived from the target size and the source duration so
    the result lands near the cap while staying playable.

    Args:
        src: Source mp4.
        dest: Output path (may equal *src*; replaced only on success).
        target_bytes: Size budget in bytes for the encoded file.
        duration_seconds: Source duration used for bitrate estimation.
        max_height: Maximum frame height (aspect ratio preserved).

    Returns:
        True when the compressed file was produced and moved to *dest*.
    """
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(f"{dest.name}.part")

    duration = max(1, int(duration_seconds))
    audio_bytes_budget = duration * 96_000 // 8  # ~96kbps AAC
    video_budget = max(0, int(target_bytes * 0.9) - audio_bytes_budget)
    video_kbps = max(200, int(video_budget * 8 / duration / 1000))
    video_kbps = min(video_kbps, 6000)
    height = max(1, min(int(max_height), 2160))

    command = [
        ffmpeg,
        "-nostdin",
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(src),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-vf",
        f"scale=-2:min(ih\\,{height}),pad=ceil(iw/2)*2:ceil(ih/2)*2:0:0",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-b:v",
        f"{video_kbps}k",
        "-maxrate",
        f"{int(video_kbps * 1.5)}k",
        "-bufsize",
        f"{int(video_kbps * 2)}k",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "96k",
        "-movflags",
        "+faststart",
        "-f",
        "mp4",
        str(partial),
    ]
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
        if process.returncode != 0:
            logger.warning(
                "ffmpeg compression failed: %s",
                stderr.decode(errors="ignore")[:500],
            )
            partial.unlink(missing_ok=True)
            return False
        if not partial.is_file() or partial.stat().st_size <= 0:
            partial.unlink(missing_ok=True)
            return False
        partial.replace(dest)
        return True
    except (OSError, asyncio.CancelledError):
        partial.unlink(missing_ok=True)
        if asyncio.current_task() is not None and asyncio.current_task().cancelling():
            raise
        return False
