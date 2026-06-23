"""Small font scaling helper for Tkinter UI."""
from __future__ import annotations

_FONT_FAMILY = "Segoe UI"
_BASE_SIZE = 10


def get_ui_font_size() -> int:
    return _BASE_SIZE


def ui_scale() -> float:
    return max(0.8, min(1.6, _BASE_SIZE / 10.0))


def scaled_px(value: int | float) -> int:
    try:
        return max(1, int(round(float(value) * ui_scale())))
    except Exception:
        return int(value)


def set_ui_font_size(size: int) -> None:
    global _BASE_SIZE
    try:
        _BASE_SIZE = max(8, min(16, int(size)))
    except Exception:
        _BASE_SIZE = 10


def ui_font(size: int, *styles: str):
    """Return a Segoe UI font tuple scaled relative to base size 10."""
    try:
        scaled = int(size) + (_BASE_SIZE - 10)
    except Exception:
        scaled = 10
    scaled = max(7, min(28, scaled))
    if styles:
        return (_FONT_FAMILY, scaled, *styles)
    return (_FONT_FAMILY, scaled)
