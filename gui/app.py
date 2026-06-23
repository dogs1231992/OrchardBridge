"""
主 GUI 應用程式
使用 tkinter + ttk 建立跨平台圖形界面
"""

import sys
import os
import subprocess
import threading
import webbrowser
import zipfile
import shutil
import urllib.parse
import urllib.request
import json
from email.message import EmailMessage
import datetime as dt
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from tkinter import font as tkfont
from pathlib import Path

from core.device_manager import DeviceManager, PhotoItem, STATUS_CONNECTED, STATUS_NO_DEVICE, STATUS_BRIDGE_MISSING
from core.backup_manager import BackupManager, BackupProgress
from core.preferences import Preferences, load_preferences, save_preferences, prefs_to_dict, get_settings_path, get_supported_languages, default_preferences
from .photo_grid import PhotoGridFrame
from .device_panel import DevicePanelFrame, StorageUsageBar
from .backup_panel import BackupPanelFrame
from .tools import HEICConverterWindow, DuplicateCleanerWindow
from core.app_logging import get_latest_log_path, get_bug_report_dir, get_current_log_path, get_log_dir, snapshot_latest_log
from core.ui_fonts import ui_font, set_ui_font_size, scaled_px
from core.i18n import load_locale_table, translate_text

# 顏色主題
THEME_DARK = {
    "mode": "dark",
    "bg": "#1e1e2e",
    "surface": "#2a2a3e",
    "surface2": "#313145",
    "accent": "#7c3aed",
    "accent_hover": "#6d28d9",
    "accent2": "#0ea5e9",
    "text": "#e2e8f0",
    "text_dim": "#94a3b8",
    "success": "#22c55e",
    "warning": "#f59e0b",
    "error": "#ef4444",
    "border": "#3f3f5c",
    "nav_bg": "#25253a",
    "nav_active": "#3b2f78",
    "slider_trough": "#a78bfa",
    "slider_bg": "#3b3b55",
    "highlight": "#4a4a66",
}

THEME_LIGHT = {
    "mode": "light",
    "bg": "#f4f7fb",
    "surface": "#ffffff",
    "surface2": "#eaf1fb",
    "accent": "#2f7cf6",
    "accent_hover": "#1f63d1",
    "accent2": "#0ea5e9",
    "text": "#0f172a",
    "text_dim": "#64748b",
    "success": "#16a34a",
    "warning": "#f59e0b",
    "error": "#ef4444",
    "border": "#d8e2f0",
    "nav_bg": "#f8fbff",
    "nav_active": "#dbeafe",
    "slider_trough": "#93c5fd",
    "slider_bg": "#ffffff",
    "highlight": "#ffffff",
}

def get_theme(mode: str | None) -> dict:
    return dict(THEME_DARK if str(mode).lower() == "dark" else THEME_LIGHT)

# Updated after preferences are loaded.
THEME = get_theme("light")

APP_VERSION = "v1.2026.06.23"
# Auto-detection polling intervals are centralized here so they are easy to tune.
# Source mode can probe more aggressively because it runs a normal Python interpreter.
# Frozen onefile mode probes less often because the bundled runtime is heavier.
SOURCE_DISCONNECTED_PROBE_DELAY_MS = 500
FROZEN_DISCONNECTED_PROBE_DELAY_MS = 1500
CONNECTED_HEALTH_DELAY_MS = 2000
APP_DISPLAY_NAME = "OrchardBridge"
AUTHOR_NAME = "Shih-Han Wang"
AUTHOR_EMAIL = "wangsh@vt.edu"
GITHUB_URL = "https://github.com/dogs1231992/OrchardBridge"
SPONSOR_URL = "https://github.com/sponsors/dogs1231992"
BUYMEACOFFEE_URL = "https://buymeacoffee.com/dogs1231992"
BUG_REPORT_EMAIL = "wangsh@vt.edu"
GITHUB_RELEASES_URL = "https://github.com/dogs1231992/OrchardBridge/releases/latest"
# Version check is enabled, but all network / 404 / repository-not-yet-published
# failures are treated as "already up to date" and are ignored silently by the UI.
ENABLE_UPDATE_CHECK = True
VERSION_CHECK_URLS = [
    "https://raw.githubusercontent.com/dogs1231992/OrchardBridge/main/VERSION.json",
    "https://api.github.com/repos/dogs1231992/OrchardBridge/releases/latest",
]


def resource_path(relative_path: str) -> Path:
    """Return a file path that works both from source and from a PyInstaller bundle."""
    try:
        base = Path(getattr(sys, "_MEIPASS"))
    except Exception:
        base = Path(__file__).resolve().parents[1]
    return base / relative_path



class LinearProgressBar(tk.Canvas):
    """Deterministic dark-theme left-to-right progress bar.

    Used for full-backup progress.  It intentionally has the same visual
    language as the storage usage bar, but represents task progress rather than
    phone storage capacity.
    """

    def __init__(self, parent, theme: dict, height: int = 18):
        super().__init__(parent, height=height, bg=theme["bg"], highlightthickness=0, bd=0)
        self._theme = theme
        self._pct = 0.0
        self._height = height
        self.bind("<Configure>", lambda _e: self._draw())
        self._draw()

    def set_value(self, pct: float):
        try:
            pct = float(pct)
        except Exception:
            pct = 0.0
        self._pct = max(0.0, min(100.0, pct))
        self._draw()

    def _draw(self):
        self.delete("all")
        w = max(1, int(self.winfo_width() or self.cget("width") or 300))
        h = max(12, int(self.winfo_height() or self._height))
        pad = 1
        x0, y0 = pad, pad
        x1, y1 = w - pad, h - pad
        usable = max(1, x1 - x0)
        filled = int(round(usable * self._pct / 100.0))
        self.create_rectangle(x0, y0, x1, y1, fill=self._theme["surface2"], outline=self._theme["border"], width=1)
        if self._pct > 0:
            filled = max(3, filled)
            self.create_rectangle(x0, y0, min(x0 + filled, x1), y1, fill=self._theme["accent"], outline="")
        self.create_line(x0 + 1, y0 + 1, x1 - 1, y0 + 1, fill=self._theme.get("highlight", "#4a4a66"))
        self.create_rectangle(x0, y0, x1, y1, outline=self._theme["border"], width=1)


