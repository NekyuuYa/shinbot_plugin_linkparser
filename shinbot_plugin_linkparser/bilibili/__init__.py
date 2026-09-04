"""Bilibili video support for the ShinBot LinkParser plugin (v1)."""

from .client import (
    BilibiliClient,
    BilibiliError,
    DashPlan,
    SingleFilePlan,
    VideoMeta,
)
from .download import compress_to_target, download_plan_to_file, ffmpeg_available

__all__ = [
    "BilibiliClient",
    "BilibiliError",
    "DashPlan",
    "SingleFilePlan",
    "VideoMeta",
    "compress_to_target",
    "download_plan_to_file",
    "ffmpeg_available",
]
