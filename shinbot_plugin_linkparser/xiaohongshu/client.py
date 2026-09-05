"""Xiaohongshu note client built on the note page's ``__INITIAL_STATE__``.

Xiaohongshu has no open metadata API; the SSR note page embeds a
``window.__INITIAL_STATE__= {...}`` JSON blob that carries the note type,
title, author, image list and the video stream (HLS master URLs). Requests use
a browser User-Agent, an HTML Accept header and (optionally) the caller's web
cookies to pass the anti-bot checks.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from ..models import XHSNoteInfo

logger = logging.getLogger("shinbot_plugin_linkparser.xhs")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
REFERER = "https://www.xiaohongshu.com"
_ACCEPT_HTML = (
    "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
    "image/webp,image/apng,*/*;q=0.8"
)
_INITIAL_STATE_RE = re.compile(r"window\.__INITIAL_STATE__=(.*?)</script>", re.S)
_STREAM_KEYS = ("h265", "h264", "av1", "h266")


class XHSError(RuntimeError):
    """Raised when a Xiaohongshu operation fails (message is user-facing)."""

    def __init__(self, message: str) -> None:
        """Initialize the error with a user-facing message."""
        super().__init__(message)


def normalize_xhs_url(url: str) -> str:
    """Normalise a xiaohongshu URL into ``https://www.xiaohongshu.com...``."""
    parsed = urlparse(url)
    query = f"?{parsed.query}" if parsed.query else ""
    return f"https://www.xiaohongshu.com{parsed.path}{query}"


def _get(data: dict[str, Any], *keys: str, default: Any = None) -> Any:
    """Deep-dict getter tolerating missing nodes."""
    node: Any = data
    for key in keys:
        if not isinstance(node, dict):
            return default
        node = node.get(key)
        if node is None:
            return default
    return node


def _pick_video_master(note: dict[str, Any]) -> str | None:
    """Pick the first available HLS master URL (h265 > h264 > av1 > h266)."""
    media = _get(note, "video", "media") or {}
    stream = media.get("stream") or {}
    for key in _STREAM_KEYS:
        variants = stream.get(key)
        if isinstance(variants, list) and variants:
            master = variants[0].get("masterUrl")
            if isinstance(master, str) and master:
                return master
    return None


def _author_name(note: dict[str, Any]) -> str:
    user = _get(note, "user") or {}
    return str(user.get("nickname") or user.get("nickName") or "")


def _image_urls(note: dict[str, Any], key_names: tuple[str, ...]) -> list[str]:
    """Collect image urls for the chosen field names."""
    urls: list[str] = []
    for image in note.get("imageList") or []:
        if not isinstance(image, dict):
            continue
        for key in key_names:
            value = image.get(key)
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                urls.append(value)
                break
    return urls


def extract_note_info(
    html: str,
    *,
    note_id: str | None,
    page_url: str,
) -> XHSNoteInfo | None:
    """Extract structured note info from a note page's HTML.

    Args:
        html: Page HTML returned by Xiaohongshu.
        note_id: Note id (used for the state lookup and for discovery layout).
        page_url: The URL the page was fetched from (for display).

    Returns:
        A :class:`XHSNoteInfo`, or None when the expected state is absent
        (deleted note, login wall, or an anti-bot page).
    """
    match = _INITIAL_STATE_RE.search(html)
    if not match:
        return None
    raw = match.group(1).replace("undefined", "null")
    try:
        state = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(state, dict):
        return None

    # Explore layout: note.noteDetailMap[<id>].note
    note_map = _get(state, "note", "noteDetailMap") or {}
    if isinstance(note_map, dict):
        for note_id_candidate in (note_id, *list(note_map.keys())[:5]):
            if not note_id_candidate:
                continue
            note = _get(note_map, str(note_id_candidate), "note")
            if isinstance(note, dict) and note.get("type"):
                return _build_info(
                    note,
                    resolved_id=str(note_id_candidate),
                    page_url=page_url,
                    image_keys=("urlDefault", "url"),
                )

    # Discovery layout: noteData.data.noteData (+ clean preload cover images)
    note_data_root = state.get("noteData")
    if isinstance(note_data_root, dict):
        inner = note_data_root.get("data") or {}
        note_data = inner.get("noteData")
        if isinstance(note_data, dict):
            return _build_info(
                note_data,
                resolved_id=note_id or "",
                page_url=page_url,
                image_keys=("urlSizeLarge", "url"),
            )
    return None


