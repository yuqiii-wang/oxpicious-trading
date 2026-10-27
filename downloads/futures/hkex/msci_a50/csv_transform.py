"""downloads.futures.hkex.msci_a50.csv_transform — shape HKEX MCA rows into
the CFFEX daily-CSV format.

The audit drop aligns with ``temps/cffex_trend/YYYYMM/YYYYMMDD_futures.csv``
(one file per exchange date, one instrument per row, UTF-8 BOM, CRLF) so the
same downstream tooling can read both markets. The header row and encoding
are imported from the cffex trend module itself — single source of truth.

HKEX daily-report fields map onto the CFFEX columns as:
  合约代码←code (MCA<YY><M>)  今开盘←day_open  最高价←day_high
  最低价←day_low  成交量←volume (day + after-hours of the same trading
  date; "" while the after-hours report is pending)  持仓量←open_interest
  持仓变化←oi_change  今结算←settlement  前结算←prev_settlement
  涨跌1←settlement_change
  成交金额 / 今收盘 / 涨跌2 / Delta ← "" (the exchange publishes no
  turnover, no session close — the settlement IS the official close — no
  percentage change, and writes Delta empty for futures too)
"""

from __future__ import annotations

import csv
import logging
from collections import defaultdict
from pathlib import Path
from typing import DefaultDict, Dict, List

from downloads.futures.cffex.trend.config import CSV_HEADERS, OUTPUT_CSV_ENCODING

logger: logging.Logger = logging.getLogger("hkex_mca")

# Row-dict keys (fetch_daily_rows output) in CFFEX column order; None → "".
_CFFEX_COLUMN_SOURCES: Dict[str, str] = {
    "合约代码": "code",
    "今开盘": "day_open",
    "最高价": "day_high",
    "最低价": "day_low",
    "成交量": "volume",
    "成交金额": "",          # not published by HKEX daily reports
    "持仓量": "open_interest",
    "持仓变化": "oi_change",
    "今收盘": "",            # no session close published — settlement is the official close
    "今结算": "settlement",
    "前结算": "prev_settlement",
    "涨跌1": "settlement_change",
    "涨跌2": "",             # no percentage change published
    "Delta": "",             # CFFEX writes it empty for futures too
}


def _cell(value: object) -> str:
    """One CSV cell: None → "", floats/ints through str()."""
    if value is None:
        return ""
    return str(value)


def cffex_row(row: Dict[str, object]) -> List[str]:
    """Map one HKEX MCA row to a CFFEX-ordered cell list."""
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
    by 合约代码 — a refresh run (e.g. completing an earlier date's
    after-hours fields) must not thin out the other contracts' rows that
    earlier runs wrote for that date. Instruments sort by code
    (alphabetical month letters = chronological by contract month).

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
        ordered = sorted(merged.values(), key=lambda cells: cells[0])

        with open(path, "w", encoding=OUTPUT_CSV_ENCODING, newline="") as f:
            writer = csv.writer(f)
            writer.writerow(CSV_HEADERS)
            writer.writerows(ordered)
        written += 1
        logger.info("  wrote %s (%d instruments)", path.name, len(ordered))
    return written
