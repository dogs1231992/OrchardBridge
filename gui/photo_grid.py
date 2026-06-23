"""
照片網格顯示元件
支援縮圖預覽、多選、滾動
"""

import tkinter as tk
from tkinter import ttk
import gc
from typing import Optional, Callable
from pathlib import Path

from core.device_manager import PhotoItem
from core.ui_fonts import ui_font
from core.i18n import load_locale_table, translate_text

# 每個縮圖格子的大小
# Keep the thumbnail inside its own preview box so it never covers the filename,
# file size, or selection checkmark.
CELL_W = 150
CELL_H = 190
THUMB_W = 126
THUMB_H = 112
THUMB_TOP = 18
NAME_Y = 150
SIZE_Y = 167
COLS = 0  # 自動計算


class PhotoCell:
    """單個照片格子"""

    def __init__(self, canvas: tk.Canvas, x: int, y: int, photo: PhotoItem,
                 theme: dict, on_toggle: Callable, on_open: Callable | None = None):
        self._canvas = canvas
        self._photo = photo
        self._theme = theme
        self._on_toggle = on_toggle
        self._on_open = on_open
        self._click_after_id = None
        self._tk_image = None  # 保持參考防止 GC
        self._selected = photo.selected

        # 格子背景
        pad = 6
        self._rect = canvas.create_rectangle(
            x + pad, y + pad,
            x + CELL_W - pad, y + CELL_H - pad,
            fill=theme["surface"],
            outline=theme["border"],
            width=1,
            tags=("cell", f"cell_{id(photo)}")
        )

        # 縮圖佔位符
        self._img_item = canvas.create_rectangle(
            x + (CELL_W - THUMB_W) // 2,
            y + THUMB_TOP,
            x + (CELL_W - THUMB_W) // 2 + THUMB_W,
            y + THUMB_TOP + THUMB_H,
            fill=theme["surface2"],
            outline="",
            tags=("thumb_bg",)
        )

        # 載入中文字
        self._loading_text = canvas.create_text(
            x + CELL_W // 2,
            y + THUMB_TOP + THUMB_H // 2,
            text="📷" if not photo.is_video else "🎬",
            fill=theme["text_dim"],
            font=ui_font(18),
            tags=("loading",)
        )

        # 圖片項目（待載入）
        self._img_display = None

        # 檔名（截斷）
        name = photo.filename
        if len(name) > 16:
            name = name[:13] + "..."
        self._name_item = canvas.create_text(
            x + CELL_W // 2,
            y + NAME_Y,
            text=name,
            fill=theme["text_dim"],
            font=ui_font(8),
            tags=("name",)
        )

        # 大小
        self._size_item = canvas.create_text(
            x + CELL_W // 2,
            y + SIZE_Y,
            text=photo.size_str,
            fill=theme["text_dim"],
            font=ui_font(7),
            tags=("size",)
        )

        # 勾選框
        self._check_bg = canvas.create_rectangle(
            x + CELL_W - 28, y + 12,
            x + CELL_W - 10, y + 30,
            fill=theme["surface2"],
            outline=theme["border"],
            width=1,
        )
        self._check_text = canvas.create_text(
            x + CELL_W - 19, y + 21,
            text="✓" if photo.selected else "",
            fill=theme["success"],
            font=ui_font(12, "bold"),
        )

        # 影片標籤
        self._video_badge_items = []
        if photo.is_video:
            self._video_badge_items.append(canvas.create_rectangle(
                x + 12, y + THUMB_TOP + 2,
                x + 38, y + THUMB_TOP + 16,
                fill="#000000",
                outline="",
            ))
            self._video_badge_items.append(canvas.create_text(
                x + 25, y + THUMB_TOP + 9,
                text="▶",
                fill="#fff",
                font=ui_font(7),
            ))

        # 點擊事件：單擊切換勾選，雙擊開啟檔案。
        # 單擊延遲一點點執行，讓雙擊可以取消單擊的勾選動作。
        for tag in (self._rect, self._img_item, self._loading_text,
                    self._name_item, self._size_item, self._check_bg, self._check_text):
            canvas.tag_bind(tag, "<Button-1>", self._single_click)
            canvas.tag_bind(tag, "<Double-Button-1>", self._double_click)

        self._x = x
        self._y = y
        self._update_style()

    def _single_click(self, event=None):
        try:
            if self._click_after_id is not None:
                self._canvas.after_cancel(self._click_after_id)
        except Exception:
            pass
        self._click_after_id = self._canvas.after(220, self._toggle)
        return "break"

    def _double_click(self, event=None):
        try:
            if self._click_after_id is not None:
                self._canvas.after_cancel(self._click_after_id)
                self._click_after_id = None
        except Exception:
            pass
        if self._on_open:
            self._on_open(self._photo)
        return "break"

    def _bind_item(self, item):
        self._canvas.tag_bind(item, "<Button-1>", self._single_click)
        self._canvas.tag_bind(item, "<Double-Button-1>", self._double_click)

    def _toggle(self):
        self._click_after_id = None
        self._photo.selected = not self._photo.selected
        self._selected = self._photo.selected
        self._update_style()
        self._on_toggle()

    def set_selected(self, val: bool):
        self._photo.selected = val
        self._selected = val
        self._update_style()

    def _update_style(self):
        T = self._theme
        # Always use the underlying PhotoItem state as the single source of truth.
        # This fixes select-all/deselect-all visual desync after filtering/sorting.
        self._selected = bool(self._photo.selected)
        if self._selected:
            self._canvas.itemconfigure(
                self._rect,
                fill=T["surface"],
                outline=T["accent"],
                width=2,
            )
            self._canvas.itemconfigure(self._check_text, text="✓")
            self._canvas.itemconfigure(self._check_bg, fill=T["accent"], outline="")
        else:
            self._canvas.itemconfigure(
                self._rect,
                fill=T["surface"],
                outline=T["border"],
                width=1,
            )
            self._canvas.itemconfigure(self._check_text, text="")
            self._canvas.itemconfigure(self._check_bg, fill=T["surface2"],
                                       outline=T["border"])

    def set_thumbnail(self, pil_image):
        """設定縮圖（PIL Image）

        The cached thumbnail file may be larger than the grid preview area.
        Always fit it into THUMB_W x THUMB_H before creating the Tk image, and
        raise text/checkmark layers afterward so late thumbnail loading can never
        cover the filename, file size, or selected checkmark.
        """
        if pil_image is None:
            return
        try:
            from PIL import Image, ImageTk, ImageOps

            if self._loading_text is not None:
                self._canvas.delete(self._loading_text)
                self._loading_text = None

            img = pil_image.copy()
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGB")
            img = ImageOps.contain(img, (THUMB_W, THUMB_H), Image.LANCZOS)

            tk_img = ImageTk.PhotoImage(img)
            self._tk_image = tk_img  # 保持參考防止 GC

            x = self._x + (CELL_W - THUMB_W) // 2
            y = self._y + THUMB_TOP

            if self._img_display:
                self._canvas.delete(self._img_display)

            self._img_display = self._canvas.create_image(
                x + THUMB_W // 2,
                y + THUMB_H // 2,
                image=tk_img,
                anchor="center",
            )
            self._bind_item(self._img_display)

            # Keep UI overlays above the thumbnail even though thumbnail arrives later.
            for item in [self._name_item, self._size_item, self._check_bg, self._check_text, *self._video_badge_items]:
                try:
                    self._canvas.tag_raise(item)
                except Exception:
                    pass
        except Exception:
            pass

    def destroy(self):
        try:
            if self._click_after_id is not None:
                self._canvas.after_cancel(self._click_after_id)
        except Exception:
            pass
        self._tk_image = None
        try:
            for item in [self._rect, self._img_item, self._loading_text, self._img_display, self._name_item, self._size_item, self._check_bg, self._check_text, *self._video_badge_items]:
                if item:
                    self._canvas.delete(item)
        except Exception:
            pass