class BackupApp:
    """主應用程式"""

    def _lang_en(self) -> bool:
        lang = str(getattr(self.preferences, "language", "en-US"))
        return lang == "en-US" or (not lang.startswith("zh") and not getattr(self, "_locale_table", None))

    def _load_locale_table(self) -> dict:
        return load_locale_table(str(getattr(self.preferences, "language", "en-US")))

    def _t(self, zh: str, en: str) -> str:
        lang = str(getattr(self.preferences, "language", "en-US"))
        return translate_text(lang, zh, en, getattr(self, "_locale_table", {}) or None)

    def _fmt_t(self, zh: str, en: str, **kwargs) -> str:
        """Translate a format-string template, then interpolate values."""
        try:
            return self._t(zh, en).format(**kwargs)
        except Exception:
            return en.format(**kwargs)

    def _play_completion_sound(self):
        """Best-effort audible cue after a successful backup."""
        try:
            self.root.bell()
        except Exception:
            pass
        if sys.platform.startswith("win"):
            try:
                import winsound
                winsound.MessageBeep(winsound.MB_ICONASTERISK)
            except Exception:
                pass


    @staticmethod
    def _normalize_version_label(value: str) -> str:
        """Normalize labels enough for update checks without imposing SemVer.

        The public version label is intentionally human-readable, for example
        v1.2026.06.23.  The app only needs to know whether the label published
        on GitHub differs from the bundled label.
        """
        text = str(value or "").strip()
        if text.lower().startswith("version"):
            text = text.split(":", 1)[-1].strip()
        if text.lower().startswith("v"):
            text = text[1:].strip()
        return text

    def _start_version_check(self):
        if getattr(self, "_version_check_running", False):
            return
        self._version_check_running = True
        threading.Thread(target=self._version_check_worker, daemon=True).start()

    def _version_check_worker(self):
        try:
            info = self._fetch_latest_version_info()
            if not info:
                return
            latest = str(info.get("version") or "").strip()
            if not latest:
                return
            current_norm = self._normalize_version_label(APP_VERSION)
            latest_norm = self._normalize_version_label(latest)
            if latest_norm and current_norm and latest_norm != current_norm:
                self.root.after(0, lambda i=info: self._show_update_available(i))
            else:
                print(f"[version] Up to date: current={APP_VERSION}, latest={latest}")
        except Exception as exc:
            # Version checks must never block or break the backup app.
            print(f"[version] check failed: {exc!r}")
        finally:
            self._version_check_running = False

    def _fetch_latest_version_info(self) -> dict | None:
        headers = {
            "User-Agent": f"OrchardBridge/{APP_VERSION}",
            "Accept": "application/json, text/plain, */*",
        }
        last_error = None
        for url in VERSION_CHECK_URLS:
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=5) as resp:
                    raw = resp.read(200000).decode("utf-8", errors="replace").strip()
                if not raw:
                    continue
                try:
                    data = json.loads(raw)
                except Exception:
                    data = {"version": raw.splitlines()[0].strip()}

                if isinstance(data, dict):
                    # Raw VERSION.json format.
                    version = data.get("version") or data.get("latest_version") or data.get("tag_name")
                    release_url = data.get("release_url") or data.get("download_url") or data.get("html_url") or GITHUB_RELEASES_URL
                    notes = data.get("notes") or data.get("message") or ""

                    # GitHub Releases API fallback format.
                    if data.get("tag_name"):
                        version = data.get("tag_name")
                        release_url = data.get("html_url") or GITHUB_RELEASES_URL
                        notes = data.get("body") or ""

                    if version:
                        print(f"[version] latest from {url}: {version}")
                        return {"version": str(version), "release_url": str(release_url), "notes": str(notes)}
            except Exception as exc:
                last_error = exc
                print(f"[version] failed to read {url}: {exc!r}")
        # If the repository, VERSION.json, or Releases page is unavailable, keep the
        # app quiet and behave as if the bundled version is already up to date.
        if last_error:
            print(f"[version] update check unavailable; assuming current version is latest: {last_error!r}")
        return None

    def _show_update_available(self, info: dict):
        latest = str(info.get("version") or "").strip()
        release_url = str(info.get("release_url") or GITHUB_RELEASES_URL)
        notes = str(info.get("notes") or "").strip()
        title = self._t("發現新版本", "Update available")
        message = (
            f"{self._t('目前版本', 'Current version')}：{APP_VERSION}\n"
            f"{self._t('最新版本', 'Latest version')}：{latest}\n\n"
            f"{self._t('是否開啟 GitHub Releases 下載新版？', 'Open GitHub Releases to download the new version?')}"
        )
        if notes:
            short_notes = notes[:500]
            message += "\n\n" + self._t("更新說明", "Release notes") + "：\n" + short_notes
        try:
            if messagebox.askyesno(title, message, parent=self.root):
                webbrowser.open(release_url)
        except Exception as exc:
            print(f"[version] failed to show update dialog: {exc!r}")


    def __init__(self):
        # Load preferences before creating widgets so the selected theme is used
        # from the first frame.
        self.preferences: Preferences = load_preferences()
        self._locale_table = self._load_locale_table()
        try:
            set_ui_font_size(getattr(self.preferences, "ui_font_size", 10))
        except Exception:
            pass
        global THEME
        THEME = get_theme(getattr(self.preferences, "theme_mode", "light"))

        try:
            from tkinterdnd2 import TkinterDnD  # optional; enables drag-and-drop in tools
            self.root = TkinterDnD.Tk()
        except Exception:
            self.root = tk.Tk()
        self.root.title(f"{APP_DISPLAY_NAME} {APP_VERSION}")
        self.root.geometry("1280x800")
        self.root.minsize(1180, 720)
        self.root.configure(bg=THEME["bg"])
        self._apply_window_icon()

        # 設置 DPI 感知（Windows）
        try:
            from ctypes import windll
            windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

        # 核心元件
        self._saved_preferences_dict = prefs_to_dict(self.preferences)
        self.device_manager = DeviceManager()
        try:
            self.device_manager.apply_preferences(self.preferences)
        except Exception:
            pass
        self.backup_manager = BackupManager(self.device_manager, self.preferences)
        self.photos: list[PhotoItem] = []

        # Auto-connect state
        # Source mode: disconnected probes are fast.
        # Frozen onefile mode: avoid overly aggressive probing because device
        # detection runs in-process and the bundled runtime is heavier.
        # Connected: run a lightweight health check every 2 seconds.
        self._app_closing = False
        self._auto_probe_running = False
        self._auto_connect_started = False
        self._health_fail_count = 0
        self._scan_in_progress = False
        self._photo_backup_in_progress = False
        self._full_backup_in_progress = False
        self._thumbnail_load_in_progress = False
        self._last_waiting_status = ""
        self._disconnected_probe_delay_ms = (
            FROZEN_DISCONNECTED_PROBE_DELAY_MS if getattr(sys, "frozen", False) else SOURCE_DISCONNECTED_PROBE_DELAY_MS
        )

        self._setup_styles()
        self._build_ui()
        self._setup_close_handler()
        self._setup_screenshot_hotkeys()
        self._start_auto_connect_monitor()
        if ENABLE_UPDATE_CHECK:
            self.root.after(2500, self._start_version_check)

    # ─────────────────────────────────────────
    # UI 建構
    # ─────────────────────────────────────────

    def _apply_window_icon(self):
        """Apply the OrchardBridge icon to the Tk window when icon assets exist."""
        try:
            ico_path = resource_path("assets/orchardbridge_icon.ico")
            if os.name == "nt" and ico_path.exists():
                self.root.iconbitmap(default=str(ico_path))
        except Exception as exc:
            try:
                print(f"[ui] failed to apply .ico window icon: {exc!r}")
            except Exception:
                pass
        try:
            png_path = resource_path("assets/orchardbridge_icon.png")
            if png_path.exists():
                self._window_icon_image = tk.PhotoImage(file=str(png_path))
                self.root.iconphoto(True, self._window_icon_image)
        except Exception as exc:
            try:
                print(f"[ui] failed to apply .png window icon: {exc!r}")
            except Exception:
                pass

    def _setup_styles(self):
        """設定 ttk 樣式"""
        style = ttk.Style()
        style.theme_use("clam")

        style.configure("Dark.TFrame", background=THEME["bg"])
        style.configure("Surface.TFrame", background=THEME["surface"])
        style.configure("Surface2.TFrame", background=THEME["surface2"])

        style.configure(
            "Dark.TLabel",
            background=THEME["bg"],
            foreground=THEME["text"],
            font=ui_font(10),
        )
        style.configure(
            "Dim.TLabel",
            background=THEME["bg"],
            foreground=THEME["text_dim"],
            font=ui_font(9),
        )
        style.configure(
            "Title.TLabel",
            background=THEME["bg"],
            foreground=THEME["text"],
            font=ui_font(14, "bold"),
        )
        style.configure(
            "Surface.TLabel",
            background=THEME["surface"],
            foreground=THEME["text"],
            font=ui_font(10),
        )

        # 進度條
        style.configure(
            "Accent.Horizontal.TProgressbar",
            background=THEME["accent"],
            troughcolor=THEME["surface2"],
            bordercolor=THEME["border"],
            lightcolor=THEME["accent"],
            darkcolor=THEME["accent"],
        )

        # Notebook（分頁標籤）
        style.configure(
            "TNotebook",
            background=THEME["bg"],
            borderwidth=0,
        )
        style.configure(
            "TNotebook.Tab",
            background=THEME["surface"],
            foreground=THEME["text_dim"],
            padding=[16, 8],
            font=ui_font(10),
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", THEME["accent"])],
            foreground=[("selected", "#ffffff")],
        )
        # FoneTool-like navigation uses a left menu; the notebook tabs are hidden.
        style.layout("Hidden.TNotebook.Tab", [])
        style.configure("Hidden.TNotebook", background=THEME["bg"], borderwidth=0, tabmargins=0)

        style.configure(
            "TCombobox",
            fieldbackground=THEME["surface2"],
            background=THEME["surface2"],
            foreground=THEME["text"],
            bordercolor=THEME["border"],
            arrowcolor=THEME["text_dim"],
        )

        # 捲軸
        style.configure(
            "Dark.Vertical.TScrollbar",
            background=THEME["surface2"],
            troughcolor=THEME["bg"],
            bordercolor=THEME["border"],
            arrowcolor=THEME["text_dim"],
        )


        style.configure(
            "App.TCombobox",
            fieldbackground=THEME["surface2"],
            background=THEME["surface2"],
            foreground=THEME["text"],
            arrowcolor=THEME["text"],
            bordercolor=THEME["border"],
            lightcolor=THEME["surface2"],
            darkcolor=THEME["surface2"],
            font=ui_font(12),
            padding=6,
        )
        style.map(
            "App.TCombobox",
            fieldbackground=[("readonly", THEME["surface2"])],
            foreground=[("readonly", THEME["text"])],
            background=[("readonly", THEME["surface2"])],
        )
        try:
            self.root.option_add("*TCombobox*Listbox*Background", THEME["surface2"])
            self.root.option_add("*TCombobox*Listbox*Foreground", THEME["text"])
            self.root.option_add("*TCombobox*Listbox*selectBackground", THEME["accent"])
            self.root.option_add("*TCombobox*Listbox*selectForeground", "#ffffff")
            self.root.option_add("*TCombobox*Listbox.font", ui_font(12))
            self.root.option_add("*Listbox.background", THEME["surface2"])
            self.root.option_add("*Listbox.foreground", THEME["text"])
            self.root.option_add("*Listbox.selectBackground", THEME["accent"])
            self.root.option_add("*Listbox.selectForeground", "#ffffff")
        except Exception:
            pass

        # Checkbutton
        style.configure(
            "Dark.TCheckbutton",
            background=THEME["surface"],
            foreground=THEME["text"],
            font=ui_font(9),
        )

    def _build_ui(self):
        """建立主界面佈局"""
        # ── 頂部標題列 ──
        header = tk.Frame(self.root, bg=THEME["surface"])
        header.pack(fill=tk.X, side=tk.TOP)
        # No fixed header height: translated titles and larger UI fonts must
        # be allowed to request the height they need.

        # Logo + 標題
        title_frame = tk.Frame(header, bg=THEME["surface"])
        title_frame.pack(side=tk.LEFT, padx=20, pady=10)

        logo_label = tk.Label(
            title_frame,
            text="📱",
            bg=THEME["surface"],
            fg=THEME["text"],
            font=ui_font(20),
        )
        logo_label.pack(side=tk.LEFT)

        tk.Label(
            title_frame,
            text=f"  {APP_DISPLAY_NAME}",
            bg=THEME["surface"],
            fg=THEME["text"],
            font=ui_font(14, "bold"),
        ).pack(side=tk.LEFT)

        tk.Label(
            title_frame,
            text=self._t("  ●  支援照片備份 / 整機備份", "  ●  Photo backup / Full device backup"),
            bg=THEME["surface"],
            fg=THEME["text_dim"],
            font=ui_font(9),
        ).pack(side=tk.LEFT, padx=8)

        # ── 主內容區：左側導覽 + 右側內容 ──
        main_frame = tk.Frame(self.root, bg=THEME["bg"])
        main_frame.pack(fill=tk.BOTH, expand=True)

        # 左側導覽：用功能框取代舊版設備資訊欄，較接近 FoneTool 的設計。
        self.device_panel = SideNavigationPanel(
            main_frame,
            app=self,
            theme=THEME,
            language=self.preferences.language,
        )
        self.device_panel.frame.pack(side=tk.LEFT, fill=tk.Y, padx=0, pady=0)

        # 右側：分頁內容；實際切換由左側導覽控制，頂部分頁標籤隱藏。
        right_frame = tk.Frame(main_frame, bg=THEME["bg"])
        right_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.notebook = ttk.Notebook(right_frame, style="Hidden.TNotebook")
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=0, pady=0)
        self.notebook.bind("<<NotebookTabChanged>>", lambda _e: getattr(self.device_panel, "refresh_buttons", lambda: None)())

        # ── 分頁 1：我的裝置 / 管理裝置 ──
        self.device_tab = tk.Frame(self.notebook, bg=THEME["bg"])
        self.notebook.add(self.device_tab, text=self._t("  📱  我的裝置  ", "  📱  My device  "))
        self._build_device_dashboard_tab()

        # ── 分頁 2：照片備份 ──
        self.photo_tab = tk.Frame(self.notebook, bg=THEME["bg"])
        self.notebook.add(self.photo_tab, text=self._t("  📷  照片備份  ", "  📷  Photos  "))

        self.photo_grid = PhotoGridFrame(
            self.photo_tab,
            theme=THEME,
            on_selection_change=self._on_selection_change,
            on_scan_photos=self._on_scan_photos,
            on_open_media=self._open_media_item,
            language=self.preferences.language,
        )
        self.photo_grid.frame.pack(fill=tk.BOTH, expand=True)

        # 照片備份底部操作列
        self.backup_panel = BackupPanelFrame(
            self.photo_tab,
            theme=THEME,
            on_backup=self._on_backup_photos,
            on_select_all=self._on_select_all,
            on_deselect_all=self._on_deselect_all,
            default_dest=self.preferences.default_photo_backup_folder,
            default_convert_heic=self.preferences.convert_after_backup,
            default_output_format=self.preferences.image_output_format,
            language=self.preferences.language,
        )
        self.backup_panel.frame.pack(fill=tk.X, side=tk.BOTTOM)

        # ── 分頁 2：整機備份 ──
        self.full_tab = tk.Frame(self.notebook, bg=THEME["bg"])
        self.notebook.add(self.full_tab, text=self._t("  💾  整機備份  ", "  💾  Full backup  "))
        self._build_full_backup_tab()

        # ── 分頁 3：小工具 ──
        self.toolbox_tab = tk.Frame(self.notebook, bg=THEME["bg"])
        self.notebook.add(self.toolbox_tab, text=self._t("  🧰  小工具  ", "  🧰  Toolbox  "))
        self._build_toolbox_tab()

        # ── 分頁 4：設定 ──
        self.settings_tab = tk.Frame(self.notebook, bg=THEME["bg"])
        self.notebook.add(self.settings_tab, text=self._t("  ⚙  設定  ", "  ⚙  Settings  "))
        self._build_settings_tab()

        # ── 分頁 5：關於 ──
        self.about_tab = tk.Frame(self.notebook, bg=THEME["bg"])
        self.notebook.add(self.about_tab, text=self._t("  ℹ  關於  ", "  ℹ  About  "))
        self._build_about_tab()

        try:
            self.device_panel.refresh_buttons()
        except Exception:
            pass

        # ── 底部狀態列 ──
        self.status_bar = StatusBar(self.root, theme=THEME)
        self.status_bar.frame.pack(fill=tk.X, side=tk.BOTTOM)
        self.status_bar.set(self._t("就緒", "Ready"), "info")

    def _fmt_bytes(self, value: int) -> str:
        try:
            value = int(value or 0)
        except Exception:
            value = 0
        if value >= 1024 ** 3:
            return f"{value / 1024 ** 3:.2f} GB"
        if value >= 1024 ** 2:
            return f"{value / 1024 ** 2:.1f} MB"
        if value >= 1024:
            return f"{value / 1024:.0f} KB"
        return f"{value} B"

    def _build_device_dashboard_tab(self):
        """FoneTool-inspired device management dashboard.

        This is the first step toward a broader FoneTool-like home page: a
        large device summary card, storage details, and quick action cards.
        """
        outer = self._make_scrollable_tab(self.device_tab, padx=34, pady=30)

        title = tk.Label(
            outer,
            text=self._t("管理裝置", "Manage device"),
            bg=THEME["bg"],
            fg=THEME["text"],
            font=ui_font(18, "bold"),
            anchor="w",
        )
        title.pack(fill=tk.X, pady=(0, 18))

        hero = tk.Frame(outer, bg=THEME["surface"], bd=0)
        hero.pack(fill=tk.X, pady=(0, 24))

        hero_inner = tk.Frame(hero, bg=THEME["surface"])
        hero_inner.pack(fill=tk.X, padx=28, pady=24)

        phone_box = tk.Frame(hero_inner, bg=THEME["surface2"], width=220, height=270)
        phone_box.pack(side=tk.LEFT, padx=(0, 42), pady=4)
        phone_box.pack_propagate(False)

        self.dashboard_phone_icon = tk.Label(
            phone_box,
            text="▦",
            bg=THEME["surface2"],
            fg=THEME["accent"],
            font=ui_font(72, "bold"),
        )
        self.dashboard_phone_icon.pack(expand=True)
        tk.Label(
            phone_box,
            text=self._t("自動偵測中", "Auto detecting"),
            bg=THEME["surface2"],
            fg=THEME["text_dim"],
            font=ui_font(10),
        ).pack(pady=(0, 18))

        info = tk.Frame(hero_inner, bg=THEME["surface"])
        info.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.dashboard_name = tk.Label(
            info,
            text=self._t("等待裝置連線", "Waiting for device"),
            bg=THEME["surface"],
            fg=THEME["text"],
            font=ui_font(18, "bold"),
            anchor="w",
        )
        self.dashboard_name.pack(fill=tk.X, pady=(8, 22))

        self.dashboard_rows = {}
        for key, zh, en in [
            ("model", "裝置型號", "Model"),
            ("ios", "系統版本", "iOS version"),
            ("total", "總空間", "Total storage"),
            ("used", "已使用空間", "Used"),
            ("free", "可用空間", "Free"),
            ("battery", "電量", "Battery"),
        ]:
            row = tk.Frame(info, bg=THEME["surface"])
            row.pack(fill=tk.X, pady=5)
            tk.Label(
                row,
                text=f"{self._t(zh, en)}:",
                bg=THEME["surface"],
                fg=THEME["text_dim"],
                font=ui_font(10),
                anchor="w",
            ).pack(side=tk.LEFT, padx=(0, scaled_px(14)))
            value = tk.Label(
                row,
                text="—",
                bg=THEME["surface"],
                fg=THEME["text"],
                font=ui_font(10),
                anchor="w",
            )
            value.pack(side=tk.LEFT, fill=tk.X, expand=True)
            self.dashboard_rows[key] = value

        storage_label_row = tk.Frame(info, bg=THEME["surface"])
        storage_label_row.pack(fill=tk.X, pady=(18, 4))
        self.dashboard_storage_text = tk.Label(
            storage_label_row,
            text=self._t("儲存空間使用狀態", "Storage usage"),
            bg=THEME["surface"],
            fg=THEME["text_dim"],
            font=ui_font(9),
            anchor="w",
        )
        self.dashboard_storage_text.pack(side=tk.LEFT)
        self.dashboard_storage_pct = tk.Label(
            storage_label_row,
            text="0%",
            bg=THEME["surface"],
            fg=THEME["text_dim"],
            font=ui_font(9),
            anchor="e",
        )
        self.dashboard_storage_pct.pack(side=tk.RIGHT)
        self.dashboard_storage_bar = StorageUsageBar(info, THEME, width=520, height=18)
        self.dashboard_storage_bar.pack(fill=tk.X, pady=(0, 18))

        action_row = tk.Frame(info, bg=THEME["surface"])
        action_row.pack(fill=tk.X, pady=(4, 0))
        ModernButton(
            action_row,
            text=self._t("管理照片", "Manage photos"),
            theme=THEME,
            style="primary",
            command=lambda: self.notebook.select(self.photo_tab),
            width=16,
        ).pack(side=tk.LEFT, padx=(0, 10))
        ModernButton(
            action_row,
            text=self._t("整機備份", "Full backup"),
            theme=THEME,
            style="secondary",
            command=lambda: self.notebook.select(self.full_tab),
            width=16,
        ).pack(side=tk.LEFT, padx=(0, 10))
        ModernButton(
            action_row,
            text=self._t("設定", "Settings"),
            theme=THEME,
            style="secondary",
            command=lambda: self.notebook.select(self.settings_tab),
            width=18,
        ).pack(side=tk.LEFT)

        tk.Label(
            outer,
            text=self._t("熱門功能", "Quick actions"),
            bg=THEME["bg"],
            fg=THEME["text"],
            font=ui_font(14, "bold"),
            anchor="w",
        ).pack(fill=tk.X, pady=(4, 12))

        quick = tk.Frame(outer, bg=THEME["bg"])
        quick.pack(fill=tk.X)
        self._build_quick_action_card(quick, "📷", self._t("裝置到電腦", "Device to PC"), self._t("選擇照片與影片備份", "Select photos and videos"), lambda: self.notebook.select(self.photo_tab)).pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 14))
        self._build_quick_action_card(quick, "💾", self._t("自定義備份", "Custom backup"), self._t("完整手機備份", "Full device backup"), lambda: self.notebook.select(self.full_tab)).pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 14))
        self._build_quick_action_card(quick, "⚙", self._t("偏好設定", "Preferences"), self._t("調整轉檔與快取", "Conversion and cache"), lambda: self.notebook.select(self.settings_tab)).pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 14))
        self._build_quick_action_card(quick, "ℹ", self._t("關於", "About"), self._t("版本與作者資訊", "Version and author"), lambda: self.notebook.select(self.about_tab)).pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._update_device_dashboard(None)

    def _build_quick_action_card(self, parent, icon: str, title: str, subtitle: str, command):
        """Action card with font-size-aware height.

        Tk labels do not automatically enlarge parent rows in all pack/grid
        combinations when the user changes the global font size.  Use a
        generous scaled minimum height and wraplength updates so titles and
        subtitles are never clipped at large font sizes.
        """
        card = tk.Frame(parent, bg=THEME["surface"], bd=0, cursor="hand2")
        # Let action cards grow with translated text and larger fonts.

        icon_label = tk.Label(
            card, text=icon, bg=THEME["surface"], fg=THEME["accent2"],
            font=ui_font(22), anchor="w"
        )
        icon_label.pack(fill=tk.X, padx=scaled_px(18), pady=(scaled_px(16), scaled_px(4)))

        title_label = tk.Label(
            card, text=title, bg=THEME["surface"], fg=THEME["text"],
            font=ui_font(11, "bold"), anchor="w", justify="left"
        )
        title_label.pack(fill=tk.X, padx=scaled_px(18))

        sub_label = tk.Label(
            card, text=subtitle, bg=THEME["surface"], fg=THEME["text_dim"],
            font=ui_font(9), anchor="w", justify="left"
        )
        sub_label.pack(fill=tk.X, padx=scaled_px(18), pady=(scaled_px(6), scaled_px(16)))

        def _fit(event=None):
            w = max(120, (event.width if event else card.winfo_width()) - scaled_px(36))
            try:
                title_label.configure(wraplength=w)
                sub_label.configure(wraplength=w)
            except Exception:
                pass
        card.bind("<Configure>", _fit)

        for widget in (card, icon_label, title_label, sub_label):
            widget.bind("<Button-1>", lambda _e: command())
        return card

    def _update_device_dashboard(self, info=None):
        if not hasattr(self, "dashboard_name"):
            return
        if info is None:
            self.dashboard_name.configure(text=self._t("等待裝置連線", "Waiting for device"))
            for v in self.dashboard_rows.values():
                v.configure(text="—")
            self.dashboard_storage_pct.configure(text="0%")
            self.dashboard_storage_bar.set_value(0, 0)
            return
        self.dashboard_name.configure(text=getattr(info, "name", "Device") or "Device")
        self.dashboard_rows["model"].configure(text=getattr(info, "model", "") or "Device")
        self.dashboard_rows["ios"].configure(text=(f"iOS {info.ios_version}" if getattr(info, "ios_version", "") else "—"))
        total = int(getattr(info, "storage_total", 0) or 0)
        used = int(getattr(info, "storage_used", 0) or 0)
        free = max(0, total - used) if total else 0
        self.dashboard_rows["total"].configure(text=self._fmt_bytes(total) if total else "—")
        self.dashboard_rows["used"].configure(text=self._fmt_bytes(used) if used else "—")
        self.dashboard_rows["free"].configure(text=self._fmt_bytes(free) if total else "—")
        battery = int(getattr(info, "battery_level", 0) or 0)
        self.dashboard_rows["battery"].configure(text=(f"{battery}%" if battery else "—"))
        if total:
            pct = max(0.0, min(100.0, used / total * 100.0))
            self.dashboard_storage_pct.configure(text=f"{pct:.1f}%")
            self.dashboard_storage_bar.set_value(pct)
        else:
            self.dashboard_storage_pct.configure(text="0%")
            self.dashboard_storage_bar.set_value(0, 0)

    def _build_toolbox_tab(self):
        """Placeholder toolbox page for future utilities."""
        outer = self._make_scrollable_tab(self.toolbox_tab, padx=40, pady=32)
        title = tk.Label(
            outer,
            text=self._t("小工具", "Toolbox"),
            bg=THEME["bg"],
            fg=THEME["text"],
            font=ui_font(18, "bold"),
            anchor="w",
        )
        title.pack(fill=tk.X, pady=(0, 18))
        desc = tk.Label(
            outer,
            text=self._t(
                "這裡會放與備份、轉檔、檔案整理相關的輔助工具。",
                "Companion utilities for conversion, backup organization, and file cleanup.",
            ),
            bg=THEME["bg"],
            fg=THEME["text_dim"],
            font=ui_font(11),
            anchor="w",
            justify="left",
        )
        desc.pack(fill=tk.X, pady=(0, 20))

        grid = tk.Frame(outer, bg=THEME["bg"])
        grid.pack(fill=tk.X)
        self._build_quick_action_card(
            grid,
            "🖼",
            self._t("HEIC 轉換器", "HEIC converter"),
            self._t("將本機 HEIC/HEIF 批次轉為 JPEG 或 PNG。", "Batch convert local HEIC/HEIF files to JPEG or PNG."),
            self._open_heic_converter,
        ).pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 14))
        self._build_quick_action_card(
            grid,
            "🧹",
            self._t("刪除重複檔案", "Remove duplicate files"),
            self._t("掃描資料夾並依 SHA-256 找出重複檔案。", "Scan folders and detect duplicate files by SHA-256."),
            self._open_duplicate_cleaner,
        ).pack(side=tk.LEFT, fill=tk.BOTH, expand=True)


    def _open_heic_converter(self):
        try:
            HEICConverterWindow(self.root, THEME, self.preferences, self._t)
        except Exception as exc:
            print(f"[toolbox] Failed to open HEIC converter: {exc!r}")
            messagebox.showerror(self._t("開啟失敗", "Open failed"), str(exc), parent=self.root)

    def _open_duplicate_cleaner(self):
        try:
            DuplicateCleanerWindow(self.root, THEME, self._t)
        except Exception as exc:
            print(f"[toolbox] Failed to open duplicate cleaner: {exc!r}")
            messagebox.showerror(self._t("開啟失敗", "Open failed"), str(exc), parent=self.root)

    def _build_full_backup_tab(self):
        """建立整機備份分頁"""
        pad_frame = tk.Frame(self.full_tab, bg=THEME["bg"])
        pad_frame.pack(fill=tk.BOTH, expand=True, padx=40, pady=40)

        # 說明卡片
        card = tk.Frame(pad_frame, bg=THEME["surface"], bd=0)
        card.pack(fill=tk.X, pady=(0, 20))

        tk.Label(
            card,
            text=self._t("  💾  整機備份（iTunes 格式）", "  💾  Full backup (iTunes format)"),
            bg=THEME["surface"],
            fg=THEME["text"],
            font=ui_font(13, "bold"),
            anchor="w",
            pady=12,
            padx=16,
        ).pack(fill=tk.X)

        desc = self._t(
            "整機備份會將手機所有資料（通訊錄、簡訊、應用程式資料、設定等）\n"
            "完整備份到電腦，格式與 iTunes/Finder 備份相容。\n\n"
            "• 備份完成後可用 iTunes、Finder 或 iMazing 等軟體還原\n"
            "• 備份資料夾會以時間戳命名，避免覆蓋舊備份\n"
            "• 建議在穩定的 USB 連線下進行，時間約 10 分鐘至數小時不等",
            "Full backup stores device data such as contacts, messages, app data, and settings on this computer.\n"
            "The output is compatible with iTunes/Finder-style backups.\n\n"
            "• The backup folder is timestamped so old backups are not overwritten.\n"
            "• Keep the device unlocked and connected during backup.\n"
            "• Depending on phone size and USB speed, this may take from minutes to hours."
        )
        tk.Label(
            card,
            text=desc,
            bg=THEME["surface"],
            fg=THEME["text_dim"],
            font=ui_font(11),
            anchor="w",
            justify="left",
            padx=16,
            pady=0,
        ).pack(fill=tk.X, pady=(0, 16))

        # 輸出資料夾選擇
        folder_frame = tk.Frame(pad_frame, bg=THEME["bg"])
        folder_frame.pack(fill=tk.X, pady=(0, 20))

        tk.Label(
            folder_frame,
            text=self._t("備份位置：", "Backup folder:"),
            bg=THEME["bg"],
            fg=THEME["text"],
            font=ui_font(10),
        ).pack(side=tk.LEFT)

        self.full_backup_folder_var = tk.StringVar(value=str(self.preferences.default_full_backup_folder))
        folder_entry = tk.Entry(
            folder_frame,
            textvariable=self.full_backup_folder_var,
            bg=THEME["surface2"],
            fg=THEME["text"],
            insertbackground=THEME["text"],
            relief="flat",
            font=ui_font(10),
            width=50,
        )
        folder_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(scaled_px(8), scaled_px(8)), ipady=scaled_px(4))

        ModernButton(
            folder_frame,
            text=self._t("瀏覽…", "Browse…"),
            theme=THEME,
            style="secondary",
            command=lambda: self._browse_folder(self.full_backup_folder_var),
        ).pack(side=tk.LEFT)

        # 進度顯示：這條橫條代表「整機備份任務進度」，不是手機容量。
        self.full_progress_title = tk.Label(
            pad_frame,
            text=self._t("整機備份進度：尚未開始", "Full backup progress: not started"),
            bg=THEME["bg"],
            fg=THEME["text"],
            font=ui_font(10, "bold"),
            anchor="w",
        )
        self.full_progress_title.pack(fill=tk.X, pady=(0, 6))

        self.full_progress_label = tk.Label(
            pad_frame,
            text=self._t("等待使用者開始備份", "Waiting to start backup"),
            bg=THEME["bg"],
            fg=THEME["text_dim"],
            font=ui_font(11),
            anchor="w",
        )
        self.full_progress_label.pack(fill=tk.X, pady=(0, 8))

        self.full_progress_bar = LinearProgressBar(pad_frame, THEME, height=18)
        self.full_progress_bar.pack(fill=tk.X, pady=(0, 20))

        # 開始備份按鈕
        self.full_backup_btn = ModernButton(
            pad_frame,
            text=self._t("  🚀  開始整機備份", "  🚀  Start full backup"),
            theme=THEME,
            style="primary",
            command=self._on_full_backup,
            width=24,
        )
        self.full_backup_btn.pack(anchor="w")

    def _make_scrollable_tab(self, parent, padx: int = 40, pady: int = 32):
        """Return a padded frame inside a vertically scrollable tab.

        The container only scrolls when the tab content is actually taller than
        the visible viewport. This prevents short pages such as the device
        dashboard from being dragged downward and leaving a large blank area above
        the real content.
        """
        outer = tk.Frame(parent, bg=THEME["bg"])
        outer.pack(fill=tk.BOTH, expand=True)

        canvas = tk.Canvas(
            outer,
            bg=THEME["bg"],
            highlightthickness=0,
            bd=0,
        )
        scrollbar = tk.Scrollbar(
            outer,
            orient=tk.VERTICAL,
            command=canvas.yview,
            bg=THEME["surface2"],
            troughcolor=THEME["bg"],
            relief="flat",
            bd=0,
            width=28,
        )
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        content = tk.Frame(canvas, bg=THEME["bg"])
        padded = tk.Frame(content, bg=THEME["bg"])
        padded.pack(fill=tk.BOTH, expand=True, padx=padx, pady=pady)
        window_id = canvas.create_window((0, 0), window=content, anchor="nw")
        scroll_state = {"enabled": True}

        def _update_scrollregion(_event=None):
            bbox = canvas.bbox("all")
            if not bbox:
                canvas.configure(scrollregion=(0, 0, max(1, canvas.winfo_width()), max(1, canvas.winfo_height())))
                scroll_state["enabled"] = False
                return

            viewport_h = max(1, canvas.winfo_height())
            viewport_w = max(1, canvas.winfo_width())
            content_h = max(1, bbox[3] - bbox[1])
            content_w = max(1, bbox[2] - bbox[0])

            if content_h <= viewport_h:
                # When content fits, clamp the scroll region to the viewport and
                # force the view back to the top. Without this, Tk can keep a
                # stale y-offset and the tab appears to scroll into empty space.
                scroll_state["enabled"] = False
                canvas.configure(scrollregion=(0, 0, max(content_w, viewport_w), viewport_h))
                canvas.yview_moveto(0)
            else:
                scroll_state["enabled"] = True
                canvas.configure(scrollregion=(0, 0, max(content_w, viewport_w), content_h))

        def _fit_width(event):
            # Keep cards using the full available width, minus a tiny guard.
            canvas.itemconfigure(window_id, width=max(1, event.width - 2))
            canvas.after_idle(_update_scrollregion)

        def _on_mousewheel(event):
            _update_scrollregion()
            if not scroll_state.get("enabled", True):
                canvas.yview_moveto(0)
                return "break"
            if event.num == 4:
                canvas.yview_scroll(-1, "units")
            elif event.num == 5:
                canvas.yview_scroll(1, "units")
            else:
                canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            return "break"

        content.bind("<Configure>", _update_scrollregion)
        canvas.bind("<Configure>", _fit_width)
        canvas.bind("<MouseWheel>", _on_mousewheel)
        canvas.bind("<Button-4>", _on_mousewheel)
        canvas.bind("<Button-5>", _on_mousewheel)
        content.bind("<MouseWheel>", _on_mousewheel)
        padded.bind("<MouseWheel>", _on_mousewheel)
        return padded

    def _build_settings_tab(self):
        """Build the preferences/settings tab."""
        pad_frame = self._make_scrollable_tab(self.settings_tab, padx=40, pady=32)

        card = tk.Frame(pad_frame, bg=THEME["surface"], bd=0)
        card.pack(fill=tk.X, pady=(0, 20))

        tk.Label(
            card,
            text=self._t("  ⚙  偏好設定", "  ⚙  Preferences"),
            bg=THEME["surface"],
            fg=THEME["text"],
            font=ui_font(13, "bold"),
            anchor="w",
            pady=12,
            padx=16,
        ).pack(fill=tk.X)

        body = tk.Frame(card, bg=THEME["surface"])
        body.pack(fill=tk.X, padx=16, pady=(4, 16))

        # Language
        self.language_var = tk.StringVar(value=self._language_display(self.preferences.language))
        self._settings_row_combo(
            body,
            self._t("語言", "Language"),
            self.language_var,
            list(get_supported_languages().values()),
            self._t(
                "切換語言後會自動儲存並重新啟動軟體。語言檔位於 locales 資料夾；若某些字串尚未收錄，會自動使用英文作為備援。",
                "Changing language will save settings and restart the app. Traditional Chinese and English are fully supported now; other languages currently use the English UI as a base.",
            ),
        )

        # Theme / appearance
        self.theme_mode_var = tk.StringVar(value=self._theme_display(getattr(self.preferences, "theme_mode", "light")))
        self._settings_row_combo(
            body,
            self._t("外觀", "Appearance"),
            self.theme_mode_var,
            [self._theme_display("light"), self._theme_display("dark")],
            self._t("切換外觀後會自動儲存並重新啟動軟體。", "Changing appearance will save settings and restart the app."),
        )

        self.ui_font_size_var = tk.IntVar(value=int(getattr(self.preferences, "ui_font_size", 10)))
        self._settings_row_slider_entry(
            body,
            self._t("介面字體大小", "UI font size"),
            self.ui_font_size_var,
            8,
            16,
            self._t("調整後需儲存並重新啟動，避免版面被舊元件尺寸影響。", "Save and restart after changing this so layouts can recalculate."),
        )

        # Photo backup folder
        self.photo_backup_folder_var = tk.StringVar(value=str(self.preferences.default_photo_backup_folder))
        self._settings_row_folder(
            body,
            self._t("預設照片備份位置", "Default photo backup folder"),
            self.photo_backup_folder_var,
        )

        # Full backup folder
        self.full_backup_folder_pref_var = tk.StringVar(value=str(self.preferences.default_full_backup_folder))
        self._settings_row_folder(
            body,
            self._t("預設整機備份位置", "Default full backup folder"),
            self.full_backup_folder_pref_var,
        )

        self.existing_file_action_var = tk.StringVar(value=self._existing_action_display(getattr(self.preferences, "existing_file_action", "rename")))
        self._settings_row_combo(
            body,
            self._t("同名檔案處理方式", "If a file already exists"),
            self.existing_file_action_var,
            [self._existing_action_display("rename"), self._existing_action_display("overwrite"), self._existing_action_display("skip")],
            self._t("預設會自動重新命名，例如 IMG_0001 (1).HEIC。", "Default is to rename, for example IMG_0001 (1).HEIC."),
        )

        # Conversion card
        convert_card = tk.Frame(pad_frame, bg=THEME["surface"], bd=0)
        convert_card.pack(fill=tk.X, pady=(0, 20))
        tk.Label(
            convert_card,
            text=self._t("  🖼  HEIC/HEIF 轉圖檔", "  🖼  HEIC/HEIF to image file"),
            bg=THEME["surface"],
            fg=THEME["text"],
            font=ui_font(13, "bold"),
            anchor="w",
            pady=12,
            padx=16,
        ).pack(fill=tk.X)
        cbody = tk.Frame(convert_card, bg=THEME["surface"])
        cbody.pack(fill=tk.X, padx=16, pady=(4, 16))

        self.convert_heic_var = tk.BooleanVar(value=bool(self.preferences.convert_after_backup))
        self._settings_row_check(
            cbody,
            self._t("備份後轉圖檔", "Convert image after backup"),
            self.convert_heic_var,
        )

        self.image_output_format_var = tk.StringVar(value="PNG" if str(self.preferences.image_output_format).upper() == "PNG" else "JPEG")
        self._settings_row_combo(
            cbody,
            self._t("輸出圖檔格式", "Output image format"),
            self.image_output_format_var,
            ["JPEG", "PNG"],
            self._t("目前只會轉換 HEIC/HEIF，原始檔仍會保留。", "Only HEIC/HEIF files are converted; originals are kept."),
        )

        tk.Label(
            cbody,
            text=self._t("JPEG 設定", "JPEG settings"),
            bg=THEME["surface"],
            fg=THEME["accent"],
            font=ui_font(10, "bold"),
            anchor="w",
        ).pack(fill=tk.X, pady=(8, 6))

        self.jpeg_quality_var = tk.IntVar(value=int(self.preferences.jpeg_quality))
        self._settings_row_slider_entry(
            cbody,
            self._t("圖片品質", "Image quality"),
            self.jpeg_quality_var,
            0,
            100,
            self._t("JPEG 專用；右側數值會與拉桿連動。", "JPEG only; the numeric field is linked to the slider."),
        )

        self.jpeg_subsampling_var = tk.StringVar(value=self._subsampling_display(self.preferences.jpeg_subsampling))
        self._settings_row_combo(
            cbody,
            self._t("JPEG 色彩取樣", "JPEG color sampling"),
            self.jpeg_subsampling_var,
            [
                self._subsampling_display(0),
                self._subsampling_display(1),
                self._subsampling_display(2),
            ],
            self._t("JPEG 專用；建議使用最佳品質。", "JPEG only; Best quality is recommended."),
        )

        self.jpeg_optimize_var = tk.BooleanVar(value=bool(self.preferences.jpeg_optimize))
        self._settings_row_check(
            cbody,
            self._t("啟用 JPEG 檔案最佳化", "Enable JPEG optimize"),
            self.jpeg_optimize_var,
        )

        tk.Label(
            cbody,
            text=self._t("PNG 設定", "PNG settings"),
            bg=THEME["surface"],
            fg=THEME["accent"],
            font=ui_font(10, "bold"),
            anchor="w",
        ).pack(fill=tk.X, pady=(8, 6))

        self.png_compress_level_var = tk.IntVar(value=int(getattr(self.preferences, "png_compress_level", 0)))
        self._settings_row_slider_entry(
            cbody,
            self._t("PNG 壓縮等級", "PNG compression level"),
            self.png_compress_level_var,
            0,
            9,
            self._t("PNG 是無損格式：0 最快但檔案大，9 檔案較小但較慢。", "PNG is lossless: 0 is fastest/largest, 9 is smaller/slower."),
        )

        import os as _os
        cpu_total = _os.cpu_count() or 1
        self.conversion_workers_var = tk.IntVar(value=max(1, min(cpu_total, int(getattr(self.preferences, "conversion_workers", max(1, cpu_total - 2))))))
        self._settings_row_slider_entry(
            cbody,
            self._t("轉檔使用核心數", "Conversion CPU cores"),
            self.conversion_workers_var,
            1,
            cpu_total,
            self._t("預設保留約 2 核給系統與介面使用。", "Default leaves about 2 cores for the system and UI."),
        )

        # Cache card
        cache_card = tk.Frame(pad_frame, bg=THEME["surface"], bd=0)
        cache_card.pack(fill=tk.X, pady=(0, 20))
        tk.Label(
            cache_card,
            text=self._t("  🧹  快取", "  🧹  Cache"),
            bg=THEME["surface"],
            fg=THEME["text"],
            font=ui_font(13, "bold"),
            anchor="w",
            pady=12,
            padx=16,
        ).pack(fill=tk.X)
        cache_body = tk.Frame(cache_card, bg=THEME["surface"])
        cache_body.pack(fill=tk.X, padx=16, pady=(4, 16))

        self.delete_cache_on_exit_var = tk.BooleanVar(value=bool(self.preferences.delete_thumbnail_cache_on_exit))
        self._settings_row_check(
            cache_body,
            self._t("關閉程式時刪除縮圖快取", "Delete thumbnail cache when closing the app"),
            self.delete_cache_on_exit_var,
        )

        self.keep_original_cache_var = tk.BooleanVar(value=bool(getattr(self.preferences, "keep_original_cache", True)))
        self._settings_row_check(
            cache_body,
            self._t("保留原始檔快取以加速備份", "Keep original-file cache for faster backup"),
            self.keep_original_cache_var,
        )

        self.close_to_tray_var = tk.BooleanVar(value=bool(getattr(self.preferences, "close_to_tray_on_close", False)))
        self._settings_row_check(
            cache_body,
            self._t("按右上角關閉時縮小到系統匣", "Minimize to system tray when clicking close"),
            self.close_to_tray_var,
        )

        cache_info_label = tk.Label(
            cache_body,
            text=self._t(
                "設定檔：{settings_path}\n快取位置：{cache_dir}",
                "Settings file: {settings_path}\nCache folder: {cache_dir}",
            ).format(settings_path=get_settings_path(), cache_dir=self.device_manager.cache_dir),
            bg=THEME["surface"],
            fg=THEME["text_dim"],
            font=ui_font(8),
            anchor="w",
            justify="left",
        )
        cache_info_label.pack(fill=tk.X, pady=(0, scaled_px(12)))
        cache_info_label.bind("<Configure>", lambda e, lab=cache_info_label: lab.configure(wraplength=max(scaled_px(360), e.width - scaled_px(4))))

        cache_btn_row = tk.Frame(cache_body, bg=THEME["surface"])
        cache_btn_row.pack(anchor="w", fill=tk.X)
        # Put the three "open folder" actions on the first line and the two
        # destructive delete actions on the second line.
        cache_buttons = [
            (0, 0, self._t("開啟快取位置", "Open cache folder"), self._open_cache_location),
            (0, 1, self._t("開啟原始檔位置", "Open original-cache folder"), self._open_original_cache_location),
            (0, 2, self._t("開啟設定檔位置", "Open settings-file folder"), self._open_settings_location),
            (1, 0, self._t("刪除快取", "Delete thumbnail cache"), self._clear_thumbnail_cache_now),
            (1, 1, self._t("刪除原始檔", "Delete original cache"), self._clear_original_cache_now),
        ]
        for col in range(3):
            cache_btn_row.grid_columnconfigure(col, weight=1, uniform="cache_buttons")
        for row, col, btn_text, btn_cmd in cache_buttons:
            ModernButton(
                cache_btn_row,
                text=btn_text,
                theme=THEME,
                style="secondary",
                command=btn_cmd,
            ).grid(
                row=row,
                column=col,
                sticky="ew",
                padx=(0, scaled_px(10)),
                pady=(0, scaled_px(8)),
            )

        # Runtime log info card
        log_card = tk.Frame(pad_frame, bg=THEME["surface"], bd=0)
        log_card.pack(fill=tk.X, pady=(0, 20))
        tk.Label(
            log_card,
            text=self._t("  📄  Log 檔案", "  📄  Log files"),
            bg=THEME["surface"],
            fg=THEME["text"],
            font=ui_font(13, "bold"),
            anchor="w",
            pady=12,
            padx=16,
        ).pack(fill=tk.X)
        log_body = tk.Frame(log_card, bg=THEME["surface"])
        log_body.pack(fill=tk.X, padx=16, pady=(4, 16))
        log_info_label = tk.Label(
            log_body,
            text=self._t(
                "每次執行都會自動儲存 log。目前 log：{log_path}",
                "A runtime log is saved automatically for every run. Current log: {log_path}",
            ).format(log_path=(get_current_log_path() or get_latest_log_path())),
            bg=THEME["surface"],
            fg=THEME["text_dim"],
            font=ui_font(9),
            anchor="w",
            justify="left",
        )
        log_info_label.pack(fill=tk.X, pady=(0, scaled_px(10)))
        log_info_label.bind("<Configure>", lambda e, lab=log_info_label: lab.configure(wraplength=max(scaled_px(360), e.width - scaled_px(4))))

        log_btn_row = tk.Frame(log_body, bg=THEME["surface"])
        log_btn_row.pack(anchor="w", fill=tk.X)
        for col in range(2):
            log_btn_row.grid_columnconfigure(col, weight=1, uniform="log_buttons")
        ModernButton(
            log_btn_row,
            text=self._t("開啟最新 log 檔", "Open latest log file"),
            theme=THEME,
            style="secondary",
            command=self._open_latest_log_file,
        ).grid(row=0, column=0, sticky="ew", padx=(0, scaled_px(10)), pady=(0, scaled_px(8)))
        ModernButton(
            log_btn_row,
            text=self._t("刪除全部 log 檔", "Delete all log files"),
            theme=THEME,
            style="secondary",
            command=self._clear_logs_now,
        ).grid(row=0, column=1, sticky="ew", padx=(0, scaled_px(10)), pady=(0, scaled_px(8)))

        # Save row
        save_row = tk.Frame(pad_frame, bg=THEME["bg"])
        save_row.pack(fill=tk.X, pady=(6, 0))
        self.settings_dirty_label = tk.Label(
            save_row,
            text=self._t("設定已是最新狀態", "Settings are up to date"),
            bg=THEME["bg"],
            fg=THEME["text_dim"],
            font=ui_font(9),
        )
        self.settings_dirty_label.pack(side=tk.LEFT)

        self.save_settings_btn = tk.Button(
            save_row,
            text=self._t("儲存設定", "Save settings"),
            bg=THEME["surface2"],
            fg=THEME["text_dim"],
            activebackground=THEME["accent_hover"],
            activeforeground="#ffffff",
            relief="flat",
            font=ui_font(10, "bold"),
            padx=18,
            pady=8,
            state="disabled",
            cursor="",
            command=self._save_settings_clicked,
        )
        self.save_settings_btn.pack(side=tk.RIGHT)

        self.reset_settings_btn = tk.Button(
            save_row,
            text=self._t("恢復預設", "Restore defaults"),
            bg=THEME["surface2"],
            fg=THEME["text"],
            activebackground=THEME["border"],
            activeforeground=THEME["text"],
            relief="flat",
            font=ui_font(10, "bold"),
            padx=18,
            pady=8,
            cursor="hand2",
            command=self._restore_default_settings,
        )
        self.reset_settings_btn.pack(side=tk.RIGHT, padx=(0, 10))

        # Track changes.  The Save button is enabled only when the current UI
        # values differ from the last saved settings snapshot.
        for var in (
            self.language_var,
            self.theme_mode_var,
            self.ui_font_size_var,
            self.photo_backup_folder_var,
            self.full_backup_folder_pref_var,
            self.existing_file_action_var,
            self.convert_heic_var,
            self.image_output_format_var,
            self.jpeg_quality_var,
            self.jpeg_subsampling_var,
            self.jpeg_optimize_var,
            self.png_compress_level_var,
            self.conversion_workers_var,
            self.keep_original_cache_var,
            self.delete_cache_on_exit_var,
            self.close_to_tray_var,
        ):
            try:
                var.trace_add("write", lambda *_args: self._update_settings_dirty_state())
            except Exception:
                pass
        self._update_settings_dirty_state()

    def _build_about_tab(self):
        """Build the About tab with project and contact information."""
        pad_frame = self._make_scrollable_tab(self.about_tab, padx=40, pady=32)

        card = tk.Frame(pad_frame, bg=THEME["surface"], bd=0)
        card.pack(fill=tk.X, pady=(0, 20))
        tk.Label(
            card,
            text=self._t("  ℹ  關於 OrchardBridge", "  ℹ  About OrchardBridge"),
            bg=THEME["surface"],
            fg=THEME["text"],
            font=ui_font(13, "bold"),
            anchor="w",
            pady=12,
            padx=16,
        ).pack(fill=tk.X)

        body = tk.Frame(card, bg=THEME["surface"])
        body.pack(fill=tk.X, padx=16, pady=(4, 16))

        def _about_row(label_text, value_text, command=None):
            """One-line About row.

            Only the value part is clickable.  The row frame may fill the full
            width, but the link Label is packed at its natural text width, so
            hovering blank space on the right no longer triggers link feedback.
            """
            row = tk.Frame(body, bg=THEME["surface"])
            row.pack(fill=tk.X, pady=(0, scaled_px(12)))
            prefix = tk.Label(
                row,
                text=f"{label_text}: ",
                bg=THEME["surface"],
                fg=THEME["text"],
                font=ui_font(10),
                anchor="w",
            )
            prefix.pack(side=tk.LEFT)
            clickable = command is not None
            value = tk.Label(
                row,
                text=str(value_text),
                bg=THEME["surface"],
                fg=(THEME["accent"] if clickable else THEME["text"]),
                font=ui_font(10, "underline") if clickable else ui_font(10),
                anchor="w",
                justify="left",
                cursor="hand2" if clickable else "",
            )
            value.pack(side=tk.LEFT)
            if clickable:
                value.bind("<Button-1>", lambda _e: command())
                value.bind("<Enter>", lambda _e: value.configure(fg=THEME["accent_hover"]))
                value.bind("<Leave>", lambda _e: value.configure(fg=THEME["accent"]))

        _about_row(self._t("版本", "Version"), APP_VERSION)
        _about_row(self._t("作者", "Author"), AUTHOR_NAME)
        _about_row(self._t("作者信箱", "Author email"), AUTHOR_EMAIL, lambda: webbrowser.open(f"mailto:{AUTHOR_EMAIL}"))
        _about_row("GitHub", GITHUB_URL, lambda: webbrowser.open(GITHUB_URL))
        _about_row(self._t("贊助：GitHub Sponsors", "Donate: GitHub Sponsors"), SPONSOR_URL, lambda: webbrowser.open(SPONSOR_URL))
        _about_row(self._t("贊助：Buy Me a Coffee", "Donate: Buy Me a Coffee"), BUYMEACOFFEE_URL, lambda: webbrowser.open(BUYMEACOFFEE_URL))
        _about_row(self._t("Bug 回報信箱", "Bug report email"), BUG_REPORT_EMAIL, self._open_bug_report_window)

        desc_card = tk.Frame(pad_frame, bg=THEME["surface"], bd=0)
        desc_card.pack(fill=tk.X, pady=(0, 20))
        tk.Label(
            desc_card,
            text=self._t("  📝  說明", "  📝  Notes"),
            bg=THEME["surface"],
            fg=THEME["text"],
            font=ui_font(13, "bold"),
            anchor="w",
            pady=12,
            padx=16,
        ).pack(fill=tk.X)
        note = self._t(
            "OrchardBridge v1.2026.06.23 是第一個準備發布的版本。版本號採用 v主版本.日期，方便日後對照發布日期。\n\n"
            "此專案是獨立工具，產品名稱避免使用任何第三方商標；若介面或文件提到特定裝置名稱，只是為了描述相容性。",
            "OrchardBridge v1.2026.06.23 is the first release-ready version. The version label uses vMajor.YYYY.MM.DD so future releases can be matched to their release dates.\n\n"
            "This is an independent tool. The product name avoids third-party trademarks; any device names in the UI or documentation are used only to describe compatibility.",
        )
        note_label = tk.Label(
            desc_card,
            text=note,
            bg=THEME["surface"],
            fg=THEME["text_dim"],
            font=ui_font(11),
            anchor="w",
            justify="left",
            padx=16,
            pady=0,
        )
        note_label.pack(fill=tk.X, pady=(0, 16))
        note_label.bind("<Configure>", lambda e, lab=note_label: lab.configure(wraplength=max(scaled_px(520), e.width - scaled_px(32))))

        link_row = tk.Frame(desc_card, bg=THEME["surface"])
        link_row.pack(fill=tk.X, padx=16, pady=(0, 16))

        def _accent_button(parent, text, command):
            return tk.Button(
                parent,
                text=text,
                bg=THEME["accent"],
                fg="#ffffff",
                activebackground=THEME["accent_hover"],
                activeforeground="#ffffff",
                relief="flat",
                borderwidth=0,
                highlightthickness=0,
                takefocus=False,
                font=ui_font(10, "bold"),
                padx=scaled_px(14),
                pady=scaled_px(7),
                cursor="hand2",
                command=command,
            )

        about_buttons = [
            (self._t("開啟 GitHub", "Open GitHub"), lambda: webbrowser.open(GITHUB_URL)),
            (self._t("贊助：GitHub Sponsors", "Donate: GitHub Sponsors"), lambda: webbrowser.open(SPONSOR_URL)),
            (self._t("贊助：Buy Me a Coffee", "Donate: Buy Me a Coffee"), lambda: webbrowser.open(BUYMEACOFFEE_URL)),
            (self._t("寄信給作者", "Email author"), lambda: webbrowser.open(f"mailto:{AUTHOR_EMAIL}")),
            (self._t("回報 Bug…", "Report bug…"), self._open_bug_report_window),
        ]
        for text, cmd in about_buttons:
            _accent_button(link_row, text, cmd).pack(side=tk.LEFT, padx=(0, scaled_px(12)), pady=(0, scaled_px(8)))


    def _settings_row_base(self, parent, label_text: str, help_text: str | None = None):
        row = tk.Frame(parent, bg=THEME["surface"])
        row.pack(fill=tk.X, pady=(0, scaled_px(26)))

        title = tk.Label(
            row,
            text=label_text,
            bg=THEME["surface"],
            fg=THEME["text"],
            font=ui_font(11),
            anchor="w",
            justify="left",
        )
        title.pack(fill=tk.X, pady=(0, scaled_px(5)))
        title.bind("<Configure>", lambda e, lab=title: lab.configure(wraplength=max(320, e.width - 4)))

        if help_text:
            help_lbl = tk.Label(
                row,
                text=help_text,
                bg=THEME["surface"],
                fg=THEME["text_dim"],
                font=ui_font(9),
                anchor="w",
                justify="left",
            )
            help_lbl.pack(fill=tk.X, pady=(0, scaled_px(10)))
            help_lbl.bind("<Configure>", lambda e, lab=help_lbl: lab.configure(wraplength=max(320, e.width - 4)))
        right = tk.Frame(row, bg=THEME["surface"])
        right.pack(fill=tk.X)
        return right

    def _settings_row_combo(self, parent, label_text, variable, values, help_text=None):
        right = self._settings_row_base(parent, label_text, help_text)
        combo = ttk.Combobox(
            right,
            textvariable=variable,
            values=values,
            state="readonly",
            style="App.TCombobox",
            width=34,
            font=ui_font(11),
            takefocus=False,
        )
        combo.pack(anchor="w", ipady=scaled_px(3))

        def _defocus(_event=None, cb=combo):
            try:
                cb.selection_clear()
                cb.icursor(tk.END)
            except Exception:
                pass
            try:
                self.root.focus_set()
            except Exception:
                pass

        combo.bind("<<ComboboxSelected>>", lambda e: self.root.after(50, _defocus), add="+")
        return combo

    def _settings_row_spin(self, parent, label_text, variable, from_, to, help_text=None):
        right = self._settings_row_base(parent, label_text, help_text)
        spin = tk.Spinbox(
            right,
            from_=from_,
            to=to,
            textvariable=variable,
            bg=THEME["surface2"],
            fg=THEME["text"],
            insertbackground=THEME["text"],
            relief="flat",
            width=8,
            font=ui_font(10),
            command=self._update_settings_dirty_state,
        )
        spin.pack(anchor="w", ipady=3)
        return spin

    def _settings_row_slider_entry(self, parent, label_text, variable, from_, to, help_text=None):
        right = self._settings_row_base(parent, label_text, help_text)
        row = tk.Frame(right, bg=THEME["surface"])
        row.pack(fill=tk.X)

        scale = tk.Scale(
            row,
            from_=from_,
            to=to,
            orient=tk.HORIZONTAL,
            variable=variable,
            bg=("#f8fafc" if THEME.get("mode") == "dark" else THEME.get("slider_bg", THEME["surface"])),
            fg=("#f8fafc" if THEME.get("mode") == "dark" else THEME["text"]),
            troughcolor=("#c4b5fd" if THEME.get("mode") == "dark" else THEME.get("slider_trough", THEME["surface2"])),
            activebackground=THEME["accent"],
            highlightbackground=THEME["surface"],
            highlightcolor=THEME["accent"],
            highlightthickness=0,
            length=520,
            showvalue=False,
            command=lambda _v: self._update_settings_dirty_state(),
        )
        scale.pack(side=tk.LEFT, fill=tk.X, expand=True)

        def _wheel(event, v=variable, lo=from_, hi=to):
            try:
                cur = int(v.get())
            except Exception:
                cur = lo
            step = 1 if getattr(event, "delta", 0) > 0 or getattr(event, "num", None) == 4 else -1
            v.set(max(lo, min(hi, cur + step)))
            self._update_settings_dirty_state()
            return "break"
        scale.bind("<MouseWheel>", _wheel)
        scale.bind("<Button-4>", _wheel)
        scale.bind("<Button-5>", _wheel)

        entry = tk.Entry(
            row,
            textvariable=variable,
            bg=THEME["surface2"],
            fg=THEME["text"],
            insertbackground=THEME["text"],
            relief="flat",
            width=6,
            font=ui_font(10),
            justify="center",
        )
        entry.pack(side=tk.LEFT, padx=(10, 0), ipady=3)

        def _clamp(*_args):
            try:
                value = int(variable.get())
            except Exception:
                value = from_
            value = max(from_, min(to, value))
            if value != variable.get():
                try:
                    variable.set(value)
                except Exception:
                    pass
            self._update_settings_dirty_state()

        try:
            variable.trace_add("write", lambda *_args: self.root.after_idle(_clamp))
        except Exception:
            pass
        return scale, entry

    def _settings_row_check(self, parent, label_text, variable):
        """Large custom checkbox row with label before the checkbox.

        Tk's native checkbox indicator does not scale reliably on Windows, so
        this uses a small Canvas indicator that follows the UI font size.  The
        clickable checkbox is placed after the label as requested.
        """
        row = tk.Frame(parent, bg=THEME["surface"])
        row.pack(fill=tk.X, pady=(0, scaled_px(22)))
        line = tk.Frame(row, bg=THEME["surface"])
        line.pack(fill=tk.X)
        lbl = tk.Label(
            line,
            text=label_text,
            bg=THEME["surface"],
            fg=THEME["text"],
            font=ui_font(11),
            anchor="w",
            justify="left",
        )
        lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)

        box_size = scaled_px(30)
        box = tk.Canvas(
            line,
            width=box_size,
            height=box_size,
            bg=THEME["surface"],
            highlightthickness=0,
            cursor="hand2",
        )
        box.pack(side=tk.LEFT, padx=(scaled_px(12), scaled_px(4)))

        def redraw(*_args):
            try:
                box.delete("all")
                pad = scaled_px(3)
                size = box_size - pad * 2
                selected = bool(variable.get())
                fill = THEME["accent"] if selected else THEME["surface2"]
                outline = THEME.get("border", "#94a3b8")
                box.create_rectangle(pad, pad, pad + size, pad + size, fill=fill, outline=outline, width=2)
                if selected:
                    box.create_text(
                        pad + size / 2,
                        pad + size / 2,
                        text="✓",
                        fill="#ffffff",
                        font=ui_font(16, "bold"),
                    )
            except Exception:
                pass

        def toggle(_event=None):
            try:
                variable.set(not bool(variable.get()))
                redraw()
                self._update_settings_dirty_state()
            except Exception:
                pass

        for w in (line, lbl, box):
            w.bind("<Button-1>", toggle)
        try:
            variable.trace_add("write", lambda *_: redraw())
        except Exception:
            pass
        lbl.bind("<Configure>", lambda e, lab=lbl: lab.configure(wraplength=max(240, e.width - 4)))
        redraw()
        return box

    def _settings_row_folder(self, parent, label_text, variable):
        right = self._settings_row_base(parent, label_text)
        entry = tk.Entry(
            right,
            textvariable=variable,
            bg=THEME["surface2"],
            fg=THEME["text"],
            insertbackground=THEME["text"],
            relief="flat",
            font=ui_font(10),
            width=68,
        )
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=4)
        tk.Button(
            right,
            text=self._t("瀏覽…", "Browse…"),
            bg=THEME["surface2"],
            fg=THEME["text"],
            activebackground=THEME["border"],
            activeforeground=THEME["text"],
            relief="flat",
            font=ui_font(9),
            padx=8,
            pady=4,
            cursor="hand2",
            command=lambda v=variable: self._browse_settings_folder(v),
        ).pack(side=tk.LEFT, padx=(8, 0))
        return entry

    def _browse_settings_folder(self, var: tk.StringVar):
        folder = filedialog.askdirectory(parent=self.root)
        if folder:
            var.set(folder)
            self._update_settings_dirty_state()

    def _theme_display(self, mode: str) -> str:
        mode = str(mode or "light").lower().strip()
        return self._t("暗色", "Dark") if mode == "dark" else self._t("亮色", "Light")

    def _theme_value_from_display(self, value: str) -> str:
        text = str(value).strip()
        lowered = text.lower()
        if text == self._theme_display("dark") or lowered in {"dark", "暗色"} or "dark" in lowered or "暗" in text:
            return "dark"
        return "light"

    def _language_display(self, language: str) -> str:
        languages = get_supported_languages()
        return languages.get(str(language), languages.get("en-US", "English"))

    def _language_code_from_display(self, value: str) -> str:
        text = str(value).strip()
        for code, label in get_supported_languages().items():
            if text == label:
                return code
        # Backward compatibility with old two-option settings.
        return "en-US"

    def _subsampling_display(self, value: int) -> str:
        try:
            value = int(value)
        except Exception:
            value = 0
        if value == 2:
            return self._t("較小檔案", "Smaller file")
        if value == 1:
            return self._t("平衡", "Balanced")
        return self._t("最佳品質", "Best quality")

    def _subsampling_value_from_display(self, value: str) -> int:
        text = str(value).strip().lower()
        if value == self._subsampling_display(2) or "smaller" in text or "較小" in value or "小" in value:
            return 2
        if value == self._subsampling_display(1) or "balanced" in text or "平衡" in value:
            return 1
        return 0

    def _existing_action_display(self, action: str) -> str:
        action = str(action or "rename").lower().strip()
        if action == "overwrite":
            return self._t("覆蓋同名檔案", "Overwrite existing file")
        if action == "skip":
            return self._t("略過同名檔案", "Skip existing file")
        return self._t("自動重新命名", "Rename automatically")

    def _existing_action_value_from_display(self, value: str) -> str:
        text = str(value).strip().lower()
        if value == self._existing_action_display("overwrite") or "overwrite" in text or "覆蓋" in value:
            return "overwrite"
        if value == self._existing_action_display("skip") or "skip" in text or "略過" in value:
            return "skip"
        return "rename"

    def _preferences_from_ui(self) -> Preferences:
        try:
            quality = int(self.jpeg_quality_var.get())
        except Exception:
            quality = 100
        try:
            png_level = int(self.png_compress_level_var.get())
        except Exception:
            png_level = 0
        try:
            workers = int(self.conversion_workers_var.get())
        except Exception:
            import os as _os
            workers = max(1, (_os.cpu_count() or 2) - 2)
        return Preferences(
            language=self._language_code_from_display(self.language_var.get()),
            theme_mode=self._theme_value_from_display(self.theme_mode_var.get()) if hasattr(self, "theme_mode_var") else getattr(self.preferences, "theme_mode", "light"),
            default_photo_backup_folder=str(self.photo_backup_folder_var.get()).strip() or str(Path.home() / "OrchardBridgePhotosBackup"),
            default_full_backup_folder=str(self.full_backup_folder_pref_var.get()).strip() or str(Path.home() / "OrchardBridgeFullBackup"),
            existing_file_action=self._existing_action_value_from_display(self.existing_file_action_var.get()),
            convert_after_backup=bool(self.convert_heic_var.get()),
            image_output_format="PNG" if str(self.image_output_format_var.get()).upper() == "PNG" else "JPEG",
            jpeg_quality=max(1, min(100, quality)),
            jpeg_subsampling=self._subsampling_value_from_display(self.jpeg_subsampling_var.get()),
            jpeg_optimize=bool(self.jpeg_optimize_var.get()),
            png_compress_level=max(0, min(9, png_level)),
            conversion_workers=max(1, min(__import__("os").cpu_count() or 1, workers)),
            keep_original_cache=bool(self.keep_original_cache_var.get()) if hasattr(self, "keep_original_cache_var") else True,
            delete_thumbnail_cache_on_exit=bool(self.delete_cache_on_exit_var.get()),
            close_to_tray_on_close=bool(self.close_to_tray_var.get()) if hasattr(self, "close_to_tray_var") else False,
            ui_font_size=int(self.ui_font_size_var.get()) if hasattr(self, "ui_font_size_var") else int(getattr(self.preferences, "ui_font_size", 10)),
        )

    def _update_settings_dirty_state(self):
        try:
            current = prefs_to_dict(self._preferences_from_ui())
            dirty = current != self._saved_preferences_dict
        except Exception:
            dirty = True
        if not hasattr(self, "save_settings_btn"):
            return
        if dirty:
            self.save_settings_btn.configure(
                state="normal",
                bg=THEME["accent"],
                fg="#ffffff",
                cursor="hand2",
            )
            self.settings_dirty_label.configure(
                text=self._t("有尚未儲存的設定", "There are unsaved settings"),
                fg=THEME["warning"],
            )
        else:
            self.save_settings_btn.configure(
                state="disabled",
                bg=THEME["surface2"],
                fg=THEME["text_dim"],
                cursor="",
            )
            self.settings_dirty_label.configure(
                text=self._t("設定已是最新狀態", "Settings are up to date"),
                fg=THEME["text_dim"],
            )

    def _restore_default_settings(self):
        if not messagebox.askyesno(
            self._t("恢復預設", "Restore defaults"),
            self._t(
                "確定要恢復所有設定為預設值嗎？設定會立即儲存，只有語言、外觀或字體大小實際需要變更時才會重新啟動。",
                "Restore all settings to defaults? Settings will be saved immediately. The app restarts only if language, appearance, or font size actually changes.",
            ),
            parent=self.root,
        ):
            return

        # Snapshot the currently applied settings, the visible unsaved UI values,
        # and the factory defaults. Restore must work even when the user has
        # changed controls but has not pressed Save yet.
        app_prefs = self.preferences
        app_dict = prefs_to_dict(app_prefs)
        try:
            ui_prefs = self._preferences_from_ui()
            ui_dict = prefs_to_dict(ui_prefs)
        except Exception as exc:
            print(f"[settings] Failed to read current UI settings before restoring defaults: {exc!r}")
            ui_prefs = app_prefs
            ui_dict = app_dict

        default_prefs = default_preferences()
        default_dict = prefs_to_dict(default_prefs)
        restart_keys = ("language", "theme_mode", "ui_font_size")
        needs_restart = any(app_dict.get(k) != ui_dict.get(k) for k in restart_keys) or any(
            ui_dict.get(k) != default_dict.get(k) for k in restart_keys
        ) or any(app_dict.get(k) != default_dict.get(k) for k in restart_keys)

        if app_dict == default_dict and ui_dict == default_dict:
            self.status_bar.set(self._t("目前已經是預設設定", "Settings are already at defaults"), "info")
            self._update_settings_dirty_state()
            return

        # First persist the visible UI snapshot as requested, then replace it
        # with factory defaults. This makes Restore deterministic even when the
        # settings page contains unsaved edits.
        try:
            print(f"[settings] Snapshotting visible UI settings before restore: {ui_dict}")
            save_preferences(ui_prefs)
        except Exception as exc:
            print(f"[settings] Failed to snapshot visible UI settings before restore: {exc!r}")

        print(f"[settings] Restoring defaults: {default_dict}; needs_restart={needs_restart}")
        save_preferences(default_prefs)
        self.preferences = default_prefs
        self._saved_preferences_dict = default_dict
        self.backup_manager.apply_preferences(default_prefs)
        try:
            self.device_manager.apply_preferences(default_prefs)
        except Exception:
            pass

        if needs_restart:
            self._restart_app()
            return

        try:
            self.language_var.set(self._language_display(default_prefs.language))
            self.theme_mode_var.set(self._theme_display(getattr(default_prefs, "theme_mode", "light")))
            self.ui_font_size_var.set(int(getattr(default_prefs, "ui_font_size", 10)))
            self.photo_backup_folder_var.set(str(default_prefs.default_photo_backup_folder))
            self.full_backup_folder_pref_var.set(str(default_prefs.default_full_backup_folder))
            self.existing_file_action_var.set(self._existing_action_display(default_prefs.existing_file_action))
            self.convert_heic_var.set(bool(default_prefs.convert_after_backup))
            self.image_output_format_var.set(default_prefs.image_output_format)
            self.jpeg_quality_var.set(int(default_prefs.jpeg_quality))
            self.jpeg_subsampling_var.set(self._subsampling_display(default_prefs.jpeg_subsampling))
            self.jpeg_optimize_var.set(bool(default_prefs.jpeg_optimize))
            self.png_compress_level_var.set(int(default_prefs.png_compress_level))
            self.conversion_workers_var.set(int(default_prefs.conversion_workers))
            self.delete_cache_on_exit_var.set(bool(default_prefs.delete_thumbnail_cache_on_exit))
            self.keep_original_cache_var.set(bool(default_prefs.keep_original_cache))
            self.close_to_tray_var.set(bool(default_prefs.close_to_tray_on_close))
            self.backup_panel.set_preferences(
                default_dest=default_prefs.default_photo_backup_folder,
                default_convert_heic=default_prefs.convert_after_backup,
                default_output_format=default_prefs.image_output_format,
            )
        except Exception as exc:
            print(f"[settings] Failed to refresh settings UI after defaults: {exc!r}")
        self._update_settings_dirty_state()
        self.status_bar.set(self._t("已恢復預設設定", "Default settings restored"), "success")

    def _save_settings_clicked(self):
        old_language = self.preferences.language
        old_theme = getattr(self.preferences, "theme_mode", "light")
        old_font_size = int(getattr(self.preferences, "ui_font_size", 10))
        new_prefs = self._preferences_from_ui()
        print(f"[settings] Saving preferences: {prefs_to_dict(new_prefs)}")
        save_preferences(new_prefs)
        self.preferences = new_prefs
        self._locale_table = self._load_locale_table()
        self._saved_preferences_dict = prefs_to_dict(new_prefs)
        self.backup_manager.apply_preferences(new_prefs)
        try:
            self.device_manager.apply_preferences(new_prefs)
        except Exception:
            pass

        # Photo backup / conversion settings take effect immediately for future
        # backup operations without restarting the app.
        try:
            self.backup_panel.set_preferences(
                default_dest=new_prefs.default_photo_backup_folder,
                default_convert_heic=new_prefs.convert_after_backup,
                default_output_format=new_prefs.image_output_format,
            )
        except Exception:
            pass
        try:
            self.full_backup_folder_var.set(str(new_prefs.default_full_backup_folder))
        except Exception:
            pass

        self._update_settings_dirty_state()
        self.status_bar.set(self._t("設定已儲存", "Settings saved"), "success")

        if (new_prefs.language != old_language
                or getattr(new_prefs, "theme_mode", "light") != old_theme
                or int(getattr(new_prefs, "ui_font_size", 10)) != old_font_size):
            messagebox.showinfo(
                self._t("需要重新啟動", "Restart required"),
                self._t(
                    "語言、外觀或字體設定已儲存。軟體將自動重新啟動以套用新的設定。",
                    "Language, appearance, or font setting saved. The app will restart automatically to apply it.",
                ),
                parent=self.root,
            )
            self._restart_app()

    def _restart_app(self):
        """Restart OrchardBridge using an explicit, space-safe command.

        A previous implementation used ``os.execv(sys.executable, [sys.executable] + sys.argv)``.
        That works on some machines, but it is fragile on Windows when the local
        venv path contains spaces, for example ``C:/Users/Pei-Ting Gao/...``.
        On one laptop Windows/Conda rebuilt the command incorrectly and tried to
        open a duplicated path.  Build the restart command from the actual app
        entry point instead of reusing ``sys.argv`` and start it with
        ``subprocess.Popen(list_args)`` so Windows receives each argument safely.
        """
        self._app_closing = True
        try:
            self.backup_manager.cancel()
            self.device_manager.disconnect()
        except Exception:
            pass

        app_dir = Path(__file__).resolve().parents[1]
        env = os.environ.copy()
        try:
            cur_log = get_current_log_path()
            if cur_log:
                env["IPBT_ACTIVE_LOG"] = str(cur_log)
        except Exception:
            pass

        if getattr(sys, "frozen", False):
            cmd = [sys.executable]
        else:
            cmd = [sys.executable, str(app_dir / "main.py")]

        creationflags = 0
        if os.name == "nt" and hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
            creationflags |= subprocess.CREATE_NEW_PROCESS_GROUP

        try:
            print(f"[restart] Relaunching app with command: {cmd!r}; cwd={str(app_dir)!r}")
            subprocess.Popen(cmd, cwd=str(app_dir), env=env, creationflags=creationflags)
        except Exception as exc:
            print(f"[restart] Relaunch failed: {exc!r}")
            try:
                messagebox.showerror(
                    self._t("重新啟動失敗", "Restart failed"),
                    self._fmt_t(
                        "設定已儲存，但自動重新啟動失敗：{error}",
                        "Settings were saved, but the automatic restart failed: {error}",
                        error=exc,
                    ),
                    parent=self.root,
                )
            except Exception:
                pass
            return

        try:
            self.root.destroy()
        except Exception:
            pass



    def _open_bug_report_window(self):
        """Open bug report dialog and send report via the default mail client.

        The email body includes a short header, the user's description, and the
        current run log content.  A local .eml draft is created because long
        mailto: bodies are often discarded by Windows mail clients.
        """
        win = tk.Toplevel(self.root)
        win.title(self._t("回報 Bug", "Report a bug"))
        base_w = max(scaled_px(900), int(self.root.winfo_width() * 0.58))
        base_h = max(scaled_px(720), int(self.root.winfo_height() * 0.70))
        win.geometry(f"{base_w}x{base_h}")
        win.minsize(scaled_px(820), scaled_px(620))
        win.configure(bg=THEME["bg"])
        win.transient(self.root)
        win.grab_set()

        pad = tk.Frame(win, bg=THEME["bg"])
        pad.pack(fill=tk.BOTH, expand=True, padx=scaled_px(28), pady=scaled_px(24))
        pad.rowconfigure(1, weight=1)
        pad.columnconfigure(0, weight=1)

        tk.Label(
            pad,
            text=self._t("請描述你遇到的問題", "Describe the issue"),
            bg=THEME["bg"],
            fg=THEME["text"],
            font=ui_font(12, "bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", pady=(0, scaled_px(10)))

        text = tk.Text(
            pad,
            bg=THEME["surface"],
            fg=THEME["text"],
            insertbackground=THEME["text"],
            relief="flat",
            wrap="word",
            font=ui_font(10),
        )
        text.grid(row=1, column=0, sticky="nsew")

        current_log = get_current_log_path() or get_latest_log_path()
        log_path_text = str(current_log) if current_log else self._t("找不到目前 log 檔", "No current log file found")

        info_row = tk.Frame(pad, bg=THEME["bg"])
        info_row.grid(row=2, column=0, sticky="ew", pady=(scaled_px(14), scaled_px(8)))
        info_row.columnconfigure(1, weight=1)
        tk.Label(
            info_row,
            text=self._t("目前 log：", "Current log: "),
            bg=THEME["bg"], fg=THEME["text_dim"], font=ui_font(9), anchor="w",
        ).grid(row=0, column=0, sticky="w")
        log_entry = tk.Entry(info_row, bg=THEME["surface"], fg=THEME["text_dim"], relief="flat", font=ui_font(9))
        log_entry.grid(row=0, column=1, sticky="ew", padx=(scaled_px(6), 0))
        log_entry.insert(0, log_path_text)
        log_entry.configure(state="readonly")

        status_label = tk.Label(
            pad,
            text=self._fmt_t("Bug 回報信箱：{email}", "Bug report email: {email}", email=BUG_REPORT_EMAIL),
            bg=THEME["bg"], fg=THEME["text_dim"], font=ui_font(9), anchor="w",
        )
        status_label.grid(row=3, column=0, sticky="ew", pady=(0, scaled_px(12)))

        btn_row = tk.Frame(pad, bg=THEME["bg"])
        btn_row.grid(row=4, column=0, sticky="ew")

        def _read_log_text(path: Path | None) -> str:
            if not path or not Path(path).exists():
                return self._t("找不到目前 log 檔。", "Current log file was not found.")
            try:
                return Path(path).read_text(encoding="utf-8", errors="replace")
            except Exception as exc:
                return f"[failed to read log: {exc!r}]"

        def _create_report():
            desc = text.get("1.0", "end").strip()
            log_path = get_current_log_path() or get_latest_log_path()
            print(f"[bug_report] User description: {desc}")
            if log_path and Path(log_path).exists():
                try:
                    with open(log_path, "a", encoding="utf-8", errors="replace") as f:
                        f.write("\n" + "=" * 72 + "\n")
                        f.write("USER BUG REPORT DESCRIPTION\n")
                        f.write(f"Time: {dt.datetime.now().isoformat(timespec='seconds')}\n")
                        f.write(desc + "\n")
                        f.write("=" * 72 + "\n")
                except Exception as exc:
                    print(f"[bug_report] Failed to append user description to active log: {exc!r}")

            log_text = _read_log_text(log_path)
            subject_text = "OrchardBridge bug report"
            # The user description is appended into the run log above under
            # USER BUG REPORT DESCRIPTION.  Keep the email body as the log only
            # to avoid showing the same description twice.
            body_text = log_text

            try:
                self.root.clipboard_clear()
                self.root.clipboard_append(body_text)
            except Exception:
                pass

            # Write a real .eml draft instead of relying on a long mailto: URL.
            # Several Windows mail clients silently drop the body when the
            # mailto body is large.  Opening an .eml preserves the user's text
            # and the full log in the message body.
            try:
                report_dir = get_bug_report_dir()
                ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
                eml_path = report_dir / f"OrchardBridge_bug_report_{ts}.eml"
                msg = EmailMessage()
                msg["To"] = BUG_REPORT_EMAIL
                msg["Subject"] = subject_text
                msg["X-Unsent"] = "1"
                msg.set_content(body_text, subtype="plain", charset="utf-8")
                eml_path.write_bytes(bytes(msg))
                if os.name == "nt":
                    os.startfile(str(eml_path))  # type: ignore[attr-defined]
                else:
                    webbrowser.open(eml_path.as_uri())
                print(f"[bug_report] EML draft created: {eml_path}")
            except Exception as exc:
                print(f"[bug_report] EML draft failed: {exc!r}")
                # Fallback: keep the complete report on clipboard, then open a
                # simple addressed email.  This is intentionally short so mail
                # clients do not discard the body.
                try:
                    subject = urllib.parse.quote(subject_text)
                    short = urllib.parse.quote(self._t(
                        "完整 Bug 回報內容與 log 已複製到剪貼簿，請直接貼到這封信中。",
                        "The full bug report and log have been copied to the clipboard. Please paste them into this email.",
                    ))
                    webbrowser.open(f"mailto:{BUG_REPORT_EMAIL}?subject={subject}&body={short}")
                except Exception:
                    try:
                        webbrowser.open(f"mailto:{BUG_REPORT_EMAIL}")
                    except Exception:
                        pass

            status_label.configure(text=self._t("已開啟 Email 回報草稿，完整內容也已複製到剪貼簿。", "Email report draft opened; full content was also copied to the clipboard."), fg=THEME["success"])
            try:
                win.destroy()
            except Exception:
                pass

        tk.Button(
            btn_row,
            text=self._t("開啟 Email 回報", "Open email report"),
            bg=THEME["accent"], fg="#ffffff",
            activebackground=THEME["accent_hover"], activeforeground="#ffffff",
            relief="flat", font=ui_font(10, "bold"),
            padx=scaled_px(16), pady=scaled_px(10), cursor="hand2",
            command=_create_report,
        ).pack(side=tk.RIGHT)

        tk.Button(
            btn_row,
            text=self._t("關閉", "Close"),
            bg=THEME["surface2"], fg=THEME["text"],
            activebackground=THEME["border"], activeforeground=THEME["text"],
            relief="flat", font=ui_font(10),
            padx=scaled_px(16), pady=scaled_px(10), cursor="hand2",
            command=win.destroy,
        ).pack(side=tk.RIGHT, padx=(0, scaled_px(10)))

    def _confirm_and_clear_cache(self, kind: str):
        folder, count = self.device_manager.cache_file_count("originals" if kind == "originals" else "thumbs")
        title = self._t("確認刪除", "Confirm delete")
        if kind == "originals":
            msg = self._fmt_t(
                "即將刪除原始檔快取資料夾：\n{folder}\n\n包含 {count} 個檔案。\n\n這不會刪除正式備份資料夾，但之後備份可能需要重新從裝置傳輸。是否繼續？",
                "You are about to delete the original-file cache folder:\n{folder}\n\nIt contains {count} file(s).\n\nThis will not delete formal backup output, but future backups may need to transfer files from the device again. Continue?",
                folder=folder,
                count=count,
            )
        else:
            msg = self._fmt_t(
                "即將刪除縮圖快取資料夾：\n{folder}\n\n包含 {count} 個檔案。\n\n這不會刪除正式備份資料夾。是否繼續？",
                "You are about to delete the thumbnail cache folder:\n{folder}\n\nIt contains {count} file(s).\n\nThis will not delete formal backup output. Continue?",
                folder=folder,
                count=count,
            )
        if not messagebox.askyesno(title, msg, parent=self.root):
            return
        if kind == "originals":
            ok, raw_msg = self.device_manager.clear_original_cache()
            msg = self._fmt_t("已刪除原始檔快取：{folder}", "Original-file cache deleted: {folder}", folder=folder) if ok else raw_msg
        else:
            ok, raw_msg = self.device_manager.clear_thumbnail_cache()
            msg = self._fmt_t("已刪除縮圖快取：{folder}", "Thumbnail cache deleted: {folder}", folder=folder) if ok else raw_msg
        if ok:
            self.status_bar.set(msg, "success")
            messagebox.showinfo(self._t("完成", "Done"), msg, parent=self.root)
        else:
            self.status_bar.set(msg, "error")
            messagebox.showerror(self._t("錯誤", "Error"), msg, parent=self.root)

    def _clear_thumbnail_cache_now(self):
        self._confirm_and_clear_cache("thumbs")

    def _clear_original_cache_now(self):
        self._confirm_and_clear_cache("originals")

    def _setup_screenshot_hotkeys(self):
        """Do not intercept Print Screen on Windows.

        The expected Windows behavior is controlled by the user's system setting:
        pressing Print Screen should open the Windows Snipping Tool / screen clip
        UI.  If OrchardBridge binds ``<Print>`` and returns ``break``, that global
        Windows behavior can be blocked.  Therefore, on Windows this method is a
        deliberate no-op.  The app-window capture helper remains available only
        as a manual/internal fallback and is not bound to Print Screen.
        """
        if os.name == "nt":
            print("[screenshot] Print Screen is not intercepted; Windows will handle Snipping Tool behavior.")
            return

        # Non-Windows fallback: keep the old app-window capture shortcut because
        # there is no single cross-platform Snipping Tool behavior to delegate to.
        for sequence in (
            "<Print>",
            "<KeyPress-Print>",
            "<Shift-Print>",
            "<Alt-Print>",
            "<Control-Print>",
        ):
            try:
                self.root.bind_all(sequence, self._capture_app_screenshot, add="+")
            except Exception:
                pass

    def _maybe_open_system_screenshot_tool(self, event=None):
        """Fallback dispatcher for keyboards whose Print Screen keysym varies."""
        try:
            keysym = str(getattr(event, "keysym", "") or "").lower()
            keycode = int(getattr(event, "keycode", 0) or 0)
        except Exception:
            keysym = ""
            keycode = 0
        if keysym in {"print", "printscreen", "snapshot", "sys_req"} or keycode == 44:
            return self._open_system_screenshot_tool(event)
        return None

    def _open_system_screenshot_tool(self, event=None):
        """Open the operating system screenshot tool for Print Screen."""
        if os.name == "nt":
            try:
                try:
                    os.startfile("ms-screenclip:")  # type: ignore[attr-defined]
                except Exception:
                    subprocess.Popen(
                        ["explorer.exe", "ms-screenclip:"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=(subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0),
                    )
                self.status_bar.set(
                    self._t("已開啟 Windows 螢幕截圖工具", "Windows screenshot tool opened"),
                    "info",
                )
                print("[screenshot] opened Windows screen snipping tool")
                return "break"
            except Exception as exc:
                print(f"[screenshot] Failed to open Windows screen snipping tool: {exc!r}; falling back to app-window capture")
                return self._capture_app_screenshot(event)

        # Non-Windows fallback: preserve the app-window capture helper.
        return self._capture_app_screenshot(event)

    def _capture_app_screenshot(self, event=None):
        """Fallback capture of the visible OrchardBridge window.

        This is no longer bound to ordinary Print Screen on Windows.  It remains
        available as an emergency fallback if the Windows screen snipping URI
        cannot be opened.
        """
        try:
            from PIL import ImageGrab
            self.root.update_idletasks()
            x = int(self.root.winfo_rootx())
            y = int(self.root.winfo_rooty())
            w = max(1, int(self.root.winfo_width()))
            h = max(1, int(self.root.winfo_height()))
            img = ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True)
            shot_dir = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / APP_DISPLAY_NAME / "Screenshots"
            shot_dir.mkdir(parents=True, exist_ok=True)
            shot_path = shot_dir / f"OrchardBridge_screenshot_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            img.save(shot_path)

            copied = False
            if os.name == "nt":
                try:
                    import io
                    import win32clipboard
                    import win32con
                    output = io.BytesIO()
                    img.convert("RGB").save(output, "BMP")
                    data = output.getvalue()[14:]
                    output.close()
                    win32clipboard.OpenClipboard()
                    try:
                        win32clipboard.EmptyClipboard()
                        win32clipboard.SetClipboardData(win32con.CF_DIB, data)
                        copied = True
                    finally:
                        win32clipboard.CloseClipboard()
                except Exception as exc:
                    print(f"[screenshot] Clipboard image copy failed: {exc!r}")

            if not copied:
                try:
                    self.root.clipboard_clear()
                    self.root.clipboard_append(str(shot_path))
                except Exception:
                    pass

            if copied:
                msg = self._fmt_t(
                    "已儲存截圖並複製到剪貼簿：{path}",
                    "Screenshot saved and copied to clipboard: {path}",
                    path=shot_path,
                )
            else:
                msg = self._fmt_t(
                    "已儲存截圖；圖片剪貼簿不可用，已改複製檔案路徑：{path}",
                    "Screenshot saved; image clipboard was unavailable, so the file path was copied instead: {path}",
                    path=shot_path,
                )
            self.status_bar.set(msg, "success")
            print(f"[screenshot] {msg}")
            return "break"
        except Exception as exc:
            print(f"[screenshot] Capture failed: {exc!r}")
            try:
                self.status_bar.set(self._fmt_t("截圖失敗：{error}", "Screenshot failed: {error}", error=exc), "error")
            except Exception:
                pass
            return None

    def _setup_close_handler(self):
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _start_auto_connect_monitor(self):
        """Start background device auto-detection.

        When disconnected, the app attempts to connect twice per second.
        Once connected, it periodically checks whether the device is still
        reachable. If not, the app marks it disconnected and resumes fast
        auto-connect attempts.
        """
        if self._auto_connect_started:
            return
        self._auto_connect_started = True
        self.status_bar.set(self._t("自動偵測裝置中... 請用 USB 連接並點選『信任此電腦』", "Auto detecting device... Connect by USB and tap Trust This Computer"), "info")
        self.device_panel.update_device_info(None)
        self.root.after(100, self._auto_connect_tick)

    def _auto_connect_tick(self):
        if self._app_closing:
            return

        connected = self.device_manager.is_connected
        next_delay = CONNECTED_HEALTH_DELAY_MS if connected else self._disconnected_probe_delay_ms

        # Avoid piling up probes, and avoid probing while a long AFC operation
        # is already running. A failed scan/backup will mark the device state.
        busy = (
            self._auto_probe_running
            or self._scan_in_progress
            or self._photo_backup_in_progress
            or self._full_backup_in_progress
            or self._thumbnail_load_in_progress
        )
        if busy:
            self.root.after(next_delay, self._auto_connect_tick)
            return

        self._auto_probe_running = True
        mode = "health" if connected else "connect"

        def _do_probe():
            try:
                if mode == "health":
                    ok, msg = self.device_manager.check_connection()
                else:
                    ok, msg = self.device_manager.connect()
            except Exception as exc:
                ok, msg = False, repr(exc)
            try:
                self.root.after(0, lambda: self._after_auto_probe(mode, ok, msg))
            except Exception:
                pass

        threading.Thread(target=_do_probe, daemon=True).start()
        self.root.after(next_delay, self._auto_connect_tick)

    def _localized_device_probe_message(self, msg: str) -> tuple[str, str]:
        """Map language-neutral device-manager status codes to UI text."""
        if msg == STATUS_BRIDGE_MISSING:
            return (
                self._t(
                    "未偵測到 Apple Mobile Device / usbmux 連線橋接服務；Windows 檔案總管看得到裝置不代表此服務可用。請安裝或修復 Apple Devices/iTunes 所附的 Apple Mobile Device Support 後再試。",
                    "Apple Mobile Device / usbmux bridge was not detected. Windows Explorer visibility does not guarantee this service is available. Install or repair Apple Devices/iTunes Apple Mobile Device Support, then try again.",
                ),
                "warning",
            )
        if msg == STATUS_NO_DEVICE:
            return (
                self._t(
                    "等待裝置連線中... 請確認 USB、解鎖並信任此電腦",
                    "Waiting for device... Check USB, unlock the phone, and trust this computer",
                ),
                "info",
            )
        if msg == STATUS_CONNECTED:
            return (self._t("連線成功", "Connected"), "success")

        default_waiting = self._t(
            "等待裝置連線中... 請確認 USB、解鎖並信任此電腦",
            "Waiting for device... Check USB, unlock the phone, and trust this computer",
        )
        text = str(msg or "").strip() or default_waiting
        return (text, "warning" if text != default_waiting else "info")

    def _after_auto_probe(self, mode: str, ok: bool, msg: str):
        if self._app_closing:
            return
        self._auto_probe_running = False

        if ok:
            self._health_fail_count = 0
            info = self.device_manager.get_device_info()
            self.device_panel.update_device_info(info)
            self._update_device_dashboard(info)
            if mode == "connect":
                device_name = getattr(info, "name", "Device") or "Device"
                self.status_bar.set(
                    self._t("已自動連線：{device}", "Auto connected: {device}").format(device=device_name),
                    "success",
                )
            return

        # Failure while connected can be transient on Windows while Lockdown/AFC
        # transports are being cleaned up.  Do not immediately erase the model,
        # iOS version, or storage fields.  Only mark disconnected after repeated
        # consecutive health-check failures.
        if mode == "health":
            self._health_fail_count += 1
            if self._health_fail_count < 3:
                info = self.device_manager.get_device_info()
                self.device_panel.update_device_info(info)
                self._update_device_dashboard(info)
                self.status_bar.set(
                    self._t(
                        "裝置連線檢查暫時不穩，保留目前裝置資訊並繼續確認...",
                        "Device health check is temporarily unstable. Keeping current device info and checking again...",
                    ),
                    "warning",
                )
                return
            self.device_manager.disconnect()
            self.device_panel.update_device_info(None)
            self._update_device_dashboard(None)
            self.status_bar.set(self._t("裝置已斷線，恢復自動偵測中...", "Device disconnected. Resuming auto detection..."), "warning")
            return

        # Failure while disconnected is expected. Do not show modal dialogs.
        waiting, level = self._localized_device_probe_message(msg)
        if self._last_waiting_status != waiting:
            self._last_waiting_status = waiting
            self.status_bar.set(waiting, level)
        self.device_panel.update_device_info(None)

    def _on_connect(self):
        """Hidden compatibility hook. Auto-connect replaces the old button."""
        if not self.device_manager.is_connected:
            self._auto_connect_tick()

    def _after_connect(self, ok: bool, msg: str):
        """Hidden compatibility hook for older callbacks."""
        self._after_auto_probe("connect", ok, msg)

    def _on_disconnect(self):
        """Hidden compatibility hook. The app now disconnects automatically."""
        self.device_manager.disconnect()
        self.device_panel.update_device_info(None)
        self._update_device_dashboard(None)
        self.photo_grid.clear()
        self.photos = []
        self.backup_panel.update_count(0, 0)
        self.status_bar.set(self._t("已斷線，恢復自動偵測中...", "Disconnected. Resuming auto detection..."), "warning")

    def _on_scan_photos(self):
        """掃描照片"""
        if not self.device_manager.is_connected:
            messagebox.showwarning(self._t("未連線", "Not connected"), self._t("請先連線裝置！", "Please connect a device first."), parent=self.root)
            return

        print("[scan] Scan button clicked")
        self._scan_in_progress = True
        self.photo_grid.clear()
        self.photos = []
        self.backup_panel.update_count(0, 0)
        self.status_bar.set(self._t("正在掃描照片...", "Scanning media..."), "info")
        self.device_panel.set_scanning(True)
        try:
            self.photo_grid.set_scanning(True)
        except Exception:
            pass

        def _do():
            try:
                def on_progress(current, total, name):
                    def _update_scan_status(c=current, n=name):
                        template = self._t(
                            "掃描中... 已找到 {current} 個媒體檔案（{name}）",
                            "Scanning... found {current} media files ({name})",
                        )
                        self.status_bar.set(template.format(current=c, name=n), "info")
                    self.root.after(0, _update_scan_status)

                photos = self.device_manager.list_photos(
                    progress_callback=on_progress
                )
                self.root.after(0, lambda: self._after_scan(photos))
            except Exception as e:
                self.root.after(0, lambda: self._scan_error(str(e)))

        threading.Thread(target=_do, daemon=True).start()

    def _after_scan(self, photos: list[PhotoItem]):
        self._scan_in_progress = False
        self.device_panel.set_scanning(False)
        try:
            self.photo_grid.set_scanning(False)
        except Exception:
            pass
        print(f"[scan] UI received scan results: {len(photos)} media items")
        self.photos = photos
        self.photo_grid.set_photos(photos)
        self.backup_panel.update_count(len(photos), len(photos))
        template = self._t(
            "掃描完成，共找到 {count} 個媒體檔案",
            "Scan complete. Found {count} media files",
        )
        self.status_bar.set(template.format(count=len(photos)), "success")

        # 開始背景載入全部照片縮圖（影片顯示圖示，不產生縮圖）。
        self._thumbnail_load_in_progress = True
        threading.Thread(
            target=self._load_thumbnails_bg,
            args=(photos,),
            daemon=True,
        ).start()

    def _load_thumbnails_bg(self, photos: list[PhotoItem]):
        """在背景執行緒中批次載入縮圖（使用 MVP v3 核心）。"""
        if not self.device_manager.is_connected:
            return

        def on_thumb(photo, thumb):
            # 用 remote_path 更新，避免排序/篩選後 index 對錯照片。
            self.root.after(0, lambda p=photo, t=thumb: self.photo_grid.update_thumbnail_by_path(p.remote_path, t))

        def on_progress(msg: str):
            if msg:
                self.root.after(0, lambda m=msg: self.status_bar.set(m, "info"))

        try:
            self.device_manager.read_thumbnails_batch(
                photos,
                max_items=None,
                on_thumbnail=on_thumb,
                on_progress=on_progress,
            )
            self.root.after(0, lambda: self.status_bar.set(self._t("全部縮圖載入完成", "All thumbnails loaded"), "success"))
        except Exception as exc:
            self.root.after(0, lambda e=exc: self.status_bar.set(self._fmt_t("縮圖載入失敗：{error}", "Thumbnail loading failed: {error}", error=e), "warning"))
        finally:
            self._thumbnail_load_in_progress = False

    def _scan_error(self, msg: str):
        self._scan_in_progress = False
        self.device_panel.set_scanning(False)
        try:
            self.photo_grid.set_scanning(False)
        except Exception:
            pass
        lower = msg.lower()
        if "no device" in lower or "usbmux" in lower or "lockdown" in lower or "斷線" in msg:
            self.device_manager.disconnect()
            self.device_panel.update_device_info(None)
            self.status_bar.set(self._t("掃描失敗：裝置可能已斷線，恢復自動偵測中...", "Scan failed: the device may have disconnected; auto-detection is resuming..."), "warning")
        else:
            self.status_bar.set(self._fmt_t("掃描失敗：{msg}", "Scan failed: {msg}", msg=msg), "error")
        messagebox.showerror(self._t("掃描失敗", "Scan failed"), msg, parent=self.root)

    def _on_selection_change(self, selected_count: int):
        # Count selection from the actual data model, not from visible checkmark widgets.
        # This keeps the bottom counter correct after filtering/sorting.
        total = len(self.photos)
        selected = sum(1 for p in self.photos if p.selected)
        self.backup_panel.update_count(total, selected)

    def _on_select_all(self):
        for p in self.photos:
            p.selected = True
        self.photo_grid.refresh_selection()
        self.backup_panel.update_count(len(self.photos), len(self.photos))

    def _on_deselect_all(self):
        for p in self.photos:
            p.selected = False
        self.photo_grid.refresh_selection()
        self.backup_panel.update_count(len(self.photos), 0)

    def _open_path_in_explorer(self, path: Path):
        try:
            path = Path(path)
            if not path.exists():
                path = path.parent if path.parent.exists() else path
            if os.name == "nt":
                os.startfile(str(path))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as exc:
            messagebox.showwarning(self._t("無法開啟資料夾", "Cannot open folder"), str(exc), parent=self.root)

    def _open_cache_location(self):
        try:
            folder = Path(self.device_manager.cache_dir)
            folder.mkdir(parents=True, exist_ok=True)
            self._open_path_in_explorer(folder)
        except Exception as exc:
            messagebox.showwarning(self._t("無法開啟資料夾", "Cannot open folder"), str(exc), parent=self.root)

    def _open_original_cache_location(self):
        try:
            folder = Path(self.device_manager.cache_dir) / "originals"
            folder.mkdir(parents=True, exist_ok=True)
            self._open_path_in_explorer(folder)
        except Exception as exc:
            messagebox.showwarning(self._t("無法開啟資料夾", "Cannot open folder"), str(exc), parent=self.root)

    def _open_settings_location(self):
        try:
            path = Path(get_settings_path())
            path.parent.mkdir(parents=True, exist_ok=True)
            # The user asked to open the settings-file location, so open the
            # containing folder rather than trying to edit the JSON file.
            self._open_path_in_explorer(path.parent)
        except Exception as exc:
            messagebox.showwarning(self._t("無法開啟資料夾", "Cannot open folder"), str(exc), parent=self.root)

    def _open_latest_log_file(self):
        try:
            log_path = get_current_log_path() or get_latest_log_path()
            if not log_path or not Path(log_path).exists():
                messagebox.showwarning(
                    self._t("找不到 log 檔", "Log file not found"),
                    self._t("目前沒有可開啟的 log 檔。", "There is no log file to open right now."),
                    parent=self.root,
                )
                return
            self._open_path_in_explorer(Path(log_path))
        except Exception as exc:
            messagebox.showwarning(self._t("無法開啟 log 檔", "Cannot open log file"), str(exc), parent=self.root)

    def _clear_logs_now(self):
        try:
            log_dir = get_log_dir()
            logs = list(log_dir.glob("*.log")) + list(log_dir.glob("latest.txt"))
            count = len(logs)
            if not messagebox.askyesno(
                self._t("確認刪除", "Confirm delete"),
                self._fmt_t(
                    "即將刪除 log 資料夾中的 {count} 個 log 檔。\n\n資料夾：\n{folder}\n\n是否繼續？",
                    "You are about to delete {count} log file(s) from the log folder.\n\nFolder:\n{folder}\n\nContinue?",
                    count=count,
                    folder=log_dir,
                ),
                parent=self.root,
            ):
                return
            current = get_current_log_path()
            deleted = 0
            for path in logs:
                try:
                    # Do not delete the active log while this process is still writing to it.
                    # Truncate it instead so the user's request is honored without breaking logging.
                    if current and Path(path).resolve() == Path(current).resolve():
                        Path(path).write_text("", encoding="utf-8")
                    else:
                        Path(path).unlink(missing_ok=True)
                    deleted += 1
                except Exception as exc:
                    print(f"[logs] Failed to delete {path}: {exc!r}")
            msg = self._fmt_t("已清除 {count} 個 log 檔。", "Cleared {count} log file(s).", count=deleted)
            self.status_bar.set(msg, "success")
            messagebox.showinfo(self._t("完成", "Done"), msg, parent=self.root)
        except Exception as exc:
            messagebox.showerror(self._t("錯誤", "Error"), str(exc), parent=self.root)

    def _open_media_item(self, photo: PhotoItem):
        """Double-click preview: open cached/downloaded media with the OS default app."""
        if photo is None:
            return
        self.status_bar.set(self._fmt_t("正在開啟：{filename}", "Opening: {filename}", filename=photo.filename), "info")

        def _do():
            try:
                cached = self.device_manager.cached_original_path(photo)
                if not cached.exists():
                    cached.parent.mkdir(parents=True, exist_ok=True)
                    ok = self.device_manager.download_photo(photo, cached)
                    if not ok:
                        raise RuntimeError(self._t("無法從裝置讀取檔案", "Unable to read the file from the device"))
                self.root.after(0, lambda: self._open_path_in_explorer(cached))
            except Exception as exc:
                self.root.after(0, lambda e=exc: messagebox.showerror(self._t("開啟失敗", "Open failed"), str(e), parent=self.root))

        threading.Thread(target=_do, daemon=True).start()

    def _show_backup_complete_dialog(self, title: str, message: str, folder: Path):
        self._play_completion_sound()
        win = tk.Toplevel(self.root)
        win.title(title)
        win.configure(bg=THEME["surface"])
        win.resizable(False, False)
        win.transient(self.root)
        win.grab_set()
        tk.Label(
            win,
            text=message,
            bg=THEME["surface"],
            fg=THEME["text"],
            font=ui_font(10),
            justify="left",
            anchor="w",
            padx=22,
            pady=18,
            wraplength=520,
        ).pack(fill=tk.BOTH, expand=True)
        btns = tk.Frame(win, bg=THEME["surface"])
        btns.pack(fill=tk.X, padx=18, pady=(0, 16))
        def close():
            try:
                win.destroy()
            except Exception:
                pass
        ModernButton(
            btns,
            text=self._t("確定", "OK"),
            theme=THEME,
            style="secondary",
            command=close,
            width=14,
        ).pack(side=tk.RIGHT, padx=(8, 0))
        ModernButton(
            btns,
            text=self._t("瀏覽結果", "Open folder"),
            theme=THEME,
            style="primary",
            command=lambda: (self._open_path_in_explorer(folder), close()),
            width=16,
        ).pack(side=tk.RIGHT)
        win.update_idletasks()
        w, h = 600, max(220, win.winfo_reqheight())
        x = self.root.winfo_rootx() + max(0, (self.root.winfo_width() - w) // 2)
        y = self.root.winfo_rooty() + max(0, (self.root.winfo_height() - h) // 2)
        win.geometry(f"{w}x{h}+{x}+{y}")

    def _on_backup_photos(self):
        """開始照片備份"""
        selected = [p for p in self.photos if p.selected]
        if not selected:
            messagebox.showwarning(
                self._t("未選取", "Nothing selected"),
                self._t("請先選取要備份的照片！", "Please select the photos or videos to back up first."),
                parent=self.root,
            )
            return

        dest_path = self.backup_panel.dest_folder
        if not dest_path or not str(dest_path).strip():
            messagebox.showwarning(
                self._t("未設定位置", "Backup folder not set"),
                self._t("請先設定備份儲存位置！", "Please set the backup destination folder first."),
                parent=self.root,
            )
            return

        convert_heic = self.backup_panel.convert_heic
        output_format = self.backup_panel.output_format

        # 如果圖檔轉換選項未勾選，但有 HEIC 檔案，提示一次
        if not convert_heic:
            heic_count = sum(1 for p in selected if p.ext in (".heic", ".heif"))
            if heic_count > 0:
                convert_heic = messagebox.askyesno(
                    self._t("轉換 HEIC/HEIF？", "Convert HEIC/HEIF?"),
                    self._fmt_t(
                        "選取的媒體中有 {count} 張 HEIC/HEIF 格式。\n是否在備份後自動轉換為 {fmt} 圖檔？\n\n（原始 HEIC 檔案仍會保留，不影響備份）",
                        "The selected media contains {count} HEIC/HEIF file(s).\nConvert them to {fmt} after backup?\n\n(The original HEIC files will still be kept.)",
                        count=heic_count,
                        fmt=output_format,
                    ),
                    parent=self.root,
                )

        # 確認總覽
        msg = self._fmt_t(
            "即將備份 {count} 個媒體檔案\n儲存位置：{dest}",
            "Ready to back up {count} media file(s)\nSave location: {dest}",
            count=len(selected),
            dest=dest_path,
        )
        if convert_heic:
            msg += "\n" + self._fmt_t(
                "✓ 備份後將 HEIC/HEIF 轉換為 {fmt} 圖檔",
                "✓ HEIC/HEIF files will be converted to {fmt} after backup",
                fmt=output_format,
            )
        msg += "\n\n" + self._t("確定開始？", "Start now?")

        if not messagebox.askyesno(self._t("確認備份", "Confirm backup"), msg, parent=self.root):
            return

        self._run_photo_backup(selected, dest_path, convert_heic, output_format)

    def _run_photo_backup(
        self, photos: list[PhotoItem], dest: Path, convert_heic: bool, output_format: str = "JPEG"
    ):
        """執行照片備份（在進度視窗中）"""
        self._photo_backup_in_progress = True
        progress_win = BackupProgressWindow(
            self.root,
            theme=THEME,
            total=len(photos),
            on_cancel=self.backup_manager.cancel,
            t=self._t,
        )

        def _do():
            def on_progress(prog: BackupProgress):
                self.root.after(0, lambda: progress_win.update(prog))

            results = self.backup_manager.backup_photos(
                photos=photos,
                dest_folder=dest,
                convert_heic=convert_heic,
                output_format=output_format,
                progress_callback=on_progress,
            )
            self.root.after(0, lambda: self._backup_done(results, dest))

        threading.Thread(target=_do, daemon=True).start()

    def _backup_done(self, results, dest: Path):
        print(f"[backup] UI backup done, results={len(results)}, dest={dest}")
        self._photo_backup_in_progress = False
        success = sum(1 for r in results if r.status == "success")
        skipped = sum(1 for r in results if r.status == "skipped")
        errors = sum(1 for r in results if r.status == "error")
        converted_total = sum(1 for r in results if r.converted)
        converted_created = sum(1 for r in results if getattr(r, "converted_created", False))

        msg = self._fmt_t(
            "備份完成！\n\n✓ 成功：{success} 張\n⊘ 跳過（已存在）：{skipped} 張\n✗ 錯誤：{errors} 張\n",
            "Backup complete!\n\n✓ Successful: {success}\n⊘ Skipped (already existed): {skipped}\n✗ Errors: {errors}\n",
            success=success,
            skipped=skipped,
            errors=errors,
        )
        if converted_created:
            msg += self._fmt_t("🔄 本次轉換圖檔：{count} 張\n", "🔄 Converted this run: {count}\n", count=converted_created)
        elif converted_total:
            msg += self._fmt_t("🔄 轉檔結果已存在：{count} 張\n", "🔄 Converted files already existed: {count}\n", count=converted_total)
        msg += "\n" + self._fmt_t("儲存於：{dest}", "Saved to: {dest}", dest=dest)

        self.status_bar.set(
            self._fmt_t(
                "備份完成 ✓  成功 {success}  跳過 {skipped}  錯誤 {errors}",
                "Backup complete ✓  Successful {success}  Skipped {skipped}  Errors {errors}",
                success=success,
                skipped=skipped,
                errors=errors,
            ),
            "success",
        )
        self._show_backup_complete_dialog(self._t("備份完成", "Backup complete"), msg, dest)

    def _on_full_backup(self):
        """整機備份"""
        if not self.device_manager.is_connected:
            messagebox.showwarning(self._t("未連線", "Not connected"), self._t("請先連線裝置！", "Please connect a device first."), parent=self.root)
            return

        dest = self.full_backup_folder_var.get().strip()
        if not dest:
            messagebox.showwarning(
                self._t("未選擇", "No folder selected"),
                self._t("請選擇備份儲存位置！", "Please choose a backup destination folder."),
                parent=self.root,
            )
            return

        confirmed = messagebox.askyesno(
            self._t("開始整機備份", "Start full backup"),
            self._fmt_t(
                "整機備份可能需要先在裝置螢幕上輸入密碼。\n\n請確認：\n1. 裝置已解鎖並信任此電腦。\n2. 如果手機跳出要求，請輸入裝置密碼以允許備份。\n3. 備份期間請不要拔掉 USB。\n\n備份位置：\n{dest}\n\n確認後開始備份？",
                "Full backup may require entering the device passcode on the device screen.\n\nPlease confirm:\n1. The device is unlocked and trusts this computer.\n2. If prompted, enter the device passcode to allow backup.\n3. Do not unplug USB during backup.\n\nBackup folder:\n{dest}\n\nStart now?",
                dest=dest,
            ),
            parent=self.root,
        )
        if not confirmed:
            return

        self._full_backup_in_progress = True
        self.full_backup_btn.configure(state="disabled")
        self.full_progress_bar.set_value(2)
        self.full_progress_title.configure(text=self._t("整機備份進度：準備中", "Full backup progress: preparing"))
        self.full_progress_label.configure(text=self._t("正在初始化備份...", "Initializing backup..."))
        self.status_bar.set(self._t("正在執行整機備份...", "Running full backup..."), "info")

        dest_path = Path(dest)
        self._last_full_backup_parent = dest_path

        def _do():
            def on_progress(msg, pct):
                self.root.after(0, lambda: self._full_backup_progress(msg, pct))

            ok, result_msg = self.backup_manager.full_device_backup(
                dest_folder=dest_path,
                progress_callback=on_progress,
            )
            self.root.after(0, lambda: self._full_backup_done(ok, result_msg))

        threading.Thread(target=_do, daemon=True).start()

    def _clean_cli_text(self, text: str) -> str:
        """Remove ANSI color codes and carriage-return progress noise from CLI output."""
        import re
        text = str(text or "")
        text = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text)
        text = text.replace("\r", " ").replace("\x1b", "")
        text = re.sub(r"\[[0-9;]*m", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _full_backup_progress(self, msg: str, pct: float):
        msg = self._clean_cli_text(msg) or self._t("備份進行中...", "Backup in progress...")
        self.full_progress_label.configure(text=msg)
        if pct and pct > 0:
            shown = max(2.0, min(99.0, pct * 100.0))
            self.full_progress_bar.set_value(shown)
            self.full_progress_title.configure(text=self._fmt_t("整機備份進度：{pct:.0f}%", "Full backup progress: {pct:.0f}%", pct=shown))
        else:
            # pymobiledevice3 CLI often emits text without a reliable percent.
            # Keep a small visible bar so users know the task has started.
            self.full_progress_bar.set_value(5)
            self.full_progress_title.configure(text=self._t("整機備份進度：執行中", "Full backup progress: running"))

    def _full_backup_done(self, ok: bool, msg: str):
        self._full_backup_in_progress = False
        self.full_backup_btn.configure(state="normal")
        self.full_progress_bar.set_value(100 if ok else 0)
        self.full_progress_title.configure(text=self._t("整機備份進度：完成" if ok else "整機備份進度：失敗", "Full backup progress: complete" if ok else "Full backup progress: failed"))
        if ok and str(msg).startswith("備份完成，儲存於："):
            msg = self._fmt_t("備份完成，儲存於：{dest}", "Backup complete. Saved to: {dest}", dest=str(msg).split("：", 1)[-1])
        self.full_progress_label.configure(text=msg)

        if ok:
            self.status_bar.set(self._t("整機備份完成 ✓", "Full backup complete ✓"), "success")
            folder = getattr(self, "_last_full_backup_parent", Path.home())
            self._show_backup_complete_dialog(self._t("備份完成", "Backup complete"), msg, folder)
        else:
            self.status_bar.set(self._t("整機備份失敗", "Full backup failed"), "error")
            messagebox.showerror(self._t("備份失敗", "Backup failed"), msg, parent=self.root)

    def _browse_folder(self, var: tk.StringVar):
        folder = filedialog.askdirectory(parent=self.root)
        if folder:
            var.set(folder)

    def _on_close(self):
        # Optional close-to-tray behavior.  This keeps the current process and
        # current run log alive; no latest.txt snapshot is created until the user
        # really exits or creates a bug report.
        close_to_tray = bool(getattr(self.preferences, "close_to_tray_on_close", False))
        if hasattr(self, "close_to_tray_var"):
            try:
                close_to_tray = bool(self.close_to_tray_var.get())
            except Exception:
                pass
        if close_to_tray and not getattr(self, "_force_exit", False):
            print("[window] Close button pressed; hiding to tray/minimized state")
            # Persist the visible choice so it is kept across restarts even if
            # the user did not press Save before closing to tray.
            self._save_current_settings_on_exit()
            self._hide_to_tray()
            return
        self._really_exit()

    def _save_current_settings_on_exit(self):
        """Persist visible Settings-page values during real application exit."""
        try:
            if not hasattr(self, "language_var"):
                return
            prefs = self._preferences_from_ui()
            save_preferences(prefs)
            self.preferences = prefs
            self._saved_preferences_dict = prefs_to_dict(prefs)
            print(f"[settings] Saved current UI settings on exit: {self._saved_preferences_dict}")
        except Exception as exc:
            print(f"[settings] Failed to save settings on exit: {exc!r}")

    def _really_exit(self):
        self._app_closing = True
        print("[window] Exiting application")
        try:
            self._save_current_settings_on_exit()
        except Exception as exc:
            print(f"[window] Settings save-on-exit error: {exc!r}")
        try:
            self.backup_manager.cancel()
            self.device_manager.disconnect()
            delete_on_exit = False
            if hasattr(self, "delete_cache_on_exit_var"):
                delete_on_exit = bool(self.delete_cache_on_exit_var.get())
            if delete_on_exit:
                self.device_manager.clear_thumbnail_cache()
        except Exception as exc:
            print(f"[window] Exit cleanup error: {exc!r}")
        try:
            icon = getattr(self, "_tray_icon", None)
            if icon:
                icon.stop()
        except Exception:
            pass
        self.root.destroy()

    def _hide_to_tray(self):
        try:
            self.root.withdraw()
        except Exception:
            try:
                self.root.iconify()
            except Exception:
                pass
            return
        try:
            if not getattr(self, "_tray_icon", None):
                import pystray
                from PIL import Image, ImageDraw
                img = Image.new("RGB", (64, 64), (47, 124, 246))
                draw = ImageDraw.Draw(img)
                draw.rounded_rectangle((12, 10, 52, 54), radius=8, fill=(255, 255, 255))
                draw.rectangle((22, 18, 42, 46), outline=(47, 124, 246), width=3)

                def _show(_icon=None, _item=None):
                    self.root.after(0, self._restore_from_tray)

                def _exit(_icon=None, _item=None):
                    self._force_exit = True
                    self.root.after(0, self._really_exit)

                menu = pystray.Menu(
                    pystray.MenuItem(self._t("開啟", "Open"), _show, default=True),
                    pystray.MenuItem(self._t("結束", "Exit"), _exit),
                )
                self._tray_icon = pystray.Icon("OrchardBridge", img, "OrchardBridge", menu)
                threading.Thread(target=self._tray_icon.run, daemon=True).start()

            if not getattr(self, "_tray_notice_shown", False):
                self._tray_notice_shown = True
                msg = self._t(
                    "程式仍在背景執行，已縮小到 Windows 右下角系統匣。點系統匣圖示可重新開啟。",
                    "The app is still running in the background. It has been minimized to the Windows system tray.",
                )
                try:
                    self._tray_icon.notify(msg, self._t("OrchardBridge 已縮小", "OrchardBridge minimized"))
                except Exception:
                    self.status_bar.set(msg, "info")
        except Exception as exc:
            print(f"[window] System tray unavailable, minimized instead: {exc!r}")
            try:
                self.root.deiconify()
                self.root.iconify()
            except Exception:
                pass

    def _restore_from_tray(self):
        print("[window] Restored from tray/minimized state")
        try:
            self.root.deiconify()
            self.root.state("zoomed")
            self.root.lift()
            self.root.focus_force()
        except Exception:
            pass

    def run(self):
        # Start maximized by default.  If the platform rejects zoomed state,
        # fall back to full screen-size geometry without hiding the taskbar.
        self.root.update_idletasks()
        try:
            self.root.state("zoomed")
        except Exception:
            try:
                sw = self.root.winfo_screenwidth()
                sh = self.root.winfo_screenheight()
                self.root.geometry(f"{sw}x{max(720, sh-80)}+0+0")
            except Exception:
                self.root.geometry("1280x800")
        self.root.mainloop()


# ─────────────────────────────────────────
# 共用元件
# ─────────────────────────────────────────


class SideNavigationPanel:
    """FoneTool-like left navigation replacing the old device-information rail.

    The side rail must never rely on a single hard-coded width: Thai, Russian,
    German, and larger UI fonts can all require more room than Traditional
    Chinese or English.  The panel therefore measures the translated labels and
    resizes itself within a sensible min/max range; very long labels wrap
    instead of being clipped.
    """

    def __init__(self, parent, app, theme: dict, language: str = "en-US"):
        self.app = app
        self._theme = theme
        self._language = language
        self._nav_min_width = scaled_px(232)
        self._nav_max_width = scaled_px(430)
        self.frame = tk.Frame(parent, bg=theme.get("nav_bg", theme["surface"]), width=self._nav_min_width)
        self.frame.pack_propagate(False)
        self._buttons: list[tuple[tk.Frame, str]] = []
        self._nav_text_labels: list[tk.Label] = []
        self._status_label: tk.Label | None = None
        self._title_label: tk.Label | None = None
        self._build()
        try:
            self.frame.after_idle(self._resize_to_content)
        except Exception:
            pass

    def _t(self, zh: str, en: str) -> str:
        try:
            return self.app._t(zh, en)
        except Exception:
            return translate_text(getattr(self, "_language", "en-US"), zh, en)

    def _text_width(self, text: str, font_spec) -> int:
        try:
            return tkfont.Font(font=font_spec).measure(str(text))
        except Exception:
            return max(0, len(str(text)) * scaled_px(9))

    def _resize_to_content(self):
        """Resize the left panel based on translated text and current font size."""
        try:
            nav_font = ui_font(10, "bold")
            title_font = ui_font(12, "bold")
            widths = [self._text_width("OrchardBridge", title_font) + scaled_px(78)]
            widths.extend(self._text_width(lbl.cget("text"), nav_font) + scaled_px(92) for lbl in self._nav_text_labels)
            target = max([self._nav_min_width] + widths)
            target = min(self._nav_max_width, target)
            self.frame.configure(width=target)
            wrap_nav = max(scaled_px(110), target - scaled_px(92))
            for lbl in self._nav_text_labels:
                lbl.configure(wraplength=wrap_nav)
            if self._title_label is not None:
                self._title_label.configure(wraplength=max(scaled_px(120), target - scaled_px(66)))
            if self._status_label is not None:
                self._status_label.configure(wraplength=max(scaled_px(120), target - scaled_px(34)))
        except Exception:
            pass

    def _build(self):
        T = self._theme
        header = tk.Frame(self.frame, bg=T.get("nav_bg", T["surface"]))
        header.pack(fill=tk.X, padx=scaled_px(16), pady=(scaled_px(18), scaled_px(18)))
        tk.Label(
            header,
            text="▣",
            bg=T.get("nav_bg", T["surface"]),
            fg=T["accent"],
            font=ui_font(24, "bold"),
        ).pack(side=tk.LEFT, padx=(0, scaled_px(10)))
        self._title_label = tk.Label(
            header,
            text="OrchardBridge",
            bg=T.get("nav_bg", T["surface"]),
            fg=T["text"],
            justify="left",
            anchor="w",
            font=ui_font(12, "bold"),
        )
        self._title_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        items = [
            ("device_tab", "📱", self._t("我的裝置", "My device")),
            ("photo_tab", "📷", self._t("照片備份", "Photo backup")),
            ("full_tab", "💾", self._t("整機備份", "Full backup")),
            ("toolbox_tab", "🧰", self._t("小工具", "Toolbox")),
            ("settings_tab", "⚙", self._t("設定", "Settings")),
            ("about_tab", "ℹ", self._t("關於", "About")),
        ]
        for attr, icon, label in items:
            self._make_nav_button(attr, icon, label).pack(fill=tk.X, padx=scaled_px(12), pady=scaled_px(5))

        status = tk.Frame(self.frame, bg=T.get("nav_bg", T["surface"]))
        status.pack(side=tk.BOTTOM, fill=tk.X, padx=scaled_px(16), pady=(scaled_px(8), scaled_px(18)))
        tk.Label(
            status,
            text=self._t("連線狀態", "Connection"),
            bg=T.get("nav_bg", T["surface"]),
            fg=T["text_dim"],
            font=ui_font(9, "bold"),
            anchor="w",
            justify="left",
        ).pack(fill=tk.X, pady=(0, scaled_px(6)))
        self._status_label = tk.Label(
            status,
            text=self._t("● 自動偵測中", "● Auto detecting"),
            bg=T.get("nav_bg", T["surface"]),
            fg=T["warning"],
            font=ui_font(9),
            anchor="w",
            justify="left",
            wraplength=max(scaled_px(120), self._nav_min_width - scaled_px(34)),
        )
        self._status_label.pack(fill=tk.X)
        tk.Label(
            status,
            text=f"{APP_VERSION}",
            bg=T.get("nav_bg", T["surface"]),
            fg=T["text_dim"],
            font=ui_font(8),
            anchor="w",
        ).pack(fill=tk.X, pady=(scaled_px(12), 0))

    def _make_nav_button(self, tab_attr: str, icon: str, label: str):
        T = self._theme
        frame = tk.Frame(self.frame, bg=T.get("nav_bg", T["surface"]), cursor="hand2")
        inner = tk.Frame(frame, bg=T["surface"], cursor="hand2")
        inner.pack(fill=tk.X)
        tk.Label(
            inner,
            text=icon,
            bg=T["surface"],
            fg=T["accent"],
            font=ui_font(14),
            width=3,
        ).pack(side=tk.LEFT, padx=(scaled_px(8), scaled_px(2)), pady=scaled_px(10))
        text_label = tk.Label(
            inner,
            text=label,
            bg=T["surface"],
            fg=T["text"],
            font=ui_font(10, "bold"),
            anchor="w",
            justify="left",
        )
        text_label.pack(side=tk.LEFT, fill=tk.X, expand=True, pady=scaled_px(10))
        self._nav_text_labels.append(text_label)
        for w in (frame, inner) + tuple(inner.winfo_children()):
            w.bind("<Button-1>", lambda _e, a=tab_attr: self._select_tab(a))
        self._buttons.append((inner, tab_attr))
        return frame

    def _select_tab(self, tab_attr: str):
        try:
            tab = getattr(self.app, tab_attr)
            self.app.notebook.select(tab)
            self.refresh_buttons()
        except Exception:
            pass

    def refresh_buttons(self):
        T = self._theme
        current = None
        try:
            current_widget = self.app.notebook.nametowidget(self.app.notebook.select())
            for attr in ("device_tab", "photo_tab", "full_tab", "toolbox_tab", "settings_tab", "about_tab"):
                if getattr(self.app, attr, None) is current_widget:
                    current = attr
                    break
        except Exception:
            current = None
        for inner, attr in self._buttons:
            bg = T.get("nav_active", T["surface2"]) if attr == current else T["surface"]
            inner.configure(bg=bg)
            for child in inner.winfo_children():
                child.configure(bg=bg)
        self._resize_to_content()

    def update_device_info(self, info):
        if not self._status_label:
            return
        if info is None:
            self._status_label.configure(text=self._t("● 自動偵測中", "● Auto detecting"), fg=self._theme["warning"])
        else:
            name = getattr(info, "name", "Device") or "Device"
            text = self._t("● 已連線：{device}", "● Connected: {device}").format(device=name)
            self._status_label.configure(text=text, fg=self._theme["success"])
        self._resize_to_content()

    def set_scanning(self, state: bool):
        if self._status_label and state:
            self._status_label.configure(text=self._t("● 掃描中", "● Scanning"), fg=self._theme["accent2"])
            self._resize_to_content()

class ModernButton(tk.Frame):
    """現代風格按鈕"""

    def __init__(self, parent, text, theme, command, style="primary",
                 width=None, **kwargs):
        super().__init__(parent, bg=parent.cget("bg") if hasattr(parent, "cget") else theme["bg"])

        if style == "primary":
            bg = theme["accent"]
            hover_bg = theme["accent_hover"]
            fg = "#ffffff"
        elif style == "secondary":
            bg = theme["accent"]
            hover_bg = theme["accent_hover"]
            fg = "#ffffff"
        elif style == "danger":
            bg = theme["error"]
            hover_bg = "#dc2626"
            fg = "#ffffff"
        elif style == "success":
            bg = theme["accent"]
            hover_bg = theme["accent_hover"]
            fg = "#ffffff"
        else:
            bg = theme["surface2"]
            hover_bg = theme["surface"]
            fg = theme["text"]

        self._bg = bg
        self._hover_bg = hover_bg
        self._command = command
        self._state = "normal"

        btn_kwargs = dict(
            text=text,
            bg=bg,
            fg=fg,
            activebackground=hover_bg,
            activeforeground=fg,
            relief="flat",
            bd=0,
            highlightthickness=0,
            takefocus=False,
            cursor="hand2",
            font=ui_font(10, "bold"),
            pady=7,
            padx=16,
            command=command,
        )
        # Do not pass Tk's ``width`` option here.  It is measured in average
        # characters and becomes a hard clipping limit for long translations
        # or larger UI fonts.  The frame/button request their natural width
        # from the text plus padding, so callers may still pass ``width`` for
        # backward compatibility without risking truncated labels.
        self._min_char_width = width

        self._btn = tk.Button(self, **btn_kwargs)
        self._btn.pack(fill=tk.BOTH, expand=True)

        self._btn.bind("<Enter>", lambda e: self._on_enter())
        self._btn.bind("<Leave>", lambda e: self._on_leave())

    def _on_enter(self):
        if self._state == "normal":
            self._btn.configure(bg=self._hover_bg)

    def _on_leave(self):
        if self._state == "normal":
            self._btn.configure(bg=self._bg)

    def configure(self, **kwargs):
        state = kwargs.pop("state", None)
        if state is not None:
            self._state = state
            self._btn.configure(state=state)
            if state == "disabled":
                self._btn.configure(bg=self._bg, cursor="")
            else:
                self._btn.configure(cursor="hand2")
        if kwargs:
            self._btn.configure(**kwargs)

    def pack(self, **kwargs):
        super().pack(**kwargs)


class StatusBar:
    """底部狀態列"""

    COLOR_MAP_DARK = {
        "info": "#94a3b8",
        "success": "#22c55e",
        "error": "#ef4444",
        "warning": "#f59e0b",
    }
    COLOR_MAP_LIGHT = {
        "info": "#334155",
        "success": "#15803d",
        "error": "#b91c1c",
        "warning": "#b45309",
    }

    def __init__(self, parent, theme):
        self.frame = tk.Frame(parent, bg=theme["surface"], height=scaled_px(34))
        self.frame.pack_propagate(False)

        self._label = tk.Label(
            self.frame,
            text="就緒  ·  請連線裝置",
            bg=theme["surface"],
            fg=theme["text_dim"],
            font=ui_font(9),
            anchor="w",
            padx=scaled_px(12),
        )
        self._label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self._theme = theme

    def set(self, text: str, level: str = "info"):
        color_map = self.COLOR_MAP_DARK if self._theme.get("mode") == "dark" else self.COLOR_MAP_LIGHT
        color = color_map.get(level, self._theme["text_dim"])
        self._label.configure(text=f"  {text}", fg=color)
        try:
            print(f"[status:{level}] {text}")
        except Exception:
            pass


class BackupProgressWindow:
    """備份進度彈出視窗"""

    def __init__(self, parent, theme, total: int, on_cancel=None, t=None):
        self._t = t or (lambda zh, en: en)
        self.win = tk.Toplevel(parent)
        self.win.title(self._t("備份進度", "Backup progress"))
        self.win.geometry("520x300")
        self.win.resizable(False, False)
        self.win.configure(bg=theme["bg"])
        self.win.grab_set()
        self.win.focus_set()

        # 置中
        self.win.update_idletasks()
        pw = parent.winfo_rootx() + (parent.winfo_width() - 520) // 2
        ph = parent.winfo_rooty() + (parent.winfo_height() - 300) // 2
        self.win.geometry(f"520x300+{pw}+{ph}")

        self._theme = theme
        self._total = total
        self._done = False

        # 標題
        tk.Label(
            self.win,
            text=self._t("⏳  備份進行中...", "⏳  Backup in progress..."),
            bg=theme["bg"],
            fg=theme["text"],
            font=ui_font(13, "bold"),
        ).pack(pady=(24, 8), padx=24, anchor="w")

        # 目前檔案
        self.file_label = tk.Label(
            self.win,
            text=self._t("準備中...", "Preparing..."),
            bg=theme["bg"],
            fg=theme["text_dim"],
            font=ui_font(9),
            anchor="w",
            wraplength=460,
        )
        self.file_label.pack(padx=24, anchor="w")

        # 進度條
        self.bar = ttk.Progressbar(
            self.win,
            style="Accent.Horizontal.TProgressbar",
            mode="determinate",
            maximum=100,
        )
        self.bar.pack(fill=tk.X, padx=24, pady=16)

        # 數字
        self.count_label = tk.Label(
            self.win,
            text=f"0 / {total}",
            bg=theme["bg"],
            fg=theme["text"],
            font=ui_font(11, "bold"),
        )
        self.count_label.pack()

        # 摘要
        self.summary_label = tk.Label(
            self.win,
            text="",
            bg=theme["bg"],
            fg=theme["text_dim"],
            font=ui_font(9),
        )
        self.summary_label.pack(pady=4)

        # 取消按鈕
        if on_cancel:
            cancel_btn = tk.Button(
                self.win,
                text=self._t("取消", "Cancel"),
                bg=theme["error"],
                fg="#fff",
                relief="flat",
                font=ui_font(10),
                pady=6,
                padx=24,
                command=lambda: (on_cancel(), self._mark_done()),
            )
            cancel_btn.pack(pady=(8, 0))

    def update(self, prog: BackupProgress):
        if self._done:
            return

        pct = prog.percent * 100
        self.bar.configure(value=pct)
        self.count_label.configure(text=f"{prog.done} / {prog.total}")
        self.file_label.configure(text=prog.current_file)
        self.summary_label.configure(text=self._t(
            "完成 {done}/{total}  ✓ {success}  ⊘ {skipped}  ✗ {errors}",
            "Done {done}/{total}  ✓ {success}  ⊘ {skipped}  ✗ {errors}",
        ).format(done=prog.done, total=prog.total, success=prog.success, skipped=prog.skipped, errors=prog.errors))

        if prog.finished or prog.cancelled:
            self._mark_done()

    def _mark_done(self):
        self._done = True
        try:
            self.win.destroy()
        except Exception:
            pass
