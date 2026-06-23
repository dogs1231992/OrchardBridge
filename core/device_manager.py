"""
Device adapter for the desktop GUI.

The GUI uses a stable data model while this module delegates
all mobile-device access to the core media service implementation
(`core.iphone_core`).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import subprocess
import sys
import shutil
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Callable, Any

from PIL import Image

from . import iphone_core
from .image_tools import unique_cache_path
from .preferences import get_cache_dir
from .device_types import product_name
from .cli_runner import pymobiledevice3_cmd

PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".gif", ".bmp", ".tiff", ".tif", ".webp", ".dng", ".raw"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".3gp"}
MEDIA_EXTENSIONS = PHOTO_EXTENSIONS | VIDEO_EXTENSIONS

# Internal status codes returned to the GUI.  Keep core/device_manager.py
# language-neutral; gui/app.py maps these codes to the active UI language.
STATUS_CONNECTED = "__ORCHARD_STATUS_CONNECTED__"
STATUS_NO_DEVICE = "__ORCHARD_STATUS_NO_DEVICE__"
STATUS_BRIDGE_MISSING = "__ORCHARD_STATUS_BRIDGE_MISSING__"

# Last direct usbmux probe status.  This lets the GUI distinguish between
# "the bridge works but no device is attached" and "the Apple Mobile Device /
# usbmux bridge itself is not reachable".
_LAST_USBMUX_DIRECT_OK: bool | None = None
_LAST_USBMUX_DIRECT_ERROR: str = ""


def _last_usbmux_bridge_reachable() -> bool | None:
    """Return whether the most recent direct usbmux probe reached the bridge."""
    return _LAST_USBMUX_DIRECT_OK


def _last_usbmux_direct_error() -> str:
    """Return the most recent direct usbmux probe error class name."""
    return _LAST_USBMUX_DIRECT_ERROR


@dataclass
class PhotoItem:
    """Represents one device media item for the GUI."""
    remote_path: str
    filename: str
    size: int = 0
    modified_time: int = 0
    is_video: bool = False
    thumbnail: Optional[object] = None
    selected: bool = True
    _core_item: Optional[iphone_core.PhotoItem] = field(default=None, repr=False)

    @property
    def ext(self) -> str:
        return Path(self.filename).suffix.lower()

    @property
    def size_str(self) -> str:
        return iphone_core.bytes_to_human(self.size)


@dataclass
class DeviceInfo:
    name: str = "未知設備"
    model: str = ""  # Friendly product name, e.g. "iPhone 15"
    product_type: str = ""  # Raw Apple identifier, e.g. "iPhone15,4"
    ios_version: str = ""
    serial: str = ""
    udid: str = ""
    storage_total: int = 0
    storage_used: int = 0
    battery_level: int = 0

    @property
    def storage_str(self) -> str:
        def fmt(b: int) -> str:
            try:
                b = int(b)
            except Exception:
                b = 0
            if b >= 1024 ** 3:
                return f"{b / 1024 ** 3:.1f} GB"
            if b >= 1024 ** 2:
                return f"{b / 1024 ** 2:.1f} MB"
            if b >= 1024:
                return f"{b / 1024:.0f} KB"
            return f"{b} B"
        if self.storage_total > 0:
            return f"{fmt(self.storage_used)} / {fmt(self.storage_total)}"
        return ""




def _display_product_name(product_type: str) -> str:
    """Return the detected device model name for user-facing model fields.

    The app keeps its own product name neutral, but model fields should show the
    actual detected model name (for example, iPhone 15) so the user can confirm
    which physical device is connected.
    """
    return product_name(product_type) or "Device"


def _run_windows_command(args: list[str], timeout: float = 4.0) -> tuple[int, str]:
    """Best-effort Windows command runner for diagnostics only."""
    try:
        proc = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=(subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0),
        )
        return int(proc.returncode), proc.stdout or ""
    except Exception as exc:
        return 999, f"{type(exc).__name__}: {exc}"


def _diagnose_windows_device_bridge() -> list[str]:
    """Return actionable Windows-side diagnostics for usbmux/AMDS problems.

    Windows Explorer can sometimes browse phone photos through the Portable
    Device/MTP layer while pymobiledevice3 still cannot talk to usbmux.  This
    app needs the Apple Mobile Device / usbmux bridge for AFC and full backup
    operations, so we log the relevant service/driver state when usbmux sees no
    devices.
    """
    if os.name != "nt":
        return []
    lines: list[str] = []
    lines.append("Windows Explorer visibility is not enough for OrchardBridge; the app also needs the Apple Mobile Device / usbmux service path.")
    for service in ("Apple Mobile Device Service", "AppleMobileDeviceService"):
        code, out = _run_windows_command(["sc.exe", "query", service])
        compact = " ".join((out or "").split())
        lines.append(f"sc query {service!r}: code={code}; {compact[:500]}")
    common_paths = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Common Files" / "Apple" / "Mobile Device Support",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Common Files" / "Apple" / "Mobile Device Support",
        Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "Apple" / "Installer Cache",
    ]
    for path in common_paths:
        lines.append(f"path exists {path}: {path.exists()}")
    driver_candidates = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Common Files" / "Apple" / "Mobile Device Support" / "Drivers" / "usbaapl64.inf",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Common Files" / "Apple" / "Mobile Device Support" / "Drivers" / "usbaapl64.inf",
    ]
    for path in driver_candidates:
        lines.append(f"driver inf exists {path}: {path.exists()}")
    return lines


def _run(coro):
    """Run an async pymobiledevice3 operation from a normal GUI worker thread.

    A tiny drain delay helps Windows clean up SSL/usbmux transports before the
    event loop closes, reducing non-fatal "Fatal error on SSL transport" noise.
    """
    async def _runner():
        try:
            return await coro
        finally:
            try:
                await asyncio.sleep(0.2)
            except Exception:
                pass
    return asyncio.run(_runner())


def _parse_mtime(text: str) -> int:
    if not text:
        return 0
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return int(dt.datetime.strptime(text, fmt).timestamp())
        except Exception:
            pass
    return 0


def _storage_from_disk(disk: dict[str, Any]) -> tuple[int, int]:
    """Return (total, used) bytes from Apple's disk_usage dictionary."""
    def as_int(value) -> int:
        try:
            return int(value or 0)
        except Exception:
            return 0

    total = as_int(disk.get("TotalDiskCapacity") or disk.get("TotalDataCapacity"))
    used = as_int(disk.get("AmountDataUsed"))
    available = as_int(disk.get("AmountDataAvailable") or disk.get("FreeDiskCapacity"))
    if used <= 0 and total > 0 and available > 0:
        used = max(0, total - available)
    if total > 0:
        used = min(max(used, 0), total)
    else:
        used = max(used, 0)
    return total, used


