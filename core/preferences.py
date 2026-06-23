r"""JSON-backed preference store for OrchardBridge.

Settings are stored in a normal Windows application data folder:
    %APPDATA%\OrchardBridge\settings.json

Thumbnail/cache files are stored under:
    %LOCALAPPDATA%\OrchardBridge\Cache
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict, fields
from pathlib import Path
from typing import Any

APP_NAME = "OrchardBridge"
def _base_roaming_dir() -> Path:
    root = os.environ.get("APPDATA")
    if root:
        return Path(root)
    return Path.home() / "AppData" / "Roaming"


def _base_local_dir() -> Path:
    root = os.environ.get("LOCALAPPDATA")
    if root:
        return Path(root)
    return Path.home() / "AppData" / "Local"


SETTINGS_DIR = _base_roaming_dir() / APP_NAME
PREF_PATH = SETTINGS_DIR / "settings.json"
LOCAL_DATA_DIR = _base_local_dir() / APP_NAME
CACHE_DIR = LOCAL_DATA_DIR / "Cache"
LOG_DIR = LOCAL_DATA_DIR / "Logs"

def default_preferences() -> "Preferences":
    return Preferences()

# User-visible language choices. UI translation is loaded from JSON files under locales/.
# Missing keys fall back to English so the app remains usable.
SUPPORTED_LANGUAGES = {
    "zh-TW": "繁體中文",
    "zh-CN": "简体中文",
    "en-US": "English",
    "es-ES": "Español",
    "de-DE": "Deutsch",
    "fr-FR": "Français",
    "ar-SA": "العربية",
    "ja-JP": "日本語",
    "ko-KR": "한국어",
    "th-TH": "ไทย",
    "id-ID": "Bahasa Indonesia",
    "pt-BR": "Português",
    "ru-RU": "Русский",
}


def get_supported_languages() -> dict[str, str]:
    return dict(SUPPORTED_LANGUAGES)


@dataclass
class Preferences:
    settings_schema_version: int = 1
    language: str = "en-US"  # see SUPPORTED_LANGUAGES
    theme_mode: str = "light"  # light / dark
    default_photo_backup_folder: str = str(Path.home() / "OrchardBridgePhotosBackup")
    default_full_backup_folder: str = str(Path.home() / "OrchardBridgeFullBackup")

    # Conversion settings.
    convert_after_backup: bool = True
    image_output_format: str = "JPEG"  # JPEG / PNG

    # JPEG-only settings.
    jpeg_quality: int = 100
    jpeg_subsampling: int = 0  # 0 = 4:4:4 best quality; 2 = smaller 4:2:0
    jpeg_optimize: bool = True

    # PNG-only setting.  PNG is lossless; this controls compression effort, not image quality.
    png_compress_level: int = 0  # 0 fastest/largest, 9 smallest/slowest

    # Original-file cache. When enabled, thumbnail generation keeps a full-size
    # original cache so later backups can copy locally instead of pulling the
    # same file from the device again.
    keep_original_cache: bool = True

    # Conversion worker count. Default uses most cores while leaving two for the UI/system.
    conversion_workers: int = max(1, (os.cpu_count() or 2) - 2)

    # Destination conflict behavior for original backup and converted files.
    # rename: create "name (1).ext", overwrite: replace existing file, skip: keep existing.
    existing_file_action: str = "rename"

    delete_thumbnail_cache_on_exit: bool = False

    # Closing behavior. When enabled, clicking the window X hides the app to
    # the system tray instead of ending the current run.
    close_to_tray_on_close: bool = False

    # UI font size. 10 is the current standard; higher values enlarge most UI text.
    ui_font_size: int = 10


def get_settings_path() -> Path:
    return PREF_PATH


def get_cache_dir() -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR


def _coerce_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return default if value is None else bool(value)


def _sanitize(data: dict[str, Any]) -> Preferences:
    defaults = Preferences()
    # Accept the earlier internal key name if it appears in a development-time settings file.
    if "convert_after_backup" not in data and "convert_heic_to_jpeg" in data:
        data = dict(data)
        data["convert_after_backup"] = data.get("convert_heic_to_jpeg")

    allowed = {f.name for f in fields(Preferences)}
    clean: dict[str, Any] = {}
    for key in allowed:
        if key in data:
            clean[key] = data[key]

    prefs = Preferences(**{**asdict(defaults), **clean})

    if prefs.language not in SUPPORTED_LANGUAGES:
        prefs.language = defaults.language

    prefs.theme_mode = str(getattr(prefs, "theme_mode", defaults.theme_mode)).lower().strip()
    if prefs.theme_mode not in {"light", "dark"}:
        prefs.theme_mode = defaults.theme_mode

    # This repository is still on the first public settings schema.  Keep the
    # value stable and avoid release-to-release migrations until a published
    # version needs one.
    prefs.settings_schema_version = defaults.settings_schema_version

    try:
        prefs.jpeg_quality = int(prefs.jpeg_quality)
    except Exception:
        prefs.jpeg_quality = defaults.jpeg_quality
    prefs.jpeg_quality = max(1, min(100, prefs.jpeg_quality))

    try:
        prefs.jpeg_subsampling = int(prefs.jpeg_subsampling)
    except Exception:
        prefs.jpeg_subsampling = defaults.jpeg_subsampling
    if prefs.jpeg_subsampling not in {0, 1, 2}:
        prefs.jpeg_subsampling = 0

    prefs.convert_after_backup = _coerce_bool(prefs.convert_after_backup, defaults.convert_after_backup)

    fmt = str(getattr(prefs, "image_output_format", defaults.image_output_format)).upper().strip()
    prefs.image_output_format = "PNG" if fmt == "PNG" else "JPEG"

    prefs.jpeg_optimize = _coerce_bool(prefs.jpeg_optimize, defaults.jpeg_optimize)
    prefs.keep_original_cache = _coerce_bool(prefs.keep_original_cache, defaults.keep_original_cache)
    prefs.delete_thumbnail_cache_on_exit = _coerce_bool(
        prefs.delete_thumbnail_cache_on_exit,
        defaults.delete_thumbnail_cache_on_exit,
    )
    prefs.close_to_tray_on_close = _coerce_bool(
        getattr(prefs, "close_to_tray_on_close", defaults.close_to_tray_on_close),
        defaults.close_to_tray_on_close,
    )

    try:
        prefs.png_compress_level = int(prefs.png_compress_level)
    except Exception:
        prefs.png_compress_level = defaults.png_compress_level
    prefs.png_compress_level = max(0, min(9, prefs.png_compress_level))


    try:
        prefs.conversion_workers = int(prefs.conversion_workers)
    except Exception:
        prefs.conversion_workers = defaults.conversion_workers
    prefs.conversion_workers = max(1, min(os.cpu_count() or 1, prefs.conversion_workers))

    action = str(getattr(prefs, "existing_file_action", defaults.existing_file_action)).lower().strip()
    if action not in {"rename", "overwrite", "skip"}:
        action = "rename"
    prefs.existing_file_action = action

    try:
        prefs.ui_font_size = int(getattr(prefs, "ui_font_size", defaults.ui_font_size))
    except Exception:
        prefs.ui_font_size = defaults.ui_font_size
    prefs.ui_font_size = max(8, min(16, prefs.ui_font_size))

    prefs.default_photo_backup_folder = str(prefs.default_photo_backup_folder or defaults.default_photo_backup_folder)
    prefs.default_full_backup_folder = str(prefs.default_full_backup_folder or defaults.default_full_backup_folder)

    # Pre-release cleanup: if a development settings file still contains the old
    # user-visible default folder names, move it to the neutral OrchardBridge names.
    # Custom user folders are preserved.
    old_photo_default = str(Path.home() / "iPhonePhotosBackup")
    old_full_default = str(Path.home() / "iPhoneBackup")
    if str(getattr(prefs, "default_photo_backup_folder", "")) == old_photo_default:
        prefs.default_photo_backup_folder = defaults.default_photo_backup_folder
    if str(getattr(prefs, "default_full_backup_folder", "")) == old_full_default:
        prefs.default_full_backup_folder = defaults.default_full_backup_folder
    return prefs


def load_preferences() -> Preferences:
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    LOCAL_DATA_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    try:
        if PREF_PATH.exists():
            data = json.loads(PREF_PATH.read_text(encoding="utf-8"))
            return _sanitize(data if isinstance(data, dict) else {})
    except Exception:
        pass
    prefs = Preferences()
    save_preferences(prefs)
    return prefs


def save_preferences(prefs: Preferences) -> None:
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    prefs = _sanitize(asdict(prefs))
    PREF_PATH.write_text(
        json.dumps(asdict(prefs), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def prefs_to_dict(prefs: Preferences) -> dict[str, Any]:
    return asdict(_sanitize(asdict(prefs)))
