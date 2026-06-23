"""
Service layer for OrchardBridge.

It uses pymobiledevice3's AFC service for photo discovery/download and invokes
pymobiledevice3 backup2 via subprocess for full-device backup.
"""

from __future__ import annotations

import asyncio
import csv
import datetime as dt
import inspect
import os
import posixpath
import re
import subprocess
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

from .cli_runner import pymobiledevice3_cmd
from .image_tools import (
    PHOTO_EXTENSIONS,
    HEIC_EXTENSIONS,
    VIDEO_EXTENSIONS,
    MEDIA_EXTENSIONS,
    bytes_to_human,
    convert_to_jpeg,
    is_photo_name,
    is_media_name,
    make_thumbnail_file,
    make_video_thumbnail_file,
    safe_filename,
    thumbnail_cache_path,
    unique_cache_path,
)

ProgressCallback = Callable[[str, dict], None]


@dataclass
class PhotoItem:
    remote_path: str
    name: str
    folder: str
    suffix: str
    size: int | None = None
    mtime_text: str = ""

    @property
    def size_text(self) -> str:
        return bytes_to_human(self.size)

    @property
    def rel_path(self) -> Path:
        # Remote AFC root is /var/mobile/Media. Keep DCIM/... structure locally.
        clean = self.remote_path.replace("\\", "/").lstrip("/")
        parts = [safe_filename(p) for p in clean.split("/") if p]
        return Path(*parts) if parts else Path(safe_filename(self.name))


class PymobiledeviceImportError(RuntimeError):
    pass


def _import_pymobiledevice3():
    try:
        # pymobiledevice3 9.x no longer supports directly instantiating
        # LockdownClient(); use create_using_usbmux() instead.
        from pymobiledevice3.lockdown import create_using_usbmux
        from pymobiledevice3.services.afc import AfcService
        return create_using_usbmux, AfcService
    except Exception as exc:
        tb = traceback.format_exc()
        print("[pymobiledevice3] import failed while loading Lockdown/AFC:")
        print(tb)
        if getattr(sys, "frozen", False):
            hint = (
                "這是打包後 EXE 內部缺少 pymobiledevice3 / AFC / Lockdown 依賴的問題，"
                "不是手機沒有連線。請用最新版 build_onefile_exe 重新打包；若仍失敗，"
                "請提供 %LOCALAPPDATA%\\OrchardBridge\\Logs 裡最新的 run_*.log。"
            )
        else:
            hint = "請先執行 pip install -r requirements.txt。"
        raise PymobiledeviceImportError(
            f"無法載入 pymobiledevice3 的 Lockdown/AFC 模組。{hint}\n"
            f"原始錯誤：{exc!r}"
        ) from exc


async def _new_lockdown_client(udid: str | None = None):
    create_using_usbmux, _AfcService = _import_pymobiledevice3()
    kwargs = {"autopair": True}
    if udid:
        # The current public API uses serial=<UDID>.
        kwargs["serial"] = udid
    try:
        return await create_using_usbmux(**kwargs)
    except TypeError:
        # Keep compatibility with older releases that may not accept autopair.
        kwargs.pop("autopair", None)
        return await create_using_usbmux(**kwargs)


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


async def _open_afc(udid: str | None = None):
    _create_using_usbmux, AfcService = _import_pymobiledevice3()
    lockdown = await _new_lockdown_client(udid)
    afc = AfcService(lockdown)
    # pymobiledevice3 9.x AfcService must be entered so its background
    # AFC reader task is started.
    if hasattr(afc, "__aenter__"):
        afc = await afc.__aenter__()
        return afc, True
    return afc, False


async def _close_afc(afc, entered: bool):
    if entered and hasattr(afc, "__aexit__"):
        await afc.__aexit__(None, None, None)
    elif hasattr(afc, "close"):
        await _maybe_await(afc.close())


async def _isdir(afc, path: str) -> bool:
    try:
        return bool(await _maybe_await(afc.isdir(path)))
    except Exception:
        return False


async def _listdir(afc, path: str) -> list[str]:
    return list(await _maybe_await(afc.listdir(path)))


async def _stat(afc, path: str) -> dict:
    try:
        stat = await _maybe_await(afc.stat(path))
        return dict(stat) if not isinstance(stat, dict) else stat
    except Exception:
        return {}


def _parse_mtime(value) -> str:
    if value is None:
        return ""
    try:
        if hasattr(value, "strftime"):
            return value.strftime("%Y-%m-%d %H:%M")
        if isinstance(value, (int, float)):
            return dt.datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M")
        return str(value)
    except Exception:
        return ""


def _parse_size(stat: dict) -> int | None:
    for key in ("st_size", "size", "Size"):
        if key in stat:
            try:
                return int(stat[key])
            except Exception:
                return None
    return None


