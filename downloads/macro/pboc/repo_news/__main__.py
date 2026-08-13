from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

from downloads._common.core import (
    COMMON_BASE_HEADERS,
    DEFAULT_TIMEOUT,
    DEFAULT_START_DATE,
    AntiBotProxy,
    AntiBotConfig,
    setup_logger,
    resolve_out_dir,
    parse_date_window,
    scan_present_dates_with_pattern,
    build_default_session,
    RunStats,
    business_days,
)


PBOC_BASE = "https://www.pbc.gov.cn"

CATEGORY_OMO_TRANSACTION = "omo_transaction"
CATEGORY_OUTRIGHT_REPO = "outright_repo"

CATEGORY_CONFIGS: Dict[str, Dict[str, str]] = {
    CATEGORY_OMO_TRANSACTION: {
        "label": "公开市场业务交易公告",
        "list_base": "/zhengcehuobisi/125207/125213/125431/125475/",
        "file_prefix": "pboc_omo_trans",
    },
    CATEGORY_OUTRIGHT_REPO: {
        "label": "公开市场买断式逆回购业务公告",
        "list_base": "/zhengcehuobisi/125207/125213/125431/5492845/",
        "file_prefix": "pboc_outright_repo",
    },
}

RE_PAGING_TAG = re.compile(r"""tagname=(['"])([^'"]*/(\w+)-(\d+)\.html)\1""")

PBOC_MIN_VALID_BYTES = 200
SLEEP_SEC = 5.0
EMPTY_PLACEHOLDER_SUFFIX = "_empty.md"

RE_PUBDATE_META = re.compile(r"(\d{4}-\d{2}-\d{2})\s+\d{2}:\d{2}:\d{2}")

RE_CHINESE_DATE = re.compile(
    r"([\u4e00\u96f6\u4e8c\u516b\u516d\u4e09\u4e94\u4e00\u4e5d\u56db\u4e03\u3007\u3007]+"
    r"年[\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341\u767e\u5343]+"
    r"月[\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341\u767e\u5343]+日)"
)


def _ws(cn: str) -> str:
    return r"\s*".join(re.escape(ch) for ch in cn)


RE_RATE_OP = re.compile(_ws("操作利率") + r"[\s\S]{0,300}?([\d\s.]+?)\s*%")
RE_RATE_WIN = re.compile(_ws("中标利率") + r"[\s\S]{0,300}?([\d\s.]+?)\s*%")
RE_RATE_INLINE = re.compile(r"利率\s*(?:为|是)?\s*[:：]?\s*([\d\s.]+?)\s*%")
RE_RATE_LOOSE = re.compile(r"(?<![A-Za-z])([\d\s.]+?)\s*%")

RE_DURATION_DAYS = re.compile(r"(\d+)\s*天")
RE_DURATION_MONTHS = re.compile(r"(\d+)\s*个月")
RE_DURATION_YEARS = re.compile(r"(?<=\s)(\d)\s*年")
RE_DURATION_TENOR = re.compile(r"(\d+)\s*天期")
RE_DURATION_OVERNIGHT = re.compile(r"隔夜(?!利)")
RE_DURATION_PAREN = re.compile(r"[（(]\s*(\d+)\s*天\s*[)）]")

RE_QUANTITY_YIYUAN = re.compile(r"([\d.,]+)\s*亿元")
RE_QUANTITY_WANYI = re.compile(r"([\d.,]+)\s*万亿元")

RE_SERIAL = re.compile(r"\[\s*(\d{4})\s*\]\s*第\s*(\d+)\s*号")
RE_DETAIL_SLUG = re.compile(r"/(\d{5,})/index\.html$")

RE_TITLE_DATE = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日")


def _norm_num(s: str) -> str:
    return re.sub(r"\s+", "", s).strip(".")


def create_empty_placeholder(out_dir: Path, category: str, d: date) -> None:
    prefix = CATEGORY_CONFIGS[category]["file_prefix"]
    fname = f"{prefix}_{d.strftime('%Y-%m-%d')}_empty.md"
    fpath = out_dir / fname
    if not fpath.exists():
        content = f"---\ncategory: {category}\npub_date: {d.strftime('%Y-%m-%d')}\nstatus: empty\n---\n# No announcement for {d.strftime('%Y-%m-%d')}\n\nNo PBoC announcement found for this date.\n"
        fpath.write_text(content, encoding="utf-8")


def estimate_item_date(item: AnnouncementItem) -> Optional[date]:
    if item.pub_date:
        try:
            return datetime.strptime(item.pub_date, "%Y-%m-%d").date()
        except ValueError:
            pass
    m = RE_TITLE_DATE.search(item.title)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    m = RE_CHINESE_DATE.search(item.title)
    if m:
        return parse_chinese_date(m.group(1))
    return None


