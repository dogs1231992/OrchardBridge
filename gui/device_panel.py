"""
左側設備資訊面板

Auto-connect edition:
- 不再顯示「連線 / 斷線」按鈕。
- App 啟動後由主程式背景自動偵測裝置。
"""

import tkinter as tk
from core.ui_fonts import ui_font
from core.i18n import load_locale_table, translate_text
from tkinter import ttk
from typing import Callable


class StorageUsageBar(tk.Canvas):
    """Small custom storage bar: used space fills from left, free space remains muted.

    Tkinter/ttk progress bars are difficult to style consistently across Windows
    themes, so this canvas gives us deterministic dark-theme rendering.
    """

    def __init__(self, parent, theme: dict, width: int = 188, height: int = 16):
        super().__init__(
            parent,
            width=width,
            height=height,
            bg=theme["surface"],
            highlightthickness=0,
            bd=0,
        )
        self._theme = theme
        self._used_pct = 0.0
        self._free_pct = 0.0
        self._height = height
        self.bind("<Configure>", lambda _e: self._draw())
        self._draw()

    def set_value(self, used_pct: float, free_pct: float | None = None):
        try:
            used_pct = float(used_pct)
        except Exception:
            used_pct = 0.0
        used_pct = max(0.0, min(100.0, used_pct))
        if free_pct is None:
            free_pct = max(0.0, 100.0 - used_pct)
        self._used_pct = used_pct
        self._free_pct = max(0.0, min(100.0, float(free_pct)))
        self._draw()

    def _used_color(self) -> str:
        # Calm purple normally, warmer colors only when storage is getting tight.
        if self._used_pct >= 90:
            return self._theme["error"]
        if self._used_pct >= 75:
            return self._theme["warning"]
        return self._theme["accent"]

    def _draw(self):
        self.delete("all")
        w = max(1, int(self.winfo_width() or self.cget("width") or 188))
        h = max(12, int(self.winfo_height() or self._height))
        pad = 1
        x0, y0 = pad, pad
        x1, y1 = w - pad, h - pad
        usable_w = max(1, x1 - x0)
        used_w = int(round(usable_w * self._used_pct / 100.0))

        # Free/background section.
        self.create_rectangle(x0, y0, x1, y1, fill=self._theme["surface2"], outline=self._theme["border"], width=1)

        # Used section.  For very small non-zero percentages, draw at least 2 px.
        if self._used_pct > 0:
            used_w = max(2, used_w)
            self.create_rectangle(x0, y0, min(x0 + used_w, x1), y1, fill=self._used_color(), outline="")

        # Subtle top highlight and border.
        self.create_line(x0 + 1, y0 + 1, x1 - 1, y0 + 1, fill="#4a4a66")
        self.create_rectangle(x0, y0, x1, y1, outline=self._theme["border"], width=1)


