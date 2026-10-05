"""Backwards-compatible re-export of the shared long-image stitcher.

The implementation now lives in :mod:`shinbot_plugin_linkparser.imagestitch`
(shared by the Xiaohongshu and X/Twitter pipelines).
"""

from ..imagestitch import pillow_available, stitch_to_long_image

__all__ = ["pillow_available", "stitch_to_long_image"]
