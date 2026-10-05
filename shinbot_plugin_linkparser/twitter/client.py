"""X/Twitter post client: official syndication API + fxtwitter fallback.

Two no-login data sources are supported:

- **official syndication** (``cdn.syndication.twimg.com/tweet-result``): no
  third-party dependency, returns text/author/photos and video variants
  (progressive mp4 + HLS). Preferred backend.
- **fxtwitter** (``api.fxtwitter.com``): third-party mirror used as a fallback
  when the official endpoint is rate-limited or unavailable; also provides a
  direct high-resolution mp4 and the video duration.

Both parse into a single :class:`~shinbot_plugin_linkparser.models.XTweetInfo`.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from ..models import XTweetInfo

logger = logging.getLogger("shinbot_plugin_linkparser.x")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
REFERER = "https://x.com/"
_SYNDICATION_URL = "https://cdn.syndication.twimg.com/tweet-result"
_FXTWITTER_URL = "https://api.fxtwitter.com/i/status/{status_id}"
_STATUS_URL = "https://x.com/i/status/{status_id}"

_HEIGHT_RE = re.compile(r"/(\d{2,5})x(\d{2,5})/")
_MP4 = "video/mp4"
_HLS_TYPES = ("application/x-mpegURL", "application/vnd.apple.mpegurl")


class XError(RuntimeError):
    """Raised when an X/Twitter operation fails (message is user-facing)."""

    def __init__(self, message: str) -> None:
        """Initialize the error with a user-facing message."""
        super().__init__(message)


def _photo_url(url: str) -> str:
    """Return an image URL at the ``large`` size (bandwidth-friendly)."""
    if not url:
        return ""
    if "name=" in url:
        return re.sub(r"name=[A-Za-z]+", "name=large", url)
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}name=large"


def _height_from_url(url: str) -> int | None:
    """Extract the height token from a Twitter video URL (``/1280x720/``)."""
    match = _HEIGHT_RE.search(url)
    if match:
        try:
            return int(match.group(2))
        except ValueError:
            return None
    return None


def _normalize_variants(variants: list[dict]) -> list[dict]:
    """Normalize raw variant dicts into ``{url, content_type, bitrate, height}``."""
    normalized: list[dict] = []
    for variant in variants:
        if not isinstance(variant, dict):
            continue
        url = str(variant.get("url") or "")
        if not url:
            continue
        normalized.append(
            {
                "url": url,
                "content_type": str(variant.get("content_type") or ""),
                "bitrate": variant.get("bitrate"),
                "height": _height_from_url(url),
            }
        )
    return normalized


def pick_video_variant(
    variants: list[dict],
    *,
    max_height: int = 720,
) -> dict | None:
    """Choose a video variant, preferring mp4 at no more than *max_height*.

    Selection order: the highest-height progressive ``video/mp4`` variant not
    exceeding *max_height*; otherwise the smallest such mp4 (to minimize
    bandwidth); otherwise the first HLS variant (downloaded via ffmpeg).

    Args:
        variants: Normalized variant dicts (see ``_normalize_variants``).
        max_height: Preferred maximum video height in pixels.

    Returns:
        The chosen variant dict, or None when nothing is playable.
    """
    if not variants:
        return None
    mp4 = [v for v in variants if str(v.get("content_type")) == _MP4]
    if mp4:
        within = [v for v in mp4 if (v.get("height") or 0) <= max_height and v.get("height")]
        if within:
            return max(within, key=lambda v: v.get("height") or 0)
        sized = [v for v in mp4 if v.get("height")]
        if sized:
            return min(sized, key=lambda v: v.get("height") or 0)
        return mp4[0]
    for variant in variants:
        if str(variant.get("content_type")) in _HLS_TYPES:
            return variant
    return None


def parse_syndication(data: dict[str, Any], status_id: str) -> XTweetInfo | None:
    """Parse a ``tweet-result`` syndication payload into :class:`XTweetInfo`."""
    if not isinstance(data, dict) or not data.get("text") and not data.get("mediaDetails"):
        if not isinstance(data, dict):
            return None
    user = data.get("user") or {}
    photos: list[str] = []
    variants: list[dict] = []
    cover: str | None = None
    duration: float | None = None
    for media in data.get("mediaDetails") or []:
        if not isinstance(media, dict):
            continue
        media_type = str(media.get("type") or "")
        if media_type == "photo":
            url = _photo_url(str(media.get("media_url_https") or ""))
            if url:
                photos.append(url)
            continue
        if media_type in ("video", "animated_gif"):
            if cover is None:
                cover = str(media.get("media_url_https") or "") or None
            info = media.get("video_info") or {}
            variants.extend(_normalize_variants(info.get("variants") or []))
            millis = info.get("duration_millis")
            if isinstance(millis, (int, float)) and millis > 0:
                duration = float(millis) / 1000.0
    return XTweetInfo(
        status_id=status_id,
        url=_STATUS_URL.format(status_id=status_id),
        text=str(data.get("text") or ""),
        author_name=str(user.get("name") or ""),
        author_handle=str(user.get("screen_name") or ""),
        created_at=str(data.get("created_at") or ""),
        photos=photos,
        video_variants=variants,
        video_duration=duration,
        cover_url=cover,
    )


def parse_fxtwitter(data: dict[str, Any], status_id: str) -> XTweetInfo | None:
    """Parse an ``api.fxtwitter.com`` payload into :class:`XTweetInfo`."""
    tweet = data.get("tweet") if isinstance(data, dict) else None
    if not isinstance(tweet, dict):
        return None
    author = tweet.get("author") or {}
    media = tweet.get("media") or {}
    photos = [
        _photo_url(str(photo.get("url") or ""))
        for photo in (media.get("photos") or [])
        if isinstance(photo, dict) and photo.get("url")
    ]
    variants: list[dict] = []
    duration: float | None = None
    cover: str | None = None
    for video in media.get("videos") or []:
        if not isinstance(video, dict) or not video.get("url"):
            continue
        height = video.get("height")
        if height is None:
            height = _height_from_url(str(video.get("url") or ""))
        variants.append(
            {
                "url": str(video["url"]),
                "content_type": _MP4,
                "bitrate": None,
                "height": height,
            }
        )
        if duration is None and isinstance(video.get("duration"), (int, float)):
            duration = float(video["duration"])
        if cover is None:
            cover = str(video.get("thumbnail_url") or "") or None
    return XTweetInfo(
        status_id=status_id,
        url=str(tweet.get("url") or _STATUS_URL.format(status_id=status_id)),
        text=str(tweet.get("text") or ""),
        author_name=str(author.get("name") or ""),
        author_handle=str(author.get("screen_name") or ""),
        created_at=str(tweet.get("created_at") or ""),
        photos=photos,
        video_variants=variants,
        video_duration=duration,
        cover_url=cover,
    )


class XClient:
    """Fetches X/Twitter posts through the configured backend chain.

    Args:
        backend: ``"auto"`` (syndication then fxtwitter), ``"syndication"`` or
            ``"fxtwitter"``.
        logger: Optional logger.
        transport: Optional ``httpx`` transport (used by tests).
    """

    def __init__(
        self,
        *,
        backend: str = "auto",
        logger: logging.Logger | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._backend = backend if backend in ("auto", "syndication", "fxtwitter") else "auto"
        self.logger = logger or logging.getLogger("shinbot_plugin_linkparser.x")
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=15.0, read=None, write=60.0, pool=10.0),
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Referer": REFERER},
            transport=transport,
        )

    @property
    def http(self) -> httpx.AsyncClient:
        """Return the shared HTTP client (used for media downloads)."""
        return self._http

    async def close(self) -> None:
        """Close the shared HTTP client."""
        await self._http.aclose()

    async def fetch_tweet(self, status_id: str) -> XTweetInfo:
        """Fetch a post, trying the configured backends in order.

        Args:
            status_id: The numeric tweet/status id.

        Returns:
            The parsed post info.

        Raises:
            XError: When every enabled backend fails.
        """
        order = {
            "auto": ("syndication", "fxtwitter"),
            "syndication": ("syndication",),
            "fxtwitter": ("fxtwitter",),
        }[self._backend]
        last_error: Exception | None = None
        for backend in order:
            try:
                info = await (
                    self._fetch_syndication(status_id)
                    if backend == "syndication"
                    else self._fetch_fxtwitter(status_id)
                )
                if info is not None:
                    return info
                last_error = XError("推文不存在、已删除或受保护。")
            except (httpx.HTTPError, XError, ValueError) as exc:
                self.logger.debug("X backend %s failed: %s", backend, exc)
                last_error = exc
        if isinstance(last_error, XError):
            raise last_error
        raise XError("X 推文获取失败（可能被限流或需要登录）。")

    async def _fetch_syndication(self, status_id: str) -> XTweetInfo | None:
        response = await self._http.get(
            _SYNDICATION_URL,
            params={"id": status_id, "lang": "en", "token": "a"},
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return parse_syndication(response.json(), status_id)

    async def _fetch_fxtwitter(self, status_id: str) -> XTweetInfo | None:
        response = await self._http.get(_FXTWITTER_URL.format(status_id=status_id))
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return parse_fxtwitter(response.json(), status_id)