class DevicePanelFrame:
    """左側欄：設備資訊 + 自動連線狀態 + 操作"""

    def __init__(
        self,
        parent,
        theme: dict,
        on_connect: Callable,
        on_disconnect: Callable,
        on_scan_photos: Callable,
        on_full_backup: Callable,
        language: str = "en-US",
    ):
        self._theme = theme
        self._on_connect = on_connect
        self._on_disconnect = on_disconnect
        self._on_scan_photos = on_scan_photos
        self._on_full_backup = on_full_backup
        self._language = language
        self._locale_table = load_locale_table(language)

        self.frame = tk.Frame(parent, bg=theme["surface"], width=255)
        self.frame.pack_propagate(False)

        self._build()

    def _t(self, zh: str, en: str) -> str:
        return translate_text(getattr(self, "_language", "en-US"), zh, en, getattr(self, "_locale_table", {}) or None)

    def _fmt_t(self, zh: str, en: str, **kwargs) -> str:
        try:
            return self._t(zh, en).format(**kwargs)
        except Exception:
            return en.format(**kwargs)

    def _build(self):
        T = self._theme
        f = self.frame

        tk.Label(
            f,
            text=self._t("設備", "Device"),
            bg=T["surface"],
            fg=T["text_dim"],
            font=ui_font(9, "bold"),
            anchor="w",
            padx=16,
            pady=12,
        ).pack(fill=tk.X)

        icon_frame = tk.Frame(f, bg=T["surface"])
        icon_frame.pack(pady=(8, 0))

        self._device_icon = tk.Label(
            icon_frame,
            text="📵",
            bg=T["surface"],
            fg=T["text_dim"],
            font=ui_font(36),
        )
        self._device_icon.pack()

        self._name_label = tk.Label(
            f,
            text=self._t("等待裝置", "Waiting for device"),
            bg=T["surface"],
            fg=T["text"],
            font=ui_font(11, "bold"),
            wraplength=225,
        )
        self._name_label.pack(pady=(6, 2))

        self._model_label = tk.Label(
            f,
            text=self._t("請用 USB 連接並信任此電腦", "Connect via USB and trust this computer"),
            bg=T["surface"],
            fg=T["text_dim"],
            font=ui_font(9),
            wraplength=225,
        )
        self._model_label.pack()

        self._ios_label = tk.Label(
            f,
            text="",
            bg=T["surface"],
            fg=T["text_dim"],
            font=ui_font(9),
        )
        self._ios_label.pack(pady=(0, 4))

        self._storage_frame = tk.Frame(f, bg=T["surface"])
        self._storage_frame.pack(fill=tk.X, padx=16, pady=(8, 0))

        self._storage_label = tk.Label(
            self._storage_frame,
            text="",
            bg=T["surface"],
            fg=T["text_dim"],
            font=ui_font(8),
        )
        self._storage_label.pack(anchor="w")

        self._storage_bar = StorageUsageBar(self._storage_frame, theme=T, width=220, height=16)
        self._storage_bar.pack(fill=tk.X, pady=4)

        self._battery_label = tk.Label(
            f,
            text="",
            bg=T["surface"],
            fg=T["text_dim"],
            font=ui_font(9),
        )
        self._battery_label.pack(pady=(0, 8))

        tk.Frame(f, bg=T["border"], height=1).pack(fill=tk.X, padx=16, pady=8)

        tk.Label(
            f,
            text=self._t("連線狀態", "Connection status"),
            bg=T["surface"],
            fg=T["text_dim"],
            font=ui_font(9, "bold"),
            anchor="w",
            padx=16,
        ).pack(fill=tk.X, pady=(0, 4))

        self._status_dot = tk.Label(
            f,
            text=self._t("● 自動偵測中...", "● Auto detecting..."),
            bg=T["surface"],
            fg=T["warning"],
            font=ui_font(9),
            wraplength=225,
            justify="center",
        )
        self._status_dot.pack(pady=(4, 8), padx=12)

        tk.Frame(f, bg=T["border"], height=1).pack(fill=tk.X, padx=16, pady=8)

        tk.Label(
            f,
            text=self._t("操作", "Actions"),
            bg=T["surface"],
            fg=T["text_dim"],
            font=ui_font(9, "bold"),
            anchor="w",
            padx=16,
        ).pack(fill=tk.X, pady=(0, 4))

        self._scan_btn = self._make_btn(
            f, self._t("📷  掃描照片", "📷  Scan photos"), T["accent2"], self._on_scan_photos
        )
        self._scan_btn.pack(fill=tk.X, padx=16, pady=4)
        self._scan_btn.configure(state="disabled")

        tk.Label(
            f,
            text=self._t("OrchardBridge\nv1.2026.06.23", "OrchardBridge\nv1.2026.06.23"),
            bg=T["surface"],
            fg=T["border"],
            font=ui_font(8),
            justify="center",
        ).pack(side=tk.BOTTOM, pady=8)

    def _make_btn(self, parent, text, color, command):
        return tk.Button(
            parent,
            text=text,
            bg=color,
            fg="#ffffff",
            activebackground=color,
            activeforeground="#ffffff",
            relief="flat",
            font=ui_font(10),
            pady=8,
            cursor="hand2",
            command=command,
        )

    def update_device_info(self, info):
        """更新設備資訊顯示"""
        T = self._theme
        if info is None:
            self._device_icon.configure(text="📵", fg=T["text_dim"])
            self._name_label.configure(text=self._t("等待裝置", "Waiting for device"), fg=T["text_dim"])
            self._model_label.configure(text=self._t("請用 USB 連接並信任此電腦", "Connect via USB and trust this computer"))
            self._ios_label.configure(text="")
            self._storage_label.configure(text="")
            self._storage_bar.set_value(0, 0)
            self._battery_label.configure(text="")
            self._scan_btn.configure(state="disabled")
            self._status_dot.configure(text=self._t("● 自動偵測中...", "● Auto detecting..."), fg=T["warning"])
        else:
            self._device_icon.configure(text="📱", fg=T["accent"])
            self._name_label.configure(text=info.name, fg=T["text"])
            self._model_label.configure(text=info.model if info.model else "Device")
            self._ios_label.configure(text=f"iOS {info.ios_version}" if info.ios_version else "")

            if info.storage_total > 0:
                used_pct = min(100, info.storage_used / info.storage_total * 100)
                free_pct = max(0, 100 - used_pct)
                self._storage_label.configure(text=self._fmt_t("空間：{storage}\n已用 {used_pct:.1f}%", "Storage: {storage}\nUsed {used_pct:.1f}%", storage=info.storage_str, used_pct=used_pct))
                self._storage_bar.set_value(used_pct, free_pct)
            else:
                self._storage_label.configure(text="")
                self._storage_bar.set_value(0, 0)

            if info.battery_level > 0:
                self._battery_label.configure(text=self._fmt_t("🔋 電量：{level}%", "🔋 Battery: {level}%", level=info.battery_level))
            else:
                self._battery_label.configure(text="")

            self._scan_btn.configure(state="normal")
            self._status_dot.configure(text=self._t("● 已連線", "● Connected"), fg=T["success"])

    def set_auto_connecting(self, state: bool):
        T = self._theme
        if state:
            self._status_dot.configure(text=self._t("● 自動偵測中...", "● Auto detecting..."), fg=T["warning"])
        else:
            self._status_dot.configure(text=self._t("● 已連線", "● Connected"), fg=T["success"])

    def set_connecting(self, state: bool):
        # 保留相容性：舊版 app.py 可能仍會呼叫此方法。
        self.set_auto_connecting(state)

    def set_scanning(self, state: bool):
        if state:
            self._scan_btn.configure(text=self._t("掃描中...", "Scanning..."), state="disabled")
        else:
            if self._device_icon.cget("text") == "📱":
                self._scan_btn.configure(text=self._t("📷  掃描照片", "📷  Scan photos"), state="normal")
            else:
                self._scan_btn.configure(text=self._t("📷  掃描照片", "📷  Scan photos"), state="disabled")