async def scan_photos(progress: Optional[ProgressCallback] = None, udid: str | None = None) -> list[PhotoItem]:
    """Scan /DCIM on the device via AFC and return photo/video media files.

    This intentionally includes .MOV/.MP4 files so Live Photo motion clips and
    normal videos show up in the GUI and can be backed up.
    """
    afc, entered = await _open_afc(udid)
    photos: list[PhotoItem] = []
    scanned_dirs = 0

    async def walk(path: str):
        nonlocal scanned_dirs
        scanned_dirs += 1
        if progress:
            progress("scan_dir", {"path": path, "count": len(photos), "dirs": scanned_dirs})
        try:
            names = await _listdir(afc, path)
        except Exception as exc:
            if progress:
                progress("log", {"text": f"無法讀取 {path}: {exc}"})
            return
        for name in names:
            full = posixpath.join(path, name)
            try:
                if await _isdir(afc, full):
                    await walk(full)
                elif is_media_name(name):
                    stat = await _stat(afc, full)
                    photos.append(
                        PhotoItem(
                            remote_path=full,
                            name=name,
                            folder=posixpath.dirname(full),
                            suffix=Path(name).suffix.lower(),
                            size=_parse_size(stat),
                            mtime_text=_parse_mtime(stat.get("st_mtime") or stat.get("mtime")),
                        )
                    )
                    if progress and len(photos) % 100 == 0:
                        progress("scan_progress", {"count": len(photos)})
            except Exception as exc:
                if progress:
                    progress("log", {"text": f"略過 {full}: {exc}"})

    try:
        # AFC media root is /var/mobile/Media; /DCIM is the camera roll directory.
        await walk("/DCIM")
    finally:
        await _close_afc(afc, entered)

    photos.sort(key=lambda p: (p.folder, p.name))
    if progress:
        progress("scan_done", {"count": len(photos)})
    return photos


async def _pull_to_exact_path(afc, remote_path: str, dest_path: Path) -> Path:
    """Pull a single remote file to an exact local path using afc.pull."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    # afc.pull(file, directory) creates directory/basename(file)
    await _maybe_await(afc.pull(remote_path, str(dest_path.parent), progress_bar=False))
    pulled = dest_path.parent / safe_filename(posixpath.basename(remote_path))
    # Some pymobiledevice3 versions use the original basename without our sanitization.
    original_pulled = dest_path.parent / posixpath.basename(remote_path)
    if original_pulled.exists():
        pulled = original_pulled
    if pulled != dest_path and pulled.exists():
        if dest_path.exists():
            dest_path.unlink()
        pulled.replace(dest_path)
    return dest_path


async def build_thumbnails(
    photos: list[PhotoItem],
    cache_dir: Path,
    *,
    max_items: int = 250,
    progress: Optional[ProgressCallback] = None,
    udid: str | None = None,
    keep_originals: bool = False,
) -> dict[str, str]:
    """Download selected originals to cache and make thumbnail PNGs.

    If keep_originals is True, the full-size local original is retained under
    cache/originals so later backups can copy it locally instead of pulling the
    same file from the device again. If False, the original is treated as a
    temporary file and deleted after the thumbnail is generated.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    originals_dir = cache_dir / "originals"
    thumbs_dir = cache_dir / "thumbs"
    result: dict[str, str] = {}
    target = [p for p in photos if p.suffix.lower() in MEDIA_EXTENSIONS][:max_items]
    if not target:
        return result

    afc, entered = await _open_afc(udid)
    try:
        for i, photo in enumerate(target, start=1):
            thumb_path = thumbnail_cache_path(thumbs_dir, photo.remote_path)
            try:
                if not thumb_path.exists():
                    local_original = unique_cache_path(originals_dir, photo.remote_path)
                    try:
                        if not local_original.exists():
                            await _pull_to_exact_path(afc, photo.remote_path, local_original)
                        make_video_thumbnail_file(local_original, thumb_path) if photo.suffix.lower() in VIDEO_EXTENSIONS else make_thumbnail_file(local_original, thumb_path)
                    finally:
                        if not keep_originals:
                            # When original caching is disabled, the preview cache
                            # should not behave like a hidden backup. Keep only the
                            # generated thumbnail PNG and remove the temporary original.
                            try:
                                if local_original.exists():
                                    local_original.unlink()
                            except Exception:
                                pass
                result[photo.remote_path] = str(thumb_path)
                if progress:
                    progress(
                        "thumbnail",
                        {"remote_path": photo.remote_path, "thumb_path": str(thumb_path), "index": i, "total": len(target)},
                    )
            except Exception as exc:
                if progress:
                    progress("log", {"text": f"縮圖失敗 {photo.remote_path}: {exc}"})
    finally:
        if not keep_originals:
            try:
                if originals_dir.exists() and not any(originals_dir.iterdir()):
                    originals_dir.rmdir()
            except Exception:
                pass
        await _close_afc(afc, entered)
    return result


