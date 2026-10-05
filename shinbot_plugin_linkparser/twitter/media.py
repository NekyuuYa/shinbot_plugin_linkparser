"""Media download helpers for X/Twitter (photos + video variants)."""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from ..ffmpeg_media import download_hls_with_ffmpeg, ffmpeg_available
from ..models import XTweetInfo
from .client import REFERER, USER_AGENT, XError, pick_video_variant

logger = logging.getLogger("shinbot_plugin_linkparser.x.media")

_CHUNK_BYTES = 1 << 20
_MP4 = "video/mp4"


async def _stream_to_file(
    http: httpx.AsyncClient,
    url: str,
    dest: Path,
    *,
    headers: dict[str, str],
    label: str,
    max_bytes: int = 0,
) -> None:
    """Stream *url* into *dest* with a size guard, raising XError on failure."""
    try:
        async with http.stream("GET", url, headers=headers) as response:
            if response.status_code != 200:
                raise XError(f"{label}下载失败（HTTP {response.status_code}）。")
            content_length = int(response.headers.get("content-length") or 0)
            if max_bytes > 0 and content_length > max_bytes:
                raise XError(f"{label}体积超过上限（{max_bytes // (1024 * 1024)}MB）。")
            dest.parent.mkdir(parents=True, exist_ok=True)
            partial = dest.with_name(f"{dest.name}.part")
            total = 0
            try:
                with open(partial, "wb") as file_obj:
                    async for chunk in response.aiter_bytes(_CHUNK_BYTES):
                        total += len(chunk)
                        if max_bytes > 0 and total > max_bytes:
                            raise XError(
                                f"{label}体积超过上限（{max_bytes // (1024 * 1024)}MB）。"
                            )
                        file_obj.write(chunk)
            except BaseException:
                partial.unlink(missing_ok=True)
                raise
            partial.replace(dest)
    except httpx.HTTPError as exc:
        raise XError(f"{label}网络下载失败，请稍后再试。") from exc


async def download_photo(
    http: httpx.AsyncClient,
    url: str,
    dest: Path,
    *,
    max_bytes: int = 0,
) -> None:
    """Download one tweet photo.

    Args:
        http: Shared HTTP client.
        url: Photo URL (already normalized to the ``large`` size).
        dest: Destination file path.
        max_bytes: Optional size cap (0 disables).

    Raises:
        XError: On HTTP failure or oversize.
    """
    await _stream_to_file(
        http,
        url,
        dest,
        headers={"User-Agent": USER_AGENT, "Referer": REFERER},
        label="图片",
        max_bytes=max_bytes,
    )


async def download_video(
    http: httpx.AsyncClient,
    info: XTweetInfo,
    dest: Path,
    *,
    max_height: int = 720,
    max_bytes: int = 0,
) -> tuple[bool, int | None]:
    """Download the selected video variant for a post.

    Progressive ``video/mp4`` variants are streamed directly; HLS-only posts
    are downloaded through ffmpeg.

    Args:
        http: Shared HTTP client.
        info: Parsed tweet info carrying ``video_variants``.
        dest: Output mp4 path.
        max_height: Preferred maximum resolution.
        max_bytes: Optional size cap (0 disables).

    Returns:
        ``(success, height)`` where *height* is the chosen variant height.

    Raises:
        XError: When the post has no playable variant or ffmpeg is missing.
    """
    variant = pick_video_variant(info.video_variants, max_height=max_height)
    if variant is None:
        raise XError("该推文没有可下载的视频流。")
    url = str(variant.get("url") or "")
    height = variant.get("height")
    if str(variant.get("content_type")) == _MP4:
        await _stream_to_file(
            http,
            url,
            dest,
            headers={"User-Agent": USER_AGENT, "Referer": REFERER},
            label="视频",
            max_bytes=max_bytes,
        )
        return True, height if isinstance(height, int) else None
    if not ffmpeg_available():
        raise XError("下载该推文视频需要 ffmpeg，当前环境未安装。")
    ok = await download_hls_with_ffmpeg(
        url,
        dest,
        headers={"User-Agent": USER_AGENT, "Referer": REFERER},
    )
    return ok, height if isinstance(height, int) else None
