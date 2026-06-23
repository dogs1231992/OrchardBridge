"""Small JSON-based UI translation helpers for OrchardBridge.

Translations are keyed by the Traditional Chinese source string used in code.
Missing translations fall back to English, except zh-CN where a small built-in
Traditional-to-Simplified fallback is applied so the UI does not show Traditional
Chinese when a locale file does not contain a specific string.
"""
from __future__ import annotations

import json
from pathlib import Path
from functools import lru_cache

_TRAD_TO_SIMP = str.maketrans({
    "備": "备", "體": "体", "機": "机", "裝": "装", "置": "置", "設": "设", "定": "定",
    "關": "关", "於": "于", "檔": "档", "資": "资", "夾": "夹", "圖": "图", "轉": "转",
    "換": "换", "儲": "储", "選": "选", "擇": "择", "刪": "删", "除": "除", "複": "复",
    "徑": "径", "這": "这", "裡": "里", "曳": "曳", "與": "与", "啟": "启", "郵": "邮",
    "箱": "箱", "輸": "输", "質": "质", "應": "应", "確": "确", "認": "认",
    "語": "语", "後": "后", "會": "会", "動": "动", "軟": "软", "體": "体", "現": "现",
    "時": "时", "開": "开", "閉": "闭", "右": "右", "角": "角", "縮": "缩", "匣": "匣",
    "顯": "显", "示": "示", "訊": "讯", "錯": "错", "誤": "误", "連": "连", "線": "线",
    "態": "态", "狀": "状", "數": "数", "據": "据", "專": "专", "覽": "览", "欄": "栏",
    "僅": "仅", "舊": "旧", "頁": "页", "層": "层", "並": "并", "復": "复", "較": "较",
    "議": "议", "讓": "让", "產": "产", "雲": "云", "無": "无", "損": "损", "徑": "径",
})

# Multi-character replacements run before the simple character map.
_BAD_PLACEHOLDER_TRANSLATIONS = {
    "Mensagem do sistema",
    "Системное сообщение",
    "Pesan sistem",
    "رسالة نظام",
}

_MULTI_ZH_CN = {
    "照片": "照片",
    "整機": "整机",
    "備份": "备份",
    "設定": "设置",
    "關於": "关于",
    "檔案": "文件",
    "資料夾": "文件夹",
    "瀏覽": "浏览",
    "確定": "确定",
    "取消": "取消",
    "系統匣": "系统托盘",
    "回收桶": "回收站",
    "重新啟動": "重新启动",
    "語言": "语言",
    "外觀": "外观",
    "轉檔": "转换",
    "圖檔": "图像文件",
    "圖片": "图片",
    "螢幕": "屏幕",
}

@lru_cache(maxsize=32)
def load_locale_table(language: str) -> dict[str, str]:
    lang = str(language or "en-US")
    if lang in {"zh-TW", "en-US"}:
        return {}
    path = Path(__file__).resolve().parent.parent / "locales" / f"{lang}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in data.items()}
    except Exception:
        return {}


def zh_tw_to_zh_cn(text: str) -> str:
    out = str(text)
    for old, new in _MULTI_ZH_CN.items():
        out = out.replace(old, new)
    return out.translate(_TRAD_TO_SIMP)


def translate_text(language: str, zh: str, en: str, table: dict[str, str] | None = None) -> str:
    lang = str(language or "en-US")
    if lang == "zh-TW":
        return zh
    if lang == "en-US":
        return en
    if table is None:
        table = load_locale_table(lang)
    if table:
        if zh in table:
            value = str(table[zh])
            if value.strip() not in _BAD_PLACEHOLDER_TRANSLATIONS:
                return value
        stripped = str(zh).strip()
        if stripped in table:
            value = str(table[stripped])
            if value.strip() not in _BAD_PLACEHOLDER_TRANSLATIONS:
                leading = str(zh)[:len(str(zh)) - len(str(zh).lstrip())]
                trailing = str(zh)[len(str(zh).rstrip()):]
                return leading + value + trailing
    if lang == "zh-CN":
        return zh_tw_to_zh_cn(zh)
    return en
