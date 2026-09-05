"""Xiaohongshu (小红书) note support for the ShinBot LinkParser plugin."""

from .client import (
    XHSClient,
    XHSError,
    extract_note_info,
    normalize_xhs_url,
)
from .media import download_hls_video, download_note_image
from .stitch import stitch_to_long_image

__all__ = [
    "XHSClient",
    "XHSError",
    "download_hls_video",
    "download_note_image",
    "extract_note_info",
    "normalize_xhs_url",
    "stitch_to_long_image",
]
