"""Exchange-level margin summary (analysis.exchange_margin_summary) —
shared reader + incremental sync for the daily builds.stock / builds.etf
margin passes.

Source: the per-date MARKET-WIDE summary CSVs the downloads layer already
writes alongside the per-security margin detail exports — previously
downloaded but unconsumed by any build:

  temps/sse_margin/sse_margin_summary_YYYYMMDD.csv   (SSE RZRQ_HZ_INFO)
  temps/szse_margin/szse_margin_summary_YYYYMMDD.csv (SZSE 1837_xxpl tab1)

One data row per file. Column coverage differs by exchange (SSE carries
信用交易日期 + 融资偿还额; SZSE has neither — its date comes from the
FILENAME); the reader stores the UNION schema, source columns a file
lacks surface as NULL.

NULL convention (matches the 2026-10-19 DDL, NOT the per-security margin
builds): NULL = that exchange's export has no such column / blank value;
0 is a REAL reported zero. The numeric columns are never fillna'd.

Grain/perf model: ~1,600+ tiny files per exchange — one cudf CSV engine
launch per file costs ~10s of pure parser overhead (profiled in
builds/stock/margin), so files are grouped by EXACT header bytes and each
group is parsed with ONE pd.read_csv call, with "date" (from the
filename) and "exchange" injected as leading quoted columns. Total data
is ~3.4K rows — no chunking needed. The host-numpy boundary is the row
emission itself (records_from_frame, one transfer per column).
"""
from __future__ import annotations

import glob
import io
import os

import numpy as np
import pandas as pd

from _common.build_commons import ymd_from_filename, ymd_to_date
from _common.df_utils import safe_columns
from builds._commons.paths import SSE_MARGIN_DIR, SZSE_MARGIN_DIR
from builds._commons.row_emission import records_from_frame

import logging
logger = logging.getLogger(__name__)

TABLE = "analysis.exchange_margin_summary"

# Unified target columns (DDL: database/sql/analysis/28_exchange_margin_summary.sql)
_TARGET_COLS: list[str] = [
    "rz_balance", "rz_buy", "rz_repay", "rq_sell_qty",
    "rq_balance_qty", "rq_balance_amt", "total_balance",
]

# Source header → target column, UNION over both exchanges (a header group
# renames only the keys it actually has; SSE 融券余量/融券卖出量 are unitless,
# SZSE carries the (股/份) suffix — same semantics, distinct header strings).
_SUMMARY_RENAME: dict[str, str] = {
    "融资余额(元)": "rz_balance",
    "融资买入额(元)": "rz_buy",
    "融资偿还额(元)": "rz_repay",
    "融券余量": "rq_balance_qty",
    "融券余量(股/份)": "rq_balance_qty",
    "融券余量金额(元)": "rq_balance_amt",
    "融券余额(元)": "rq_balance_amt",
    "融券卖出量": "rq_sell_qty",
    "融券卖出量(股/份)": "rq_sell_qty",
    "融资融券余额(元)": "total_balance",
}

# Superset dtype contract: read_csv ignores keys absent from a group's
# header; injected leading columns are str. A parse error is a
# downloads-conversion bug — the group is skipped loudly (degrade, don't
# poison the run), never zero-filled.
_SUMMARY_DTYPES: dict[str, str] = {
    "date": str, "exchange": str,
    **{src: "float64" for src in _SUMMARY_RENAME},
}

_PLACEHOLDER_MARKS: tuple[bytes, ...] = (
    "没有找到符合条件的数据".encode(), "无数据".encode(),
)

# (scan_dir, filename prefix, exchange code) — exchange matches the
# canonical source-CSV values and the DDL CHECK constraint.
_SUMMARY_SOURCES: list[tuple[str, str, str]] = [
    (SZSE_MARGIN_DIR, "szse_margin_summary_", "SZ"),
    (SSE_MARGIN_DIR, "sse_margin_summary_", "SS"),
]


def _glob_summary_files() -> list[tuple[str, str, str]]:
    """(path, exchange, filename prefix) for both exchanges' summary CSVs."""
    out: list[tuple[str, str, str]] = []
    for scan_dir, prefix, exchange in _SUMMARY_SOURCES:
        if os.path.isdir(scan_dir):
            out.extend(
                (p, exchange, prefix)
                for p in sorted(glob.glob(os.path.join(scan_dir, f"{prefix}*.csv")))
            )
    return out


