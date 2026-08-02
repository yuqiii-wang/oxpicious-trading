"""Shared Chinese-date & text-parsing helpers for PBoC downloaders.

The three PBoC leaves (``repo_news``, ``oma``, ``lpr_news``) all reuse the
same well-tested date-extraction regexes and Chinese-numeral coercion
functions that originally lived in ``download_pboc_repo_news.py``.
Extracting them here breaks the cross-script import chain
(``download_pboc_oma`` / ``download_pboc_lpr_news`` ->
``download_pboc_repo_news``) so each leaf is independently importable.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Optional


# --- Chinese numeral table -------------------------------------------------
CN_NUM = {
    "〇": 0, "零": 0, "○": 0, "Ｏ": 0,
    "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}


# --- Shared date / slug regexes --------------------------------------------
RE_PUBDATE_META = re.compile(r"(\d{4}-\d{2}-\d{2})\s+\d{2}:\d{2}:\d{2}")

RE_CHINESE_DATE = re.compile(
    r"([\u4e00\u96f6\u4e8c\u516b\u516d\u4e09\u4e94\u4e00\u4e5d\u56db\u4e03\u3007\u3007]+"
    r"年[\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341\u767e\u5343]+"
    r"月[\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341\u767e\u5343]+日)"
)

RE_SERIAL = re.compile(r"\[\s*(\d{4})\s*\]\s*第\s*(\d+)\s*号")
RE_DETAIL_SLUG = re.compile(r"/(\d{5,})/index\.html$")
RE_TITLE_DATE = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日")


# --- Chinese numeral coercion ----------------------------------------------
def cn_year_to_num(s: str) -> int:
    out = 0
    for ch in s:
        if ch in CN_NUM:
            out = out * 10 + CN_NUM[ch]
        elif ch.isdigit():
            out = out * 10 + int(ch)
    return out


def cn_monthday_to_num(s: str) -> int:
    if s in CN_NUM:
        return CN_NUM[s]
    if s == "十":
        return 10
    if s.startswith("十") and len(s) == 2:
        return 10 + CN_NUM.get(s[1], 0)
    if s.endswith("十"):
        return CN_NUM.get(s[0], 0) * 10
    if "十" in s:
        a, b = s.split("十", 1)
        return CN_NUM.get(a, 0) * 10 + CN_NUM.get(b, 0)
    total = 0
    cur = 0
    for ch in s:
        if ch in CN_NUM:
            cur = CN_NUM[ch]
        elif ch == "百":
            total += cur * 100
            cur = 0
    return total + cur


def parse_chinese_date(s: str) -> Optional[date]:
    try:
        s = s.strip()
        yi = s.index("年")
        yue = s.index("月")
        ri = s.index("日")
        year_s = s[:yi]
        month_s = s[yi + 1 : yue]
        day_s = s[yue + 1 : ri]
        y = cn_year_to_num(year_s)
        m = cn_monthday_to_num(month_s)
        d = cn_monthday_to_num(day_s)
        return date(y, m, d)
    except Exception:
        return None


def _clean_text(s: str) -> str:
    s = s.replace("\u3000", " ").replace("\xa0", " ")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


__all__ = [
    "CN_NUM",
    "RE_PUBDATE_META",
    "RE_CHINESE_DATE",
    "RE_SERIAL",
    "RE_DETAIL_SLUG",
    "RE_TITLE_DATE",
    "cn_year_to_num",
    "cn_monthday_to_num",
    "parse_chinese_date",
    "_clean_text",
]
