"""Pure data models shared by LinkParser modules.

Kept free of framework and third-party imports so these types can be used in
plain unit tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True, frozen=True)
class LinkCandidate:
    """A link found inside a message that may point to a Bilibili video.

    Attributes:
        platform: Source platform name (``"bilibili"``).
        kind: Content kind (``"video"``).
        bvid: Bilibili video id (``BV...``) when directly present.
        avid: Bilibili ``av`` id when directly present.
        page: 1-based part/page number requested.
        matched: Raw matched snippet (used for logging and debounce keys).
        short_code: ``b23.tv`` code when the link needs a redirect first.
    """

    platform: str
    kind: str
    bvid: str | None
    avid: int | None
    page: int = 1
    matched: str = ""
    short_code: str | None = None

    @property
    def needs_redirect(self) -> bool:
        """Return True when the candidate must be resolved via redirect first."""
        return self.short_code is not None

    def resource_key(self, *, resolved_bvid: str | None = None) -> str:
        """Dedupe key identifying the underlying resource.

        Uses the resolved BV when available (short links share the same
        resource as their canonical target), otherwise the id found inline.
        """
        bvid = resolved_bvid or self.bvid
        if bvid:
            return f"bilibili:video:{bvid}:p{self.page}"
        if self.avid:
            return f"bilibili:video:av{self.avid}:p{self.page}"
        return f"bilibili:video:unknown:{self.matched}"


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
