"""Standalone utility windows for OrchardBridge.

The tool windows operate on local files/folders and are intentionally independent
from the connected device.  They use the same preferences/theme/font scaling as
main app windows.
"""
from __future__ import annotations

import hashlib
import os
import threading
import traceback
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import tkinter.font as tkfont

from core.backup_manager import _convert_image_file
from core.image_tools import HEIC_EXTENSIONS, MEDIA_EXTENSIONS, bytes_to_human
from core.preferences import Preferences
from core.ui_fonts import ui_font, scaled_px, get_ui_font_size

try:
    from tkinterdnd2 import DND_FILES  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    DND_FILES = None

try:
    from send2trash import send2trash  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    send2trash = None


@dataclass(frozen=True)
class ConvertTask:
    src: Path
    dst: Path


def _sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def _convert_task_worker(task: ConvertTask, fmt: str, quality: int, subsampling: int, optimize: bool, png_level: int):
    ok, err = _convert_image_file(
        task.src,
        task.dst,
        output_format=fmt,
        quality=quality,
        subsampling=subsampling,
        optimize=optimize,
        png_compress_level=png_level,
    )
    return str(task.src), str(task.dst), ok, err


def _normalize_path(path: Path) -> str:
    try:
        return str(path.resolve()).lower()
    except Exception:
        return str(path.absolute()).lower()


def _walk_files(paths: Iterable[Path], *, exts: set[str] | None = None) -> list[Path]:
    """Expand files/folders recursively and return each real file path once.

    The function intentionally deduplicates after expansion instead of only
    deduplicating the top-level dragged items.  Example: if the user drops A,
    A/B, and A/C, files under B and C are discovered multiple times but shown
    and processed only once.  ``exts=None`` means every file format is accepted.
    """
    seen: set[str] = set()
    out: list[Path] = []
    for p in paths:
        try:
            p = Path(p)
            iterator = p.rglob("*") if p.is_dir() else [p]
            for f in iterator:
                try:
                    if not f.is_file():
                        continue
                    if exts is not None and f.suffix.lower() not in exts:
                        continue
                    key = _normalize_path(f)
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append(f)
                except Exception:
                    continue
        except Exception:
            continue
    return out


def _unique_output_path(base: Path, reserved: set[str]) -> Path:
    parent = base.parent
    stem = base.stem
    suffix = base.suffix
    i = 0
    while True:
        candidate = base if i == 0 else parent / f"{stem} ({i}){suffix}"
        key = _normalize_path(candidate)
        if key not in reserved and not candidate.exists():
            reserved.add(key)
            return candidate
        i += 1


def _desktop_dir() -> Path:
    # Keep this simple and predictable for Windows users.  If Desktop does not
    # exist for some unusual profile, fall back to home.
    d = Path.home() / "Desktop"
    return d if d.exists() else Path.home()


