"""X/Twitter (推特) support for the ShinBot LinkParser plugin."""

from .client import (
    XClient,
    XError,
    parse_fxtwitter,
    parse_syndication,
    pick_video_variant,
)
from .media import download_photo, download_video

__all__ = [
    "XClient",
    "XError",
    "download_photo",
    "download_video",
    "parse_fxtwitter",
    "parse_syndication",
    "pick_video_variant",
]
