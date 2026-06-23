"""
Image conversion and thumbnail utilities for OrchardBridge.

This module intentionally keeps the HEIC/HEIF -> JPEG settings close to the
script supplied by the user:
    quality=100, subsampling=0, optimize=True, preserve EXIF when possible.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import os
from pathlib import Path
from typing import Optional

from PIL import Image, ImageOps, ExifTags

try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except Exception:
    # The GUI will show a clearer dependency message when conversion/preview fails.
    pass

PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".heic", ".heif", ".png", ".tif", ".tiff", ".gif", ".bmp", ".webp", ".dng", ".raw"}
# Live Photos normally store the short motion clip as a matching .MOV file
# beside the still photo inside /DCIM. Include common iOS video extensions here.
VIDEO_EXTENSIONS = {".mov", ".mp4", ".m4v", ".3gp"}
MEDIA_EXTENSIONS = PHOTO_EXTENSIONS | VIDEO_EXTENSIONS
HEIC_EXTENSIONS = {".heic", ".heif"}

JPEG_QUALITY = 100
JPEG_SUBSAMPLING = 0
JPEG_OPTIMIZE = True


def is_photo_name(name: str) -> bool:
    return Path(name).suffix.lower() in PHOTO_EXTENSIONS


def is_media_name(name: str) -> bool:
    return Path(name).suffix.lower() in MEDIA_EXTENSIONS


def is_video_name(name: str) -> bool:
    return Path(name).suffix.lower() in VIDEO_EXTENSIONS


def is_heic_name(name: str) -> bool:
    return Path(name).suffix.lower() in HEIC_EXTENSIONS


def safe_filename(name: str) -> str:
    """Return a Windows-safe filename segment."""
    bad = '<>:"/\\|?*\0'
    out = "".join("_" if c in bad else c for c in name)
    out = out.strip().strip(".")
    return out or "unnamed"


def bytes_to_human(num: int | None) -> str:
    if num is None:
        return ""
    n = float(num)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if n < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(n)} {unit}"
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{num} B"


def unique_cache_path(cache_dir: Path, remote_path: str) -> Path:
    """Return a stable original-cache filename that is human-recognizable.

    The cache name keeps a SHA-1 digest for uniqueness and prefixes the
    original stem so users can visually match cache files to their final backup
    names, e.g. ``IMG_6734_00bffac2429a27676ef44a48a90886e7cf269070.HEIC``.
    """
    suffix = Path(remote_path).suffix.lower() or ".bin"
    digest = hashlib.sha1(remote_path.encode("utf-8", errors="ignore")).hexdigest()
    stem = safe_filename(Path(remote_path).stem)
    if not stem:
        stem = "media"
    # Keep paths short enough for Windows while remaining readable.
    stem = stem[:80]
    return cache_dir / f"{stem}_{digest}{suffix}"


def thumbnail_cache_path(cache_dir: Path, remote_path: str) -> Path:
    digest = hashlib.sha1(("thumb:" + remote_path).encode("utf-8", errors="ignore")).hexdigest()
    return cache_dir / f"{digest}.png"


def get_datetime_from_exif(image: Image.Image) -> Optional[str]:
    """Return EXIF datetime as YYYYMMDDHHMM, or None."""
    try:
        exif = image.getexif()
        if not exif:
            return None
        tag_map = {ExifTags.TAGS.get(k, k): v for k, v in exif.items()}
        dt = tag_map.get("DateTimeOriginal") or tag_map.get("DateTimeDigitized") or tag_map.get("DateTime")
        if not dt:
            return None
        dt = str(dt).strip()
        date_part, time_part = dt.split(" ")
        yyyy, mm, dd = date_part.split(":")
        hh, minute, _ss = time_part.split(":")
        return f"{yyyy}{mm}{dd}{hh}{minute}"
    except Exception:
        return None


def file_modified_time_prefix(path: Path) -> str:
    ts = path.stat().st_mtime
    dt = _dt.datetime.fromtimestamp(ts)
    return dt.strftime("%Y%m%d%H%M")


def make_thumbnail_file(input_path: Path, output_png: Path, size: tuple[int, int] = (160, 160)) -> Path:
    """Create a PNG thumbnail preserving orientation."""
    output_png.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(input_path) as img:
        img = ImageOps.exif_transpose(img)
        img.thumbnail(size)
        # Dark padding matches the app preview card background.
        canvas = Image.new("RGB", size, (49, 49, 69))
        x = (size[0] - img.width) // 2
        y = (size[1] - img.height) // 2
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGB")
        if img.mode == "RGBA":
            canvas.paste(img, (x, y), img)
        else:
            canvas.paste(img, (x, y))
        canvas.save(output_png, format="PNG")
    return output_png


def make_video_thumbnail_file(input_path: Path, output_png: Path, size: tuple[int, int] = (160, 160)) -> Path:
    """Create a PNG thumbnail from the first decodable video frame.

    Uses PyAV when available. If PyAV cannot decode the file, create a simple
    video placeholder thumbnail so the grid still has a stable preview.
    """
    output_png.parent.mkdir(parents=True, exist_ok=True)
    try:
        import av
        container = av.open(str(input_path))
        try:
            for frame in container.decode(video=0):
                img = frame.to_image()
                img = ImageOps.contain(img.convert("RGB"), size, Image.LANCZOS)
                canvas = Image.new("RGB", size, (234, 241, 251))
                x = (size[0] - img.width) // 2
                y = (size[1] - img.height) // 2
                canvas.paste(img, (x, y))
                canvas.save(output_png, format="PNG")
                return output_png
        finally:
            container.close()
    except Exception:
        pass

    # Fallback placeholder if video decoding is unavailable.
    canvas = Image.new("RGB", size, (234, 241, 251))
    from PIL import ImageDraw
    draw = ImageDraw.Draw(canvas)
    w, h = size
    box = (w//2 - 28, h//2 - 22, w//2 + 28, h//2 + 22)
    draw.rounded_rectangle(box, radius=8, outline=(47, 124, 246), width=3)
    tri = [(w//2 - 8, h//2 - 12), (w//2 - 8, h//2 + 12), (w//2 + 14, h//2)]
    draw.polygon(tri, fill=(47, 124, 246))
    canvas.save(output_png, format="PNG")
    return output_png


def convert_to_jpeg(input_path: Path, output_path: Path, *, delete_original: bool = False) -> Path:
    """Convert HEIC/HEIF or another readable image to high-quality JPEG."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(input_path) as img:
        exif_bytes = img.info.get("exif")
        img = ImageOps.exif_transpose(img)
        img = img.convert("RGB")
        save_kwargs = {
            "format": "JPEG",
            "quality": JPEG_QUALITY,
            "subsampling": JPEG_SUBSAMPLING,
            "optimize": JPEG_OPTIMIZE,
        }
        if exif_bytes:
            save_kwargs["exif"] = exif_bytes
        img.save(output_path, **save_kwargs)

    if delete_original:
        try:
            os.remove(input_path)
        except OSError:
            pass
    return output_path