class _ModalMixin:
    def _t(self, zh: str, en: str) -> str:
        return self._translate(zh, en)

    def _fmt_t(self, zh: str, en: str, **kwargs) -> str:
        try:
            return self._t(zh, en).format(**kwargs)
        except Exception:
            return en.format(**kwargs)

    def _button(self, parent, text, command, *, primary=False):
        bg = self._theme["accent"] if primary else self._theme["surface2"]
        fg = "#ffffff" if primary else self._theme["text"]
        return tk.Button(
            parent,
            text=text,
            bg=bg,
            fg=fg,
            activebackground=self._theme.get("accent_hover", self._theme["accent"]) if primary else self._theme["border"],
            activeforeground="#ffffff" if primary else self._theme["text"],
            relief="flat",
            font=ui_font(11, "bold" if primary else "normal"),
            padx=scaled_px(18),
            pady=scaled_px(9),
            cursor="hand2",
            command=command,
        )

    def _split_dnd_files(self, data: str) -> list[Path]:
        try:
            items = self.win.tk.splitlist(data)
        except Exception:
            items = [data]
        return [Path(x) for x in items]

    def _register_drop_target(self, widget, callback):
        """Register one widget as a file/folder drop target.

        tkinterdnd2 exposes convenient ``drop_target_register`` / ``dnd_bind``
        methods only on widgets created through its wrapper classes.  Some
        layouts create ordinary ``tk.Toplevel`` / child widgets under a
        DnD-enabled root; in that case the package is present, but those helper
        methods are missing and drag-and-drop silently stops working.

        To make the tool windows robust, first try the high-level tkinterdnd2
        API.  If that is not available on the specific widget, fall back to the
        underlying Tcl/Tk tkdnd commands and pass the raw %D drop payload into a
        Python callback.  ``_split_dnd_files`` then parses the Tcl list and
        preserves paths containing spaces, CJK characters, and braces.
        """
        if DND_FILES is None:
            return False

        # Fast path: widgets directly mixed with tkinterdnd2's wrapper.
        try:
            if hasattr(widget, "drop_target_register") and hasattr(widget, "dnd_bind"):
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<Drop>>", lambda e: callback(self._split_dnd_files(e.data)), add="+")
                return True
        except Exception as exc:
            print(f"[tool:dnd] high-level DnD registration failed for {type(widget).__name__}: {exc!r}")

        # Robust path: use the tkdnd Tcl package directly.  This works for
        # ordinary Toplevel/Frame/Treeview widgets when tkinterdnd2 is installed
        # but the Python wrapper methods are not present on the child widget.
        try:
            widget.tk.call("package", "require", "tkdnd")
            widget.tk.call("tkdnd::drop_target", "register", widget._w, DND_FILES)
            command = widget.register(lambda data: callback(self._split_dnd_files(data)))
            existing = widget.tk.call("bind", widget._w, "<<Drop>>")
            script = f"{command} %D"
            if existing:
                widget.tk.call("bind", widget._w, "<<Drop>>", f"{existing}\n{script}")
            else:
                widget.tk.call("bind", widget._w, "<<Drop>>", script)
            return True
        except Exception as exc:
            print(f"[tool:dnd] Tcl tkdnd registration failed for {type(widget).__name__}: {exc!r}")
            return False

    def _register_drop_target_recursive(self, widget, callback):
        """Make as much of a Toplevel as possible accept file/folder drops."""
        if DND_FILES is None:
            print("[tool:dnd] tkinterdnd2 is not available; drag-and-drop disabled")
            return False
        ok = False
        try:
            ok = self._register_drop_target(widget, callback) or ok
        except Exception:
            pass
        try:
            for child in widget.winfo_children():
                ok = self._register_drop_target_recursive(child, callback) or ok
        except Exception:
            pass
        return ok

    def _clear_widget_focus(self, widget=None):
        try:
            if widget is not None and hasattr(widget, "selection_clear"):
                widget.selection_clear()
            self.win.focus_set()
        except Exception:
            pass

    def _large_checkbutton(self, parent, text: str, variable: tk.BooleanVar, command=None):
        """Large indicator-style toggle for tool windows."""
        frame = tk.Frame(parent, bg=self._theme["bg"])
        label = tk.Label(frame, text=text, bg=self._theme["bg"], fg=self._theme["text"], font=ui_font(11), anchor="w")
        label.pack(side=tk.LEFT, fill=tk.X, expand=True)
        box = tk.Canvas(frame, width=scaled_px(28), height=scaled_px(28), bg=self._theme["bg"], highlightthickness=0, cursor="hand2")
        box.pack(side=tk.LEFT, padx=(scaled_px(8), 0))

        def redraw(*_):
            box.delete("all")
            size = scaled_px(24)
            pad = scaled_px(2)
            fill = self._theme["surface2"] if not variable.get() else self._theme["accent"]
            outline = self._theme.get("border", "#94a3b8")
            box.create_rectangle(pad, pad, pad + size, pad + size, fill=fill, outline=outline, width=2)
            if variable.get():
                box.create_text(pad + size // 2, pad + size // 2, text="✓", fill="#ffffff", font=ui_font(15, "bold"))

        def toggle(_event=None):
            variable.set(not bool(variable.get()))
            redraw()
            if command:
                command()

        for w in (frame, label, box):
            w.bind("<Button-1>", toggle)
        try:
            variable.trace_add("write", lambda *_: redraw())
        except Exception:
            pass
        redraw()
        return frame


class HEICConverterWindow(_ModalMixin):
    """Local HEIC/HEIF batch converter."""

    def __init__(self, parent, theme: dict, prefs: Preferences, translate: Callable[[str, str], str]):
        self.parent = parent
        self._theme = theme
        self._prefs = prefs
        self._translate = translate
        self._files: dict[str, Path] = {}
        self._running = False
        self._output_user_chosen = False

        self.win = tk.Toplevel(parent)
        self.win.title(self._t("HEIC 轉換器", "HEIC converter"))
        base_font = max(10, int(get_ui_font_size()))
        w = min(max(1380, base_font * 105), 1800)
        h = min(max(820, base_font * 64), 1100)
        self.win.geometry(f"{w}x{h}")
        self.win.minsize(1250, 760)
        self.win.configure(bg=theme["bg"])
        # Do not use transient/grab_set.  With Win+M, modal/transient tool
        # windows can become difficult to restore from the taskbar.
        self._build()

    def _default_output_folder(self) -> Path:
        fmt = "PNG" if getattr(self, "format_var", tk.StringVar(value=str(self._prefs.image_output_format))).get().upper() == "PNG" else "JPEG"
        return _desktop_dir() / ("HEIC2PNG" if fmt == "PNG" else "HEIC2JPEG")

    def _build(self):
        T = self._theme
        root = tk.Frame(self.win, bg=T["bg"])
        root.pack(fill=tk.BOTH, expand=True, padx=scaled_px(26), pady=scaled_px(22))

        tk.Label(root, text=self._t("HEIC 轉換器", "HEIC converter"), bg=T["bg"], fg=T["text"], font=ui_font(18, "bold"), anchor="w").pack(fill=tk.X, pady=(0, scaled_px(16)))

        body = tk.Frame(root, bg=T["bg"])
        body.pack(fill=tk.BOTH, expand=True)
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=0)
        body.grid_rowconfigure(0, weight=1)

        left = tk.Frame(body, bg=T["surface"], bd=0)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, scaled_px(18)))
        right = tk.Frame(body, bg=T["surface"], bd=0, width=max(380, scaled_px(360)))
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_propagate(False)

        # Drag instruction is rendered as a watermark inside the file list so the
        # table can use the full available height.
        self.drop_area = None

        self.summary_var = tk.StringVar(value=self._t("尚未選擇檔案", "No files selected"))
        tk.Label(left, textvariable=self.summary_var, bg=T["surface"], fg=T["text"], font=ui_font(10, "bold"), anchor="w").pack(fill=tk.X, padx=scaled_px(18), pady=(0, scaled_px(8)))

        list_frame = tk.Frame(left, bg=T["surface"])
        list_frame.pack(fill=tk.BOTH, expand=True, padx=scaled_px(18), pady=(0, scaled_px(14)))
        self.tree = ttk.Treeview(list_frame, columns=("path", "size"), show="headings", selectmode="extended")
        self.tree.heading("path", text=self._t("檔案", "File"))
        self.tree.heading("size", text=self._t("大小", "Size"))
        self.tree.column("path", width=620, anchor="w")
        self.tree.column("size", width=120, anchor="e")
        ybar = tk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.tree.yview, width=scaled_px(22))
        self.tree.configure(yscrollcommand=ybar.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ybar.pack(side=tk.RIGHT, fill=tk.Y)
        self.empty_label = tk.Label(
            list_frame,
            text=self._t(
                "將 HEIC/HEIF 檔案或資料夾拖曳到這裡\n或按下『添加圖片 / 添加資料夾』",
                "Drag HEIC/HEIF files or folders here\nor click Add images / Add folder",
            ),
            bg=T["surface"], fg=T["text_dim"], font=ui_font(12), justify="center",
        )
        self.empty_label.place(relx=0.5, rely=0.5, anchor="center")

        btns = tk.Frame(left, bg=T["surface"])
        btns.pack(fill=tk.X, padx=scaled_px(18), pady=(0, scaled_px(18)))
        self._button(btns, self._t("添加圖片", "Add images"), self._add_files).pack(side=tk.LEFT, padx=(0, scaled_px(10)))
        self._button(btns, self._t("添加資料夾", "Add folder"), self._add_folder).pack(side=tk.LEFT, padx=(0, scaled_px(10)))
        self._button(btns, self._t("移除選取", "Remove selected"), self._remove_selected).pack(side=tk.LEFT, padx=(0, scaled_px(10)))
        self._button(btns, self._t("移除所有", "Remove all"), self._clear_all).pack(side=tk.LEFT)

        tk.Label(right, text=self._t("轉換設定", "Conversion settings"), bg=T["surface"], fg=T["text"], font=ui_font(13, "bold"), anchor="w").pack(fill=tk.X, padx=scaled_px(22), pady=(scaled_px(22), scaled_px(16)))

        tk.Label(right, text=self._t("儲存格式", "Output format"), bg=T["surface"], fg=T["text_dim"], font=ui_font(10), anchor="w").pack(fill=tk.X, padx=scaled_px(22))
        self.format_var = tk.StringVar(value="PNG" if str(self._prefs.image_output_format).upper() == "PNG" else "JPEG")
        self.format_combo = ttk.Combobox(right, textvariable=self.format_var, values=["JPEG", "PNG"], state="readonly", style="App.TCombobox", font=ui_font(11), takefocus=False)
        self.format_combo.pack(fill=tk.X, padx=scaled_px(22), pady=(scaled_px(6), scaled_px(18)), ipady=scaled_px(4))
        self.format_combo.bind("<<ComboboxSelected>>", self._on_format_change, add="+")

        tk.Label(right, text=self._t("儲存位置", "Output folder"), bg=T["surface"], fg=T["text_dim"], font=ui_font(10), anchor="w").pack(fill=tk.X, padx=scaled_px(22))
        out_row = tk.Frame(right, bg=T["surface"])
        out_row.pack(fill=tk.X, padx=scaled_px(22), pady=(scaled_px(6), scaled_px(18)))
        self.output_var = tk.StringVar(value=str(self._default_output_folder()))
        tk.Entry(out_row, textvariable=self.output_var, bg=T["surface2"], fg=T["text"], insertbackground=T["text"], relief="flat", font=ui_font(10)).pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=scaled_px(6))
        self._button(out_row, self._t("選擇", "Browse"), self._browse_output).pack(side=tk.LEFT, padx=(scaled_px(8), 0))

        self.progress_var = tk.StringVar(value="")
        tk.Label(right, textvariable=self.progress_var, bg=T["surface"], fg=T["text_dim"], font=ui_font(9), anchor="w", justify="left", wraplength=max(320, scaled_px(320))).pack(fill=tk.X, padx=scaled_px(22), pady=(0, scaled_px(10)))
        self.progress = ttk.Progressbar(right, style="Accent.Horizontal.TProgressbar", mode="determinate", maximum=100)
        self.progress.pack(fill=tk.X, padx=scaled_px(22), pady=(0, scaled_px(18)))

        self.start_btn = self._button(right, self._t("開始轉換", "Start conversion"), self._start_conversion, primary=True)
        self.start_btn.pack(fill=tk.X, padx=scaled_px(22), pady=(0, scaled_px(22)))

        ok = self._register_drop_target_recursive(self.win, self._add_paths)
        if not ok and self.empty_label is not None:
            self.empty_label.configure(text=self._t("拖曳支援未啟用；請使用下方按鈕添加檔案或資料夾", "Drag-and-drop is not enabled; use the buttons below."))

    def _on_format_change(self, _event=None):
        if not self._output_user_chosen:
            self.output_var.set(str(self._default_output_folder()))
        self.win.after(20, lambda: self._clear_widget_focus(self.format_combo))

    def _add_files(self):
        paths = filedialog.askopenfilenames(parent=self.win, title=self._t("選擇 HEIC/HEIF 檔案", "Select HEIC/HEIF files"), filetypes=[("HEIC/HEIF", "*.heic *.HEIC *.heif *.HEIF"), ("All files", "*.*")])
        if paths:
            self._add_paths([Path(p) for p in paths])

    def _add_folder(self):
        folder = filedialog.askdirectory(parent=self.win, title=self._t("選擇資料夾", "Select folder"))
        if folder:
            self._add_paths([Path(folder)])

    def _add_paths(self, paths: list[Path]):
        found = _walk_files(paths, exts=HEIC_EXTENSIONS)
        if not found:
            messagebox.showinfo(self._t("沒有找到 HEIC/HEIF", "No HEIC/HEIF found"), self._t("選取的項目中沒有 HEIC 或 HEIF 檔案。", "No HEIC or HEIF files were found in the selected items."), parent=self.win)
            return
        added = 0
        for p in found:
            key = _normalize_path(p)
            if key not in self._files:
                self._files[key] = p
                added += 1
        self._refresh_list()
        print(f"[tool:heic_converter] Added {added} file(s), total={len(self._files)}")

    def _remove_selected(self):
        for item in self.tree.selection():
            self._files.pop(str(item), None)
        self._refresh_list()

    def _clear_all(self):
        self._files.clear()
        self._refresh_list()

    def _refresh_list(self):
        self.tree.delete(*self.tree.get_children())
        try:
            if self._files:
                self.empty_label.place_forget()
            else:
                self.empty_label.place(relx=0.5, rely=0.5, anchor="center")
        except Exception:
            pass
        total = 0
        for key, p in sorted(self._files.items(), key=lambda kv: str(kv[1]).lower()):
            try:
                size = p.stat().st_size
                total += size
            except Exception:
                size = 0
            self.tree.insert("", "end", iid=key, values=(str(p), bytes_to_human(size)), tags=(key,))
        self.summary_var.set(self._fmt_t("所選檔案：{count}，總大小：{size}", "Selected: {count}, total: {size}", count=len(self._files), size=bytes_to_human(total)))

    def _browse_output(self):
        folder = filedialog.askdirectory(parent=self.win, title=self._t("選擇儲存位置", "Select output folder"))
        if folder:
            self._output_user_chosen = True
            self.output_var.set(folder)

    def _prepare_tasks(self) -> list[ConvertTask]:
        files = list(self._files.values())
        if not files:
            return []
        by_name: dict[str, list[Path]] = {}
        for p in files:
            by_name.setdefault(p.name.lower(), []).append(p)
        kept: list[Path] = []
        for _name, group in by_name.items():
            if len(group) == 1:
                kept.append(group[0])
            else:
                seen_hashes: set[str] = set()
                for p in group:
                    try:
                        h = _sha256_file(p)
                    except Exception:
                        h = _normalize_path(p)
                    if h in seen_hashes:
                        print(f"[tool:heic_converter] Skip duplicate same name/hash: {p}")
                        continue
                    seen_hashes.add(h)
                    kept.append(p)
        out_dir = Path(self.output_var.get()).expanduser()
        out_dir.mkdir(parents=True, exist_ok=True)
        fmt = self.format_var.get().upper().strip()
        suffix = ".png" if fmt == "PNG" else ".jpeg"
        reserved: set[str] = set()
        tasks: list[ConvertTask] = []
        for src in kept:
            base = out_dir / (src.stem + suffix)
            dst = _unique_output_path(base, reserved)
            tasks.append(ConvertTask(src=src, dst=dst))
        return tasks

    def _start_conversion(self):
        if self._running:
            return
        tasks = self._prepare_tasks()
        if not tasks:
            messagebox.showwarning(self._t("沒有檔案", "No files"), self._t("請先添加 HEIC/HEIF 檔案。", "Please add HEIC/HEIF files first."), parent=self.win)
            return
        self._running = True
        self.start_btn.configure(state="disabled")
        self.progress.configure(value=0, maximum=len(tasks))
        self.progress_var.set(self._fmt_t("準備轉換 {count} 個檔案…", "Preparing to convert {count} file(s)…", count=len(tasks)))
        threading.Thread(target=self._convert_thread, args=(tasks,), daemon=True).start()

    def _convert_thread(self, tasks: list[ConvertTask]):
        ok_count = 0
        err_count = 0
        fmt = self.format_var.get().upper().strip()
        workers = max(1, int(getattr(self._prefs, "conversion_workers", 1)))
        try:
            with ProcessPoolExecutor(max_workers=workers) as executor:
                futures = [executor.submit(_convert_task_worker, t, fmt, int(self._prefs.jpeg_quality), int(self._prefs.jpeg_subsampling), bool(self._prefs.jpeg_optimize), int(self._prefs.png_compress_level)) for t in tasks]
                for idx, fut in enumerate(as_completed(futures), start=1):
                    try:
                        src, dst, ok, err = fut.result()
                    except Exception as exc:
                        src, dst, ok, err = "", "", False, repr(exc)
                    if ok:
                        ok_count += 1
                    else:
                        err_count += 1
                        print(f"[tool:heic_converter] ERROR {src} -> {dst}: {err}")
                    self.win.after(0, lambda i=idx, s=src: self._update_progress(i, len(tasks), s))
        except Exception as exc:
            print(f"[tool:heic_converter] Fatal conversion error: {exc!r}\n{traceback.format_exc()}")
            err_count = len(tasks) - ok_count
        self.win.after(0, lambda: self._conversion_done(ok_count, err_count))

    def _update_progress(self, done: int, total: int, src: str):
        self.progress.configure(value=done)
        self.progress_var.set(self._t("轉換 {done}/{total}: {name}", "Converting {done}/{total}: {name}").format(done=done, total=total, name=Path(src).name))

    def _conversion_done(self, ok: int, err: int):
        self._running = False
        self.start_btn.configure(state="normal")
        self.progress_var.set(self._t("完成：成功 {ok}，錯誤 {err}", "Done: {ok} succeeded, {err} failed").format(ok=ok, err=err))
        messagebox.showinfo(
            self._t("轉換完成", "Conversion complete"),
            self._t("成功轉換 {ok} 個檔案。\n錯誤：{err}", "Converted {ok} file(s).\nErrors: {err}").format(ok=ok, err=err),
            parent=self.win,
        )



