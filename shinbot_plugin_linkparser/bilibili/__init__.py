"""Bilibili video support for the ShinBot LinkParser plugin (v1)."""

from .client import (
    BilibiliClient,
    BilibiliError,
    DashPlan,
    SingleFilePlan,
    VideoMeta,
)
from .download import download_plan_to_file, ffmpeg_available

__all__ = [
    "BilibiliClient",
    "BilibiliError",
    "DashPlan",
    "SingleFilePlan",
    "VideoMeta",
    "download_plan_to_file",
    "ffmpeg_available",
]
