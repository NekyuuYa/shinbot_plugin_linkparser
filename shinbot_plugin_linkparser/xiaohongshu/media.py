"""Media download helpers for Xiaohongshu (images + HLS video)."""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

import httpx

from .client import REFERER, USER_AGENT, XHSError

logger = logging.getLogger("shinbot_plugin_linkparser.xhs.media")

_CHUNK_BYTES = 1 << 20


async def download_note_image(
    http: httpx.AsyncClient,
    url: str,
    dest: Path,
    *,
    max_bytes: int = 0,
) -> None:
    """Download one note image with Xiaohongshu referer/UA headers.

    Args:
        http: Shared HTTP client.
        url: Image URL (usually a ``sns-webpic`` CDN link).
        dest: Destination file path.
        max_bytes: Optional size cap (0 disables).

    Raises:
        XHSError: On HTTP failure or oversize.
    """
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": REFERER,
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    }
    try:
        async with http.stream("GET", url, headers=headers) as response:
            if response.status_code != 200:
                raise XHSError(f"图片下载失败（HTTP {response.status_code}）。")
            content_length = int(response.headers.get("content-length") or 0)
            if max_bytes > 0 and content_length > max_bytes:
                raise XHSError(f"图片体积超过上限（{max_bytes // (1024 * 1024)}MB）。")
            dest.parent.mkdir(parents=True, exist_ok=True)
            partial = dest.with_name(f"{dest.name}.part")
            total = 0
            try:
                with open(partial, "wb") as file_obj:
                    async for chunk in response.aiter_bytes(_CHUNK_BYTES):
                        total += len(chunk)
                        if max_bytes > 0 and total > max_bytes:
                            raise XHSError(
                                f"图片体积超过上限（{max_bytes // (1024 * 1024)}MB）。"
                            )
                        file_obj.write(chunk)
            except BaseException:
                partial.unlink(missing_ok=True)
                raise
            partial.replace(dest)
    except httpx.HTTPError as exc:
        raise XHSError("图片网络下载失败，请稍后再试。") from exc


async def download_hls_video(
    master_url: str,
    dest: Path,
    *,
    cookie: str = "",
) -> bool:
    """Download an HLS master playlist into a single mp4 via ffmpeg.

    Xiaohongshu video notes expose HLS master URLs (``masterUrl``); the ts
    segments are merged with ``ffmpeg -c copy``. Requires ffmpeg on PATH.

    Args:
        master_url: The HLS master playlist URL.
        dest: Output mp4 path.
        cookie: Optional web cookie forwarded to every HTTP request.

    Returns:
        True when the file was produced.

    Raises:
        XHSError: When ffmpeg is missing.
    """
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise XHSError("下载小红书视频需要 ffmpeg，当前环境未安装。")
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(f"{dest.name}.part")
    headers = f"Referer: {REFERER}\r\nUser-Agent: {USER_AGENT}\r\n"
    if cookie:
        headers += f"Cookie: {cookie}\r\n"
    command = [
        ffmpeg,
        "-nostdin",
        "-y",
        "-loglevel",
        "error",
        "-headers",
        headers,
        "-i",
        master_url,
        "-c",
        "copy",
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
                "xhs HLS download failed: %s", stderr.decode(errors="ignore")[:500]
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
