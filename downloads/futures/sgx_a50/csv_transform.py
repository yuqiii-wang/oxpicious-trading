"""downloads.futures.sgx_a50.csv_transform — shape A50 rows into the CFFEX
daily-CSV format.

The audit drop aligns with ``temps/cffex_trend/YYYYMM/YYYYMMDD_futures.csv``
(one file per exchange date, one instrument per row, UTF-8 BOM, CRLF) so the
same downstream tooling can read both markets. The header row and encoding
are imported from the cffex trend module itself — single source of truth.

Eastmoney fields map onto the CFFEX columns as:
  合约代码←code  今开盘←open  最高价←high  最低价←low  成交量←volume
  成交金额←amount (EM reports 0 for global futures)  今收盘←close
  涨跌1←change (vs prev settlement)  涨跌2←change_pct
  持仓量 / 持仓变化 / 今结算 / 前结算 / Delta ← "" (not published by EM klines)
"""

from __future__ import annotations

import csv
import logging
from collections import defaultdict
from pathlib import Path
from typing import DefaultDict, Dict, List

from downloads.futures.cffex.trend.config import CSV_HEADERS, OUTPUT_CSV_ENCODING

logger: logging.Logger = logging.getLogger("sgx_a50")

# Row-dict keys (fetch_daily_rows output) in CFFEX column order; None → "".
_CFFEX_COLUMN_SOURCES: Dict[str, str] = {
    "合约代码": "code",
    "今开盘": "open",
    "最高价": "high",
    "最低价": "low",
    "成交量": "volume",
    "成交金额": "amount",
    "持仓量": "",          # not published by EM klines
    "持仓变化": "",        # not published by EM klines
    "今收盘": "close",
    "今结算": "",          # not published by EM klines
    "前结算": "",          # not published by EM klines
    "涨跌1": "change",
    "涨跌2": "change_pct",
    "Delta": "",           # CFFEX writes it empty for futures too
}


def _cell(value: object) -> str:
    """One CSV cell: None → "", floats/ints through str()."""
    if value is None:
        return ""
    return str(value)


def cffex_row(row: Dict[str, object]) -> List[str]:
    """Map one long-format A50 row to a CFFEX-ordered cell list."""
    cells: List[str] = []
    for header in CSV_HEADERS:
        source = _CFFEX_COLUMN_SOURCES[header]
        cells.append(_cell(row.get(source)) if source else "")
    return cells


def _read_existing(path: Path) -> Dict[str, List[str]]:
    """Existing rows of a date file keyed by 合约代码 (empty when absent)."""
    if not path.exists():
        return {}
    with open(path, "r", encoding=OUTPUT_CSV_ENCODING, newline="") as f:
        reader = csv.reader(f)
        next(reader, None)  # header
        return {row[0]: row for row in reader if row}


def write_cffex_style_files(rows: List[Dict[str, object]], out_dir: Path) -> int:
    """Group fetched rows by date and write CFFEX-style per-date files.

    Rows are grouped under ``out_dir/YYYYMM/YYYYMMDD_futures.csv``. When a
    file already exists, its rows are kept and this run's rows overlay them
    by 合约代码 — a daily tail run (continuous only) must not thin out the
    contract rows earlier runs wrote for that date. Instruments sort
    continuous-first then by code (= chronological by contract month).

    Returns the number of files written.
    """
    by_date: DefaultDict[str, Dict[str, List[str]]] = defaultdict(dict)
    for row in rows:
        cells = cffex_row(row)
        date_str = str(row["date"]).replace("-", "")
        by_date[date_str][cells[0]] = cells

    written = 0
    for date_str in sorted(by_date):
        month_dir = out_dir / date_str[:6]
        month_dir.mkdir(parents=True, exist_ok=True)
        path = month_dir / f"{date_str}_futures.csv"

        merged = _read_existing(path)
        merged.update(by_date[date_str])
        ordered = sorted(
            merged.values(),
            key=lambda cells: (cells[0] != "CN00Y", cells[0]),
        )

        with open(path, "w", encoding=OUTPUT_CSV_ENCODING, newline="") as f:
            writer = csv.writer(f)
            writer.writerow(CSV_HEADERS)
            writer.writerows(ordered)
        written += 1
        logger.info("  wrote %s (%d instruments)", path.name, len(ordered))
    return written