async def _list_usbmux_devices_direct() -> list[dict[str, Any]]:
    """Return usbmux device records using pymobiledevice3's Python API only.

    This intentionally talks only to usbmuxd / Apple Mobile Device Service.  It
    does *not* open Lockdown, so it can detect a physically connected phone even
    when pairing metadata, Trust prompts, or Lockdown SSL setup are not ready yet.
    The public pymobiledevice3 CLI implements ``usbmux list`` by first calling
    ``pymobiledevice3.usbmux.list_devices()`` and then enriching the result with
    Lockdown information.  For GUI auto-detection, the first step is the reliable
    one we need.
    """
    from pymobiledevice3 import usbmux

    records: list[dict[str, Any]] = []
    for dev in await usbmux.list_devices():
        serial = str(getattr(dev, "serial", "") or "")
        connection_type = str(getattr(dev, "connection_type", "") or "")
        devid = getattr(dev, "devid", "")
        records.append({
            "Identifier": serial,
            "UniqueDeviceID": serial,
            "UDID": serial,
            "ConnectionType": connection_type,
            "DeviceID": str(devid),
            "DeviceName": "Device",
            "Name": "Device",
        })
    return records


def _detect_devices_via_usbmux_direct() -> list[dict[str, Any]]:
    """Detect USB devices in-process without spawning another EXE.

    In PyInstaller onefile mode, ``sys.executable`` is OrchardBridge.exe, not a
    Python interpreter.  Repeatedly running the bundled EXE just to execute
    ``pymobiledevice3 usbmux list`` is slow and can cause busy-cursor flicker.
    This function calls the same low-level usbmux API directly in the current
    process.
    """
    global _LAST_USBMUX_DIRECT_OK, _LAST_USBMUX_DIRECT_ERROR
    try:
        records = _run(_list_usbmux_devices_direct())
        _LAST_USBMUX_DIRECT_OK = True
        _LAST_USBMUX_DIRECT_ERROR = ""
        print(f"[device] direct usbmux API devices found: {len(records)}")
        return records
    except Exception as exc:
        _LAST_USBMUX_DIRECT_OK = False
        _LAST_USBMUX_DIRECT_ERROR = type(exc).__name__
        print(f"[device] direct usbmux API probe failed: {type(exc).__name__}: {exc}")
        return []


