"""Generic ffmpeg-based media helpers shared by platform pipelines.

Currently provides HLS (m3u8) → mp4 downloading with caller-supplied HTTP
headers, used by the Xiaohongshu and X/Twitter video paths.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from collections.abc import Mapping
from pathlib import Path

logger = logging.getLogger("shinbot_plugin_linkparser.ffmpeg_media")


def ffmpeg_available() -> bool:
    """Return True when an ``ffmpeg`` binary is available on PATH."""
    return shutil.which("ffmpeg") is not None


async def download_hls_with_ffmpeg(
    url: str,
    dest: Path,
    *,
    headers: Mapping[str, str] | None = None,
) -> bool:
    """Download an HLS master playlist into a single mp4 via ``ffmpeg -c copy``.

    Args:
        url: The HLS master playlist URL.
        dest: Output mp4 path.
        headers: Extra HTTP headers forwarded to ffmpeg (Referer / UA /
            Cookie, as required by the source platform).

    Returns:
        True when the output file was produced.
    """
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(f"{dest.name}.part")
    header_text = "".join(f"{key}: {value}\r\n" for key, value in (headers or {}).items())
    command = [ffmpeg, "-nostdin", "-y", "-loglevel", "error"]
    if header_text:
        command += ["-headers", header_text]
    # NOTE: the partial name ends with ".part", so the muxer must be
    # forced explicitly (ffmpeg cannot infer a format from the extension).
    command += ["-i", url, "-c", "copy", "-f", "mp4", str(partial)]
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
        if process.returncode != 0:
            logger.warning(
                "HLS download failed: %s", stderr.decode(errors="ignore")[:500]
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