CN_NUM = {
    "〇": 0, "零": 0, "○": 0, "Ｏ": 0,
    "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}


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


def parse_quantity(s: str) -> Optional[float]:
    if not s:
        return None
    s = s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def _tenor_to_days(tenor: str) -> int:
    """Convert tenor string like '7D', '3M', '1Y' to approximate days."""
    m = re.match(r"(\d+)\s*([DMY])", tenor, re.I)
    if not m:
        return 0
    n = int(m.group(1))
    unit = m.group(2).upper()
    if unit == "D":
        return n
    if unit == "M":
        return n * 30
    if unit == "Y":
        return n * 365
    return 0


def _compute_end_date(start_date: str, tenor: str) -> str:
    """Compute end date from start date + tenor."""
    days = _tenor_to_days(tenor)
    if not days or not start_date:
        return ""
    try:
        d = datetime.strptime(start_date, "%Y-%m-%d")
        return (d + timedelta(days=days)).strftime("%Y-%m-%d")
    except Exception:
        return ""


def _normalize_tenor(raw: str) -> str:
    """Convert Chinese tenor text to normalized form: 7D, 3M, 1Y, 91D."""
    raw = raw.strip()
    # 3个月（91天）→ 91D
    m = re.search(r"(\d+)\s*个月\s*[（(]\s*(\d+)\s*天\s*[)）]", raw)
    if m:
        return f"{m.group(2)}D"
    # 91天
    m = re.search(r"(\d+)\s*天", raw)
    if m:
        return f"{m.group(1)}D"
    # 3个月
    m = re.search(r"(\d+)\s*个月", raw)
    if m:
        return f"{m.group(1)}M"
    # 1年 (but not 2022年)
    m = re.search(r"(?<!\d)(\d{1,2})\s*年(?!\d)", raw)
    if m:
        val = int(m.group(1))
        if 1 <= val <= 5:
            return f"{val}Y"
    # Already normalized: 7D, 3M, 1Y, 91D
    m = re.match(r"(\d+)\s*([DMY])$", raw, re.I)
    if m:
        return f"{m.group(1)}{m.group(2).upper()}"
    return ""


def _extract_tenors_ordered(text: str) -> List[str]:
    """Extract all tenor values from text, ordered by position."""
    results = []  # (position, tenor)
    # 1. 个月（天） → use days
    for m in re.finditer(r"(\d+)\s*个月\s*[（(]\s*(\d+)\s*天\s*[)）]", text):
        results.append((m.start(), f"{m.group(2)}D"))
    # 2. Standalone 天 (not inside 个月（...）)
    for m in re.finditer(r"(\d{1,4})\s*天", text):
        # Skip if this is part of 个月（X天）
        ctx_before = text[max(0, m.start() - 15):m.start()]
        ctx_after = text[m.end():m.end() + 5]
        if "个月" in ctx_before and ctx_after.startswith((")", "）")):
            continue
        results.append((m.start(), f"{m.group(1)}D"))
    # 3. 个月 (without parenthetical days)
    for m in re.finditer(r"(\d+)\s*个月", text):
        following = text[m.end():m.end() + 20]
        if re.match(r"\s*[（(]\s*\d+\s*天", following):
            continue  # Already captured by pattern 1
        results.append((m.start(), f"{m.group(1)}M"))
    # 4. 年 (not 2022年)
    for m in re.finditer(r"(?<!\d)(\d{1,2})\s*年(?!\d)", text):
        val = int(m.group(1))
        if 1 <= val <= 5:
            results.append((m.start(), f"{val}Y"))
    results.sort(key=lambda x: x[0])
    return [t for _, t in results]


def _extract_quantities_ordered(text: str) -> List[float]:
    """Extract all quantity values (亿元/万亿元) from text, ordered by position."""
    results = []
    for m in re.finditer(r"([\d.,]+)\s*(亿元|万亿元)", text):
        q = parse_quantity(m.group(1))
        if q is not None:
            if m.group(2) == "万亿元":
                q *= 10000
            results.append((m.start(), q))
    results.sort(key=lambda x: x[0])
    return [q for _, q in results]


def _extract_rates_ordered(text: str) -> List[float]:
    """Extract all rate values (X.XX%) from text, ordered by position."""
    results = []
    for m in re.finditer(r"(\d+\.?\d*)\s*%", text):
        val = float(m.group(1))
        if 0.01 <= val <= 20:
            results.append((m.start(), val))
    results.sort(key=lambda x: x[0])
    return [r for _, r in results]


def parse_instruments_from_body(body_text: str, pub_date: str) -> List[Dict]:
    """Parse raw body text into structured instrument data.

    Returns a list of dicts, each with:
        instrument: 'reverse_repo' | 'MLF' | 'central_bank_bill' | 'outright_repo'
        tenor: '7D' | '1Y' | '91D' | '3M' | ''
        start_date: 'YYYY-MM-DD'
        quantity: float (亿元)
        rate: float (%) or None
        end_date: 'YYYY-MM-DD' (calculated from tenor)
    """
    instruments: List[Dict] = []
    flat = re.sub(r"\s+", " ", body_text).strip()
    # Join split numbers/rates: "1. 7 0 %" → "1.70%"
    flat = re.sub(r"(?<=[\d.])\s+(?=[\d.%])", "", flat)

    # --- 1. Parse summary for quantity-instrument pairs ---
    # Pattern: quantity亿元 + (optional 公开市场) + instrument name
    summary_pairs = []
    for m in re.finditer(
        r"([\d.,]+)\s*亿元\s*(?:公开市场)?\s*(逆回购|中期借贷便利|MLF|买断式逆回购)",
        flat,
    ):
        qty = parse_quantity(m.group(1))
        inst_raw = m.group(2)
        if inst_raw in ("中期借贷便利", "MLF"):
            inst = "MLF"
        elif inst_raw == "买断式逆回购":
            inst = "outright_repo"
        else:
            inst = "reverse_repo"
        if qty is not None:
            summary_pairs.append({"instrument": inst, "quantity": qty})

    # --- 2. Parse table sections for tenor/rate by instrument ---
    # Find section boundaries: "MLF操作情况", "逆回购操作情况", "买断式逆回购操作情况"
    section_markers = []
    for m in re.finditer(
        r"((?:MLF|中期借贷便利)\s*操作情况|逆回购\s*操作情况|买断式\s*逆回购\s*操作情况)",
        flat,
    ):
        if "MLF" in m.group() or "中期借贷便利" in m.group():
            inst = "MLF"
        elif "买断式" in m.group():
            inst = "outright_repo"
        else:
            inst = "reverse_repo"
        section_markers.append((m.start(), m.end(), inst))

    section_data: Dict[str, list] = {}
    for i, (start, end, inst) in enumerate(section_markers):
        next_start = section_markers[i + 1][0] if i + 1 < len(section_markers) else len(flat)
        section_text = flat[end:next_start]
        if inst not in section_data:
            section_data[inst] = []
        section_data[inst].append({
            "tenors": _extract_tenors_ordered(section_text),
            "quantities": _extract_quantities_ordered(section_text),
            "rates": _extract_rates_ordered(section_text),
        })

    # --- 3. Match summary pairs with section data ---
    if summary_pairs:
        used_sections = set()  # keyed by (inst, section_index)
        for pair in summary_pairs:
            inst = pair["instrument"]
            qty = pair["quantity"]
            tenor = ""
            rate = None
            matched = False
            if inst in section_data:
                for si, sd in enumerate(section_data[inst]):
                    if (inst, si) in used_sections:
                        continue
                    # Match by quantity if available
                    if sd["quantities"] and any(abs(q - qty) < 0.01 for q in sd["quantities"]):
                        tenor = sd["tenors"][0] if sd["tenors"] else ""
                        rate = sd["rates"][0] if sd["rates"] else None
                        used_sections.add((inst, si))
                        matched = True
                        break
                if not matched:
                    # Use first available unused section
                    for si, sd in enumerate(section_data[inst]):
                        if (inst, si) not in used_sections:
                            tenor = sd["tenors"][0] if sd["tenors"] else ""
                            rate = sd["rates"][0] if sd["rates"] else None
                            used_sections.add((inst, si))
                            matched = True
                            break
            end_date = _compute_end_date(pub_date, tenor) if tenor else ""
            instruments.append({
                "instrument": inst,
                "tenor": tenor,
                "start_date": pub_date,
                "quantity": qty,
                "rate": rate,
                "end_date": end_date,
            })
    else:
        # No summary pairs — parse sections directly
        for inst, sections in section_data.items():
            for sd in sections:
                n = max(len(sd["tenors"]), len(sd["quantities"]), len(sd["rates"]))
                for i in range(n):
                    tenor = sd["tenors"][i] if i < len(sd["tenors"]) else ""
                    qty = sd["quantities"][i] if i < len(sd["quantities"]) else None
                    rate = sd["rates"][i] if i < len(sd["rates"]) else None
                    if qty is not None:
                        end_date = _compute_end_date(pub_date, tenor) if tenor else ""
                        instruments.append({
                            "instrument": inst,
                            "tenor": tenor,
                            "start_date": pub_date,
                            "quantity": qty,
                            "rate": rate,
                            "end_date": end_date,
                        })

    # --- 4. Handle central bank bills (央行票据) ---
    if "央行票据" in flat and not any(i["instrument"] == "central_bank_bill" for i in instruments):
        bill_section_start = flat.find("央行票据")
        if bill_section_start >= 0:
            bill_text = flat[bill_section_start:]
            qtys = _extract_quantities_ordered(bill_text)
            tenors = _extract_tenors_ordered(bill_text)
            rates = _extract_rates_ordered(bill_text)
            n = max(len(qtys), len(tenors), len(rates))
            for i in range(n):
                tenor = tenors[i] if i < len(tenors) else ""
                qty = qtys[i] if i < len(qtys) else None
                rate = rates[i] if i < len(rates) else None
                if qty is not None:
                    end_date = _compute_end_date(pub_date, tenor) if tenor else ""
                    instruments.append({
                        "instrument": "central_bank_bill",
                        "tenor": tenor,
                        "start_date": pub_date,
                        "quantity": qty,
                        "rate": rate,
                        "end_date": end_date,
                    })

    # --- 5. Fallback: if nothing found, try old-style extraction ---
    if not instruments:
        qtys = _extract_quantities_ordered(flat)
        tenors = _extract_tenors_ordered(flat)
        rates = _extract_rates_ordered(flat)
        if qtys:
            qty = qtys[0]
            tenor = tenors[0] if tenors else ""
            rate = rates[0] if rates else None
            # Only assign instrument type if body text actually mentions it
            if "中期借贷便利" in flat or "MLF" in flat:
                inst = "MLF"
            elif "逆回购" in flat or "买断式逆回购" in flat:
                inst = "reverse_repo"
            elif "央行票据" in flat:
                inst = "central_bank_bill"
            else:
                inst = "other"
            end_date = _compute_end_date(pub_date, tenor) if tenor else ""
            instruments.append({
                "instrument": inst,
                "tenor": tenor,
                "start_date": pub_date,
                "quantity": qty,
                "rate": rate,
                "end_date": end_date,
            })

    # --- 6. Deduplication: remove duplicate instruments ---
    # First pass: exact match by (instrument, tenor, start_date)
    seen = {}
    deduped = []
    for i in instruments:
        key = (i["instrument"], i["tenor"], i["start_date"])
        if key not in seen:
            seen[key] = len(deduped)
            deduped.append(i)
        else:
            existing = deduped[seen[key]]
            if existing["rate"] is None and i["rate"] is not None:
                deduped[seen[key]] = i
            elif existing["tenor"] == "" and i["tenor"] != "":
                deduped[seen[key]] = i
    instruments = deduped

    # Second pass: merge entries with same instrument+date but different tenors
    # Prefer the one with complete data (tenor and rate)
    date_seen = {}
    final = []
    for i in instruments:
        date_key = (i["instrument"], i["start_date"])
        if date_key not in date_seen:
            date_seen[date_key] = len(final)
            final.append(i)
        else:
            existing = final[date_seen[date_key]]
            # If existing has empty tenor/rate but new one has them, replace
            has_better_tenor = i["tenor"] != "" and existing["tenor"] == ""
            has_better_rate = i["rate"] is not None and existing["rate"] is None
            if has_better_tenor or has_better_rate:
                final[date_seen[date_key]] = i
            # If existing has complete data and new one doesn't, keep existing
            elif existing["tenor"] != "" and existing["rate"] is not None:
                continue
            # If both have empty tenor/rate but same quantity, skip
            elif i["quantity"] == existing["quantity"]:
                continue
            # Otherwise, keep both (different tenors with valid data)
            else:
                date_seen[date_key] = len(final)
                final.append(i)
    instruments = final

    # --- 7. Fallback: extract tenor from text if missing ---
    # For instruments without section markers (like outright_repo), extract tenor directly
    for i in instruments:
        if i["tenor"] == "" and i["instrument"] in ("outright_repo", "reverse_repo", "MLF"):
            tenors = _extract_tenors_ordered(flat)
            if tenors:
                i["tenor"] = tenors[0]
                i["end_date"] = _compute_end_date(i["start_date"], i["tenor"])

    return instruments


def _instruments_data_to_str(data: List[Dict]) -> str:
    """Serialize instruments_data to pipe-delimited string for YAML front-matter."""
    if not data:
        return ""
    entries = []
    for d in data:
        rate_str = f"{d['rate']:g}" if d.get("rate") is not None else ""
        entries.append("|".join([
            str(d.get("instrument", "")),
            str(d.get("tenor", "")),
            str(d.get("start_date", "")),
            f"{d.get('quantity', 0):g}" if d.get("quantity") is not None else "0",
            rate_str,
            str(d.get("end_date", "")),
        ]))
    return ";;".join(entries)


def _instruments_data_from_str(raw: str) -> List[Dict]:
    """Deserialize instruments_data from pipe-delimited string."""
    if not raw or not raw.strip():
        return []
    data = []
    for entry in raw.split(";;"):
        parts = entry.split("|")
        if len(parts) < 5:
            continue
        rate_val = None
        if parts[4] and parts[4] != "":
            try:
                rate_val = float(parts[4])
            except ValueError:
                pass
        data.append({
            "instrument": parts[0],
            "tenor": parts[1],
            "start_date": parts[2],
            "quantity": float(parts[3]) if parts[3] else 0.0,
            "rate": rate_val,
            "end_date": parts[5] if len(parts) > 5 else "",
        })
    return data


# ---------------------------------------------------------------------------
# CSV conversion (per-file + combined)
# ---------------------------------------------------------------------------
PBOC_INSTRUMENTS_CSV_COLUMNS = [
    "pub_date",
    "category",
    "title",
    "detail_url",
    "serial_year",
    "serial_no",
    "detail_slug",
    "instrument",
    "tenor",
    "start_date",
    "quantity",
    "rate",
    "end_date",
    "parse_warnings",
    "source_file",
]


def _parse_pboc_fm_simple(text: str) -> Dict[str, str]:
    """Parse the YAML front-matter of a PBoC .md file into a flat dict (stdlib only).

    Only supports the simple `key: value` lines used by AnnouncementItem.to_markdown();
    list-valued fields (e.g. parse_warnings) are returned as their raw string repr.
    """
    fm: Dict[str, str] = {}
    if not text.startswith("---"):
        return fm
    end = text.find("\n---", 3)
    if end < 0:
        return fm
    block = text[3:end].strip()
    for line in block.splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if val.startswith("[") and val.endswith("]"):
            # Keep list literals as raw string; callers that need them can re-parse.
            fm[key] = val
        else:
            fm[key] = val.strip("'\"")
    return fm


def convert_md_to_csv(md_path: Path, csv_path: Optional[Path] = None) -> bool:
    """Parse a single PBoC repo news .md file and write a per-file CSV.

    The CSV is written next to the .md file (same stem, .csv suffix) so downstream
    build scripts can read finished CSVs directly without re-parsing the markdown.

    Returns True on success (>=1 instrument row written), False if no instruments
    were present (e.g. empty placeholder files).
    """
    if csv_path is None:
        csv_path = md_path.with_suffix(".csv")
    try:
        text = md_path.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("[conv %s] cannot read: %s", md_path.name, e)
        return False

    fm = _parse_pboc_fm_simple(text)
    pub_date = fm.get("pub_date", "")
    if not pub_date or fm.get("status") == "empty":
        return False  # empty placeholder, no instruments

    inst_data = _instruments_data_from_str(fm.get("instruments_data", ""))
    if not inst_data:
        return False

    parse_warnings_raw = fm.get("parse_warnings", "")
    # Strip Python list literal wrapper to get a plain string
    if parse_warnings_raw.startswith("[") and parse_warnings_raw.endswith("]"):
        parse_warnings = parse_warnings_raw[1:-1].strip()
    else:
        parse_warnings = parse_warnings_raw

    import csv as _csv
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = _csv.DictWriter(f, fieldnames=PBOC_INSTRUMENTS_CSV_COLUMNS)
        writer.writeheader()
        for d in inst_data:
            rate_val = d.get("rate")
            writer.writerow({
                "pub_date":         pub_date,
                "category":         fm.get("category", ""),
                "title":            fm.get("title", ""),
                "detail_url":       fm.get("detail_url", ""),
                "serial_year":      fm.get("serial_year", ""),
                "serial_no":        fm.get("serial_no", ""),
                "detail_slug":      fm.get("detail_slug", ""),
                "instrument":       d.get("instrument", ""),
                "tenor":            d.get("tenor", ""),
                "start_date":       d.get("start_date", ""),
                "quantity":         d.get("quantity", ""),
                "rate":             "" if rate_val is None else f"{rate_val:g}",
                "end_date":         d.get("end_date", ""),
                "parse_warnings":   parse_warnings,
                "source_file":      md_path.name,
            })
    return True


def build_instruments_csv(md_dir: Path, output_dir: Optional[Path] = None) -> Dict[str, int]:
    """Aggregate all pboc_*_*.md (non-empty) files into a combined CSV.

    Prefers reading existing per-file CSVs (much faster than re-parsing MDs);
    falls back to parsing .md files when a per-file CSV is missing.

    Output: ``<output_dir>/instruments_combined.csv``
    """
    if output_dir is None:
        project_root = Path(__file__).resolve().parent
        output_dir = project_root / "temp_data" / "analysis_output" / "pboc_repo_news"
    output_dir.mkdir(parents=True, exist_ok=True)
    combined_path = output_dir / "instruments_combined.csv"

    md_files = sorted(
        list(md_dir.glob("pboc_omo_trans_*.md"))
        + list(md_dir.glob("pboc_outright_repo_*.md"))
    )
    # Exclude empty placeholders
    md_files = [f for f in md_files if not f.name.endswith("_empty.md")]

    csv_files = sorted(
        list(md_dir.glob("pboc_omo_trans_*.csv"))
        + list(md_dir.glob("pboc_outright_repo_*.csv"))
    )

    logger.info("[build-csv] scanning %s: %d .md files, %d per-file CSVs",
                md_dir, len(md_files), len(csv_files))

    import csv as _csv

    counts = {"rows": 0, "files_ok": 0, "files_empty": 0, "files_failed": 0}

    with open(combined_path, "w", encoding="utf-8-sig", newline="") as fout:
        writer = _csv.DictWriter(fout, fieldnames=PBOC_INSTRUMENTS_CSV_COLUMNS)
        writer.writeheader()

        md_stems = {f.stem: f for f in md_files}
        csv_stems = {f.stem: f for f in csv_files}

        # 1. Read existing per-file CSVs
        processed_stems = set()
        for stem, csv_path in csv_stems.items():
            # Skip per-file CSVs whose source .md is an empty placeholder
            if stem.endswith("_empty"):
                continue
            try:
                with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
                    reader = _csv.DictReader(f)
                    rows = list(reader)
            except Exception as e:
                counts["files_failed"] += 1
                logger.warning("[build-csv] failed to read %s: %s", csv_path.name, e)
                continue

            if not rows:
                counts["files_empty"] += 1
                continue

            for row in rows:
                # Ensure all expected columns exist
                out_row = {col: row.get(col, "") for col in PBOC_INSTRUMENTS_CSV_COLUMNS}
                writer.writerow(out_row)
                counts["rows"] += 1
            counts["files_ok"] += 1
            processed_stems.add(stem)

        # 2. For .md files without a per-file CSV, parse on-the-fly
        for stem, md_path in md_stems.items():
            if stem in processed_stems:
                continue
            if stem.endswith("_empty"):
                continue
            per_file_csv = md_path.with_suffix(".csv")
            try:
                ok = convert_md_to_csv(md_path, per_file_csv)
            except Exception as e:
                counts["files_failed"] += 1
                logger.warning("[build-csv] failed to parse %s: %s", md_path.name, e)
                continue
            if not ok:
                counts["files_empty"] += 1
                continue
            try:
                with open(per_file_csv, "r", encoding="utf-8-sig", newline="") as f:
                    reader = _csv.DictReader(f)
                    rows = list(reader)
            except Exception as e:
                counts["files_failed"] += 1
                logger.warning("[build-csv] failed to re-read %s: %s", per_file_csv.name, e)
                continue
            for row in rows:
                out_row = {col: row.get(col, "") for col in PBOC_INSTRUMENTS_CSV_COLUMNS}
                writer.writerow(out_row)
                counts["rows"] += 1
            counts["files_ok"] += 1

    logger.info("[build-csv] saved %s (%d rows, %d files ok, %d empty, %d failed)",
                combined_path, counts["rows"], counts["files_ok"],
                counts["files_empty"], counts["files_failed"])
    return counts


@dataclass
class AnnouncementItem:
    category: str
    title: str
    detail_url: str
    list_page: int = 0
    pub_date: Optional[str] = None
    serial_year: Optional[str] = None
    serial_no: Optional[str] = None
    detail_slug: Optional[str] = None

    instruments_data: List[Dict] = field(default_factory=list)

    raw_body: str = ""
    parse_warnings: List[str] = field(default_factory=list)

    def md_filename(self) -> str:
        prefix = CATEGORY_CONFIGS[self.category]["file_prefix"]
        d = self.pub_date or "00000000"
        serial = self.serial_no or "0"
        y = self.serial_year or d[:4]
        slug = self.detail_slug or "x"
        return f"{prefix}_{d}_{y}_{serial}_{slug}.md"

    def to_markdown(self) -> str:
        lines = []
        lines.append("---")
        lines.append(f"category: {self.category}")
        lines.append(f"title: {self.title}")
        lines.append(f"detail_url: {self.detail_url}")
        lines.append(f"pub_date: {self.pub_date or ''}")
        if self.serial_year:
            lines.append(f"serial_year: {self.serial_year}")
        if self.serial_no:
            lines.append(f"serial_no: {self.serial_no}")
        lines.append(f"instruments_data: {_instruments_data_to_str(self.instruments_data)}")
        if self.parse_warnings:
            lines.append(f"parse_warnings: {self.parse_warnings!r}")
        lines.append("---")
        lines.append("")
        lines.append(f"# {self.title}")
        lines.append("")
        lines.append(f"- Pub date: **{self.pub_date or 'n/a'}**")
        lines.append(f"- Detail: {self.detail_url}")
        lines.append("")
        lines.append("## Parsed instruments")
        lines.append("")
        if self.instruments_data:
            lines.append("| Instrument | Tenor | Start date | Quantity (亿元) | Rate (%) | End date |")
            lines.append("|------------|-------|------------|-----------------|----------|----------|")
            for d in self.instruments_data:
                rate_str = f"{d['rate']:g}" if d.get("rate") is not None else "-"
                lines.append(
                    f"| {d['instrument']} | {d.get('tenor', '')} | {d.get('start_date', '')} "
                    f"| {d.get('quantity', 0):g} | {rate_str} | {d.get('end_date', '')} |"
                )
        else:
            lines.append("(no instruments parsed)")
        if self.parse_warnings:
            lines.append("")
            lines.append("### Parse warnings")
            for w in self.parse_warnings:
                lines.append(f"- {w}")
        lines.append("")
        lines.append("## Raw body")
        lines.append("")
        lines.append("```")
        lines.append(self.raw_body.strip())
        lines.append("```")
        return "\n".join(lines) + "\n"


def _clean_text(s: str) -> str:
    s = s.replace("\u3000", " ").replace("\xa0", " ")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def parse_announcement_body(item: AnnouncementItem, html: str) -> None:
    soup = BeautifulSoup(html, "html.parser")

    meta_area = soup.get_text(" ", strip=False)

    m = RE_PUBDATE_META.search(meta_area)
    pd: Optional[date] = None
    if m:
        try:
            pd = datetime.strptime(m.group(1), "%Y-%m-%d").date()
        except ValueError:
            pd = None
    if pd is None:
        mc = RE_CHINESE_DATE.search(meta_area)
        if mc:
            pd = parse_chinese_date(mc.group(1))
    if pd is None:
        m2 = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", meta_area)
        if m2:
            try:
                pd = date(int(m2.group(1)), int(m2.group(2)), int(m2.group(3)))
            except ValueError:
                pd = None

    if pd is None:
        item.parse_warnings.append("pub_date not found")
    else:
        item.pub_date = pd.strftime("%Y-%m-%d")

    content_div = (
        soup.find("div", id="zoom")
        or soup.find("div", class_="content")
        or soup.find("td", class_="Normal")
    )
    if content_div is None:
        candidates = soup.find_all(["div", "td"], class_=re.compile(r"(TRS_Editor|content|Normal)", re.I))
        for c in candidates:
            txt = c.get_text("\n", strip=False)
            if len(txt) > 50 and any(k in txt for k in ("逆回购", "MLF", "中标量", "操作")):
                content_div = c
                break
    if content_div is None:
        content_div = soup.body or soup

    body_text = _clean_text(content_div.get_text("\n", strip=False))
    item.raw_body = body_text

    ms = RE_SERIAL.search(item.title)
    if not ms:
        ms = RE_SERIAL.search(body_text)
    if ms:
        item.serial_year = ms.group(1)
        item.serial_no = ms.group(2)
    else:
        item.parse_warnings.append("serial number not found")

    # Parse instruments from body text using the new structured approach
    pub_date_str = item.pub_date or ""
    item.instruments_data = parse_instruments_from_body(body_text, pub_date_str)

    if not item.instruments_data:
        item.parse_warnings.append("no instruments parsed")

    mslug = RE_DETAIL_SLUG.search(item.detail_url)
    if mslug:
        item.detail_slug = mslug.group(1)


logger = setup_logger("pboc_repo_news")


def build_session() -> requests.Session:
    s = build_default_session()
    s.headers.update(COMMON_BASE_HEADERS)
    return s


def detect_page_prefix(list_base: str, html: str) -> Optional[str]:
    matches = list(RE_PAGING_TAG.finditer(html))
    if not matches:
        return None
    by_page: Dict[int, str] = {}
    for m in matches:
        full_rel = m.group(2)
        slug = m.group(3)
        page_no = int(m.group(4))
        by_page[page_no] = f"{slug}-{{page}}.html"
    if not by_page:
        return None
    max_p = max(by_page.keys())
    return by_page[max_p]


def list_page_url(
    category: str, page: int, page_prefix_fmt: Optional[str] = None
) -> str:
    cfg = CATEGORY_CONFIGS[category]
    base = cfg["list_base"]
    if page <= 1:
        return PBOC_BASE + base + "index.html"
    if page_prefix_fmt:
        return PBOC_BASE + base + page_prefix_fmt.format(page=page)
    return PBOC_BASE + base + f"index_{page}.html"


def fetch_list_page(
    session: requests.Session,
    category: str,
    page: int,
    page_prefix_fmt: Optional[str] = None,
    proxy: Optional[AntiBotProxy] = None,
) -> tuple[List[AnnouncementItem], Optional[str]]:
    if proxy is None:
        proxy = AntiBotProxy(AntiBotConfig(base_sleep_sec=5.0))
    
    url = list_page_url(category, page, page_prefix_fmt)
    logger.info("Fetching list page %s page=%d (%s)", category, page, url)

    resp = proxy.get(
        session,
        url,
        timeout=DEFAULT_TIMEOUT,
        logger=logger,
        log_tag=f"[list {category} p{page}]",
    )
    if resp is None:
        logger.error("List page fetch failed %s p%d: request returned None", category, page)
        return [], None

    resp.encoding = resp.apparent_encoding or "utf-8"
    html = resp.text
    detected_prefix = None
    if page == 1:
        detected_prefix = detect_page_prefix(CATEGORY_CONFIGS[category]["list_base"], html)
        if detected_prefix:
            logger.info("  Detected pagination format for %s: %s", category, detected_prefix)

    soup = BeautifulSoup(html, "html.parser")

    # The list-page URL itself (e.g. .../125475/index.html or
    # .../5492845/index.html) matches RE_DETAIL_SLUG because the category
    # directory is 5+ digits. Exclude it so the list page is never fetched
    # as a detail page (which would capture pagination text instead of an
    # announcement body).
    list_page_self_url = list_page_url(category, 1)

    items: List[AnnouncementItem] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href:
            continue
        if href.startswith("./"):
            href = href[2:]
        if not href.startswith("http"):
            if not href.startswith("/"):
                base = CATEGORY_CONFIGS[category]["list_base"]
                href = base + href
            href = PBOC_BASE + href
        # Skip the list page itself (breadcrumb / nav self-link)
        if href == list_page_self_url:
            continue
        text = a.get_text(strip=True)
        text = text.strip('"“”').strip()
        if not text:
            continue
        if "公告" not in text and "通知" not in text and "结果" not in text:
            continue
        if text in {"公告信息", "公开市场业务交易公告", "公开市场业务公告",
                     "公开市场买断式逆回购业务公告", "中国人民银行", "货币政策司"}:
            continue
        if not RE_DETAIL_SLUG.search(href):
            continue

        pub_date = None
        next_span = a.find_next("span")
        if next_span:
            span_text = next_span.get_text(strip=True)
            if re.match(r"\d{4}-\d{2}-\d{2}", span_text):
                pub_date = span_text

        if pub_date is None:
            m = re.search(r"/(\d{8})\d*/index\.html$", href)
            if m:
                try:
                    d = datetime.strptime(m.group(1), "%Y%m%d").date()
                    pub_date = d.strftime("%Y-%m-%d")
                except ValueError:
                    pass

        item = AnnouncementItem(
            category=category,
            title=text,
            detail_url=href,
            list_page=page,
            pub_date=pub_date,
        )
        items.append(item)

    dedup: Dict[str, AnnouncementItem] = {}
    for it in items:
        dedup[it.detail_url] = it
    return list(dedup.values()), detected_prefix


def smart_pagination_pages(
    session: requests.Session,
    category: str,
    target_start: date,
    max_pages: int = 200,
    jump_interval: int = 10,
    proxy: Optional[AntiBotProxy] = None,
) -> Tuple[List[int], Optional[str]]:
    if proxy is None:
        proxy = AntiBotProxy(AntiBotConfig(base_sleep_sec=5.0))
    
    page_prefix_fmt: Optional[str] = None

    items, detected_fmt = fetch_list_page(session, category, 1, page_prefix_fmt, proxy)
    if detected_fmt:
        page_prefix_fmt = detected_fmt
    if not items:
        logger.info("[smart-pagination] page 1 returned no items, returning empty list")
        return [], page_prefix_fmt

    page_dates_1 = [d for d in (estimate_item_date(it) for it in items) if d is not None]

    if not page_dates_1:
        logger.warning("[smart-pagination] no dates found on page 1")
        return [1], page_prefix_fmt

    newest_on_page_1 = max(page_dates_1)
    oldest_on_page_1 = min(page_dates_1)
    logger.info("[smart-pagination] page 1: newest=%s, oldest=%s, target=%s",
                newest_on_page_1, oldest_on_page_1, target_start)

    if oldest_on_page_1 < target_start:
        logger.info("[smart-pagination] page 1 already spans target boundary, only page 1 needed")
        return [1], page_prefix_fmt

    current_page = 1
    last_in_range_page = 1

    while current_page < max_pages:
        if proxy.is_blocked(PBOC_BASE):
            logger.warning("[smart-pagination] pboc.gov.cn blocked, stopping pagination")
            break

        next_jump = current_page + jump_interval
        if next_jump > max_pages:
            next_jump = max_pages

        logger.info("[smart-pagination] jumping from page %d to page %d (interval=%d)",
                    current_page, next_jump, jump_interval)

        jump_items, _ = fetch_list_page(session, category, next_jump, page_prefix_fmt, proxy)
        jump_dates = [d for d in (estimate_item_date(it) for it in jump_items) if d is not None]

        if not jump_items:
            logger.info("[smart-pagination] page %d returned no items, boundary at page %d", next_jump, last_in_range_page)
            break

        if not jump_dates:
            logger.warning("[smart-pagination] no dates found on page %d, skipping", next_jump)
            current_page = next_jump
            continue

        newest_on_jump = max(jump_dates)
        oldest_on_jump = min(jump_dates)
        logger.info("[smart-pagination] page %d: newest=%s, oldest=%s",
                    next_jump, newest_on_jump, oldest_on_jump)

        if newest_on_jump >= target_start:
            logger.info("[smart-pagination] page %d newest date %s >= target %s, continuing jump",
                        next_jump, newest_on_jump, target_start)
            last_in_range_page = next_jump
            current_page = next_jump
            continue

        logger.info("[smart-pagination] page %d newest date %s < target %s, finding exact boundary",
                    next_jump, newest_on_jump, target_start)

        for p in range(last_in_range_page + 1, next_jump + 1):
            inc_items, _ = fetch_list_page(session, category, p, page_prefix_fmt, proxy)
            if not inc_items:
                logger.info("[smart-pagination] incremental page %d returned no items, boundary at page %d", p, last_in_range_page)
                break

            inc_dates = [d for d in (estimate_item_date(it) for it in inc_items) if d is not None]
            if not inc_dates:
                continue

            newest_on_inc = max(inc_dates)
            if newest_on_inc >= target_start:
                logger.info("[smart-pagination] incremental page %d: newest=%s >= target, in range",
                            p, newest_on_inc)
                last_in_range_page = p
            else:
                logger.info("[smart-pagination] incremental page %d: newest=%s < target, boundary found",
                            p, newest_on_inc)
                break

        break

    pages_to_process = list(range(1, last_in_range_page + 1))
    logger.info("[smart-pagination] pages 1-%d all in range, will process %d pages",
                last_in_range_page, len(pages_to_process))
    return pages_to_process, page_prefix_fmt


def fetch_detail(
    session: requests.Session, item: AnnouncementItem, proxy: Optional[AntiBotProxy] = None
) -> bool:
    if proxy is None:
        proxy = AntiBotProxy(AntiBotConfig(base_sleep_sec=5.0))
    
    resp = proxy.get(
        session,
        item.detail_url,
        timeout=DEFAULT_TIMEOUT,
        logger=logger,
        log_tag=f"[detail {item.title[:30]}]",
    )
    if resp is None:
        logger.error("Detail fetch failed %s: request returned None", item.title[:40])
        return False

    resp.encoding = resp.apparent_encoding or "utf-8"

    if len(resp.content) < PBOC_MIN_VALID_BYTES:
        logger.warning("Detail too small (%d bytes) for %s", len(resp.content), item.title[:40])
        return False

    parse_announcement_body(item, resp.text)
    return True


def download_pboc_repo_news(
    *,
    out_root: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    years: Optional[int] = None,
    categories: Optional[List[str]] = None,
    max_pages: int = 200,
    sleep_sec: float = SLEEP_SEC,
    convert_csv: bool = True,
    build_csv: bool = True,
) -> dict:
    out_dir = resolve_out_dir(str(Path(__file__).resolve()), "pboc_repo_news", out_root)

    if start_date is None and years is not None:
        _start, _end = parse_date_window(
            end_date=end_date,
            start_date=None,
            lookback_years=years,
        )
    else:
        if start_date is None:
            start_date = DEFAULT_START_DATE
        _start, _end = parse_date_window(
            end_date=end_date,
            start_date=start_date,
            lookback_years=None,
        )

    if categories is None:
        categories = [CATEGORY_OMO_TRANSACTION, CATEGORY_OUTRIGHT_REPO]
    for c in categories:
        if c not in CATEGORY_CONFIGS:
            raise ValueError(f"Unknown category: {c}. Valid: {list(CATEGORY_CONFIGS.keys())}")

    session = build_session()
    stats = RunStats()
    
    # Create unified AntiBotProxy
    proxy_config = AntiBotConfig(
        base_sleep_sec=sleep_sec,
    )
    proxy = AntiBotProxy(proxy_config)

    prefixes = [CATEGORY_CONFIGS[c]["file_prefix"] for c in categories]
    cached_dates_by_prefix = scan_present_dates_with_pattern(
        out_dir, prefixes=prefixes, min_bytes=100, ext_glob="*.md",
    )

    stats.skipped_cached = sum(len(v) for v in cached_dates_by_prefix.values())
    for cat in categories:
        prefix = CATEGORY_CONFIGS[cat]["file_prefix"]
        cached_dates = cached_dates_by_prefix.get(prefix, set())
        if cached_dates:
            logger.info(
                "[%s] %d dates already cached, latest=%s",
                cat, len(cached_dates), max(cached_dates),
            )
        else:
            logger.info("[%s] no prior cached files", cat)

    if years is not None:
        logger.info(
            "Starting PBoC repo news download: %s -> %s (lookback %dy). categories=%s",
            _start, _end, years, categories,
        )
    else:
        logger.info(
            "Starting PBoC repo news download: %s -> %s. categories=%s",
            _start, _end, categories,
        )

    skipped_oob = 0

    try:
        for cat in categories:
            logger.info("== Processing category %s ==", cat)
            prefix = CATEGORY_CONFIGS[cat]["file_prefix"]
            cached_dates = cached_dates_by_prefix.get(prefix, set())
            cached_years = {d.year for d in cached_dates}
            eff_start = _start

            expected_dates = set(business_days(_start, _end, reverse=False))
            missing_dates = sorted(expected_dates - cached_dates)

            logger.info(
                "  [%s] scanning local files: %s -> %s",
                cat, eff_start, _end,
            )
            logger.info(
                "  [%s] expected business days: %d, cached: %d, missing: %d",
                cat, len(expected_dates), len(cached_dates & expected_dates), len(missing_dates),
            )
            if missing_dates:
                logger.info(
                    "  [%s] missing dates range: %s -> %s",
                    cat, missing_dates[0], missing_dates[-1],
                )
            else:
                logger.info("  [%s] all dates already cached, skipping download", cat)
                continue

            found_dates = set()

            if cat == "omo_transaction":
                target_start = missing_dates[0] if missing_dates else _start
                pages_to_process, page_prefix_fmt = smart_pagination_pages(
                    session, cat, target_start, max_pages=max_pages, jump_interval=10,
                    proxy=proxy,
                )
                if not pages_to_process:
                    logger.info("  [%s] no pages to process", cat)
                    continue
            else:
                page_prefix_fmt = None
                pages_to_process = list(range(1, max_pages + 1))

            for page in pages_to_process:
                if proxy.is_blocked(PBOC_BASE):
                    logger.warning("  [host-blocked] pboc.gov.cn blocked, skipping remaining pages")
                    break

                items, detected = fetch_list_page(session, cat, page, page_prefix_fmt, proxy)
                if detected and not page_prefix_fmt:
                    page_prefix_fmt = detected
                if not items:
                    logger.info("Category %s page %d returned no items, stopping", cat, page)
                    break
                logger.info("  page %d: %d candidate items", page, len(items))

                page_in_range_count = 0
                reached_boundary = False

                for item in items:
                    if proxy.is_blocked(PBOC_BASE):
                        logger.warning("  [host-blocked] pboc.gov.cn blocked, skipping remaining items")
                        reached_boundary = True
                        break

                    mslug = RE_DETAIL_SLUG.search(item.detail_url)
                    slug = mslug.group(1) if mslug else None
                    if slug:
                        item.detail_slug = slug

                    # Title-year pre-filter: skip detail fetches for years already
                    # fully covered by cache so we only request missing data.
                    mser = RE_SERIAL.search(item.title or "")
                    ty = int(mser.group(1)) if mser else None
                    if ty is not None:
                        if ty < _start.year:
                            skipped_oob += 1
                            logger.info(
                                "  [boundary year %d < %d] stop: %s",
                                ty, _start.year, item.title[:50],
                            )
                            reached_boundary = True
                            break
                        if ty > _end.year:
                            skipped_oob += 1
                            continue
                        # Skip detail fetches for FULLY PAST years already
                        # covered by cache. The condition `ty < _end.year`
                        # ensures the current (partial) year is never skipped
                        # — without it, new announcements in a year that has
                        # ANY cached date (e.g. 2026 cached up to 07-15) would
                        # all be skipped, freezing the dataset at the last
                        # cached date.
                        if ty in cached_years and ty < _end.year and ty != _start.year:
                            stats.skipped_cached += 1
                            page_in_range_count += 1
                            continue

                    ok = fetch_detail(session, item, proxy)
                    if not ok:
                        stats.failed += 1
                        # Auto-sleep handled by proxy.get()/post()
                        continue

                    if item.pub_date:
                        try:
                            d = datetime.strptime(item.pub_date, "%Y-%m-%d").date()
                        except ValueError:
                            d = None
                    else:
                        d = None

                    if d is None:
                        stats.failed += 1
                        # Auto-sleep handled by proxy.get()/post()
                        continue

                    found_dates.add(d)

                    if d in cached_dates:
                        stats.skipped_cached += 1
                        page_in_range_count += 1
                        proxy.sleep(max(0.1, sleep_sec * 0.3))
                        continue

                    if d < eff_start:
                        skipped_oob += 1
                        logger.info(
                            "  [boundary %s < %s] stop: %s",
                            d, eff_start, item.title[:50],
                        )
                        reached_boundary = True
                        break
                    if d > _end:
                        skipped_oob += 1
                        proxy.sleep(max(0.1, sleep_sec * 0.3))
                        continue

                    page_in_range_count += 1
                    fname = item.md_filename()
                    fpath = out_dir / fname
                    with open(fpath, "w", encoding="utf-8") as f:
                        f.write(item.to_markdown())
                    stats.downloaded += 1
                    stats.files.append(str(fpath))
                    cached_dates.add(d)
                    logger.info(
                        "  [saved] %s pub=%s instruments=%d (%s)",
                        item.title[:45],
                        item.pub_date or "n/a",
                        len(item.instruments_data),
                        fname,
                    )
                    # Per-file CSV conversion (cheap; runs alongside .md write)
                    if convert_csv and item.instruments_data:
                        try:
                            convert_md_to_csv(fpath)
                        except Exception as e:
                            logger.warning("  [conv %s] per-file CSV conversion failed: %s", fname, e)
                    # Auto-sleep handled by proxy.get()/post()

                if reached_boundary:
                    logger.info(
                        "  [%s] reached boundary at page %d (dates >= %s already cached)",
                        cat, page, eff_start,
                    )
                    break
                if page_in_range_count == 0 and page > 3:
                    logger.info(
                        "No in-range items on page %d for %s (start=%s) -> stopping pagination (past boundary)",
                        page, cat, eff_start,
                    )
                    break

            still_missing = sorted(expected_dates - cached_dates - found_dates)
            if still_missing:
                logger.info(
                    "  [%s] creating %d empty placeholders for dates with no announcements",
                    cat, len(still_missing),
                )
                for d in still_missing:
                    create_empty_placeholder(out_dir, cat, d)
                    stats.skipped_cached += 1

    except KeyboardInterrupt:
        logger.warning("Interrupted by user")

    summary = stats.to_dict(
        skipped_out_of_range=skipped_oob,
        out_dir=str(out_dir),
        start_date=str(_start),
        end_date=str(_end),
        categories=categories,
    )
    logger.info(
        "Done PBoC repo news. downloaded=%d skipped_cached=%d skipped_oob=%d failed=%d out=%s",
        stats.downloaded, stats.skipped_cached, skipped_oob, stats.failed, out_dir,
    )

    # Build combined instruments CSV from all .md files (prefers existing per-file CSVs)
    if build_csv:
        try:
            csv_counts = build_instruments_csv(out_dir)
            summary["csv"] = csv_counts
        except Exception as e:
            logger.error("build_instruments_csv failed: %s", e)

    return summary


def reparse_existing_files(
    out_dir: Path,
    convert_csv: bool = True,
    build_csv: bool = True,
) -> dict:
    """Re-parse raw body from existing .md files without re-downloading.

    Reads each pboc_omo_trans_*.md and pboc_outright_repo_*.md file,
    extracts the raw body from the ``` code block, re-parses it with
    the new instrument parser, and rewrites the file with updated
    front-matter and parsed fields. Optionally also regenerates the
    per-file CSVs and the combined instruments CSV.
    """
    import yaml as _yaml

    patterns = ["pboc_omo_trans_*.md", "pboc_outright_repo_*.md"]
    files = []
    for pat in patterns:
        files.extend(sorted(out_dir.glob(pat)))

    n_total = len(files)
    n_ok = 0
    n_skip = 0
    n_fail = 0
    n_csv_ok = 0

    print(f"[REPARSE] scanning {n_total} .md files in {out_dir}", flush=True)

    for fpath in files:
        try:
            text = fpath.read_text(encoding="utf-8")
        except Exception as e:
            print(f"  [ERROR] cannot read {fpath.name}: {e}", flush=True)
            n_fail += 1
            continue

        # Extract front-matter and raw body
        if not text.startswith("---"):
            n_skip += 1
            continue

        fm_end = text.find("\n---", 3)
        if fm_end < 0:
            n_skip += 1
            continue

        fm_text = text[3:fm_end].strip()
        body_section = text[fm_end + 4:]

        # Parse YAML front-matter
        try:
            fm = _yaml.safe_load(fm_text) or {}
        except Exception:
            # Fallback: simple key-value parsing
            fm = {}
            for line in fm_text.splitlines():
                if ":" in line:
                    key, _, val = line.partition(":")
                    fm[key.strip()] = val.strip().strip("'\"")

        # Extract raw body from ``` ... ``` block
        raw_start = body_section.find("```")
        if raw_start < 0:
            n_skip += 1
            continue
        raw_end = body_section.find("```", raw_start + 3)
        if raw_end < 0:
            n_skip += 1
            continue
        raw_body = body_section[raw_start + 3:raw_end].strip()

        # Re-parse instruments
        pub_date = fm.get("pub_date", "")
        if hasattr(pub_date, "strftime"):
            pub_date = pub_date.strftime("%Y-%m-%d")
        elif pub_date:
            pub_date = str(pub_date)
        if not pub_date:
            n_skip += 1
            continue

        instruments_data = parse_instruments_from_body(raw_body, pub_date)

        # Build new AnnouncementItem
        item = AnnouncementItem(
            category=fm.get("category", "omo_transaction"),
            title=fm.get("title", ""),
            detail_url=fm.get("detail_url", ""),
            pub_date=pub_date,
            serial_year=str(fm.get("serial_year", "")) if fm.get("serial_year") else None,
            serial_no=str(fm.get("serial_no", "")) if fm.get("serial_no") else None,
            detail_slug=None,
            instruments_data=instruments_data,
            raw_body=raw_body,
            parse_warnings=[] if instruments_data else ["no instruments parsed"],
        )

        # Extract slug from detail_url
        mslug = RE_DETAIL_SLUG.search(item.detail_url)
        if mslug:
            item.detail_slug = mslug.group(1)

        # Write back
        new_content = item.to_markdown()
        fpath.write_text(new_content, encoding="utf-8")
        n_ok += 1

        # Regenerate per-file CSV alongside the .md
        if convert_csv:
            try:
                if convert_md_to_csv(fpath):
                    n_csv_ok += 1
            except Exception as e:
                print(f"  [WARN] CSV conversion failed for {fpath.name}: {e}", flush=True)

    print(f"[REPARSE] done: {n_ok} re-parsed, {n_skip} skipped, {n_fail} failed "
          f"({n_csv_ok} per-file CSVs written)", flush=True)

    result = {"total": n_total, "ok": n_ok, "skipped": n_skip, "failed": n_fail,
              "csv_files": n_csv_ok}

    if build_csv:
        try:
            csv_counts = build_instruments_csv(out_dir)
            result["combined_csv"] = csv_counts
        except Exception as e:
            print(f"  [ERROR] build_instruments_csv failed: {e}", flush=True)

    return result


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Download and parse PBoC repo news")
    parser.add_argument("--start-date", type=str, default=None,
                        help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", type=str, default=None,
                        help="End date (YYYY-MM-DD)")
    parser.add_argument("--reparse", action="store_true",
                        help="Re-parse existing .md files from raw body (no download)")
    parser.add_argument("--convert-csv", action="store_true", default=True,
                        help="Convert downloaded .md files to per-file CSV (default: enabled)")
    parser.add_argument("--no-convert-csv", action="store_true", default=False,
                        help="Skip per-file CSV conversion")
    parser.add_argument("--build-csv", action="store_true", default=True,
                        help="Build combined instruments_combined.csv (default: enabled)")
    parser.add_argument("--no-build-csv", action="store_true", default=False,
                        help="Skip building combined instruments CSV")
    args = parser.parse_args()

    convert_csv = args.convert_csv and not args.no_convert_csv
    build_csv = args.build_csv and not args.no_build_csv

    if args.reparse:
        out_dir = resolve_out_dir(str(Path(__file__).resolve()), "pboc_repo_news", None)
        print(reparse_existing_files(out_dir, convert_csv=convert_csv, build_csv=build_csv))
    else:
        print(download_pboc_repo_news(
            start_date=args.start_date,
            end_date=args.end_date,
            convert_csv=convert_csv,
            build_csv=build_csv,
        ))