def _detect_devices_via_lockdown_direct() -> list[dict[str, Any]]:
    """Fallback: detect/enrich the first device via Lockdown.

    Lockdown is useful because it returns name, model, iOS version, and serial
    number, but it is not as reliable as usbmux for the first auto-detect step.
    A phone may be visible to usbmux while Lockdown is still waiting for Trust or
    while Windows is cleaning up SSL transports.
    """
    try:
        values, _disk, _battery = _run(_read_lockdown_values())
    except Exception as exc:
        print(f"[device] direct lockdown probe failed: {type(exc).__name__}: {exc}")
        return []
    if not isinstance(values, dict) or not values:
        print("[device] direct lockdown probe returned no values")
        return []

    record = {
        "DeviceName": values.get("DeviceName") or values.get("Name") or "Device",
        "ProductType": values.get("ProductType") or "",
        "ProductVersion": values.get("ProductVersion") or "",
        "SerialNumber": values.get("SerialNumber") or "",
        "Identifier": values.get("UniqueDeviceID") or values.get("UDID") or values.get("UniqueChipID") or "",
        "UniqueDeviceID": values.get("UniqueDeviceID") or values.get("UDID") or values.get("UniqueChipID") or "",
        "ConnectionType": "USB",
    }
    print("[device] direct lockdown probe succeeded")
    return [record]


def _detect_devices_via_usbmux_cli(timeout: float = 3.0) -> list[dict[str, Any]]:
    """Detect USB devices.

    Source runs use ``pymobiledevice3 usbmux list`` because it is lightweight
    when ``sys.executable`` is a real Python interpreter and gives richer JSON.
    Frozen onefile builds must avoid subprocess probing, so they use the
    in-process usbmux API first and then Lockdown only as a fallback.
    """
    if getattr(sys, "frozen", False):
        devices = _detect_devices_via_usbmux_direct()
        if devices:
            return devices
        return _detect_devices_via_lockdown_direct()

    # On Windows, the CLI can hang for several seconds when Apple Mobile Device
    # Service / usbmux is unavailable.  Use the in-process API first and avoid
    # repeated slow subprocess timeouts in the GUI auto-detect loop.
    if os.name == "nt":
        devices = _detect_devices_via_usbmux_direct()
        if devices:
            return devices
        return _detect_devices_via_lockdown_direct()

    cmd = pymobiledevice3_cmd("usbmux", "list")
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=(subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0),
        )
    except Exception as exc:
        print(f"[device] usbmux CLI probe failed: {type(exc).__name__}: {exc}; trying direct usbmux API")
        devices = _detect_devices_via_usbmux_direct()
        return devices or _detect_devices_via_lockdown_direct()

    text = (proc.stdout or "").strip()
    if proc.returncode != 0 or not text:
        print(f"[device] usbmux CLI returned code={proc.returncode}, output_len={len(text)}; trying direct usbmux API")
        devices = _detect_devices_via_usbmux_direct()
        return devices or _detect_devices_via_lockdown_direct()
    try:
        data = json.loads(text)
    except Exception as exc:
        print(f"[device] usbmux CLI JSON parse failed: {type(exc).__name__}: {exc}; trying direct usbmux API")
        devices = _detect_devices_via_usbmux_direct()
        return devices or _detect_devices_via_lockdown_direct()
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        devices = _detect_devices_via_usbmux_direct()
        return devices or []
    devices = [d for d in data if isinstance(d, dict)]
    if devices:
        return devices
    devices = _detect_devices_via_usbmux_direct()
    return devices or _detect_devices_via_lockdown_direct()


