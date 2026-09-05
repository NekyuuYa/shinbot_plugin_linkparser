"""Combine a gallery of images into a single long image (Pillow)."""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("shinbot_plugin_linkparser.xhs.stitch")


def pillow_available() -> bool:
    """Return True when Pillow is installed."""
    try:
        import PIL  # noqa: F401

        return True
    except ImportError:
        return False


def stitch_to_long_image(
    image_paths: list[Path],
    out: Path,
    *,
    max_width: int = 1080,
    max_height: int = 12000,
    quality: int = 88,
) -> Path | None:
    """Combine images into one vertical long image saved to *out*.

    Every image is scaled (preserving aspect) to fit *max_width*; if the
    combined height would exceed *max_height*, an extra uniform downscale is
    applied so the result stays within platform-friendly dimensions.

    Args:
        image_paths: Source images in note order.
        out: Output path (``.jpg``).
        max_width: Target canvas width in px.
        max_height: Maximum canvas height in px.
        quality: JPEG save quality.

    Returns:
        The output path on success, or None (e.g. Pillow missing, empty
        input, or every source failed to open).
    """
    if not image_paths:
        return None
    if not pillow_available():
        return None

    try:
        from PIL import Image, ImageOps
    except ImportError:  # pragma: no cover
        return None

    images: list = []
    for path in image_paths:
        try:
            with Image.open(path) as image:
                image = ImageOps.exif_transpose(image)
                if image.mode not in ("RGB", "L"):
                    image = image.convert("RGB")
                images.append(image.copy())
        except Exception:
            logger.debug("stitch: failed to open %s", path, exc_info=True)
    if not images:
        return None

    scale = 1.0
    for image in images:
        if image.width > max_width:
            scale = min(scale, max_width / image.width)
    total_height = int(sum(image.height * scale for image in images))
    if total_height > max_height:
        scale = min(scale, max_height / total_height)

    widths = [int(image.width * scale) or 1 for image in images]
    heights = [int(image.height * scale) or 1 for image in images]
    canvas = Image.new("RGB", (min(max_width, max(widths)), sum(heights)), "black")

    y = 0
    for image, width, height in zip(images, widths, heights, strict=True):
        resized = image.resize((width, height), Image.LANCZOS)
        canvas.paste(resized, (0, y))
        y += height

    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(out, format="JPEG", quality=quality, optimize=True)
        return out
    except Exception:
        logger.debug("stitch: save failed for %s", out, exc_info=True)
        return None
    finally:
        canvas.close()
        for image in images:
            image.close()