async def backup_selected_photos(
    photos: list[PhotoItem],
    output_parent: Path,
    *,
    convert_jpeg: bool = False,
    keep_original: bool = True,
    progress: Optional[ProgressCallback] = None,
    udid: str | None = None,
) -> Path:
    """Back up selected photos, optionally converting HEIC/HEIF to JPEG."""
    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_root = output_parent / f"OrchardBridge_Photo_Backup_{timestamp}"
    originals_root = backup_root / "Original"
    jpeg_root = backup_root / "JPEG_Converted"
    backup_root.mkdir(parents=True, exist_ok=True)

    manifest_path = backup_root / "backup_manifest.csv"
    afc, entered = await _open_afc(udid)
    rows: list[dict[str, str]] = []

    try:
        for i, photo in enumerate(photos, start=1):
            rel = photo.rel_path
            dest_path = originals_root / rel
            converted_path = ""
            status = "success"
            error = ""
            try:
                if progress:
                    progress("backup_progress", {"index": i, "total": len(photos), "name": photo.name})
                await _pull_to_exact_path(afc, photo.remote_path, dest_path)

                if convert_jpeg and dest_path.suffix.lower() in HEIC_EXTENSIONS:
                    converted = jpeg_root / rel.with_suffix(".jpg")
                    convert_to_jpeg(dest_path, converted, delete_original=(not keep_original))
                    converted_path = str(converted)
            except Exception as exc:
                status = "error"
                error = repr(exc) + "\n" + traceback.format_exc()
                if progress:
                    progress("log", {"text": f"備份失敗 {photo.remote_path}: {exc}"})
            rows.append(
                {
                    "status": status,
                    "remote_path": photo.remote_path,
                    "local_original_path": str(dest_path),
                    "converted_jpeg_path": converted_path,
                    "size": str(photo.size or ""),
                    "error": error,
                }
            )
    finally:
        await _close_afc(afc, entered)

    with open(manifest_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["status", "remote_path", "local_original_path", "converted_jpeg_path", "size", "error"],
        )
        writer.writeheader()
        writer.writerows(rows)

    if progress:
        success = sum(1 for r in rows if r["status"] == "success")
        errors = len(rows) - success
        progress("backup_done", {"backup_root": str(backup_root), "success": success, "errors": errors})
    return backup_root


def run_cli_check(progress: Optional[ProgressCallback] = None) -> int:
    """Run pymobiledevice3 usbmux list and stream output."""
    cmd = pymobiledevice3_cmd("usbmux", "list")
    return _run_streaming_command(cmd, progress)


def run_full_backup(output_parent: Path, progress: Optional[ProgressCallback] = None) -> Path:
    """Run pymobiledevice3 backup2 backup --full DIRECTORY."""
    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = output_parent / f"OrchardBridge_Full_Backup_{timestamp}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    cmd = pymobiledevice3_cmd("backup2", "backup", "--full", str(backup_dir))
    if progress:
        progress("log", {"text": "開始整機備份，請保持裝置解鎖、信任此電腦，且不要拔線。"})
        progress("log", {"text": " ".join(cmd)})
    code = _run_streaming_command(cmd, progress)
    if code != 0:
        raise RuntimeError(f"整機備份失敗，pymobiledevice3 exit code = {code}")
    if progress:
        progress("full_backup_done", {"backup_dir": str(backup_dir)})
    return backup_dir


def _run_streaming_command(cmd: list[str], progress: Optional[ProgressCallback] = None) -> int:
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    except FileNotFoundError as exc:
        if progress:
            progress("log", {"text": f"無法執行命令：{exc}"})
        return 127

    def _clean_cli_line(text: str) -> tuple[str, float]:
        raw = str(text or "")
        pct = 0.0
        m = re.search(r"(\d{1,3})\s*%", raw)
        if m:
            try:
                pct = max(0.0, min(1.0, int(m.group(1)) / 100.0))
            except Exception:
                pct = 0.0
        clean = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", raw)
        clean = clean.replace("\r", " ").replace("\x1b", "")
        clean = re.sub(r"\[[0-9;]*m", "", clean)
        clean = re.sub(r"\s+", " ", clean).strip()
        return clean, pct

    assert proc.stdout is not None
    for line in proc.stdout:
        if progress:
            clean, pct = _clean_cli_line(line.rstrip())
            if clean:
                progress("log", {"text": clean, "pct": pct})
    return proc.wait()


async def download_single_photo(
    photo: PhotoItem,
    dest_path: Path,
    *,
    progress: Optional[ProgressCallback] = None,
    udid: str | None = None,
) -> Path:
    """Download one device photo/media item to an exact local path."""
    afc, entered = await _open_afc(udid)
    try:
        if progress:
            progress("backup_progress", {"index": 1, "total": 1, "name": photo.name})
        return await _pull_to_exact_path(afc, photo.remote_path, dest_path)
    finally:
        await _close_afc(afc, entered)