def _build_info(
    note: dict[str, Any],
    *,
    resolved_id: str,
    page_url: str,
    image_keys: tuple[str, ...],
) -> XHSNoteInfo:
    note_type = str(note.get("type") or "normal")
    image_urls = _image_urls(note, image_keys)
    return XHSNoteInfo(
        note_id=resolved_id,
        note_type="video" if note_type == "video" else "normal",
        title=str(note.get("title") or ""),
        desc=str(note.get("desc") or ""),
        author=_author_name(note),
        image_urls=image_urls,
        video_master_url=_pick_video_master(note),
        page_url=page_url,
    )


class XHSClient:
    """HTTP client for fetching Xiaohongshu note pages and media.

    Args:
        cookie: Optional web cookie string (e.g. ``a1=...; web_session=...``)
            used to bypass Xiaohongshu's anti-bot checks.
        logger: Optional logger; defaults to a module logger.
    """

    def __init__(self, *, cookie: str = "", logger: logging.Logger | None = None) -> None:
        self._cookie = cookie or ""
        self.logger = logger or logging.getLogger("shinbot_plugin_linkparser.xhs")
        headers: dict[str, str] = {
            "User-Agent": USER_AGENT,
            "Accept": _ACCEPT_HTML,
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": REFERER,
        }
        if self._cookie:
            headers["Cookie"] = self._cookie
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=15.0, read=None, write=60.0, pool=10.0),
            follow_redirects=False,
            headers=headers,
        )

    @property
    def http(self) -> httpx.AsyncClient:
        """Return the shared HTTP client (used for media downloads)."""
        return self._http

    @property
    def cookie(self) -> str:
        """Return the configured cookie string."""
        return self._cookie

    async def close(self) -> None:
        """Close the shared HTTP client."""
        await self._http.aclose()

    async def resolve_short_url(self, short_url: str) -> str:
        """Follow a redirect chain (``xhslink.com``) to the note page URL.

        Args:
            short_url: The short link to expand.

        Returns:
            The canonical (final) note URL.

        Raises:
            XHSError: On network failure.
        """
        headers = {"User-Agent": USER_AGENT, "Referer": REFERER}
        if self._cookie:
            headers["Cookie"] = self._cookie
        try:
            response = await self._http.get(
                short_url,
                headers=headers,
                follow_redirects=True,
                timeout=httpx.Timeout(connect=15.0, read=30.0, write=30.0, pool=10.0),
            )
        except httpx.HTTPError as exc:
            raise XHSError("小红书短链接跳转失败，请稍后再试。") from exc
        return str(response.url)

    async def fetch_note(self, note_url: str, *, note_id: str | None = None) -> XHSNoteInfo:
        """Fetch and parse a note page.

        Args:
            note_url: The note page URL (with ``xsec_token`` when required).
            note_id: Optional note id hint for state lookup.

        Returns:
            The parsed note info.

        Raises:
            XHSError: On network failure or when no note content is found.
        """
        try:
            response = await self._http.get(note_url)
        except httpx.HTTPError as exc:
            raise XHSError("小红书页面获取失败，请稍后再试。") from exc
        html = response.text
        info = extract_note_info(html, note_id=note_id, page_url=normalize_xhs_url(note_url))
        if info is None:
            if response.status_code in (403, 401, 412):
                raise XHSError("小红书拒绝了访问（可能被风控），可配置 xiaohongshu_cookie 后重试。")
            raise XHSError("小红书笔记不存在、已删除或需要登录后查看。")
        return info
