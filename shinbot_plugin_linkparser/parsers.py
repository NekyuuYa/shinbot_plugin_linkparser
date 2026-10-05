"""High-level parse orchestration: a LinkCandidate becomes a playable mp4 file.

Kept independent of ShinBot message plumbing so it can be reused by future
platform parsers and tested against a fake client.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .bilibili import (
    BilibiliClient,
    BilibiliError,
    DashPlan,
    compress_to_target,
    download_plan_to_file,
    ffmpeg_available,
)
from .models import (
    LinkCandidate,
    MediaItem,
    ParseOutcome,
    VideoMeta,
    XHSOutcome,
    XOutcome,
)
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


async def parse_xhs_note(
    xhs_client: Any,
    candidate: LinkCandidate,
    *,
    data_dir: Path,
    image_mode: str = "long",
    max_images: int = 9,
    stitch_max_height: int = 12000,
    video_max_height: int = 720,
    max_size_mb: int = 0,
    max_send_mb: int = 0,
    compress: bool = True,
    compress_max_height: int = 720,
) -> XHSOutcome:
    """Resolve and download a Xiaohongshu note (video or image gallery).

    Steps: expand ``xhslink`` short links → fetch note info from the page's
    ``__INITIAL_STATE__`` → video notes are downloaded via HLS (ffmpeg) and
    compressed toward *max_send_mb*; image notes are downloaded and, when
    *image_mode* is ``long``, stitched into a single long image.

    Args:
        xhs_client: Initialised Xiaohongshu client.
        candidate: The note link candidate.
        data_dir: Plugin data directory (``xhs/`` is created below it).
        image_mode: ``"long"`` to stitch galleries into one image, ``"raw"``
            to keep individual images.
        max_images: Maximum number of gallery images to fetch.
        stitch_max_height: Height cap for stitched long images.
        video_max_height: Preferred maximum video height (progressive mp4).
        max_size_mb: Download size cap in MB (0 disables).
        max_send_mb: Send size cap for video notes (0 disables compression).
        compress: Allow compression toward the send cap.
        compress_max_height: Max height for compressed video.

    Returns:
        An :class:`XHSOutcome` describing the produced local files.

    Raises:
        XHSError / BilibiliError: On failure, with a user-facing message.
    """
    from .bilibili import compress_to_target, probe_duration_seconds
    from .urls import find_xhs_candidates
    from .xiaohongshu import (
        XHSError,
        download_hls_video,
        download_note_image,
        download_note_video,
        pick_video_variant,
        stitch_to_long_image,
    )

    resolved = candidate
    if candidate.needs_redirect:
        canonical = await xhs_client.resolve_short_url(candidate.matched)
        for found in find_xhs_candidates(canonical):
            if found.note_id is not None or found.note_url is not None:
                resolved = found
                break
        else:
            raise XHSError("小红书短链接跳转后未能识别笔记 ID。")

    note_url = resolved.note_url
    if not note_url:
        raise XHSError("无法识别该链接对应的小红书笔记。")
    info = await xhs_client.fetch_note(note_url, note_id=resolved.note_id)

    base_dir = Path(data_dir) / "xhs"
    base_dir.mkdir(parents=True, exist_ok=True)
    note_key = info.note_id or resolved.note_id or "note"

    if info.note_type == "video":
        variants = list(info.video_variants)
        if not variants and info.video_master_url:
            variants = [{"url": info.video_master_url, "format": "", "height": None}]
        chosen = pick_video_variant(variants, max_height=video_max_height)
        if chosen is None or not chosen.get("url"):
            raise XHSError("该视频笔记暂无可用的播放流（可能已删除或需登录）。")
        dest = base_dir / f"{note_key}_video.mp4"
        if not _reusable_video(dest):
            video_url = str(chosen["url"])
            is_mp4 = chosen.get("format") == "mp4" or (
                video_url.split("?")[0].endswith(".mp4")
            )
            if is_mp4:
                # Progressive mp4 (most Xiaohongshu videos): plain HTTP download.
                await download_note_video(
                    xhs_client.http,
                    video_url,
                    dest,
                    max_bytes=max_size_mb * 1024 * 1024,
                )
            else:
                downloaded = await download_hls_video(
                    video_url, dest, cookie=xhs_client.cookie
                )
                if not downloaded:
                    raise XHSError("小红书视频下载失败（可能受保护或 ffmpeg 缺失）。")
        if max_send_mb > 0 and compress and _file_size_mb(dest) > max_send_mb:
            duration = await probe_duration_seconds(dest)
            if duration and duration > 0:
                await compress_to_target(
                    dest,
                    dest,
                    target_bytes=max_send_mb * 1024 * 1024,
                    duration_seconds=int(duration),
                    max_height=compress_max_height,
                )
        return XHSOutcome(kind="video", media=[MediaItem("video", dest)], info=info)

    if not info.image_urls:
        raise XHSError("该小红书笔记没有可下载的图片/视频内容。")
    image_paths: list[Path] = []
    for index, url in enumerate(info.image_urls[:max_images], start=1):
        dest = base_dir / f"{note_key}_{index}.jpg"
        if dest.is_file() and dest.stat().st_size > 0:
            image_paths.append(dest)
            continue
        try:
            await download_note_image(xhs_client.http, url, dest)
            image_paths.append(dest)
        except XHSError as exc:
            logger.warning("xhs image %d download failed: %s", index, exc)
            continue
    if not image_paths:
        raise XHSError("小红书图片下载失败（可能被风控或链接已失效）。")

    if image_mode == "long" and len(image_paths) > 1:
        stitched = base_dir / f"{note_key}_long.jpg"
        out = stitch_to_long_image(
            image_paths,
            stitched,
            max_height=stitch_max_height,
        )
        if out is not None:
            return XHSOutcome(kind="images", media=[MediaItem("image", out)], info=info)
        logger.info("xhs stitch unavailable; sending raw images")
    return XHSOutcome(
        kind="images",
        media=[MediaItem("image", path) for path in image_paths],
        info=info,
    )


async def parse_x_post(
    x_client: Any,
    candidate: LinkCandidate,
    *,
    data_dir: Path,
    image_mode: str = "long",
    stitch_max_height: int = 12000,
    video_max_height: int = 720,
    max_size_mb: int = 0,
    max_send_mb: int = 0,
    compress: bool = True,
    compress_max_height: int = 720,
) -> XOutcome:
    """Resolve and download an X/Twitter post (video, photos or text-only).

    Media attachments are processed in tweet order: each video downloads the
    best progressive mp4 variant no taller than *video_max_height* (HLS falls
    back to ffmpeg) and is compressed toward *max_send_mb*; photos are
    downloaded individually (or stitched when *image_mode* is ``long``).
    Posts mixing photos and videos keep both (``kind="mixed"``); text-only
    posts return no media.

    Args:
        x_client: Initialised X/Twitter client.
        candidate: The status link candidate.
        data_dir: Plugin data directory (``x/`` is created below it).
        image_mode: ``"long"`` to stitch multi-photo posts, ``"raw"`` to keep
            individual images.
        stitch_max_height: Height cap for stitched long images.
        video_max_height: Preferred maximum download resolution.
        max_size_mb: Download size cap in MB (0 disables).
        max_send_mb: Send size cap for videos (0 disables compression).
        compress: Allow compression toward the send cap.
        compress_max_height: Max height for compressed video.

    Returns:
        An :class:`XOutcome` describing the produced local files.

    Raises:
        XError: On failure, with a user-facing message.
    """
    from .bilibili import compress_to_target, probe_duration_seconds
    from .imagestitch import stitch_to_long_image
    from .twitter import XError, download_photo, download_video, pick_video_variant

    status_id = candidate.status_id
    if not status_id:
        raise XError("无法识别该链接对应的 X/Twitter 推文。")
    info = await x_client.fetch_tweet(status_id)

    base_dir = Path(data_dir) / "x"
    base_dir.mkdir(parents=True, exist_ok=True)

    media: list[MediaItem] = []
    media_failed = False
    unavailable = 0
    for position, item in enumerate(info.media, start=1):
        if not item.available:
            unavailable += 1
            logger.info("x media %d unavailable/withheld", position)
            continue
        if item.is_video:
            variants = list(item.variants)
            if pick_video_variant(variants, max_height=video_max_height) is None:
                continue
            dest = base_dir / f"{status_id}_video{position}.mp4"
            if not _reusable_video(dest):
                downloaded, _height = await download_video(
                    x_client.http,
                    variants,
                    dest,
                    max_height=video_max_height,
                    max_bytes=max_size_mb * 1024 * 1024,
                )
                if not downloaded:
                    media_failed = True
                    continue
            if max_send_mb > 0 and compress and _file_size_mb(dest) > max_send_mb:
                duration = item.duration or await probe_duration_seconds(dest)
                if duration and duration > 0:
                    await compress_to_target(
                        dest,
                        dest,
                        target_bytes=max_send_mb * 1024 * 1024,
                        duration_seconds=int(duration),
                        max_height=compress_max_height,
                    )
            media.append(MediaItem("video", dest))
            continue
        # photo attachment
        if not item.url:
            continue
        dest = base_dir / f"{status_id}_{position}.jpg"
        if dest.is_file() and dest.stat().st_size > 0:
            media.append(MediaItem("image", dest))
            continue
        try:
            await download_photo(x_client.http, item.url, dest)
            media.append(MediaItem("image", dest))
        except XError as exc:
            logger.warning("x photo %d download failed: %s", position, exc)
            media_failed = True
            continue

    if not media:
        if unavailable and unavailable == len(info.media):
            raise XError("该推文的媒体已被 X 限制（敏感或地区限制），无法下载。")
        if info.media or media_failed:
            raise XError("X 推文媒体下载失败（可能受保护或链接已失效）。")
        return XOutcome(kind="text", media=[], info=info)

    if (
        image_mode == "long"
        and len(media) > 1
        and all(entry.kind == "image" for entry in media)
    ):
        stitched = base_dir / f"{status_id}_long.jpg"
        stitched_path = stitch_to_long_image(
            [Path(entry.path) for entry in media],
            stitched,
            max_height=stitch_max_height,
        )
        if stitched_path is not None:
            media = [MediaItem("image", stitched_path)]

    has_video = any(entry.kind == "video" for entry in media)
    has_image = any(entry.kind == "image" for entry in media)
    kind = "mixed" if has_video and has_image else ("video" if has_video else "images")
    return XOutcome(kind=kind, media=media, info=info)
