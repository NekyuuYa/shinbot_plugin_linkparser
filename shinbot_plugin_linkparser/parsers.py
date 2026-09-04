"""High-level parse orchestration: a LinkCandidate becomes a playable mp4 file.

Kept independent of ShinBot message plumbing so it can be reused by future
platform parsers and tested against a fake client.
"""

from __future__ import annotations

from pathlib import Path

from .bilibili import (
    BilibiliClient,
    BilibiliError,
    DashPlan,
    download_plan_to_file,
    ffmpeg_available,
)
from .models import LinkCandidate, ParseOutcome, VideoMeta
from .urls import find_bilibili_candidates


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
) -> ParseOutcome:
    """Resolve, download and produce a local mp4 for *candidate*.

    Steps: expand ``b23.tv`` short links → fetch metadata → apply duration and
    size guards → resolve a stream plan → download (merging DASH streams with
    ffmpeg when needed).

    Args:
        client: Initialised Bilibili client.
        candidate: The link candidate to parse.
        data_dir: Plugin data directory (``videos/`` is created below it).
        max_duration_seconds: Duration cap in seconds (0 disables).
        max_size_mb: Download size cap in MB (0 disables).
        prefer_mp4: Prefer the single-file HTML5 mp4 stream.
        max_quality: Maximum quality (qn) allowed for the DASH fallback.

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

    videos_dir = Path(data_dir) / "videos"
    videos_dir.mkdir(parents=True, exist_ok=True)
    dest = videos_dir / _video_file_name(meta)
    max_bytes = max_size_mb * 1024 * 1024
    await download_plan_to_file(
        client.http,
        plan,
        dest,
        max_bytes=max_bytes,
    )
    return ParseOutcome(path=dest, meta=meta)


def _video_file_name(meta: VideoMeta) -> str:
    """Build a deterministic, safe output file name for a video part."""
    identifier = (meta.bvid or f"av{meta.avid}").lower()
    return f"{identifier}_p{meta.page}.mp4"
