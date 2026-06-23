"""
備份管理模組
負責照片備份排程、HEIC/HEIF 轉圖檔、進度追蹤
"""

from __future__ import annotations

import csv
import traceback
import datetime
import threading
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, Callable
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
import os
import shutil
import hashlib

from .device_manager import PhotoItem, DeviceManager
from .preferences import Preferences


@dataclass
class BackupResult:
    """備份結果"""
    photo: PhotoItem
    dest_path: Path
    status: str = "pending"   # pending / success / skipped / error
    error: str = ""
    converted: bool = False
    converted_path: str = ""
    converted_created: bool = False  # True only when this run created/recreated converted output
    source: str = ""          # cache / iphone / existing / skipped


@dataclass
class BackupProgress:
    """備份整體進度"""
    total: int = 0
    done: int = 0
    success: int = 0
    skipped: int = 0
    errors: int = 0
    current_file: str = ""
    finished: bool = False
    cancelled: bool = False

    @property
    def percent(self) -> float:
        if self.total == 0:
            return 0.0
        return self.done / self.total

    @property
    def summary(self) -> str:
        return (
            f"完成 {self.done}/{self.total}  "
            f"✓ {self.success}  "
            f"⊘ {self.skipped}  "
            f"✗ {self.errors}"
        )


def _convert_image_file(
    src_path: Path,
    dst_path: Path,
    *,
    output_format: str = "JPEG",
    quality: int = 100,
    subsampling: int = 0,
    optimize: bool = True,
    png_compress_level: int = 0,
) -> tuple[bool, str]:
    """Convert HEIC/HEIF or other image formats to JPEG or PNG."""
    try:
        from PIL import Image

        if src_path.suffix.lower() in (".heic", ".heif"):
            try:
                from pillow_heif import register_heif_opener
                register_heif_opener()
            except ImportError:
                return False, "pillow-heif 未安裝，無法轉換 HEIC/HEIF"

        output_format = str(output_format).upper().strip()
        if output_format not in ("JPEG", "PNG"):
            output_format = "JPEG"

        with Image.open(src_path) as img:
            exif_bytes = img.info.get("exif")
            dst_path.parent.mkdir(parents=True, exist_ok=True)

            if output_format == "PNG":
                if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                    out_img = img.convert("RGBA")
                else:
                    out_img = img.convert("RGB")
                out_img.save(
                    dst_path,
                    format="PNG",
                    compress_level=int(max(0, min(9, png_compress_level))),
                )
            else:
                out_img = img.convert("RGB")
                save_kwargs = {
                    "format": "JPEG",
                    "quality": int(max(1, min(100, quality))),
                    "subsampling": int(subsampling),
                    "optimize": bool(optimize),
                }
                if exif_bytes:
                    save_kwargs["exif"] = exif_bytes
                out_img.save(dst_path, **save_kwargs)

        return True, ""
    except Exception as e:
        return False, f"{repr(e)}\n{traceback.format_exc()}"


def _unique_renamed_path(path: Path) -> Path:
    """Return a non-existing path using Windows-style 'name (1).ext' suffix."""
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    i = 1
    while True:
        candidate = parent / f"{stem} ({i}){suffix}"
        if not candidate.exists():
            return candidate
        i += 1


def _file_sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def _same_file_by_size_and_hash(a: Path, b: Path) -> bool:
    try:
        if not a.exists() or not b.exists():
            return False
        if a.stat().st_size != b.stat().st_size:
            return False
        return _file_sha256(a) == _file_sha256(b)
    except Exception:
        return False


