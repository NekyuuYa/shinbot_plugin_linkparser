"""Thin async client over ``bilibili-api-python`` (design option B).

Owns the shared ``httpx`` client used for redirects and media downloads, and
wraps ``bilibili_api.video`` for metadata and stream resolution. Errors are
normalised into :class:`BilibiliError` with user-facing Chinese messages.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from ..models import DashPlan, SingleFilePlan, VideoMeta

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
REFERER = "https://www.bilibili.com/"

# VideoQuality enum values used to bound the DASH merge quality.
_QUALITY_BY_VALUE = {
    16: "_360P",
    32: "_480P",
    64: "_720P",
    80: "_1080P",
    100: "AI_REPAIR",
    112: "_1080P_PLUS",
    116: "_1080P_60",
    120: "_4K",
    125: "HDR",
    126: "DOLBY",
    127: "_8K",
}


class BilibiliError(RuntimeError):
    """Raised when a Bilibili operation fails (message is user-facing)."""

    def __init__(self, message: str, *, code: int | None = None) -> None:
        """Initialize the error.

        Args:
            message: User-facing failure reason (Chinese).
            code: Optional Bilibili API error code.
        """
        super().__init__(message)
        self.code = code


class BilibiliClient:
    """Bilibili metadata / stream client built on ``bilibili_api``.

    Args:
        cookie: Optional ``SESSDATA`` cookie value for higher-quality DASH
            streams (anonymous access is capped by Bilibili).
        logger: Optional logger; defaults to a module logger.
    """

    def __init__(self, *, cookie: str = "", logger: logging.Logger | None = None) -> None:
        self._cookie = cookie or ""
        self.logger = logger or logging.getLogger("shinbot_plugin_linkparser.bilibili")
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=15.0, read=None, write=60.0, pool=10.0),
            follow_redirects=False,
            headers={"User-Agent": USER_AGENT},
        )

    @property
    def http(self) -> httpx.AsyncClient:
        """Return the shared HTTP client (used for media downloads)."""
        return self._http

    async def close(self) -> None:
        """Close the shared HTTP client."""
        await self._http.aclose()

    # ── low-level helpers ────────────────────────────────────────────

    def _credential(self) -> Any | None:
        if not self._cookie:
            return None
        from bilibili_api import Credential  # type: ignore[import-untyped]

        return Credential(sessdata=self._cookie)

    def _make_video(self, *, bvid: str | None = None, avid: int | None = None) -> Any:
        from bilibili_api import video as vapi  # type: ignore[import-untyped]

        return vapi.Video(bvid=bvid, aid=avid, credential=self._credential())

    # ── redirects ────────────────────────────────────────────────────

    async def resolve_short_url(self, short_url: str) -> str:
        """Follow a redirect chain (e.g. ``b23.tv``) and return the final URL.

        Args:
            short_url: The short link to expand.

        Returns:
            The canonical (final) URL after redirects.

        Raises:
            BilibiliError: If the request fails.
        """
        try:
            response = await self._http.get(
                short_url,
                headers={"Referer": REFERER},
                follow_redirects=True,
                timeout=httpx.Timeout(connect=15.0, read=30.0, write=30.0, pool=10.0),
            )
        except httpx.HTTPError as exc:
            raise BilibiliError("短链接跳转失败，请稍后再试。") from exc
        return str(response.url)

    # ── metadata ─────────────────────────────────────────────────────

    async def fetch_meta(
        self,
        *,
        bvid: str | None = None,
        avid: int | None = None,
        page: int = 1,
    ) -> VideoMeta:
        """Fetch video metadata via the view API.

        Args:
            bvid: Bilibili ``BV`` id (mutually exclusive with *avid*).
            avid: Bilibili ``av`` id (mutually exclusive with *bvid*).
            page: 1-based part number to resolve (defaults to 1).

        Returns:
            A populated :class:`VideoMeta`.

        Raises:
            BilibiliError: On API failure or invalid part index.
        """
        video_obj = self._make_video(bvid=bvid, avid=avid)
        try:
            info = await video_obj.get_info()
        except Exception as exc:
            raise self._translate_api_error(exc, fallback="获取视频信息失败，请稍后再试。") from exc

        if not isinstance(info, dict):
            raise BilibiliError("获取视频信息失败：响应格式异常。")

        resolved_bvid = str(info.get("bvid") or bvid or "")
        pages = info.get("pages") or []
        if page > len(pages):
            page = len(pages) if pages else 1
        page_index = max(0, page - 1)
        page_data = pages[page_index] if page_index < len(pages) else {}

        cid = page_data.get("cid") or info.get("cid")
        duration_seconds = int(page_data.get("duration") or info.get("duration") or 0)

        owner = info.get("owner") or {}
        return VideoMeta(
            bvid=resolved_bvid,
            avid=info.get("aid"),
            cid=int(cid) if cid else None,
            page=page,
            page_index=page_index,
            title=str(info.get("title") or ""),
            part_title=str(page_data.get("part") or "") or None,
            author=str(owner.get("name") or ""),
            author_mid=owner.get("mid"),
            duration_seconds=duration_seconds,
            pic=str(info.get("pic") or ""),
            pubdate=int(info.get("pubdate") or 0),
            desc=str(info.get("desc") or ""),
            stat={str(k): int(v or 0) for k, v in (info.get("stat") or {}).items()},
            page_url=f"https://www.bilibili.com/video/{resolved_bvid}",
        )

    # ── stream resolution ────────────────────────────────────────────

    async def resolve_stream(
        self,
        *,
        bvid: str | None = None,
        avid: int | None = None,
        cid: int,
        page_index: int,
        prefer_mp4: bool = True,
        max_quality: int = 80,
    ) -> SingleFilePlan | DashPlan:
        """Resolve a playable stream plan for the given part.

        Strategy:
        1. When *prefer_mp4* is True, request the mobile HTML5 play URL which
           returns a single muxed mp4 (works anonymously, up to ~1080P).
        2. Otherwise (or when HTML5 is unavailable) fall back to DASH; the two
           separate streams then need an ffmpeg merge.

        Args:
            bvid: Video ``BV`` id.
            avid: Video ``av`` id.
            cid: Part cid.
            page_index: 0-based part index (for the HTML5 request).
            prefer_mp4: Prefer the single-file mp4 stream.
            max_quality: Maximum quality for the DASH fallback
                (Bilibili ``qn`` value).

        Returns:
            A ``SingleFilePlan`` or ``DashPlan``.

        Raises:
            BilibiliError: When no playable stream can be obtained.
        """
        video_obj = self._make_video(bvid=bvid, avid=avid)

        if prefer_mp4:
            try:
                data = await video_obj.get_download_url(
                    cid=cid, page_index=page_index, html5=True
                )
                plan = self._plan_from_html5(data)
                if plan is not None:
                    return plan
            except Exception as exc:
                self.logger.debug("Bilibili HTML5 stream unavailable: %s", exc)

        try:
            data = await video_obj.get_download_url(cid=cid, page_index=page_index)
        except Exception as exc:
            raise self._translate_api_error(
                exc, fallback="获取视频播放地址失败，可能为会员视频或需登录。"
            ) from exc
        plan = self._plan_from_dash(data, max_quality=max_quality)
        if plan is not None:
            return plan
        raise BilibiliError("暂未找到可下载的播放流（可能需要登录或为特殊视频）。")

    def _plan_from_html5(self, data: Any) -> SingleFilePlan | None:
        if not isinstance(data, dict):
            return None
        durl = data.get("durl")
        if not isinstance(durl, list) or not durl:
            return None
        first = durl[0]
        url = str((first or {}).get("url") or "")
        if not url:
            return None
        return SingleFilePlan(
            url=url,
            size=int(first.get("size") or 0) or None,
            length_ms=int(first.get("length") or 0) or None,
        )

    def _plan_from_dash(self, data: Any, *, max_quality: int) -> DashPlan | None:
        from bilibili_api.video import (  # type: ignore[import-untyped]
            AudioQuality,
            AudioStreamDownloadURL,
            VideoCodecs,
            VideoDownloadURLDataDetecter,
            VideoQuality,
            VideoStreamDownloadURL,
        )

        if not isinstance(data, dict):
            return None
        try:
            detecter = VideoDownloadURLDataDetecter(data)
        except Exception:
            return None
        if not detecter.check_video_and_audio_stream():
            return None

        quality_member = _QUALITY_BY_VALUE.get(int(max_quality or 80), "_1080P")
        video_max = getattr(VideoQuality, quality_member, VideoQuality._1080P)

        try:
            streams = detecter.detect(
                video_max_quality=video_max,
                video_min_quality=VideoQuality._360P,
                audio_max_quality=AudioQuality._192K,
                audio_min_quality=AudioQuality._64K,
                codecs=[VideoCodecs.AVC],
                no_dolby_video=True,
                no_dolby_audio=True,
                no_hdr=True,
                no_hires=True,
            )
        except Exception as exc:
            self.logger.debug("DASH detect failed: %s", exc)
            return None

        videos = [s for s in streams if isinstance(s, VideoStreamDownloadURL)]
        audios = [s for s in streams if isinstance(s, AudioStreamDownloadURL)]
        if not videos:
            return None
        video = max(videos, key=lambda s: int(s.video_quality.value))
        audio = max(audios, key=lambda s: int(s.audio_quality.value)) if audios else None
        if audio is None:
            return None
        return DashPlan(
            video_url=video.url,
            audio_url=audio.url,
            quality=int(video.video_quality.value),
            codecs=str(video.codecs),
            video_size=getattr(video, "video_size", None),
            audio_size=getattr(audio, "audio_size", None),
        )

    # ── error normalisation ──────────────────────────────────────────

    def _translate_api_error(self, exc: Exception, *, fallback: str) -> BilibiliError:
        """Map a bilibili_api exception to a user-facing BilibiliError."""
        code = getattr(exc, "code", None)
        self.logger.debug("Bilibili API error: %s", exc)
        if code in (-404, 404):
            return BilibiliError("视频不存在或已被删除。", code=code)
        if code in (-403, 403, -412):
            return BilibiliError("访问被风控或需要登录，请稍后再试。", code=code)
        if code in (-400, -4040):
            return BilibiliError("视频不可播放（可能为版权/会员限制）。", code=code)
        if isinstance(exc, (BilibiliError,)):
            return exc
        return BilibiliError(fallback)
