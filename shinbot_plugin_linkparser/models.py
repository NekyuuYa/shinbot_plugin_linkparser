"""Pure data models shared by LinkParser modules.

Kept free of framework and third-party imports so these types can be used in
plain unit tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True, frozen=True)
class LinkCandidate:
    """A link found inside a message that may point to parseable content.

    Attributes:
        platform: Source platform name (``"bilibili"`` / ``"xiaohongshu"``).
        kind: Content kind (``"video"`` / ``"post"``).
        matched: Raw matched snippet (used for logging and debounce keys).
        bvid: Bilibili video id (``BV...``) when directly present.
        avid: Bilibili ``av`` id when directly present.
        page: 1-based part/page number requested.
        short_code: Redirect code (``b23.tv`` or ``xhslink``) when the link
            needs a redirect first.
        note_id: Xiaohongshu note id when directly present.
        note_url: Full Xiaohongshu note URL (incl. ``xsec_token``) when known.
        status_id: X/Twitter status (tweet) id when directly present.
    """

    platform: str
    kind: str
    matched: str = ""
    bvid: str | None = None
    avid: int | None = None
    page: int = 1
    short_code: str | None = None
    note_id: str | None = None
    note_url: str | None = None
    status_id: str | None = None

    @property
    def needs_redirect(self) -> bool:
        """Return True when the candidate must be resolved via redirect first."""
        return self.short_code is not None

    def resource_key(
        self,
        *,
        resolved_bvid: str | None = None,
        resolved_note_id: str | None = None,
    ) -> str:
        """Dedupe key identifying the underlying resource.

        Uses resolved ids when available (short links share the resource of
        their canonical target), otherwise the id found inline.
        """
        if self.platform == "x":
            if self.status_id:
                return f"x:post:{self.status_id}"
            return f"x:unknown:{self.matched}"
        if self.platform == "xiaohongshu":
            note_id = resolved_note_id or self.note_id
            if note_id:
                return f"xiaohongshu:post:{note_id}"
            return f"xiaohongshu:unknown:{self.matched}"
        bvid = resolved_bvid or self.bvid
        if bvid:
            return f"bilibili:video:{bvid}:p{self.page}"
        if self.avid:
            return f"bilibili:video:av{self.avid}:p{self.page}"
        return f"bilibili:video:unknown:{self.matched}"


@dataclass(slots=True)
class XHSNoteInfo:
    """Parsed Xiaohongshu note content (subset of the page state)."""

    note_id: str
    note_type: str  # "normal" (image gallery) or "video"
    title: str
    desc: str
    author: str
    image_urls: list[str] = field(default_factory=list)
    video_master_url: str | None = None
    video_variants: list[dict] = field(default_factory=list)
    page_url: str = ""

    @property
    def display_title(self) -> str:
        """Return the note title or a fallback label."""
        return self.title or "小红书笔记"

    @property
    def caption(self) -> str:
        """Return the folded/text caption: author, title and note body."""
        parts = [self.author or "小红书"]
        if self.title:
            parts.append(self.title)
        desc = self.desc.strip()
        if desc and desc != self.title:
            parts.append(desc)
        return "\n".join(parts)


@dataclass(slots=True, frozen=True)
class MediaItem:
    """One locally produced media file of an outcome."""

    kind: str  # "video" | "image"
    path: object  # pathlib.Path


@dataclass(slots=True)
class XHSOutcome:
    """Result of parsing a Xiaohongshu note into local media."""

    kind: str  # "video" | "images"
    media: list[MediaItem]
    info: XHSNoteInfo

    @property
    def files(self) -> list[object]:
        """Return the produced paths (compatibility helper)."""
        return [item.path for item in self.media]


@dataclass(slots=True)
class XMedia:
    """One media attachment of a post, preserving tweet order.

    Attributes:
        kind: ``"photo"``, ``"video"`` or ``"gif"``.
        url: Photo URL (large size) for photos.
        variants: Normalized video variants for video/gif items.
        duration: Video duration in seconds, when known.
        cover_url: Poster/thumbnail URL for video/gif items.
        available: False when X reports the media as withheld/unavailable
            (``ext_media_availability.status != "Available"``).
    """

    kind: str
    url: str = ""
    variants: list[dict] = field(default_factory=list)
    duration: float | None = None
    cover_url: str | None = None
    available: bool = True

    @property
    def is_video(self) -> bool:
        """Return True for video and animated-gif attachments."""
        return self.kind in ("video", "gif")


@dataclass(slots=True)
class XTweetInfo:
    """Parsed X/Twitter post (syndication or fxtwitter shape)."""

    status_id: str
    url: str
    text: str
    author_name: str
    author_handle: str
    created_at: str = ""
    sensitive: bool = False
    media: list[XMedia] = field(default_factory=list)

    @property
    def photos(self) -> list[str]:
        """Return the photo URLs (compatibility helper)."""
        return [item.url for item in self.media if item.kind == "photo" and item.url]

    @property
    def videos(self) -> list[XMedia]:
        """Return the video/gif attachments."""
        return [item for item in self.media if item.is_video]

    @property
    def display_title(self) -> str:
        """Return ``author (@handle)`` for captions and fallbacks."""
        handle = f"(@{self.author_handle})" if self.author_handle else ""
        return f"{self.author_name} {handle}".strip() or "X 推文"

    @property
    def caption(self) -> str:
        """Return the folded/text caption: author line plus tweet text."""
        head = self.display_title
        body = self.text.strip()
        return f"{head}\n{body}" if body else head


@dataclass(slots=True)
class XOutcome:
    """Result of parsing an X/Twitter post.

    ``kind`` is ``"video"``, ``"images"``, ``"mixed"`` (both video and photos)
    or ``"text"`` (no media).
    """

    kind: str
    media: list[MediaItem]
    info: XTweetInfo

    @property
    def files(self) -> list[object]:
        """Return the produced paths (compatibility helper)."""
        return [item.path for item in self.media]


@dataclass(slots=True)
class VideoMeta:
    """Metadata of a Bilibili video (subset of the view API response)."""

    bvid: str
    avid: int | None
    cid: int | None
    page: int
    page_index: int
    title: str
    part_title: str | None
    author: str
    author_mid: int | None
    duration_seconds: int
    pic: str
    pubdate: int
    desc: str
    stat: dict[str, int] = field(default_factory=dict)
    page_url: str = ""

    @property
    def display_title(self) -> str:
        """Return the title (with a part suffix for multi-part videos)."""
        if self.part_title and self.part_title != self.title:
            return f"{self.title} - {self.part_title}"
        return self.title


@dataclass(slots=True, frozen=True)
class SingleFilePlan:
    """A single playable mp4 stream (HTML5 ``durl``), no merge required."""

    url: str
    size: int | None = None
    length_ms: int | None = None


@dataclass(slots=True, frozen=True)
class DashPlan:
    """Separated DASH audio/video streams that need an ffmpeg merge."""

    video_url: str
    audio_url: str
    quality: int
    codecs: str
    video_size: int | None = None
    audio_size: int | None = None


@dataclass(slots=True)
class ParseOutcome:
    """Result of parsing a video link."""

    path: object  # pathlib.Path of the produced mp4
    meta: VideoMeta