def _device_info_from_usbmux_record(record: dict[str, Any]) -> DeviceInfo:
    raw_product_type = str(record.get("ProductType") or "")
    return DeviceInfo(
        name=str(record.get("DeviceName") or record.get("Name") or "Device"),
        model=(_display_product_name(raw_product_type) if raw_product_type else ""),
        product_type=raw_product_type,
        ios_version=str(record.get("ProductVersion") or ""),
        serial=str(record.get("SerialNumber") or ""),
        udid=str(record.get("Identifier") or record.get("UniqueDeviceID") or record.get("UDID") or ""),
        storage_total=0,
        storage_used=0,
        battery_level=0,
    )


def _merge_lockdown_info(base: DeviceInfo) -> DeviceInfo:
    """Best-effort enrichment of device info.

    Failure here must never be treated as a disconnect, because some iOS / USB
    states briefly terminate Lockdown SSL sessions even while usbmux can still
    see the phone. This was the cause of the v5 auto-detect loop.
    """
    try:
        values, disk, battery = _run(_read_lockdown_values())
    except Exception as exc:
        print(f"[device] lockdown enrich failed: {type(exc).__name__}: {exc}")
        try:
            import traceback as _traceback
            print(_traceback.format_exc())
        except Exception:
            pass
        return base
    try:
        storage_total, storage_used = _storage_from_disk(disk)
        raw_product_type = str(values.get("ProductType") or base.product_type or base.model or "")
        return DeviceInfo(
            name=str(values.get("DeviceName") or base.name or "Device"),
            model=_display_product_name(raw_product_type),
            product_type=raw_product_type,
            ios_version=str(values.get("ProductVersion") or base.ios_version or ""),
            serial=str(values.get("SerialNumber") or base.serial or ""),
            udid=str(values.get("UniqueDeviceID") or values.get("UniqueChipID") or base.udid or ""),
            storage_total=storage_total or base.storage_total,
            storage_used=storage_used if storage_total else base.storage_used,
            battery_level=int(battery.get("BatteryCurrentCapacity") or base.battery_level or 0),
        )
    except Exception:
        return base


async def _read_lockdown_values() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    lockdown = await iphone_core._new_lockdown_client(None)

    async def get_value(*args):
        try:
            return await iphone_core._maybe_await(lockdown.get_value(*args))
        except Exception:
            return {}

    values = await get_value()
    disk = await get_value("com.apple.disk_usage")
    battery = await get_value("com.apple.mobile.battery")
    return values or {}, disk or {}, battery or {}