class PhotoGridFrame:
    """
    照片網格框架
    自動根據視窗寬度計算欄數，並支援虛擬捲動
    """

    def __init__(self, parent, theme: dict, on_selection_change: Callable, on_scan_photos: Callable | None = None, on_open_media: Callable | None = None, language: str = "en-US"):
        self._theme = theme
        self._on_selection_change = on_selection_change
        self._on_scan_photos = on_scan_photos
        self._on_open_media = on_open_media
        self._language = language
        self._locale_table = load_locale_table(language)
        self._photos: list[PhotoItem] = []
        self._cells: list[PhotoCell] = []
        self._cols = 6

        self.frame = tk.Frame(parent, bg=theme["bg"])
        self._build()

    def _t(self, zh: str, en: str) -> str:
        return translate_text(getattr(self, "_language", "en-US"), zh, en, getattr(self, "_locale_table", {}) or None)

    def _filter_labels(self) -> dict[str, str]:
        return {
            "all": self._t("全部", "All"),
            "photos": self._t("僅照片", "Photos only"),
            "videos": self._t("僅影片", "Videos only"),
        }

    def _sort_labels(self) -> dict[str, str]:
        return {
            "date_new": self._t("日期（新→舊）", "Date (new → old)"),
            "date_old": self._t("日期（舊→新）", "Date (old → new)"),
            "size_big": self._t("大小（大→小）", "Size (large → small)"),
            "size_small": self._t("大小（小→大）", "Size (small → large)"),
            "filename": self._t("檔名", "Filename"),
        }

    def _key_from_label(self, labels: dict[str, str], value: str, default: str) -> str:
        for key, label in labels.items():
            if value == label:
                return key
        return default

    def _build(self):
        T = self._theme

        # 頂部工具列
        toolbar = tk.Frame(self.frame, bg=T["surface2"])
        toolbar.pack(fill=tk.X)
        # Let the toolbar grow vertically when the user increases UI font size
        # or when translations need more height.  A fixed height clips buttons.

        self._scan_button = tk.Button(
            toolbar,
            text=self._t("📷  掃描照片", "📷  Scan photos"),
            bg=T["accent2"],
            fg="#ffffff",
            activebackground=T["accent"],
            activeforeground="#ffffff",
            relief="flat",
            font=ui_font(12, "bold"),
            padx=22,
            pady=8,
            cursor="hand2",
            command=self._on_scan_photos,
        )
        self._scan_button.pack(side=tk.LEFT, padx=(14, 14), pady=7)

        self._count_label = tk.Label(
            toolbar,
            text=self._t("尚未掃描", "Not scanned"),
            bg=T["surface2"],
            fg=T["text_dim"],
            font=ui_font(12),
            anchor="w",
        )
        self._count_label.pack(side=tk.LEFT, fill=tk.Y)

        controls = tk.Frame(toolbar, bg=T["surface2"])
        controls.pack(side=tk.RIGHT, fill=tk.Y, padx=(8, 12))

        # The intended visual order is: Filter + dropdown, then Sort + dropdown.
        tk.Label(
            controls,
            text=self._t("篩選：", "Filter:"),
            bg=T["surface2"],
            fg=T["text_dim"],
            font=ui_font(12, "bold"),
        ).pack(side=tk.LEFT, padx=(0, 6), pady=8)

        filter_labels = self._filter_labels()
        self._filter_var = tk.StringVar(value=filter_labels["all"])
        self._filter_menu = ttk.Combobox(
            controls,
            textvariable=self._filter_var,
            values=[filter_labels["all"], filter_labels["photos"], filter_labels["videos"]],
            state="readonly",
            style="App.TCombobox",
            width=20 if self._language.startswith("zh") else 24,
            font=ui_font(12),
            takefocus=False,
        )
        self._filter_menu.pack(side=tk.LEFT, padx=(0, 34), pady=8, ipady=5)
        self._filter_menu.bind("<<ComboboxSelected>>", self._on_filter_change)

        tk.Label(
            controls,
            text=self._t("排序：", "Sort:"),
            bg=T["surface2"],
            fg=T["text_dim"],
            font=ui_font(12, "bold"),
        ).pack(side=tk.LEFT, padx=(0, 6), pady=8)

        sort_labels = self._sort_labels()
        self._sort_var = tk.StringVar(value=sort_labels["date_new"])
        self._sort_menu = ttk.Combobox(
            controls,
            textvariable=self._sort_var,
            values=[sort_labels["date_new"], sort_labels["date_old"], sort_labels["size_big"], sort_labels["size_small"], sort_labels["filename"]],
            state="readonly",
            style="App.TCombobox",
            width=26 if self._language.startswith("zh") else 34,
            font=ui_font(12),
            takefocus=False,
        )
        self._sort_menu.pack(side=tk.LEFT, padx=(0, 0), pady=8, ipady=5)
        self._sort_menu.bind("<<ComboboxSelected>>", self._on_sort_change)

        # Canvas + 捲軸
        canvas_frame = tk.Frame(self.frame, bg=T["bg"])
        canvas_frame.pack(fill=tk.BOTH, expand=True)

        self._scrollbar = tk.Scrollbar(
            canvas_frame,
            orient=tk.VERTICAL,
            bg=T["surface2"],
            troughcolor=T["bg"],
            relief="flat",
            bd=0,
            width=22,
        )
        self._scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self._canvas = tk.Canvas(
            canvas_frame,
            bg=T["bg"],
            highlightthickness=0,
            yscrollcommand=self._on_canvas_yscroll,
        )
        self._canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._scrollbar.config(command=self._on_scrollbar)

        # 滾輪
        self._canvas.bind("<MouseWheel>", self._on_mousewheel)
        self._canvas.bind("<Button-4>", self._on_mousewheel)
        self._canvas.bind("<Button-5>", self._on_mousewheel)

        # 空白提示
        self._empty_label = tk.Label(
            self._canvas,
            text=self._t("📷\n\n請先連線裝置\n然後點擊「掃描照片」", "📷\n\nConnect a device, then click Scan photos"),
            bg=T["bg"],
            fg=T["text_dim"],
            font=ui_font(14),
            justify="center",
        )
        self._canvas.create_window(
            400, 200,
            window=self._empty_label,
            anchor="center",
            tags=("empty",)
        )

        self._canvas.bind("<Configure>", self._on_resize)
        self._displayed_photos: list[PhotoItem] = []
        self._filter_after_id = None
        self._resize_after_id = None
        self._render_after_id = None
        self._last_visible_range = (-1, -1)

    def _on_canvas_yscroll(self, first, last):
        try:
            self._scrollbar.set(first, last)
        except Exception:
            pass
        # This also fires when users drag the scrollbar thumb or when yview is
        # changed by keyboard/page operations.  Keep virtual cells in sync with
        # every visible-range change, not only mouse-wheel events.
        if getattr(self, "_displayed_photos", None):
            self._schedule_visible_render(delay=25)

    def _on_scrollbar(self, *args):
        """Scroll via scrollbar and refresh virtualized cells immediately.

        Tk does not emit mouse-wheel events when the user drags the scrollbar
        thumb; without this hook the canvas scrolls but the virtualized cells
        are not recreated until another wheel event arrives.
        """
        try:
            self._canvas.yview(*args)
        except Exception:
            pass
        self._render_visible_cells(force=True)

    def _on_mousewheel(self, event):
        if event.num == 4:
            self._canvas.yview_scroll(-1, "units")
        elif event.num == 5:
            self._canvas.yview_scroll(1, "units")
        else:
            self._canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        self._schedule_visible_render(delay=10)
        return "break"

    def _on_resize(self, event):
        if self._displayed_photos:
            new_cols = max(1, event.width // CELL_W)
            if new_cols != self._cols:
                self._cols = new_cols
                try:
                    if self._resize_after_id is not None:
                        self._canvas.after_cancel(self._resize_after_id)
                except Exception:
                    pass
                self._resize_after_id = self._canvas.after(160, self._render_grid)
            else:
                self._schedule_visible_render()
        else:
            self._show_empty_message(self._empty_text())

    def _clear_combo_selection(self):
        for combo in (getattr(self, "_filter_menu", None), getattr(self, "_sort_menu", None)):
            try:
                combo.selection_clear()
                combo.icursor(tk.END)
            except Exception:
                pass
        try:
            self._canvas.focus_set()
        except Exception:
            pass

    def _on_sort_change(self, event=None):
        self.frame.after(1, self._clear_combo_selection)
        self._schedule_filter_and_sort()

    def _on_filter_change(self, event=None):
        self.frame.after(1, self._clear_combo_selection)
        self._schedule_filter_and_sort()

    def _schedule_filter_and_sort(self):
        """延遲套用篩選/排序，避免快速切換下拉選單時連續重畫造成 GUI 卡住。"""
        try:
            if self._filter_after_id is not None:
                self._canvas.after_cancel(self._filter_after_id)
        except Exception:
            pass
        self._filter_after_id = self._canvas.after(280, self._apply_filter_and_sort)

    def _apply_filter_and_sort(self):
        filter_key = self._key_from_label(self._filter_labels(), self._filter_var.get(), "all")
        sort_key = self._key_from_label(self._sort_labels(), self._sort_var.get(), "date_new")

        photos = list(self._photos)

        # 篩選
        if filter_key == "photos":
            photos = [p for p in photos if not p.is_video]
        elif filter_key == "videos":
            photos = [p for p in photos if p.is_video]

        # 排序
        if sort_key == "date_new":
            photos.sort(key=lambda p: p.modified_time, reverse=True)
        elif sort_key == "date_old":
            photos.sort(key=lambda p: p.modified_time)
        elif sort_key == "size_big":
            photos.sort(key=lambda p: p.size, reverse=True)
        elif sort_key == "size_small":
            photos.sort(key=lambda p: p.size)
        elif sort_key == "filename":
            photos.sort(key=lambda p: p.filename)

        self._displayed_photos = photos
        try:
            self._canvas.focus_set()
        except Exception:
            pass
        self._render_grid()
        self._update_count_label()

    def set_photos(self, photos: list[PhotoItem]):
        """設定照片列表並渲染"""
        self._photos = photos
        self._apply_filter_and_sort()

    def set_scanning(self, state: bool):
        try:
            if state:
                self._scan_button.configure(text=self._t("掃描中…", "Scanning…"), state="disabled", bg=self._theme["surface2"], fg=self._theme["text_dim"], cursor="")
            else:
                self._scan_button.configure(text=self._t("📷  掃描照片", "📷  Scan photos"), state="normal", bg=self._theme["accent2"], fg="#ffffff", cursor="hand2")
        except Exception:
            pass

    def _empty_text(self):
        return self._t("📷\n\n請先連線裝置\n然後點擊「掃描照片」", "📷\n\nConnect a device, then click Scan photos")

    def _show_empty_message(self, text: str):
        self._canvas.delete("all")
        cw = self._canvas.winfo_width() or 800
        ch = self._canvas.winfo_height() or 400
        self._canvas.configure(scrollregion=(0, 0, cw, ch))
        self._canvas.create_text(
            cw // 2, ch // 2,
            text=text,
            fill=self._theme["text_dim"],
            font=ui_font(14),
            anchor="center",
            justify="center",
            tags=("empty",),
        )

    def clear(self):
        """清除所有照片"""
        self._photos = []
        self._displayed_photos = []
        self._cells = []
        self._last_visible_range = (-1, -1)
        self._show_empty_message(self._empty_text())
        self._count_label.configure(text=self._t("尚未掃描照片", "No photos scanned yet"))

    def _schedule_visible_render(self, delay: int = 80):
        try:
            if self._render_after_id is not None:
                self._canvas.after_cancel(self._render_after_id)
        except Exception:
            pass
        self._render_after_id = self._canvas.after(delay, self._render_visible_cells)

    def _render_grid(self):
        """Update scroll region and render only visible cells.

        Rendering every thumbnail as a Tk PhotoImage can exhaust the Windows GDI
        bitmap allocator for large libraries.  This virtualized renderer keeps
        only the visible rows plus a small buffer in memory.
        """
        self._canvas.delete("all")
        self._cells.clear()
        self._last_visible_range = (-1, -1)

        if not self._displayed_photos:
            self._show_empty_message(self._t("沒有符合篩選條件的媒體", "No media matches this filter"))
            return

        canvas_w = self._canvas.winfo_width() or 900
        self._cols = max(1, canvas_w // CELL_W)
        rows = (len(self._displayed_photos) + self._cols - 1) // self._cols
        total_h = rows * CELL_H + 20
        self._canvas.configure(scrollregion=(0, 0, canvas_w, total_h))
        self._render_visible_cells(force=True)

    def _render_visible_cells(self, force: bool = False):
        if not self._displayed_photos:
            return
        canvas_w = self._canvas.winfo_width() or 900
        self._cols = max(1, canvas_w // CELL_W)
        top = max(0, int(self._canvas.canvasy(0)))
        bottom = top + max(1, int(self._canvas.winfo_height() or 500))
        start_row = max(0, top // CELL_H - 1)
        end_row = min((len(self._displayed_photos) + self._cols - 1) // self._cols, bottom // CELL_H + 3)
        start_idx = start_row * self._cols
        end_idx = min(len(self._displayed_photos), end_row * self._cols)
        if not force and self._last_visible_range == (start_idx, end_idx):
            return
        self._last_visible_range = (start_idx, end_idx)
        # Drop Python references to old PhotoImage objects before deleting the
        # canvas items.  This reduces Windows GDI bitmap pressure when the user
        # scrolls very quickly through thousands of thumbnails.
        try:
            for cell in self._cells:
                try:
                    cell.destroy()
                except Exception:
                    pass
        except Exception:
            pass
        self._cells.clear()
        self._canvas.delete("all")
        try:
            gc.collect()
        except Exception:
            pass
        for i in range(start_idx, end_idx):
            photo = self._displayed_photos[i]
            col = i % self._cols
            row = i // self._cols
            x = col * CELL_W + (canvas_w - self._cols * CELL_W) // 2
            y = row * CELL_H + 10
            cell = PhotoCell(
                canvas=self._canvas,
                x=x,
                y=y,
                photo=photo,
                theme=self._theme,
                on_toggle=self._on_cell_toggle,
                on_open=self._on_open_media,
            )
            self._cells.append(cell)
            if photo.thumbnail is not None:
                cell.set_thumbnail(photo.thumbnail)

    def update_thumbnail(self, photo_index: int, thumb):
        """更新指定索引的縮圖（由背景執行緒觸發）"""
        if photo_index < len(self._cells) and thumb is not None:
            try:
                self._cells[photo_index].set_thumbnail(thumb)
            except Exception:
                pass


    def update_thumbnail_by_path(self, remote_path: str, thumb):
        """用 remote_path 更新縮圖，避免排序或篩選造成 index 對不上。"""
        if thumb is None:
            return
        try:
            # Store the PIL thumbnail on the model first.  If the cell is not
            # currently visible, it will be shown when scrolled into view.
            for photo in self._photos:
                if photo.remote_path == remote_path:
                    photo.thumbnail = thumb
                    break
            visible = {id(getattr(c, '_photo', None)): c for c in self._cells}
            for photo in self._displayed_photos:
                if photo.remote_path == remote_path:
                    cell = visible.get(id(photo))
                    if cell:
                        cell.set_thumbnail(thumb)
                    return
        except Exception:
            pass

    def refresh_selection(self):
        """重新渲染所有格子的選取狀態"""
        for cell in self._cells:
            cell.set_selected(bool(cell._photo.selected))
        self._update_count_label()

    def _on_cell_toggle(self):
        """格子勾選狀態改變時呼叫"""
        selected = sum(1 for p in self._photos if p.selected)
        self._on_selection_change(selected)
        self._update_count_label()

    def _update_count_label(self):
        total = len(self._displayed_photos)
        selected = sum(1 for p in self._displayed_photos if p.selected)
        photos = sum(1 for p in self._displayed_photos if not p.is_video)
        videos = sum(1 for p in self._displayed_photos if p.is_video)
        template = self._t(
            "共 {total} 個媒體（{photos} 張照片，{videos} 支影片）  ·  已選 {selected} 個",
            "{total} media ({photos} photos, {videos} videos)  ·  Selected {selected}",
        )
        self._count_label.configure(text=template.format(total=total, photos=photos, videos=videos, selected=selected))
