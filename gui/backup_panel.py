"""
備份操作面板（照片分頁底部）
顯示已選張數、設定選項、觸發備份
"""

import tkinter as tk
from core.ui_fonts import ui_font, scaled_px
from core.i18n import load_locale_table, translate_text
from tkinter import ttk, filedialog
from typing import Callable
from pathlib import Path


class BackupPanelFrame:
    """
    照片分頁底部的操作列：
    - 全選 / 全不選
    - 已選張數顯示
    - 輸出資料夾選擇
    - HEIC 轉換選項
    - 開始備份按鈕
    """


    def _t(self, zh: str, en: str) -> str:
        return translate_text(getattr(self, "_language", "en-US"), zh, en, getattr(self, "_locale_table", {}) or None)

    def __init__(
        self,
        parent,
        theme: dict,
        on_backup: Callable,
        on_select_all: Callable,
        on_deselect_all: Callable,
        default_dest: Path | str | None = None,
        default_convert_heic: bool = False,
        default_output_format: str = "JPEG",
        language: str = "en-US",
    ):
        self._theme = theme
        self._on_backup = on_backup
        self._on_select_all = on_select_all
        self._on_deselect_all = on_deselect_all
        self._language = language
        self._locale_table = load_locale_table(language)
        self._default_dest = Path(default_dest) if default_dest else (Path.home() / "OrchardBridgePhotosBackup")
        self._default_convert_heic = bool(default_convert_heic)
        self._default_output_format = "PNG" if str(default_output_format).upper() == "PNG" else "JPEG"

        T = theme

        # ── 外框 ──
        self.frame = tk.Frame(parent, bg=T["surface"])

        # ── 上分隔線 ──
        tk.Frame(self.frame, bg=T["border"], height=1).pack(fill=tk.X)

        # ── 內容列 ──
        inner = tk.Frame(self.frame, bg=T["surface"])
        inner.pack(fill=tk.BOTH, expand=True, padx=scaled_px(16), pady=scaled_px(18))

        # ── 左側：全選 / 全不選 + 張數 ──
        left = tk.Frame(inner, bg=T["surface"])
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(0, scaled_px(8)))

        # 全選按鈕
        self._sel_all_btn = tk.Button(
            left,
            text=self._t("全選", "Select all"),
            bg=T["surface2"],
            fg=T["text"],
            activebackground=T["border"],
            activeforeground=T["text"],
            relief="flat",
            font=ui_font(9),
            padx=10,
            pady=4,
            cursor="hand2",
            command=on_select_all,
        )
        self._sel_all_btn.pack(side=tk.LEFT, padx=(0, 4))

        # 全不選按鈕
        self._desel_btn = tk.Button(
            left,
            text=self._t("全不選", "Deselect all"),
            bg=T["surface2"],
            fg=T["text"],
            activebackground=T["border"],
            activeforeground=T["text"],
            relief="flat",
            font=ui_font(9),
            padx=10,
            pady=4,
            cursor="hand2",
            command=on_deselect_all,
        )
        self._desel_btn.pack(side=tk.LEFT, padx=(0, 16))

        # 張數標籤
        self._count_label = tk.Label(
            left,
            text=self._t("尚未掃描", "Not scanned"),
            bg=T["surface"],
            fg=T["text_dim"],
            font=ui_font(10),
        )
        self._count_label.pack(side=tk.LEFT, padx=(0, 8))

        # ── 中間：輸出位置 ──
        mid = tk.Frame(inner, bg=T["surface"])
        mid.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=scaled_px(8))

        tk.Label(
            mid,
            text=self._t("備份位置：", "Backup folder:"),
            bg=T["surface"],
            fg=T["text_dim"],
            font=ui_font(9),
        ).pack(side=tk.LEFT)

        self._folder_var = tk.StringVar(value=str(self._default_dest))
        folder_entry = tk.Entry(
            mid,
            textvariable=self._folder_var,
            bg=T["surface2"],
            fg=T["text"],
            insertbackground=T["text"],
            relief="flat",
            font=ui_font(9),
            # Let the entry shrink/expand with the available space; a hard width can push later buttons off-screen.
            width=24,
        )
        folder_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(scaled_px(4), scaled_px(6)), ipady=scaled_px(4))

        browse_btn = tk.Button(
            mid,
            text=self._t("瀏覽…", "Browse…"),
            bg=T["surface2"],
            fg=T["text"],
            activebackground=T["border"],
            activeforeground=T["text"],
            relief="flat",
            font=ui_font(9),
            padx=8,
            pady=4,
            cursor="hand2",
            command=self._browse_folder,
        )
        browse_btn.pack(side=tk.LEFT, padx=(0, scaled_px(10)))

        # ── HEIC 轉換選項 ──
        self._convert_var = tk.BooleanVar(value=self._default_convert_heic)

        heic_frame = tk.Frame(inner, bg=T["surface"])
        heic_frame.pack(side=tk.LEFT, fill=tk.Y)

        # 用 tk.Checkbutton 而非 ttk（更好控制顏色）
        self._heic_check = tk.Checkbutton(
            heic_frame,
            text=self._t("備份後轉圖檔", "Convert image after backup"),
            variable=self._convert_var,
            bg=T["surface"],
            fg=T["text"],
            selectcolor=("#111827" if T.get("mode") == "dark" else T["surface2"]),
            activebackground=T["surface"],
            activeforeground=T["text"],
            font=ui_font(10),
            cursor="hand2",
        )
        self._heic_check.pack(anchor="w", pady=(0, scaled_px(4)))

        fmt_row = tk.Frame(heic_frame, bg=T["surface"])
        fmt_row.pack(anchor="w", pady=(0, scaled_px(4)))
        tk.Label(
            fmt_row,
            text=self._t("格式：", "Format:"),
            bg=T["surface"],
            fg=T["text_dim"],
            font=ui_font(10),
        ).pack(side=tk.LEFT)
        self._format_var = tk.StringVar(value=self._default_output_format)
        self._format_combo = ttk.Combobox(
            fmt_row,
            textvariable=self._format_var,
            values=["JPEG", "PNG"],
            state="readonly",
            width=8,
            style="App.TCombobox",
            font=ui_font(10),
            takefocus=False,
        )
        self._format_combo.pack(side=tk.LEFT, ipady=scaled_px(2))
        self._format_combo.bind("<<ComboboxSelected>>", lambda e: self._format_combo.after(50, lambda: (self._format_combo.selection_clear(), self._format_combo.icursor(tk.END), self.frame.focus_set())), add="+")

        tk.Label(
            heic_frame,
            text=self._t("（HEIC/HEIF → 圖檔，保留原檔）", "(HEIC/HEIF → image file, originals kept)"),
            bg=T["surface"],
            fg=T["text_dim"],
            font=ui_font(8),
            wraplength=scaled_px(280),
            justify="left",
        ).pack(anchor="w", pady=(0, scaled_px(2)))

        # ── 右側：備份按鈕 ──
        right = tk.Frame(inner, bg=T["surface"])
        right.pack(side=tk.RIGHT, fill=tk.Y)

        self._backup_btn = tk.Button(
            right,
            text=self._t("  ▶  開始備份", "  ▶  Start backup"),
            bg=T["accent"],
            fg="#ffffff",
            activebackground=T["accent_hover"],
            activeforeground="#ffffff",
            relief="flat",
            font=ui_font(11, "bold"),
            padx=20,
            pady=10,
            cursor="hand2",
            command=self._on_click_backup,
        )
        self._backup_btn.pack(side=tk.RIGHT)

        self._backup_btn.bind(
            "<Enter>",
            lambda e: self._backup_btn.configure(bg=T["accent_hover"])
        )
        self._backup_btn.bind(
            "<Leave>",
            lambda e: self._backup_btn.configure(bg=T["accent"])
        )

    def _browse_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self._folder_var.set(folder)

    def _on_click_backup(self):
        self._on_backup()

    def set_preferences(self, default_dest: Path | str | None = None, default_convert_heic: bool | None = None, default_output_format: str | None = None):
        """Apply saved preferences to the bottom backup controls immediately."""
        if default_dest is not None:
            self._folder_var.set(str(default_dest))
        if default_convert_heic is not None:
            self._convert_var.set(bool(default_convert_heic))
        if default_output_format is not None and hasattr(self, "_format_var"):
            self._format_var.set("PNG" if str(default_output_format).upper() == "PNG" else "JPEG")

    @property
    def dest_folder(self) -> Path:
        return Path(self._folder_var.get())

    @property
    def convert_heic(self) -> bool:
        return self._convert_var.get()

    @property
    def output_format(self) -> str:
        return "PNG" if str(self._format_var.get()).upper() == "PNG" else "JPEG"

    def update_count(self, total: int, selected: int):
        """更新張數顯示"""
        T = self._theme
        if total == 0:
            self._count_label.configure(
                text=self._t("尚未掃描", "Not scanned"),
                fg=T["text_dim"],
            )
        else:
            color = T["accent"] if selected > 0 else T["text_dim"]
            template = self._t("已選 {selected} / {total} 個", "Selected {selected} / {total}")
            self._count_label.configure(
                text=template.format(selected=selected, total=total),
                fg=color,
            )

        # 有選取才能點備份
        has_sel = selected > 0
        self._backup_btn.configure(
            state="normal" if has_sel else "disabled",
            bg=T["accent"] if has_sel else T["surface2"],
            cursor="hand2" if has_sel else "",
        )