class DeviceManager:
    """
    GUI-facing device manager using the mobile-device core.

    It intentionally opens/closes AFC connections per operation. That is a bit
    slower than keeping a global AFC connection, but much more robust while we
    are still iterating on the app.
    """

    def __init__(self):
        self._connected = False
        self._info: Optional[DeviceInfo] = None
        self._cache_dir = get_cache_dir()
        self.keep_original_cache = False
        self.last_download_source = ""
        self._diagnostics_logged = False
        # Once a direct usbmux probe succeeds or a device connects, never report
        # a missing bridge merely because the user unplugged the device.
        self._bridge_known_available = False

    def apply_preferences(self, preferences) -> None:
        """Apply cache-related preferences immediately."""
        try:
            self.keep_original_cache = bool(getattr(preferences, "keep_original_cache", False))
        except Exception:
            self.keep_original_cache = False

    def cached_original_path(self, item: PhotoItem) -> Path:
        """Return the full-size original cache path for a remote media item.

        The filename is readable, for example ``IMG_1234_<sha1>.HEIC``.
        A digest-only fallback is still accepted so development-time cache data
        can continue to accelerate backups.
        """
        originals = self._cache_dir / "originals"
        new_path = unique_cache_path(originals, item.remote_path)
        if new_path.exists():
            return new_path
        suffix = Path(item.remote_path).suffix.lower() or ".bin"
        legacy = originals / f"{hashlib.sha1(item.remote_path.encode('utf-8', errors='ignore')).hexdigest()}{suffix}"
        if legacy.exists():
            return legacy
        return new_path

    @property
    def is_connected(self) -> bool:
        return self._connected

    def connect(self) -> tuple[bool, str]:
        """Connect/detect the first USB device.

        v6 uses usbmux list as the primary auto-detection mechanism. Optional
        Lockdown metadata such as storage/battery is read only as best effort,
        so a transient Lockdown SSL termination no longer sends the GUI back to
        "裝置已斷線" forever.
        """
        print("[device] Auto-connect probe via usbmux list")
        devices = _detect_devices_via_usbmux_cli()
        print(f"[device] usbmux devices found: {len(devices)}")
        if not devices:
            self._connected = False
            self._info = None

            direct_ok = _last_usbmux_bridge_reachable()
            direct_error = _last_usbmux_direct_error()
            if direct_ok:
                self._bridge_known_available = True

            # If the bridge has ever been reachable in this app session, then an
            # empty usbmux list means the phone is unplugged / locked / not trusted,
            # not that Apple Mobile Device Support is missing.  This avoids showing
            # the bridge-install warning after the user simply removes the USB cable.
            bridge_unreachable = (direct_ok is False and not self._bridge_known_available)

            if bridge_unreachable:
                diagnostics = _diagnose_windows_device_bridge()
                if not self._diagnostics_logged:
                    self._diagnostics_logged = True
                    print("[device_diagnostics] usbmux did not report any devices. Running Windows bridge diagnostics once...")
                    for line in diagnostics:
                        print(f"[device_diagnostics] {line}")
                if direct_error == "ConnectionFailedToUsbmuxdError":
                    return False, STATUS_BRIDGE_MISSING

            return False, STATUS_NO_DEVICE

        self._bridge_known_available = True
        info = _device_info_from_usbmux_record(devices[0])
        info = _merge_lockdown_info(info)
        self._info = info
        self._connected = True
        print(f"[device] Connected: name={info.name}, model={info.model}, product_type={info.product_type}, ios={info.ios_version}, udid={info.udid}")
        return True, STATUS_CONNECTED

    def disconnect(self):
        self._connected = False
        self._info = None

    def check_connection(self) -> tuple[bool, str]:
        """Lightweight health check used by the GUI auto-connect monitor.

        Health checks use usbmux detection instead of Lockdown.get_value().
        This still detects an unplugged device, but avoids false disconnects
        caused by Windows SSL transport cleanup warnings.
        """
        if not self._connected:
            return False, "未連線"

        devices = _detect_devices_via_usbmux_cli(timeout=3.0)
        if not devices:
            # Do not clear _info here.  A single Windows usbmux hiccup should
            # not erase the model/iOS/storage fields from the UI; the GUI will
            # disconnect only after repeated consecutive health failures.
            return False, "Device not visible through usbmux"

        current_udid = (self._info.udid if self._info else "")
        chosen = None
        if current_udid:
            for d in devices:
                did = str(d.get("Identifier") or d.get("UniqueDeviceID") or d.get("UDID") or "")
                if did == current_udid:
                    chosen = d
                    break
        if chosen is None:
            chosen = devices[0]

        base = _device_info_from_usbmux_record(chosen)
        if self._info:
            # Preserve optional values previously obtained from Lockdown, such
            # as model, iOS version, storage, and battery, because health checks
            # should stay cheap and raw usbmux records often contain only UDID.
            old = self._info
            if not base.name or base.name == "Device":
                base.name = old.name
            if not base.model or base.model == "Device":
                base.model = old.model
            if not base.ios_version:
                base.ios_version = old.ios_version
            base.storage_total = old.storage_total
            base.storage_used = old.storage_used
            base.battery_level = old.battery_level
            base.serial = base.serial or old.serial
            base.product_type = base.product_type or old.product_type
            if not base.model and base.product_type:
                base.model = product_name(base.product_type)
        self._info = base
        self._connected = True
        return True, "連線正常"

    def get_device_info(self) -> DeviceInfo:
        return self._info or DeviceInfo()

    def list_photos(self, progress_callback: Optional[Callable[[int, int, str], None]] = None) -> list[PhotoItem]:
        if not self._connected:
            return []

        def progress(event: str, payload: dict):
            if not progress_callback:
                return
            if event in ("scan_dir", "scan_progress", "scan_done"):
                progress_callback(int(payload.get("count", 0)), -1, str(payload.get("path") or payload.get("name") or ""))

        print("[scan] Starting /DCIM media scan")
        core_items = _run(iphone_core.scan_photos(progress=progress, udid=None))
        print(f"[scan] Completed /DCIM media scan: {len(core_items)} media items")
        out: list[PhotoItem] = []
        for c in core_items:
            ext = Path(c.name).suffix.lower()
            out.append(
                PhotoItem(
                    remote_path=c.remote_path,
                    filename=c.name,
                    size=int(c.size or 0),
                    modified_time=_parse_mtime(c.mtime_text),
                    is_video=(ext in VIDEO_EXTENSIONS),
                    _core_item=c,
                )
            )
        # Prefer newest first if mtime is available; otherwise stable DCIM order.
        out.sort(key=lambda p: (p.modified_time, p.remote_path), reverse=True)
        return out

    def _to_core_item(self, item: PhotoItem) -> iphone_core.PhotoItem:
        if item._core_item is not None:
            return item._core_item
        return iphone_core.PhotoItem(
            remote_path=item.remote_path,
            name=item.filename,
            folder=str(Path(item.remote_path).parent).replace("\\", "/"),
            suffix=item.ext,
            size=item.size,
            mtime_text="",
        )

    def read_thumbnail(self, item: PhotoItem, thumb_size: tuple[int, int] = (200, 200)) -> Optional[object]:
        """Read one thumbnail. Kept for compatibility; batch loading is preferred."""
        try:
            result = _run(
                iphone_core.build_thumbnails(
                    [self._to_core_item(item)],
                    self._cache_dir,
                    max_items=1,
                    progress=None,
                    udid=None,
                    keep_originals=self.keep_original_cache,
                )
            )
            thumb_path = result.get(item.remote_path)
            if not thumb_path:
                return None
            img = Image.open(thumb_path)
            img.thumbnail(thumb_size, Image.LANCZOS)
            return img.convert("RGB")
        except Exception:
            return None

    def read_thumbnails_batch(
        self,
        photos: list[PhotoItem],
        *,
        max_items: int | None = None,
        on_thumbnail: Optional[Callable[[PhotoItem, object], None]] = None,
        on_progress: Optional[Callable[[str], None]] = None,
    ) -> None:
        """Build thumbnails using the MVP batch core so we do not reconnect once per photo."""
        media_items = list(photos)
        target = media_items if max_items is None or max_items <= 0 else media_items[:max_items]
        print(f"[thumbnail] Starting thumbnail batch: {len(target)} item(s), keep_original_cache={self.keep_original_cache}")
        if not target:
            return
        core_items = [self._to_core_item(p) for p in target]
        by_path = {p.remote_path: p for p in target}

        def progress(event: str, payload: dict):
            if event == "thumbnail":
                remote_path = payload.get("remote_path")
                thumb_path = payload.get("thumb_path")
                photo = by_path.get(remote_path)
                if photo and thumb_path:
                    try:
                        img = Image.open(thumb_path).convert("RGB")
                        photo.thumbnail = img
                        if on_thumbnail:
                            on_thumbnail(photo, img)
                    except Exception:
                        pass
                if on_progress:
                    on_progress(f"縮圖 {payload.get('index')}/{payload.get('total')}")
            elif event == "log" and on_progress:
                on_progress(str(payload.get("text") or ""))

        _run(iphone_core.build_thumbnails(core_items, self._cache_dir, max_items=len(core_items), progress=progress, udid=None, keep_originals=self.keep_original_cache))
        print(f"[thumbnail] Finished thumbnail batch: {len(target)} item(s)")

    @property
    def cache_dir(self) -> Path:
        return self._cache_dir

    def _count_files(self, folder: Path) -> int:
        try:
            if not folder.exists():
                return 0
            return sum(1 for p in folder.rglob("*") if p.is_file())
        except Exception:
            return 0

    def thumbnail_cache_folder(self) -> Path:
        return self._cache_dir / "thumbs"

    def original_cache_folder(self) -> Path:
        return self._cache_dir / "originals"

    def cache_file_count(self, kind: str = "thumbs") -> tuple[Path, int]:
        folder = self.original_cache_folder() if kind == "originals" else self.thumbnail_cache_folder()
        return folder, self._count_files(folder)

    def clear_thumbnail_cache(self) -> tuple[bool, str]:
        """Delete generated preview thumbnails only; keep original-file cache."""
        folder = self.thumbnail_cache_folder()
        try:
            if folder.exists():
                shutil.rmtree(folder)
            return True, f"已刪除縮圖快取：{folder}"
        except Exception as exc:
            return False, f"刪除縮圖快取失敗：{exc!r}"

    def clear_original_cache(self) -> tuple[bool, str]:
        """Delete full-size original-file cache only."""
        folder = self.original_cache_folder()
        try:
            if folder.exists():
                shutil.rmtree(folder)
            return True, f"已刪除原始檔快取：{folder}"
        except Exception as exc:
            return False, f"刪除原始檔快取失敗：{exc!r}"

    def download_photo(self, item: PhotoItem, dest_path: Path, progress_callback: Optional[Callable[[int, int], None]] = None) -> bool:
        try:
            self.last_download_source = ""
            cached = self.cached_original_path(item)
            if cached.exists():
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(cached, dest_path)
                self.last_download_source = "cache"
                print(f"[backup] Copied from original cache: {cached} -> {dest_path}")
                return dest_path.exists()

            _run(iphone_core.download_single_photo(self._to_core_item(item), dest_path, progress=None, udid=None))
            self.last_download_source = "iphone"
            # If original caching is enabled, remember the downloaded file so
            # future backups/previews can copy locally instead of pulling again.
            try:
                if self.keep_original_cache and dest_path.exists() and not cached.exists():
                    cached.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(dest_path, cached)
            except Exception:
                pass
            print(f"[backup] Downloaded from device: {item.remote_path} -> {dest_path}")
            return dest_path.exists()
        except Exception as exc:
            self.last_download_source = "error"
            print(f"[backup] Failed to back up {item.remote_path}: {exc!r}")
            return False

    def full_backup(self, dest_folder: Path, progress_callback: Optional[Callable[[str, float], None]] = None) -> tuple[bool, str]:
        try:
            def progress(event: str, payload: dict):
                if not progress_callback:
                    return
                if event == "log":
                    progress_callback(str(payload.get("text") or ""), float(payload.get("pct") or 0.0))
                elif event == "full_backup_done":
                    progress_callback(str(payload.get("backup_dir") or "整機備份完成"), 1.0)
            backup_dir = iphone_core.run_full_backup(dest_folder, progress)
            return True, f"備份完成，儲存於：{backup_dir}"
        except Exception as e:
            return False, f"備份失敗：{repr(e)}"