def _file_data_bytes(path: str) -> tuple[bytes, list[bytes]] | None:
    """(header, data lines) when the file carries at least one real data
    row; None for holiday/placeholder/empty exports (byte-level, files are
    ~200 bytes so a full read is cheaper than any peek)."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError:
        return None
    raw = raw.replace(b"\r\n", b"\n")
    if not raw.strip(b"\xef\xbb\xbf \t"):
        return None
    lines = raw.split(b"\n")
    header = lines[0]
    if header.startswith(b"\xef\xbb\xbf"):
        header = header[3:]
    data = [ln for ln in lines[1:] if ln.strip()]
    if not data or any(m in data[0] for m in _PLACEHOLDER_MARKS):
        return None
    return header, data


def _parse_summary_files(
    items: list[tuple[str, str, str]], verbose: bool = True,
) -> pd.DataFrame:
    """Read the given summary files into the unified long DataFrame.

    Byte-grouped by exact header; one pd.read_csv per group with
    "date" (filename) + "exchange" injected as leading quoted columns.
    """
    groups: dict[bytes, list[bytes]] = {}
    n_empty = 0
    for path, exchange, prefix in items:
        ymd = ymd_from_filename(path, prefix)
        if not ymd:
            continue
        parsed = _file_data_bytes(path)
        if parsed is None:
            n_empty += 1
            continue
        header, data = parsed
        tag = f'"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}","{exchange}",'.encode("ascii")
        group = groups.setdefault(header, [])
        group.extend(tag + ln for ln in data)

    frames: list[pd.DataFrame] = []
    for header, dlines in groups.items():
        merged_header = b'"date","exchange",' + header
        merged = b"\n".join([merged_header] + dlines)
        try:
            # compression=None: the chunk is our own uncompressed bytes.
            # Raw bytes take the cudf GPU read path; host pandas cannot
            # consume bytes, so fall back to a BytesIO wrapper.
            try:
                df = pd.read_csv(merged, dtype=_SUMMARY_DTYPES, compression=None)
            except TypeError:
                df = pd.read_csv(io.BytesIO(merged), dtype=_SUMMARY_DTYPES,
                                 compression=None)
        except Exception as e:
            logger.warning(f"    [XCHG-SUMMARY] header-group parse failed "
                  f"(~{len(dlines)} rows dropped): {type(e).__name__}: {e}")
            continue
        df = df.rename(columns=_SUMMARY_RENAME)
        frames.append(df)

    if not frames:
        return pd.DataFrame(columns=["date", "exchange"] + _TARGET_COLS)

    out = pd.concat(frames, ignore_index=True)
    # Missing target columns (e.g. SZSE has no 融资偿还额) and in-group
    # blanks stay NaN — they emit as NULL (the documented convention).
    out_cols = safe_columns(out)
    for c in _TARGET_COLS:
        if c not in out_cols:
            out[c] = np.nan
        else:
            out[c] = out[c].astype("float64")
    out["date"] = pd.to_datetime(out["date"])
    if verbose:
        logger.info(f"    [XCHG-SUMMARY] parsed {len(out)} row(s) from "
              f"{len(groups)} header group(s), {n_empty} empty file(s) skipped")
    return out


async def sync_exchange_margin_summary_async(
    conn,
    *,
    force: bool = False,
    forced_date=None,
    verbose: bool = True,
) -> int:
    """Incremental (exchange, date) sync of the summary CSVs into
    analysis.exchange_margin_summary. Returns rows upserted (0 = up to date).

    Target selection:
      * default        — loadable files whose (exchange, date) is missing
                         from the table (empty markers never become rows;
                         when downloads later fills the file, the date
                         loads on a subsequent run).
      * forced_date    — that single date ALWAYS re-upserts (bypasses the
                         missing check; no truncation).
      * force          — truncate the table, reload ALL loadable files.
                         Callers pass force=False for code-scoped runs
                         (the table has no code dimension).
    """
    loadable: list[tuple[str, object, str, str]] = []
    for path, exchange, prefix in _glob_summary_files():
        ymd = ymd_from_filename(path, prefix)
        if not ymd:
            continue
        d = ymd_to_date(ymd)
        if d is None or _file_data_bytes(path) is None:
            continue
        loadable.append((exchange, d, path, prefix))

    if forced_date is not None:
        targets = [t for t in loadable if t[1] == forced_date]
    elif force:
        targets = loadable
    else:
        existing = await conn.fetch(
            f"SELECT DISTINCT exchange, date FROM {TABLE}"
        )
        existing_set = {(r["exchange"], r["date"]) for r in existing}
        targets = [t for t in loadable if (t[0], t[1]) not in existing_set]

    if not targets:
        if verbose:
            logger.info(f"    [XCHG-SUMMARY] up to date "
                  f"({len(loadable)} loadable summary files)")
        return 0
    if verbose:
        logger.info(f"    [XCHG-SUMMARY] {len(targets)} summary file(s) to "
              f"load (of {len(loadable)} loadable)")

    df = _parse_summary_files(
        [(path, exchange, prefix) for exchange, _d, path, prefix in targets],
        verbose=verbose,
    )
    if len(df) == 0:
        return 0

    if force and forced_date is None:
        from _common.db_commons import truncate_table_async
        await truncate_table_async(conn, TABLE)
        if verbose:
            logger.info(f"    [XCHG-SUMMARY] {TABLE} truncated (force mode)")

    mdf = df.to_pandas() if hasattr(df, "to_pandas") else df
    cols = ["exchange", "date"] + _TARGET_COLS
    rows = records_from_frame(mdf[cols], cols)
    from _common.db_commons import bulk_upsert_async
    n = await bulk_upsert_async(conn, TABLE, rows, key_columns=["exchange", "date"])

    # analysis_identity heartbeat (row seeded by the DDL)
    await conn.execute(
        "INSERT INTO analysis.analysis_identity (name, last_run_datetime) "
        "VALUES ('exchange_margin_summary', NOW()) "
        "ON CONFLICT (name) DO UPDATE SET last_run_datetime = NOW()"
    )
    if verbose:
        logger.info(f"    [XCHG-SUMMARY] {n:,} rows upserted into {TABLE}")
    return n
