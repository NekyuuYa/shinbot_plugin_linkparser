"""Xiaohongshu (小红书) note support for the ShinBot LinkParser plugin."""

from .client import (
    XHSClient,
    XHSError,
    extract_note_info,
    normalize_xhs_url,
    pick_video_variant,
    video_variants_of,
)
from .media import download_hls_video, download_note_image, download_note_video
from .stitch import stitch_to_long_image

__all__ = [
    "XHSClient",
    "XHSError",
    "download_hls_video",
    "download_note_image",
    "download_note_video",
    "extract_note_info",
    "normalize_xhs_url",
    "pick_video_variant",
    "stitch_to_long_image",
    "video_variants_of",
]