class DuplicateCleanerWindow(_ModalMixin):
    """Hash-based duplicate file cleaner."""

    GROUP_COLORS = ["#fde2e2", "#e0f2fe", "#dcfce7", "#fef3c7", "#ede9fe", "#fce7f3", "#dbeafe", "#ccfbf1", "#fae8ff", "#e5e7eb"]

    def __init__(self, parent, theme: dict, translate: Callable[[str, str], str]):
        self.parent = parent
        self._theme = theme
        self._translate = translate
        self._files: dict[str, Path] = {}
        self._hashes: dict[str, str] = {}
        self._groups: dict[str, int] = {}
        self._running = False
        self._rehash_after_current = False
        self._sort_col: str | None = None
        self._sort_ascending: bool = True
        self._columns = [
            ("group", self._t("Group", "Group"), 80),
            ("name", self._t("檔名", "Filename"), 220),
            ("hash", "SHA-256", 330),
            ("size", self._t("大小", "Size"), 110),
            ("path", self._t("路徑", "Path"), 640),
        ]

        self.win = tk.Toplevel(parent)
        self.win.title(self._t("刪除重複檔案", "Remove duplicate files"))
        base_font = max(10, int(get_ui_font_size()))
        w = min(max(1550, base_font * 128), 2100)
        h = min(max(900, base_font * 74), 1200)
        self.win.geometry(f"{w}x{h}")
        self.win.minsize(1350, 820)
        self.win.configure(bg=theme["bg"])
        # Keep modeless so Win+M/taskbar restore works predictably.
        self._build()

    def _build(self):
        T = self._theme
        root = tk.Frame(self.win, bg=T["bg"])
        root.pack(fill=tk.BOTH, expand=True, padx=scaled_px(20), pady=scaled_px(18))
        root.grid_columnconfigure(0, weight=1)
        root.grid_columnconfigure(1, weight=0)
        root.grid_rowconfigure(3, weight=1)

        tk.Label(root, text=self._t("刪除重複檔案", "Remove duplicate files"), bg=T["bg"], fg=T["text"], font=ui_font(18, "bold"), anchor="w").grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, scaled_px(12)))

        top = tk.Frame(root, bg=T["bg"])
        top.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, scaled_px(12)))
        top.grid_columnconfigure(0, weight=1)
        # Keep the add buttons on the same horizontal line; the drag instruction
        # itself is shown as a watermark in the table below.
        tk.Label(top, text="", bg=T["bg"]).grid(row=0, column=0, sticky="ew", padx=(0, scaled_px(12)))
        self._button(top, self._t("添加檔案", "Add files"), self._add_files).grid(row=0, column=1, sticky="ns", padx=(0, scaled_px(8)))
        self._button(top, self._t("添加資料夾", "Add folder"), self._add_folder).grid(row=0, column=2, sticky="ns")

        self.status_var = tk.StringVar(value=self._t("尚未添加檔案", "No files added"))
        tk.Label(root, textvariable=self.status_var, bg=T["bg"], fg=T["text_dim"], font=ui_font(10), anchor="w").grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, scaled_px(6)))

        tree_frame = tk.Frame(root, bg=T["bg"])
        tree_frame.grid(row=3, column=0, sticky="nsew", padx=(0, scaled_px(16)))
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(tree_frame, columns=[c[0] for c in self._columns], show="headings", selectmode="extended")
        for col, text, width in self._columns:
            self.tree.heading(col, text=text, command=lambda c=col: self._on_heading_click(c))
            self.tree.column(col, width=width, anchor="w")
        ybar = tk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree.yview, width=scaled_px(22))
        xbar = tk.Scrollbar(tree_frame, orient=tk.HORIZONTAL, command=self.tree.xview, width=scaled_px(18))
        self.tree.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        ybar.grid(row=0, column=1, sticky="ns")
        xbar.grid(row=1, column=0, sticky="ew")
        self.drop_label = tk.Label(
            tree_frame,
            text=self._t("拖曳檔案或資料夾到這裡，或使用上方按鈕添加。", "Drag files or folders here, or use the buttons above."),
            bg=T["bg"], fg=T["text_dim"], font=ui_font(13), justify="center",
        )
        self.drop_label.place(relx=0.5, rely=0.5, anchor="center")
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._update_delete_state())
        self.tree.bind("<Control-c>", self._copy_selection, add="+")
        self.tree.bind("<Control-C>", self._copy_selection, add="+")
        self.tree.bind("<Button-3>", self._show_context_menu, add="+")
        self.tree.bind("<Double-1>", self._on_tree_double_click, add="+")
        self.tree.bind("<ButtonPress-1>", self._on_tree_press, add="+")
        self.tree.bind("<ButtonRelease-1>", self._on_tree_release, add="+")
        self._drag_col = None
        self._drag_x = 0
        self._display_columns = [c[0] for c in self._columns]
        self.tree.configure(displaycolumns=self._display_columns)
        for i, color in enumerate(self.GROUP_COLORS, start=1):
            self.tree.tag_configure(f"g{i}", background=color)

        action = tk.Frame(root, bg=T["bg"], width=scaled_px(270))
        action.grid(row=3, column=1, sticky="ns")
        action.grid_propagate(False)
        self.keep_btn = self._button(action, self._t("每組均僅保留一份", "Keep one per group"), self._mark_keep_one)
        self.keep_btn.pack(fill=tk.X, pady=(0, scaled_px(12)))
        self.select_all_btn = self._button(action, self._t("全選", "Select all"), lambda: self.tree.selection_set(self.tree.get_children()))
        self.select_all_btn.pack(fill=tk.X, pady=(0, scaled_px(12)))
        self.select_none_btn = self._button(action, self._t("全不選", "Select none"), lambda: self.tree.selection_remove(self.tree.selection()))
        self.select_none_btn.pack(fill=tk.X, pady=(0, scaled_px(12)))
        self.delete_btn = self._button(action, self._t("刪除", "Delete"), self._delete_selected, primary=True)
        self.delete_btn.pack(fill=tk.X, pady=(0, scaled_px(14)))
        self.delete_btn.configure(state="disabled")
        self.direct_delete_var = tk.BooleanVar(value=False)
        self.direct_delete_widget = self._large_checkbutton(action, self._t("直接刪除，不進資源回收桶", "Delete permanently"), self.direct_delete_var)
        self.direct_delete_widget.pack(fill=tk.X, pady=(0, scaled_px(16)))
        self.progress = ttk.Progressbar(action, style="Accent.Horizontal.TProgressbar", mode="determinate", maximum=100)
        self.progress.pack(fill=tk.X, pady=(0, scaled_px(8)))
        self.progress_text_var = tk.StringVar(value="")
        tk.Label(action, textvariable=self.progress_text_var, bg=T["bg"], fg=T["text_dim"], font=ui_font(9), justify="left", wraplength=scaled_px(210)).pack(fill=tk.X)

        self._register_drop_target_recursive(self.win, self._add_paths)


    def _visible_columns(self) -> list[str]:
        try:
            cols = list(self.tree.cget("displaycolumns"))
            if cols and cols != ["#all"]:
                return [str(c) for c in cols]
        except Exception:
            pass
        return [c[0] for c in self._columns]

    def _column_title(self, col: str) -> str:
        for c, text, _w in self._columns:
            if c == col:
                return text
        return col

    def _on_tree_double_click(self, event):
        if self.tree.identify_region(event.x, event.y) == "separator":
            col_id = self.tree.identify_column(event.x)
            try:
                idx = int(col_id.replace("#", "")) - 1
                col = self._visible_columns()[idx]
                self._autosize_column(col)
                return "break"
            except Exception:
                return None
        return None

    def _on_tree_press(self, event):
        self._drag_col = None
        self._drag_x = event.x
        if self.tree.identify_region(event.x, event.y) == "heading":
            try:
                idx = int(self.tree.identify_column(event.x).replace("#", "")) - 1
                self._drag_col = self._visible_columns()[idx]
            except Exception:
                self._drag_col = None
        return None

    def _on_tree_release(self, event):
        if not self._drag_col:
            return None
        if abs(event.x - self._drag_x) < 20:
            self._drag_col = None
            return None
        if self.tree.identify_region(event.x, event.y) != "heading":
            self._drag_col = None
            return None
        try:
            cols = self._visible_columns()
            target_idx = int(self.tree.identify_column(event.x).replace("#", "")) - 1
            moving = self._drag_col
            if moving in cols and 0 <= target_idx < len(cols):
                cols.remove(moving)
                cols.insert(target_idx, moving)
                self.tree.configure(displaycolumns=cols)
        except Exception:
            pass
        self._drag_col = None
        return None

    def _autosize_column(self, col: str):
        try:
            fnt = tkfont.Font(font=ui_font(10))
            width = max(70, fnt.measure(self._column_title(col) + "  ") + 28)
            for iid in self.tree.get_children(""):
                val = str(self.tree.set(iid, col))
                width = max(width, fnt.measure(val + "  ") + 28)
            width = min(width, 1800)
            self.tree.column(col, width=width, minwidth=60)
        except Exception:
            pass

    def _copy_selection(self, _event=None):
        selected = list(self.tree.selection())
        if not selected:
            return "break"
        cols = self._visible_columns()
        headers = [self._column_title(c) for c in cols]
        lines = ["\t".join(headers)]
        for iid in selected:
            lines.append("\t".join(str(self.tree.set(iid, c)) for c in cols))
        text = "\n".join(lines)
        try:
            self.win.clipboard_clear()
            self.win.clipboard_append(text)
        except Exception:
            pass
        return "break"

    def _show_context_menu(self, event):
        try:
            iid = self.tree.identify_row(event.y)
            if iid and iid not in self.tree.selection():
                self.tree.selection_set(iid)
            menu = tk.Menu(self.win, tearoff=0)
            menu.add_command(label=self._t("複製", "Copy"), command=lambda: self._copy_selection())
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                menu.grab_release()
            except Exception:
                pass
        return "break"

    def _set_actions_state(self, state: str):
        for btn in [self.keep_btn, self.select_all_btn, self.select_none_btn]:
            try:
                btn.configure(state=state)
            except Exception:
                pass
        self._update_delete_state()

    def _add_files(self):
        paths = filedialog.askopenfilenames(parent=self.win, title=self._t("選擇檔案", "Select files"), filetypes=[("All files", "*.*")])
        if paths:
            self._add_paths([Path(p) for p in paths])

    def _add_folder(self):
        folder = filedialog.askdirectory(parent=self.win, title=self._t("選擇資料夾", "Select folder"))
        if folder:
            self._add_paths([Path(folder)])

    def _add_paths(self, paths: list[Path]):
        files = _walk_files(paths, exts=None)
        added = 0
        for p in files:
            key = _normalize_path(p)
            if key not in self._files:
                self._files[key] = p
                added += 1
        self._sort_col = None
        self._sort_ascending = True
        self._update_headings()
        self._refresh_tree()
        self.status_var.set(self._fmt_t("已添加 {count} 個檔案（新增 {added}）", "Added {count} file(s) ({added} new)", count=len(self._files), added=added))
        if added:
            self._start_hashing_for_new()

    def _start_hashing_for_new(self):
        if self._running:
            self._rehash_after_current = True
            return
        pending = [(k, p) for k, p in self._files.items() if k not in self._hashes]
        if not pending:
            self._rebuild_groups()
            self._refresh_tree()
            return
        self._running = True
        self._set_actions_state("disabled")
        self.progress.configure(value=0, maximum=len(pending))
        self.status_var.set(self._fmt_t("正在計算 HASH：0/{total}", "Calculating hashes: 0/{total}", total=len(pending)))
        threading.Thread(target=self._hash_thread, args=(pending,), daemon=True).start()

    def _hash_thread(self, pending: list[tuple[str, Path]]):
        new_hashes: dict[str, str] = {}
        workers = max(1, min(os.cpu_count() or 4, 8))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            fut_map = {ex.submit(_sha256_file, p): (key, p) for key, p in pending}
            for i, fut in enumerate(as_completed(fut_map), start=1):
                key, _p = fut_map[fut]
                try:
                    new_hashes[key] = fut.result()
                except Exception as exc:
                    new_hashes[key] = f"ERROR:{exc!r}"
                self.win.after(0, lambda n=i, total=len(pending): self._hash_progress(n, total))
        self.win.after(0, lambda: self._hash_done(new_hashes))

    def _hash_progress(self, n: int, total: int):
        self.progress.configure(value=n, maximum=total)
        self.status_var.set(self._fmt_t("正在計算 HASH：{n}/{total}", "Calculating hashes: {n}/{total}", n=n, total=total))
        self.progress_text_var.set(self.status_var.get())

    def _hash_done(self, new_hashes: dict[str, str]):
        self._running = False
        self._hashes.update(new_hashes)
        self._rebuild_groups()
        # Adding new files intentionally restores the unsorted/original order.
        self._sort_col = None
        self._sort_ascending = True
        self._update_headings()
        self._refresh_tree()
        group_count = len(set(self._groups.values()))
        dup_files = len(self._groups)
        self.status_var.set(self._fmt_t("HASH 完成。找到 {group_count} 組重複、{dup_files} 個重複組內檔案。", "Hashes done. Found {group_count} duplicate group(s), {dup_files} files in duplicate groups.", group_count=group_count, dup_files=dup_files))
        self.progress_text_var.set(self.status_var.get())
        self._set_actions_state("normal")
        if self._rehash_after_current:
            self._rehash_after_current = False
            self._start_hashing_for_new()

    def _rebuild_groups(self):
        by_hash: dict[str, list[str]] = {}
        for key in self._files.keys():
            h = self._hashes.get(key, "")
            if h and not h.startswith("ERROR:"):
                by_hash.setdefault(h, []).append(key)
        self._groups = {}
        group_index = 1
        # Use first-seen insertion order to keep group numbers stable and intuitive.
        duplicate_hashes = [(keys[0], h, keys) for h, keys in by_hash.items() if len(keys) > 1]
        duplicate_hashes.sort(key=lambda x: list(self._files.keys()).index(x[0]) if x[0] in self._files else 10**9)
        for _first, _h, keys in duplicate_hashes:
            # Defensive consistency check: every key in a group must share one hash.
            hash_values = {self._hashes.get(k, "") for k in keys}
            if len(hash_values) != 1:
                continue
            for k in keys:
                self._groups[k] = group_index
            group_index += 1

    def _update_headings(self):
        for col, text, _width in self._columns:
            label = text
            if col == self._sort_col:
                label = f"{text} {'▼' if self._sort_ascending else '▲'}"
            self.tree.heading(col, text=label, command=lambda c=col: self._on_heading_click(c))

    def _on_heading_click(self, col: str):
        if self._sort_col == col:
            self._sort_ascending = not self._sort_ascending
        else:
            self._sort_col = col
            self._sort_ascending = True
        self._update_headings()
        self._refresh_tree()

    def _sorted_items(self):
        items = []
        for idx, (key, p) in enumerate(self._files.items()):
            h = self._hashes.get(key, "")
            group = self._groups.get(key, 0)
            try:
                size_raw = p.stat().st_size
            except Exception:
                size_raw = 0
            items.append({"idx": idx, "key": key, "path_obj": p, "name": p.name, "hash": h, "group": group, "size": size_raw, "path": str(p)})
        if self._sort_col is None:
            return items
        def keyfunc(item):
            if self._sort_col == "group":
                return (item["group"] if item["group"] else 10**12, item["idx"])
            if self._sort_col == "name":
                return (str(item["name"]).lower(), item["idx"])
            if self._sort_col == "hash":
                return (str(item["hash"]).lower(), item["idx"])
            if self._sort_col == "size":
                return (int(item["size"]), item["idx"])
            if self._sort_col == "path":
                return (str(item["path"]).lower(), item["idx"])
            return item["idx"]
        return sorted(items, key=keyfunc, reverse=not self._sort_ascending)

    def _refresh_tree(self):
        selected = set(self.tree.selection())
        self.tree.delete(*self.tree.get_children())
        try:
            if self._files:
                self.drop_label.place_forget()
            else:
                self.drop_label.place(relx=0.5, rely=0.5, anchor="center")
        except Exception:
            pass
        for item in self._sorted_items():
            group = item["group"]
            tag = f"g{((group - 1) % len(self.GROUP_COLORS)) + 1}" if group else ""
            self.tree.insert(
                "", "end", iid=item["key"],
                values=(group if group else "", item["name"], item["hash"], bytes_to_human(item["size"]), item["path"]),
                tags=(tag,) if tag else (),
            )
        for k in selected:
            try:
                self.tree.selection_add(k)
            except Exception:
                pass
        self._update_delete_state()

    def _mark_keep_one(self):
        if self._running:
            messagebox.showinfo(self._t("正在計算", "Still calculating"), self._t("請等 HASH 計算完成。", "Please wait until hashing is complete."), parent=self.win)
            return
        if not self._groups:
            messagebox.showinfo(self._t("尚未找到重複", "No duplicates yet"), self._t("目前沒有可標記的重複檔案。", "There are no duplicate files to mark yet."), parent=self.win)
            return
        by_group: dict[int, list[str]] = {}
        for key, group in self._groups.items():
            by_group.setdefault(group, []).append(key)
        to_delete = []
        for _group, keys in by_group.items():
            keys_sorted = sorted(keys, key=lambda k: (len(str(self._files[k])), str(self._files[k]).lower()))
            to_delete.extend(keys_sorted[1:])
        self.tree.selection_set(to_delete)
        self.status_var.set(self._fmt_t("已標記 {count} 個檔案準備刪除。", "Marked {count} file(s) for deletion.", count=len(to_delete)))
        self._update_delete_state()

    def _update_delete_state(self):
        try:
            if getattr(self, "_running", False):
                self.delete_btn.configure(state="disabled")
            else:
                self.delete_btn.configure(state="normal" if self.tree.selection() else "disabled")
        except Exception:
            pass

    def _delete_selected(self):
        selected = list(self.tree.selection())
        if not selected or self._running:
            return
        unique_selected = [k for k in selected if not self._groups.get(k)]
        if unique_selected:
            ok = messagebox.askyesno(
                self._t("包含唯一檔案", "Unique files selected"),
                self._t(
                    "你選擇了 {count} 個不屬於重複組的唯一檔案。確定仍要刪除嗎？",
                    "You selected {count} file(s) that are not in duplicate groups. Delete anyway?",
                ).format(count=len(unique_selected)),
                parent=self.win,
            )
            if not ok:
                return
        ok = messagebox.askyesnocancel(
            self._t("確認刪除", "Confirm delete"),
            self._t("即將刪除 {count} 個檔案。", "Delete {count} file(s)?").format(count=len(selected)),
            parent=self.win,
        )
        if ok is not True:
            return
        direct = bool(self.direct_delete_var.get())
        self._running = True
        self._set_actions_state("disabled")
        self.progress.configure(value=0, maximum=len(selected))
        self.progress_text_var.set(self._t("準備刪除 0/{count}", "Preparing delete 0/{count}").format(count=len(selected)))
        threading.Thread(target=self._delete_thread, args=(selected, direct), daemon=True).start()

    def _delete_thread(self, selected: list[str], direct: bool):
        deleted = 0
        errors = 0
        removed_keys: list[str] = []
        for idx, key in enumerate(selected, start=1):
            p = self._files.get(key)
            if p:
                try:
                    if direct:
                        p.unlink()
                    else:
                        if send2trash is None:
                            raise RuntimeError("send2trash is not installed")
                        send2trash(str(p))
                    deleted += 1
                    removed_keys.append(key)
                except Exception as exc:
                    errors += 1
                    print(f"[tool:duplicate_cleaner] Failed to delete {p}: {exc!r}")
            self.win.after(0, lambda n=idx, total=len(selected), name=(p.name if p else ""): self._delete_progress(n, total, name))
        self.win.after(0, lambda: self._delete_done(removed_keys, deleted, errors))

    def _delete_progress(self, done: int, total: int, name: str):
        self.progress.configure(value=done, maximum=total)
        self.progress_text_var.set(self._t("刪除 {done}/{total}: {name}", "Deleting {done}/{total}: {name}").format(done=done, total=total, name=name))

    def _delete_done(self, removed_keys: list[str], deleted: int, errors: int):
        for key in removed_keys:
            self._files.pop(key, None)
            self._hashes.pop(key, None)
            self._groups.pop(key, None)
        self._running = False
        self._rebuild_groups()
        self._refresh_tree()
        self._set_actions_state("normal")
        self.status_var.set(self._t("刪除完成：{deleted}，錯誤：{errors}", "Delete done: {deleted}, errors: {errors}").format(deleted=deleted, errors=errors))
        self.progress_text_var.set(self.status_var.get())
        messagebox.showinfo(self._t("刪除完成", "Delete done"), self.status_var.get(), parent=self.win)