def _capture_timestamp_from_file(path: Path) -> int | None:
    """Best-effort capture time from EXIF/HEIC metadata.

    Returns a local timestamp, or None if unavailable.  Falls back to the AFC
    modified time elsewhere.
    """
    try:
        from PIL import Image, ExifTags
        if path.suffix.lower() in (".heic", ".heif"):
            try:
                from pillow_heif import register_heif_opener
                register_heif_opener()
            except Exception:
                pass
        with Image.open(path) as img:
            exif = img.getexif()
            if not exif:
                return None
            # DateTimeOriginal 36867, DateTimeDigitized 36868, DateTime 306
            for tag in (36867, 36868, 306):
                value = exif.get(tag)
                if not value:
                    continue
                text = str(value).strip().replace("\x00", "")
                for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
                    try:
                        return int(datetime.datetime.strptime(text, fmt).timestamp())
                    except Exception:
                        pass
    except Exception:
        return None
    return None


def _resolve_destination_for_source(path: Path, action: str, source_path: Path | None) -> tuple[Path | None, str]:
    """Resolve destination and avoid duplicate copies when renaming.

    Returns (destination_path_or_None, reason).  For rename, if an existing
    candidate has the same file size and SHA-256 as source_path, None is
    returned so the backup can be skipped instead of creating unlimited
    duplicate names like IMG_0001 (1), IMG_0001 (2), ... .
    """
    action = str(action or "rename").lower().strip()
    if not path.exists():
        return path, "new"
    if action == "overwrite":
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return path, "overwrite"
    if action == "skip":
        return None, "skip_existing"

    stem, suffix, parent = path.stem, path.suffix, path.parent
    i = 0
    while True:
        candidate = path if i == 0 else parent / f"{stem} ({i}){suffix}"
        if candidate.exists():
            if source_path is not None and _same_file_by_size_and_hash(candidate, source_path):
                return None, "duplicate_same_hash"
            i += 1
            continue
        return candidate, "rename"


def _resolve_destination(path: Path, action: str) -> Path | None:
    """Resolve existing destination path.

    action = rename / overwrite / skip
    """
    action = str(action or "rename").lower().strip()
    if not path.exists():
        return path
    if action == "overwrite":
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return path
    if action == "skip":
        return None
    return _unique_renamed_path(path)


