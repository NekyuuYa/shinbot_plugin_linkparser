"""High-level parse orchestration: a LinkCandidate becomes a playable mp4 file.

Kept independent of ShinBot message plumbing so it can be reused by future
platform parsers and tested against a fake client.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .bilibili import (
    BilibiliClient,
    BilibiliError,
    DashPlan,
    compress_to_target,
    download_plan_to_file,
    ffmpeg_available,
)
from .models import LinkCandidate, ParseOutcome, VideoMeta
from .urls import find_bilibili_candidates

logger = logging.getLogger("shinbot_plugin_linkparser.parsers")


class VideoTooLongError(BilibiliError):
    """Raised when the target video exceeds the configured duration limit."""


class VideoTooLargeError(BilibiliError):
    """Raised when the target video exceeds the configured size limit."""


async def parse_video(
    client: BilibiliClient,
    candidate: LinkCandidate,
    *,
    data_dir: Path,
    max_duration_seconds: int = 0,
    max_size_mb: int = 0,
    prefer_mp4: bool = True,
    max_quality: int = 80,
    cache_max_files: int = 0,
    max_send_mb: int = 0,
    compress: bool = True,
    compress_max_height: int = 720,
) -> ParseOutcome:
    """Resolve, download (or reuse) and produce a local mp4 for *candidate*.

    Steps: expand ``b23.tv`` short links → fetch metadata → apply duration and
    size guards → reuse an existing cache file when present, otherwise resolve
    a stream plan and download (merging DASH streams with ffmpeg when needed).
    When the result exceeds *max_send_mb*, re-encode it with ffmpeg toward the
    send cap (no need to upload the source's best quality). Afterward the video
    cache directory is pruned to *cache_max_files*.

    Args:
        client: Initialised Bilibili client.
        candidate: The link candidate to parse.
        data_dir: Plugin data directory (``videos/`` is created below it).
        max_duration_seconds: Duration cap in seconds (0 disables).
        max_size_mb: Download size cap in MB (0 disables).
        prefer_mp4: Prefer the single-file HTML5 mp4 stream.
        max_quality: Maximum quality (qn) allowed for the DASH fallback.
        cache_max_files: Max mp4 files kept in the cache dir (0 disables).
        max_send_mb: Send size cap in MB; oversized files are compressed toward
            this cap when possible (0 disables compression).
        compress: Allow ffmpeg compression toward the send cap.
        compress_max_height: Maximum frame height for compressed output.

    Returns:
        A :class:`ParseOutcome` with the produced file path and metadata.

    Raises:
        BilibiliError / subclasses: On any failure, with a user-facing message.
    """
    resolved = candidate
    if candidate.needs_redirect:
        canonical = await client.resolve_short_url(candidate.matched)
        candidates = find_bilibili_candidates(canonical)
        for found in candidates:
            if found.bvid is not None or found.avid is not None:
                resolved = found
                break
        else:
            raise BilibiliError("短链接跳转后未能识别视频 ID。")

    bvid = resolved.bvid
    avid = resolved.avid
    if bvid is None and avid is None:
        raise BilibiliError("无法识别该链接对应的视频 ID。")

    meta = await client.fetch_meta(bvid=bvid, avid=avid, page=resolved.page)

    if max_duration_seconds > 0 and meta.duration_seconds > max_duration_seconds:
        minutes = meta.duration_seconds // 60
        raise VideoTooLongError(
            f"视频时长 {minutes} 分钟，超过设定上限 "
            f"（{max_duration_seconds // 60} 分钟），已跳过下载。"
        )

    videos_dir = Path(data_dir) / "videos"
    videos_dir.mkdir(parents=True, exist_ok=True)
    dest = videos_dir / _video_file_name(meta)

    if _reusable_video(dest):
        outcome = ParseOutcome(path=dest, meta=meta)
    else:
        if meta.cid is None:
            raise BilibiliError("无法获取该视频的分 P 信息。")
        plan = await client.resolve_stream(
            bvid=meta.bvid,
            avid=meta.avid,
            cid=meta.cid,
            page_index=meta.page_index,
            prefer_mp4=prefer_mp4,
            max_quality=max_quality,
        )
        if isinstance(plan, DashPlan) and not ffmpeg_available():
            raise BilibiliError("该视频需要 ffmpeg 合并音视频流，但环境中未找到 ffmpeg。")
        max_bytes = max_size_mb * 1024 * 1024
        await download_plan_to_file(
            client.http,
            plan,
            dest,
            max_bytes=max_bytes,
        )
        outcome = ParseOutcome(path=dest, meta=meta)

    if (
        max_send_mb > 0
        and compress
        and meta.duration_seconds > 0
        and _file_size_mb(dest) > max_send_mb
    ):
        # Compress toward the send cap so oversized downloads can still be sent
        # as a (smaller, lower-quality) video instead of a bare link.
        compressed = await compress_to_target(
            dest,
            dest,
            target_bytes=max_send_mb * 1024 * 1024,
            duration_seconds=meta.duration_seconds,
            max_height=compress_max_height,
        )
        if compressed:
            logger.info(
                "LinkParser compressed %s to %dMB for send cap %dMB",
                dest.name,
                _file_size_mb(dest),
                max_send_mb,
            )
            outcome = ParseOutcome(path=dest, meta=meta)

    if cache_max_files > 0:
        prune_video_cache(videos_dir, keep=cache_max_files)
    return outcome


def _file_size_mb(path: Path) -> int:
    """Return a file's size in whole MiB (0 on any error)."""
    try:
        return int(path.stat().st_size // (1024 * 1024))
    except OSError:
        return 0


def prune_video_cache(videos_dir: Path, keep: int) -> int:
    """Delete oldest mp4 files beyond the newest *keep* (by mtime).

    Args:
        videos_dir: Directory holding cached mp4 files.
        keep: Number of newest files to retain (``<= 0`` keeps everything).

    Returns:
        The number of files removed.
    """
    if keep <= 0 or not videos_dir.is_dir():
        return 0
    files = sorted(
        (path for path in videos_dir.glob("*.mp4") if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    removed = 0
    for stale in files[keep:]:
        try:
            stale.unlink()
            removed += 1
        except OSError:
            continue
    return removed


def _reusable_video(path: Path) -> bool:
    """Return True when a complete cached file already exists for *path*."""
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def delete_cached_file(path: Path) -> bool:
    """Delete a cached video file (best-effort).

    Args:
        path: File to remove.

    Returns:
        True when the file existed and was removed.
    """
    try:
        if path.is_file():
            path.unlink()
            return True
    except OSError:
        return False
    return False


def _video_file_name(meta: VideoMeta) -> str:
    """Build a deterministic, safe output file name for a video part."""
    identifier = (meta.bvid or f"av{meta.avid}").lower()
    return f"{identifier}_p{meta.page}.mp4"