class BackupManager:
    """協調照片備份、同名檔案處理與轉檔。"""

    def __init__(self, device_manager: DeviceManager, preferences: Preferences | None = None):
        self._dm = device_manager
        self._cancel_flag = threading.Event()
        self.apply_preferences(preferences or Preferences())

    def apply_preferences(self, preferences: Preferences) -> None:
        self.image_output_format = str(getattr(preferences, "image_output_format", "JPEG")).upper().strip()
        if self.image_output_format not in ("JPEG", "PNG"):
            self.image_output_format = "JPEG"

        self.jpeg_quality = int(max(1, min(100, preferences.jpeg_quality)))
        self.jpeg_subsampling = int(preferences.jpeg_subsampling)
        if self.jpeg_subsampling not in (0, 1, 2):
            self.jpeg_subsampling = 0
        self.jpeg_optimize = bool(preferences.jpeg_optimize)
        self.png_compress_level = int(max(0, min(9, getattr(preferences, "png_compress_level", 0))))

        self.conversion_workers = int(getattr(preferences, "conversion_workers", max(1, (os.cpu_count() or 2) - 2)))
        self.conversion_workers = max(1, min(os.cpu_count() or 1, self.conversion_workers))

        self.existing_file_action = str(getattr(preferences, "existing_file_action", "rename")).lower().strip()
        if self.existing_file_action not in {"rename", "overwrite", "skip"}:
            self.existing_file_action = "rename"

    def cancel(self):
        self._cancel_flag.set()

    def _date_prefix(self, photo: PhotoItem, source_path: Path | None = None) -> str:
        """Return YYYYMMDDHHMMSS, preferring real capture time over mtime."""
        if source_path is not None and Path(source_path).exists() and photo.ext.lower() in {".jpg", ".jpeg", ".heic", ".heif", ".tif", ".tiff"}:
            ts = _capture_timestamp_from_file(Path(source_path))
            if ts:
                try:
                    return datetime.datetime.fromtimestamp(ts).strftime("%Y%m%d%H%M%S")
                except Exception:
                    pass
        try:
            ts = int(photo.modified_time or 0)
            if ts > 0:
                return datetime.datetime.fromtimestamp(ts).strftime("%Y%m%d%H%M%S")
        except Exception:
            pass
        return "00000000000000"

    def _clean_backup_filename(self, photo: PhotoItem, source_path: Path | None = None) -> str:
        return f"{self._date_prefix(photo, source_path)}_{photo.filename}"

    def _remote_album_folder(self, photo: PhotoItem) -> Path:
        """Return the DCIM album folder such as 107APPLE without the DCIM layer."""
        parts = [p for p in str(photo.remote_path).replace("\\", "/").strip("/").split("/") if p]
        # Expected: DCIM/107APPLE/IMG_0001.HEIC.  User requested that DCIM not
        # appear in the output path, so keep only the album folder and below.
        if len(parts) >= 3 and parts[0].upper() == "DCIM":
            return Path(*parts[1:-1])
        if len(parts) >= 2:
            return Path(*parts[:-1])
        return Path("Unknown")

    def _converted_destination(self, conv_folder: Path, photo: PhotoItem, conv_suffix: str, source_path: Path | None = None, clean_name: str | None = None) -> Path:
        rel_dir = self._remote_album_folder(photo)
        name = clean_name or self._clean_backup_filename(photo, source_path)
        return conv_folder / rel_dir / Path(name).with_suffix(conv_suffix).name

    def _conversion_needs_work(self, path: Path) -> bool:
        # Converted files are derived products.  In rename mode, never create
        # unlimited duplicates; if the requested converted path already exists,
        # treat it as complete.  In overwrite mode, replace it.
        if not path.exists():
            return True
        if self.existing_file_action == "overwrite":
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            return True
        return False

    def backup_photos(
        self,
        photos: list[PhotoItem],
        dest_folder: Path,
        convert_heic: bool = False,
        output_format: str | None = None,
        progress_callback: Optional[Callable[[BackupProgress], None]] = None,
    ) -> list[BackupResult]:
        """Back up selected media.

        Output layout:
            <dest>/Original/107APPLE/20240105152517_IMG_0001.HEIC
            <dest>/Converted/JPEG/107APPLE/20240105152517_IMG_0001.jpeg

        No DCIM layer is written.  No FoneTool-like category folders are created
        in this first release.
        """
        self._cancel_flag.clear()
        progress = BackupProgress(total=len(photos))
        results: list[BackupResult] = []
        conversion_tasks: list[tuple[BackupResult, Path, Path]] = []

        dest_folder.mkdir(parents=True, exist_ok=True)
        tmp_compare_dir = dest_folder / ".orchardbridge_tmp_compare"
        originals_root = dest_folder / "Original"
        originals_root.mkdir(parents=True, exist_ok=True)

        output_format = (output_format or self.image_output_format or "JPEG").upper().strip()
        if output_format not in ("JPEG", "PNG"):
            output_format = "JPEG"
        conv_suffix = ".png" if output_format == "PNG" else ".jpeg"
        conv_folder = dest_folder / "Converted" / output_format if convert_heic else None
        if conv_folder:
            conv_folder.mkdir(parents=True, exist_ok=True)

        print(f"[backup] Starting photo backup: total={len(photos)}, dest={dest_folder}, convert={convert_heic}, format={output_format}")
        print(f"[backup] Originals root: {originals_root}")
        if conv_folder:
            print(f"[backup] Converted root: {conv_folder}")

        for i, photo in enumerate(photos, start=1):
            if self._cancel_flag.is_set():
                progress.cancelled = True
                print("[backup] Backup cancelled by user")
                break

            progress.current_file = photo.filename
            if progress_callback:
                progress_callback(progress)

            rel_dir = self._remote_album_folder(photo)
            source_path = None
            tmp_path = None
            try:
                cached = self._dm.cached_original_path(photo) if hasattr(self._dm, "cached_original_path") else None
                if cached and cached.exists():
                    source_path = cached
            except Exception:
                source_path = None

            clean_name = self._clean_backup_filename(photo, source_path)
            requested_dest = originals_root / rel_dir / clean_name

            if source_path is None and self.existing_file_action == "rename" and requested_dest.exists():
                try:
                    tmp_compare_dir.mkdir(parents=True, exist_ok=True)
                    tmp_path = tmp_compare_dir / f"{photo.filename}.{i}.tmp"
                    if tmp_path.exists():
                        tmp_path.unlink()
                    if self._dm.download_photo(photo, tmp_path):
                        source_path = tmp_path
                except Exception as exc:
                    print(f"[backup] Temp compare pull failed for {photo.remote_path}: {exc!r}")
                    source_path = None

            dest_path, reason = _resolve_destination_for_source(requested_dest, self.existing_file_action, source_path)
            result = BackupResult(photo=photo, dest_path=dest_path or requested_dest)
            usable_original_for_conversion: Path | None = None

            if dest_path is None:
                result.status = "skipped"
                result.source = reason
                progress.skipped += 1
                print(f"[backup] SKIP {photo.remote_path} -> {requested_dest} reason={reason}")
                if requested_dest.exists():
                    usable_original_for_conversion = requested_dest
                elif source_path and source_path.exists():
                    usable_original_for_conversion = source_path
                try:
                    if tmp_path and tmp_path.exists() and tmp_path != usable_original_for_conversion:
                        tmp_path.unlink()
                except Exception:
                    pass
            else:
                if tmp_path and source_path == tmp_path and tmp_path.exists():
                    dest_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(tmp_path), str(dest_path))
                    ok = True
                    self._dm.last_download_source = "iphone_temp_compare"
                else:
                    ok = self._dm.download_photo(photo, dest_path)
                if ok:
                    # If we only learned the real EXIF capture time after downloading,
                    # rename the backed-up file from mtime_prefix_IMG_xxxx to
                    # capturetime_prefix_IMG_xxxx.
                    try:
                        exif_name = self._clean_backup_filename(photo, dest_path)
                        exif_dest = originals_root / rel_dir / exif_name
                        if exif_dest != dest_path:
                            final_dest, _reason2 = _resolve_destination_for_source(exif_dest, self.existing_file_action, dest_path)
                            if final_dest is None:
                                try:
                                    dest_path.unlink()
                                except Exception:
                                    pass
                                dest_path = exif_dest
                            else:
                                final_dest.parent.mkdir(parents=True, exist_ok=True)
                                shutil.move(str(dest_path), str(final_dest))
                                dest_path = final_dest
                            result.dest_path = dest_path
                            clean_name = dest_path.name
                    except Exception as exc:
                        print(f"[backup] EXIF rename skipped for {photo.remote_path}: {exc!r}")
                    result.status = "success"
                    result.source = getattr(self._dm, "last_download_source", "") or "iphone"
                    progress.success += 1
                    usable_original_for_conversion = dest_path
                    print(f"[backup] OK source={result.source} {photo.remote_path} -> {dest_path}")
                else:
                    result.status = "error"
                    result.error = "下載失敗"
                    progress.errors += 1
                    print(f"[backup] ERROR {photo.remote_path} -> {dest_path}: download failed")

            # Conversion is a separate requirement.  Even when the original was
            # already backed up/skipped, check whether the converted file exists.
            # If it does not, generate it from the existing original or cache.
            if convert_heic and photo.ext in (".heic", ".heif") and conv_folder:
                requested_image_path = self._converted_destination(conv_folder, photo, conv_suffix, usable_original_for_conversion, Path(usable_original_for_conversion).name if usable_original_for_conversion else clean_name)
                if self._conversion_needs_work(requested_image_path):
                    src_for_conv = usable_original_for_conversion
                    if src_for_conv is None:
                        try:
                            cached = self._dm.cached_original_path(photo)
                            if cached and cached.exists():
                                src_for_conv = cached
                        except Exception:
                            src_for_conv = None
                    if src_for_conv and Path(src_for_conv).exists():
                        requested_image_path.parent.mkdir(parents=True, exist_ok=True)
                        conversion_tasks.append((result, Path(src_for_conv), requested_image_path))
                    else:
                        print(f"[convert] MISSING_SOURCE {photo.remote_path}: cannot create {requested_image_path}")
                        result.error += "\n轉換失敗：找不到可用原始檔"
                else:
                    result.converted = True
                    result.converted_path = str(requested_image_path)
                    print(f"[convert] SKIP existing converted file: {requested_image_path}")

            results.append(result)
            progress.done += 1
            if progress_callback:
                progress_callback(progress)

        if conversion_tasks and not self._cancel_flag.is_set():
            total_conv = len(conversion_tasks)
            print(f"[convert] Starting conversion: total={total_conv}, workers={self.conversion_workers}, format={output_format}")
            progress.total = total_conv
            progress.done = 0
            progress.current_file = f"轉檔 0/{total_conv}"
            if progress_callback:
                progress_callback(progress)
            with ProcessPoolExecutor(max_workers=self.conversion_workers) as executor:
                future_map = {}
                for result, src, dst in conversion_tasks:
                    future = executor.submit(
                        _convert_image_file,
                        src,
                        dst,
                        output_format=output_format,
                        quality=self.jpeg_quality,
                        subsampling=self.jpeg_subsampling,
                        optimize=self.jpeg_optimize,
                        png_compress_level=self.png_compress_level,
                    )
                    future_map[future] = (result, src, dst)

                for j, future in enumerate(as_completed(future_map), start=1):
                    result, src, dst = future_map[future]
                    progress.done = j
                    progress.current_file = f"轉檔 {j}/{total_conv}: {src.name}"
                    if progress_callback:
                        progress_callback(progress)
                    try:
                        conv_ok, conv_err = future.result()
                    except Exception as exc:
                        conv_ok, conv_err = False, repr(exc)
                    if conv_ok:
                        result.converted = True
                        result.converted_path = str(dst)
                        result.converted_created = True
                        print(f"[convert] OK {src} -> {dst}")
                    else:
                        result.error += f"\n轉換失敗：{conv_err}"
                        print(f"[convert] ERROR {src} -> {dst}: {conv_err}")

        progress.finished = True
        if progress_callback:
            progress_callback(progress)

        # Temporary files used only for duplicate comparison are not user data.
        # Remove the working folder after the backup/conversion pass finishes.
        try:
            if tmp_compare_dir.exists():
                shutil.rmtree(tmp_compare_dir)
                print(f"[backup] Removed temporary compare folder: {tmp_compare_dir}")
        except Exception as exc:
            print(f"[backup] Failed to remove temporary compare folder {tmp_compare_dir}: {exc!r}")

        self._log_results(results)
        return results

    def _log_results(self, results: list[BackupResult]):
        """Write backup results into the runtime log only."""
        try:
            print("\n[backup_result]")
            print("status | filename | remote_path | local_path | size | source | converted | converted_created | converted_path | error")
            for r in results:
                row = [
                    r.status,
                    r.photo.filename,
                    r.photo.remote_path,
                    str(r.dest_path),
                    r.photo.size,
                    r.source,
                    r.converted,
                    getattr(r, "converted_created", False),
                    r.converted_path,
                    r.error.strip(),
                ]
                print(" | ".join(str(x) for x in row))
            print("[/backup_result]\n")
        except Exception as exc:
            print(f"[backup_result] failed to write log: {exc!r}")

    def _photo_date_folder(self, photo: PhotoItem) -> str:
        try:
            if int(photo.modified_time or 0) > 0:
                return datetime.datetime.fromtimestamp(int(photo.modified_time)).strftime("%Y-%m-%d")
        except Exception:
            pass
        return "Unknown Date"

    def _copy_category_file(self, src: Path, dst: Path) -> None:
        dst.parent.mkdir(parents=True, exist_ok=True)
        final, reason = _resolve_destination_for_source(dst, self.existing_file_action, src)
        if final is None:
            print(f"[organize] SKIP duplicate {src} -> {dst} reason={reason}")
            return
        shutil.copy2(src, final)
        print(f"[organize] COPY {src} -> {final}")

    def _create_fonetool_like_categories(self, dest_folder: Path, results: list[BackupResult]) -> None:
        """Best-effort FoneTool-like category folders.

        Without Apple's private Photos database/API we cannot perfectly know all
        smart albums.  This creates a compatible folder skeleton and fills what
        can be inferred from DCIM files:
        - Recent Project: all successfully backed up media by date.
        - Video: video files by date.
        - Screen Shoot: PNG files by date, a useful approximation for iPhone screenshots.
        - Live Photos: still/video pairs that share the same IMG_xxxx stem.
        - Burst: sequential IMG numbers in groups of >=3 on the same date.
        - Selfie: folder created for compatibility; exact front-camera detection
          requires metadata that is not reliably available through AFC alone.
        """
        category_roots = ["Burst", "Live Photos", "Recent Project", "Screen Shoot", "Selfie", "Video"]
        for name in category_roots:
            (dest_folder / name).mkdir(parents=True, exist_ok=True)

        successes = [r for r in results if r.status == "success" and Path(r.dest_path).exists()]
        by_key: dict[tuple[str, str], list[BackupResult]] = {}
        for r in successes:
            key = (str(Path(r.photo.remote_path).parent).replace("\\", "/"), Path(r.photo.filename).stem.upper())
            by_key.setdefault(key, []).append(r)

        live_keys = set()
        for key, group in by_key.items():
            has_video = any(g.photo.ext.lower() in {".mov", ".mp4", ".m4v", ".3gp"} for g in group)
            has_still = any(g.photo.ext.lower() in {".jpg", ".jpeg", ".heic", ".heif", ".png"} for g in group)
            if has_video and has_still:
                live_keys.add(key)

        # Burst heuristic: sequential IMG numbers with at least 3 still images on the same date.
        by_date_folder: dict[tuple[str, str], list[BackupResult]] = {}
        for r in successes:
            if r.photo.ext.lower() not in {".jpg", ".jpeg", ".heic", ".heif"}:
                continue
            by_date_folder.setdefault((self._photo_date_folder(r.photo), str(Path(r.photo.remote_path).parent)), []).append(r)
        burst_set = set()
        import re
        for (_date, _folder), group in by_date_folder.items():
            numbered = []
            for r in group:
                m = re.match(r"IMG_(\d+)$", Path(r.photo.filename).stem.upper())
                if m:
                    numbered.append((int(m.group(1)), r))
            numbered.sort()
            run = []
            prev = None
            for n, r in numbered:
                if prev is None or n == prev + 1:
                    run.append(r)
                else:
                    if len(run) >= 3:
                        burst_set.update(id(x) for x in run)
                    run = [r]
                prev = n
            if len(run) >= 3:
                burst_set.update(id(x) for x in run)

        for r in successes:
            src = Path(r.dest_path)
            date = self._photo_date_folder(r.photo)
            filename = src.name
            ext = r.photo.ext.lower()

            # All items go into Recent Project by date to mimic the broad FoneTool bucket.
            self._copy_category_file(src, dest_folder / "Recent Project" / date / filename)

            if ext in {".mov", ".mp4", ".m4v", ".3gp"}:
                self._copy_category_file(src, dest_folder / "Video" / date / filename)
            if ext == ".png":
                self._copy_category_file(src, dest_folder / "Screen Shoot" / date / filename)

            key = (str(Path(r.photo.remote_path).parent).replace("\\", "/"), Path(r.photo.filename).stem.upper())
            if key in live_keys:
                self._copy_category_file(src, dest_folder / "Live Photos" / date / filename)
            if id(r) in burst_set:
                self._copy_category_file(src, dest_folder / "Burst" / date / filename)

    def full_device_backup(
        self,
        dest_folder: Path,
        progress_callback: Optional[Callable[[str, float], None]] = None,
    ) -> tuple[bool, str]:
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = dest_folder / f"FullBackup_{timestamp}"
        return self._dm.full_backup(backup_dir, progress_callback)
