"""Build the daily history DataFrame (latest-missing-dates only).

Reads the CSIndex *_history.csv + *_1m.csv files (history files are
tail-read — only the last HISTORY_TAIL_ROWS rows per code, unless the code
has no DB rows at all, in which case the full file is read), plus the
SZSE / SSE / CNINDEX supplements (see loaders.py), then:
  1. Normalizes column names and units across sources.
  2. Concatenates sources; deduplicates (date, code) keeping the LAST source
     appended (priority: CNINDEX > SSE trend > SZSE > 1m > history).
  3. Backfills PE from CSIndex rows that lost the dedup (e.g. SSE trend won
     OHLCV for 000xxx codes but its PE is NULL).
  4. Optionally fills missing trading days with estimated closes.
  5. Computes moving averages (ma5, ma20, ma60, ma120, ma255) and exponential
     moving averages (ema6, ema10, ema20, ema60, ema120, ema255) over the
     FULL per-code history (must use ALL rows, not just missing, for correctness).
  6. Filters to rows that are NEW vs the DB: date > the code's latest DB
     date, or (date, code) in stale_keys.

Latest-missing-dates check (replaces the full existing-keys scan):
  * The caller passes one MAX(date) per code from stats.index_tech_stats
    (single GROUP BY) — rows are inserted per table in one transaction, so
    a code's max date >= d implies the row at d exists; only dates AFTER
    the max can be missing.
  * Every source file's last date is peeked byte-level (last CSV line —
    no parse) plus SZSE/SSE snapshot filename dates → the source "grid
    latest" date.
  * A code is NEEDED (its files are read at all) only when it has no DB
    rows (fresh ingest), has stale keys to rebuild, or its latest DB date
    is behind the grid latest (new tail data and/or daily estimated-close
    extension). Up-to-date codes are skipped entirely — no read, no parse,
    no MA recompute — which is what makes the nightly run O(changed codes)
    instead of O(all codes).
  * Up-to-date codes are absent from the frame, so their changePct at the
    estimated dates would be lost from the proxy lookup; a small recent-
    window change_pct supplement is fetched from the DB to keep proxy-
    based estimation working.

The *_1m.csv files are CSIndex's "recent 1-month" daily export with bilingual
column headers (日期Date, 开盘Open, etc.). They are appended AFTER history
so drop_duplicates(keep="last") picks the 1m version for overlapping dates —
1m has the most recent data.

--date mode (forced_date set, "YYYY-MM-DD"): the code-level needed check is
bypassed (every code is tail-read) and the final new-vs-DB filter keeps ONLY
rows at the forced date — DB missing-date skips are bypassed so the date is
always rebuilt, and rows already in the DB are refreshed through the normal
upsert write path (no truncation, no deletes). SZSE/SSE snapshot loaders
receive the forced date so their per-file date gates never skip it.
"""
from __future__ import annotations

import datetime
import glob
import os
import re

import numpy as np
import pandas as pd

from downloads._common import read_csv_gpu_safe

from builds._commons.safe_parse import safe_to_datetime, safe_to_numeric
from _common.df_utils import compute_moving_averages, compute_emas, safe_columns

from builds.index.baseline.paths import (
    CNINDEX_DIR, CSINDEX_DIR, SZSE_ARCHIVE_DIR, SZSE_TREND_DIR, SSE_TREND_DIR,
    VALID_CODE_RE,
)
from builds.index.baseline.close_estimation import fill_missing_closes
from builds.index.baseline.loaders import (
    load_szse_index_history, load_sse_index_history, load_cnindex_history,
    snapshot_file_date,
)

# B6 tail-read window: the CSIndex history archive is the full corpus
# (per-code files), yet only the most recent dates are ever missing from
# the DB. Trailing context requirements:
#   * ma255 needs the 255 rows before each new row (probe: tail == full,
#     Δ=0 on all sampled codes);
#   * ema255 (adjust=False, alpha=2/256) warm-start residual after N rows
#     is e^(-2·N/256): 800 rows → 0.19% (abs Δ up to 0.56 index points on
#     the probe), 1200 rows → 0.0085% (Δ ≈ 0.02 — below any analytical
#     significance). 1200 trailing rows satisfy both, and short files are
#     read in full automatically (the newline scan runs off the top).
# Old (date, code) pairs inside the tail are dropped by the final new-vs-DB
# filter; anything older than the tail is already in the DB under steady
# state.
HISTORY_TAIL_ROWS: int = 1200

# How far back (calendar days from the source grid latest) the DB
# change_pct supplement reaches. Needed codes estimate at dates after
# their latest DB date; codes >60 days behind degrade to carry-forward
# for older estimated dates (pathological case only).
PCT_SUPPLEMENT_WINDOW_DAYS: int = 60

_DATE_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _tail_read_history(path: str, tail_rows: int = HISTORY_TAIL_ROWS) -> pd.DataFrame | None:
    """Read only the last ~tail_rows data rows of a per-code history CSV.

    The tail boundary is located with a byte-level backward newline scan
    (C-speed ``bytes.rfind``), then the header + tail bytes are parsed
    directly from raw bytes (the GPU-clean pattern — never io.BytesIO,
    which forces a cudf.pandas slow-path fallback on every read).
    Two extra rows are included (off-by-one margin); unbalanced quotes
    (embedded newlines in quoted fields) fall back to a full parse.
    """
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    if not data:
        return None
    # CRITICAL read-path rule: normalize CRLF BEFORE any newline scanning /
    # parsing — un-normalized \r would ride on the last column's values
    # (date is the first column here, but don't rely on column order).
    data = data.replace(b"\r\n", b"\n")
    if data.count(b'"') % 2 != 0:
        return None  # unsafe to cut at a newline — caller falls back
    nl = data.find(b"\n")
    if nl == -1:
        return None  # header-only
    pos = len(data)
    for _ in range(tail_rows + 2):
        prev = data.rfind(b"\n", 0, pos)
        if prev == -1:
            break
        pos = prev
    start = max(pos + 1, nl + 1)
    payload = data[:nl + 1] + data[start:]
    if payload.startswith(b"\xef\xbb\xbf"):
        payload = payload[3:]
    try:
        return pd.read_csv(payload, dtype=str, keep_default_na=False,
                           na_values=[""], compression=None)
    except Exception:
        return None


def _csv_has_data_row(path: str) -> bool:
    """Byte-level placeholder check (B5 pattern, same as builds.etf load.py):
    True when the file has a header line AND at least one non-empty data row
    that is not the CSIndex "没有找到符合条件的数据" placeholder. Skips the
    read+parse of the 134 header-only 1m exports (each would otherwise burn
    2 to_datetime cudf fallbacks + a legacy re-parse)."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(65536)
    except OSError:
        return False
    nl = head.find(b"\n")
    if nl == -1:
        return False
    body = head[nl + 1:]
    return not (body.strip() == b"" or "没有找到符合条件的数据".encode("utf-8") in body)


def _peek_last_date(path: str) -> str | None:
    """Return the last data row's date ("YYYY-MM-DD") without parsing.

    Byte-level read of the file tail; the date is the FIRST column of both
    CSIndex history and 1m exports. Returns None when the tail can't be
    confidently parsed (caller falls back to a full read of the file).
    """
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - 4096))
            tail = fh.read()
    except OSError:
        return None
    tail = tail.replace(b"\r\n", b"\n").rstrip(b"\n")
    nl = tail.rfind(b"\n")
    line = tail[nl + 1:] if nl != -1 else tail
    if not line or b'"' in line:  # empty / quoted-embedded-newline risk
        return None
    first = line.split(b",", 1)[0].decode("utf-8", errors="replace").strip()
    return first if _DATE_ISO_RE.match(first) else None


def _normalize_and_clean(df: pd.DataFrame, code: str) -> pd.DataFrame:
    """Apply backward-compat column renames + numeric parsing + date parsing
    + 亿元→yuan conversion. Shared by history and 1m loaders.
    """
    df["code"] = code
    _cols = [str(c) for c in df.columns]

    # Backward compat: old CSVs use "turnover", new ones use "trading_amount"
    if "turnover" in _cols and "trading_amount" not in _cols:
        df = df.rename(columns={"turnover": "trading_amount"})
    # Backward compat: old CSVs use "shares", new schema uses "trading_shares"
    if "shares" in _cols and "trading_shares" not in _cols:
        df = df.rename(columns={"shares": "trading_shares"})
    # history/1m CSVs use "volume"/"amount"; DB schema uses "trading_shares"/"trading_amount"
    if "volume" in _cols and "trading_shares" not in _cols:
        df = df.rename(columns={"volume": "trading_shares"})
    if "amount" in _cols and "trading_amount" not in _cols:
        df = df.rename(columns={"amount": "trading_amount"})

    _cols = [str(c) for c in df.columns]
    for col in ["open", "high", "low", "close", "trading_shares", "trading_amount", "change", "changePct", "pe", "consNumber"]:
        if col in _cols:
            df[col] = safe_to_numeric(df[col])
    # CSIndex history trading_amount is in 亿元 → convert to yuan to match
    # the "yuan everywhere" DB convention.
    if "trading_amount" in _cols:
        df["trading_amount"] = df["trading_amount"] * 1e8  # 亿元 → yuan

    df["date"] = safe_to_datetime(df["date"])
    df = df.dropna(subset=["date"])
    return df


def _normalize_1m_headers(df: pd.DataFrame, code: str) -> pd.DataFrame:
    """Normalize bilingual column headers of a *_1m.csv (e.g. 日期Date → date).

    After header normalization, delegates to _normalize_and_clean for numeric
    parsing + unit conversion.
    """
    rename_map = {}
    for col in df.columns:
        s = str(col)
        sl = s.lower()
        if "日期" in s or sl == "date":
            rename_map[col] = "date"
        elif "代码" in s and "code" in sl:
            rename_map[col] = "indexCode"
        elif "中文简称" in s:
            rename_map[col] = "indexName"
        elif "开盘" in s or sl == "open":
            rename_map[col] = "open"
        elif "最高" in s or sl == "high":
            rename_map[col] = "high"
        elif "最低" in s or sl == "low":
            rename_map[col] = "low"
        elif "收盘" in s or sl == "close":
            rename_map[col] = "close"
        elif "涨跌幅" in s or "change%" in sl or "changepct" in sl or "change(" in sl:
            rename_map[col] = "changePct"
        elif "涨跌" in s or sl == "change":
            rename_map[col] = "change"
        elif "成交量" in s or "volume" in sl:
            rename_map[col] = "volume"
        elif "成交金额" in s or "turnover" in sl or "amount" in sl:
            rename_map[col] = "amount"
        elif "样本" in s or "cons" in sl:
            rename_map[col] = "consNumber"
    df = df.rename(columns=rename_map)

    _cols = [str(c) for c in df.columns]
    if "indexName" in _cols:
        df["indexName"] = df["indexName"].fillna("")
    if "pe" not in _cols:
        df["pe"] = np.nan  # float64 (None → object dtype breaks concat dtype)

    return _normalize_and_clean(df, code)


def _estimate_ohl_from_close(combined: pd.DataFrame, verbose: bool = True) -> pd.DataFrame:
    """Synthesize OHLC for close-only rows and flag them is_ohl_estimated.

    CSIndex does not publish intraday Open/High/Low for thematic-index
    history before 2024-10-24 (verified via fresh from2020 export: 931994
    has OHLC on only 452/1616 rows) — those rows arrive with empty (or
    legacy 0.00) OHLC and a real close. For every row where open, high AND
    low are all missing/zero:

      * open  = previous close of the same code (first row of a code has
        no predecessor → open stays NULL),
      * high  = close, low = close,
      * is_ohl_estimated = True.

    Rows with any real OHLC component are left untouched (FALSE). Runs
    AFTER fill_missing_closes so estimated-close rows (open/high/low = NaN)
    get the same treatment; `combined` is sorted by [code, date] at this
    point (post-concat sort + close_estimation's re-sort).

    Vectorized (groupby shift + np.where) — no per-row iteration.
    """
    src_cols = safe_columns(combined)
    for _c in ("open", "high", "low"):
        if _c not in src_cols:
            combined[_c] = np.nan

    def _absent(col: str) -> pd.Series:
        s = safe_to_numeric(combined[col])
        return s.isna() | (s == 0)

    need = _absent("open") & _absent("high") & _absent("low") \
        & combined["close"].notna()
    prev_close = combined.groupby("code", sort=False)["close"].shift(1)

    combined["is_ohl_estimated"] = need
    fill_open = need & prev_close.notna()
    combined["open"] = np.where(fill_open, prev_close,
                                np.asarray(safe_to_numeric(combined["open"])))
    combined["high"] = np.where(need, combined["close"],
                                np.asarray(safe_to_numeric(combined["high"])))
    combined["low"] = np.where(need, combined["close"],
                               np.asarray(safe_to_numeric(combined["low"])))

    if verbose:
        n_est = int(np.asarray(need, dtype=bool).sum())
        if n_est:
            n_no_prev = int(np.asarray(fill_open, dtype=bool).sum())
            print(f"    [EST] OHLC estimation: {n_est:,} close-only rows "
                  f"synthesized (open=prev close, high=low=close); "
                  f"{n_est - n_no_prev:,} without prev close left open=NULL",
                  flush=True)

    return combined


async def build_daily_df(conn,
                         latest_dates: dict,
                         stale_keys: set = None,
                         shared_weights: dict = None,
                         verbose: bool = True,
                         code_filter: str | None = None,
                         forced_date: str | None = None
                         ) -> pd.DataFrame:
    """Read *_history.csv + *_1m.csv files, compute MAs, keep new-vs-DB rows.

    Latest-missing-dates detection: *latest_dates* maps code → "YYYY-MM-DD"
    (per-code MAX(date) from stats.index_tech_stats; empty in --force mode
    meaning every code is fresh). Source files are peeked for their last
    date; a code is loaded only when it is FRESH (no DB rows), STALE (in
    *stale_keys*, composite "YYYY-MM-DD|code" strings), or BEHIND the
    source grid latest date. The final filter keeps only rows after the
    code's latest DB date or in stale_keys — no full existing-keys set.

    --date mode (*forced_date* = "YYYY-MM-DD"): the needed-code check is
    bypassed (every code is loaded) and the final filter keeps ONLY rows at
    the forced date — the DB missing-date skip is bypassed so the date is
    always rebuilt via the upsert write path.

    When *code_filter* is set (bare 6-digit index code), only that code's
    source files are read: CSIndex/SSE-trend/SZSE data are pushed down to
    the per-code loaders so a --code build never parses other indices' rows.

    Args:
        conn: asyncpg connection (change_pct supplement for estimation).
        latest_dates: {code: "YYYY-MM-DD"} latest DB date per code.
        stale_keys: {"YYYY-MM-DD|code", ...} rows to force-rebuild.
        shared_weights: dict of {(code_a, code_b): shared_weight} from
                        sec_composition. Used to fill missing trading days
                        with estimated close prices. If None, no estimation.
        code_filter: restrict the build to a single index code.
        forced_date: --date mode — restrict the build to this single date
                     ("YYYY-MM-DD") and bypass the DB missing-date skip.

    Returns a DataFrame with MA columns, filtered to new-vs-DB rows.
    """
    stale_keys = stale_keys or set()
    stale_codes = {k.split("|", 1)[1] for k in stale_keys}
    dfs = []
    n_skipped_files = 0

    # ---- Source file inventory + last-date peeks --------------------------
    hist_pattern = f"{code_filter}_history.csv" if code_filter else "*_history.csv"
    onem_pattern = f"{code_filter}_1m.csv" if code_filter else "*_1m.csv"
    history_files = sorted(glob.glob(os.path.join(CSINDEX_DIR, hist_pattern)))
    onem_files = sorted(glob.glob(os.path.join(CSINDEX_DIR, onem_pattern)))
    cnindex_files = sorted(glob.glob(os.path.join(CNINDEX_DIR, "*_history.csv")))
    szse_files = sorted(
        glob.glob(os.path.join(SZSE_ARCHIVE_DIR, "szse_index_*.csv"))
        + glob.glob(os.path.join(SZSE_TREND_DIR, "szse_trend_index_*.csv")))
    sse_files = sorted(glob.glob(os.path.join(SSE_TREND_DIR, "sse_trend_index_*.csv")))

    # grid_latest = newest date any source could contribute (byte-level
    # last-line peek for per-code files, filename date for snapshots).
    grid_latest: str | None = None
    for path in (history_files + onem_files + cnindex_files):
        grid_latest = max(grid_latest or "", _peek_last_date(path) or "")
    for path in (szse_files + sse_files):
        grid_latest = max(grid_latest or "", snapshot_file_date(path) or "")
    grid_latest = grid_latest or None
    if verbose:
        print(f"    [DAILY] {len(history_files)} history + {len(onem_files)} 1m CSVs in {CSINDEX_DIR}", flush=True)
        print(f"    [DAILY] source grid latest date: {grid_latest or '(none)'}", flush=True)

    # A code is needed when fresh (no DB rows), stale (rebuild), or its
    # latest DB date is behind the grid latest (new tail data and/or the
    # daily estimated-close extension). --date mode bypasses this gate:
    # every code is loaded and the final filter keeps only the forced date.
    def _is_needed(code: str) -> bool:
        if forced_date is not None:
            return True
        if code not in latest_dates or code in stale_codes:
            return True
        return grid_latest is not None and latest_dates[code] < grid_latest

    if verbose:
        all_codes = {os.path.basename(p).split("_", 1)[0]
                     for p in history_files + onem_files}
        all_codes |= {os.path.basename(p).replace("_history.csv", "")
                      for p in cnindex_files}
        n_uptodate = sum(1 for c in all_codes if not _is_needed(c))
        print(f"    [DAILY] {len(all_codes) - n_uptodate} of {len(all_codes)} CSV codes "
              f"behind grid latest → loading; {n_uptodate} up to date → skipped", flush=True)

    # ---- Load *_history.csv (trailing tail per code; B6) ------------------
    # Codes with NO DB rows at all (fresh ingest or --force after truncate)
    # are missing every date, so MA correctness demands their FULL history —
    # tail-read only codes that already have DB rows.
    for path in history_files:
        code = os.path.basename(path).replace("_history.csv", "")
        # Skip codes that violate the DB check constraint (e.g. CES100)
        # before parsing anything.
        if not VALID_CODE_RE.match(code):
            n_skipped_files += 1
            continue
        if not _is_needed(code):
            continue
        if code in latest_dates:
            df = _tail_read_history(path)
            if df is None:
                # Unsafe/odd tail cut (unbalanced quotes etc.) or
                # unreadable — fall back to the plain full read.
                df = read_csv_gpu_safe(path, dtype=str)
        else:
            df = read_csv_gpu_safe(path, dtype=str)
        if df is None or len(df) == 0:
            continue
        df = _normalize_and_clean(df, code)
        dfs.append(df)

    # ---- Load *_1m.csv (recent 1-month export, bilingual headers) --------
    # Appended AFTER history so drop_duplicates(keep="last") below picks
    # the 1m version for overlapping dates — 1m has the most recent data.
    n_1m_loaded = 0
    n_1m_skipped = 0
    for path in onem_files:
        code = os.path.basename(path).replace("_1m.csv", "")
        if not VALID_CODE_RE.match(code) or not _is_needed(code):
            if VALID_CODE_RE.match(code):
                n_1m_skipped += 1
            continue
        if not _csv_has_data_row(path):
            n_1m_skipped += 1
            continue
        df = read_csv_gpu_safe(path, dtype=str)
        if df is None or len(df) == 0:
            continue

        df = _normalize_1m_headers(df, code)
        dfs.append(df)
        n_1m_loaded += 1

    if verbose and n_1m_loaded:
        print(f"    [DAILY] loaded {n_1m_loaded} 1m CSVs "
              f"(+{n_1m_skipped} header-only / up-to-date skipped)", flush=True)

    # Also load SZSE index data (archive + trend) for 399001 / 399006.
    # Per-date snapshots contribute only rows AT their filename date, so a
    # snapshot whose date is covered by every keep code (or belongs to a
    # fully up-to-date state) is skipped without being read.
    szse_dfs = load_szse_index_history(verbose=verbose, code_filter=code_filter,
                                       latest_dates=latest_dates,
                                       forced_date=forced_date)
    for df in szse_dfs:
        dfs.append(df)

    # Also load SSE index trend data (today's EOD snapshot for ~200 SSE indices)
    sse_dfs = load_sse_index_history(verbose=verbose, code_filter=code_filter,
                                     latest_dates=latest_dates,
                                     forced_date=forced_date)
    for df in sse_dfs:
        dfs.append(df)

    # Also load CNINDEX (国证指数) history for 399303 / 399310 / 399311
    cnindex_dfs = load_cnindex_history(verbose=verbose, code_filter=code_filter)
    for df in cnindex_dfs:
        dfs.append(df)

    if n_skipped_files and verbose:
        print(f"    [DAILY] skipped {n_skipped_files} files (code violates DB check constraint)", flush=True)

    if not dfs:
        print("    [WARN] No new daily data to process", flush=True)
        return pd.DataFrame()

    combined = pd.concat(dfs, ignore_index=True)
    combined["code"] = combined["code"].astype(str).str.strip()
    combined = combined.sort_values(["code", "date"]).reset_index(drop=True)

    # Composite "YYYY-MM-DD|code" keys — ONE numpy transfer per column plus a
    # pure-host zip pass (no iterrows/apply; each would be a cudf.pandas
    # slow-path fallback PER ROW). Used by the PE backfill lookup below and
    # the final new-vs-DB filter.
    def _composite_keys(frame: pd.DataFrame) -> list:
        dvals = np.asarray(frame["date"]).astype("datetime64[D]").tolist()
        cvals = np.asarray(frame["code"]).tolist()
        return [f"{d}|{c}" for d, c in zip(dvals, cvals)]

    # Build a PE lookup from ALL sources BEFORE dedup. CSIndex history/1m
    # DataFrames carry PE (peg) from the CSIndex API; SSE trend and SZSE
    # data do not. After dedup, SSE trend wins for 000xxx codes (fresh OHLCV)
    # but its PE is NULL — this lookup fills those gaps.
    keys_pre = _composite_keys(combined)
    pe_lookup: dict = {}
    src_cols = safe_columns(combined)
    if "pe" in src_cols:
        pe_keep = np.asarray(combined["pe"].notna()).tolist()
        pe_vals = np.asarray(combined["pe"]).tolist()
        pe_lookup = {k: v for k, v, keep in zip(keys_pre, pe_vals, pe_keep) if keep}
        if verbose and pe_lookup:
            print(f"    [DAILY] PE lookup: {len(pe_lookup):,} (date, code) pairs with PE "
                  f"(from CSIndex)", flush=True)

    # Deduplicate (date, code) pairs: keep="last" picks the 1m version over
    # the history version for overlapping dates, since 1m DataFrames are
    # appended after history. SSE trend (appended after CSIndex) wins for
    # 000xxx codes — its fresh OHLCV is preserved. CNINDEX (appended last)
    # wins for 399303/399310/399311.
    n_before_dedup = len(combined)
    combined = combined.drop_duplicates(subset=["date", "code"], keep="last")
    combined = combined.reset_index(drop=True)
    n_after_dedup = len(combined)
    if verbose and n_before_dedup != n_after_dedup:
        print(f"    [DAILY] dedup: {n_before_dedup:,} → {n_after_dedup:,} rows "
              f"(1m/SZSE/SSE/CNINDEX overrode history for {n_before_dedup - n_after_dedup:,} dates)",
              flush=True)

    # Fill missing PE from the pre-dedup lookup. SSE trend rows won the dedup
    # for 000xxx codes but have NULL PE; CSIndex rows (which lost the dedup)
    # had PE — this merges it back without overriding OHLCV. Vectorized via
    # composite keys (no combined.apply(axis=1) — one fallback PER ROW).
    if pe_lookup and "pe" in src_cols:
        n_pe_missing_before = int(combined["pe"].isna().sum())
        keys_post = _composite_keys(combined)
        fill_vals = np.array(
            [np.nan if (v := pe_lookup.get(k)) is None else float(v) for k in keys_post],
            dtype=float,
        )
        need = np.asarray(combined["pe"].isna()) & ~np.isnan(fill_vals)
        if bool(need.any()):
            # Whole-array where (no boolean .loc row addressing)
            combined["pe"] = np.where(need, fill_vals,
                                      np.asarray(combined["pe"], dtype=float))
        n_pe_filled = n_pe_missing_before - int(combined["pe"].isna().sum())
        if verbose and n_pe_filled:
            print(f"    [DAILY] PE merge: filled {n_pe_filled:,} NULL PE values from CSIndex lookup",
                  flush=True)

    # Fill missing trading days with estimated close prices (if shared weights
    # available). Up-to-date codes are not loaded, so their changePct at the
    # estimated dates is supplemented from the DB (see docstring).
    if shared_weights:
        pct_supplement = await _fetch_pct_supplement(
            conn, latest_dates, stale_codes, grid_latest)
        combined = fill_missing_closes(combined, shared_weights,
                                       verbose=verbose,
                                       pct_supplement=pct_supplement)

    # Close-only rows (CSIndex thematic-index history before 2024-10-24)
    # → open = prev close, high = low = close, is_ohl_estimated = True.
    combined = _estimate_ohl_from_close(combined, verbose=verbose)

    # Compute MAs over full per-code history (must use ALL rows, not just missing)
    compute_moving_averages(
        combined,
        group_key="code",
        value_col="close",
        windows=[5, 20, 60, 120, 255],
    )
    # Compute EMAs over full per-code history (same correctness constraint).
    # Stays on pandas: cuDF lacks grouped-ewm support (see
    # analyze/mov_ave_spread/rsi.py for the same constraint).
    compute_emas(
        combined,
        group_key="code",
        value_col="close",
        spans=[6, 10, 20, 60, 120, 255],
    )

    # Filter to rows NEW vs the DB — rows after the code's latest DB date,
    # plus stale (rebuild) keys. Composite-string-key host pass replaces
    # combined.apply(axis=1) tuple lookups.
    if forced_date is not None:
        # --date mode: keep ONLY rows at the forced date — the DB missing-
        # date skip is bypassed so existing rows are refreshed through the
        # upsert write path (no truncation, no deletes).
        keys_final = _composite_keys(combined)
        keep_mask = np.asarray([
            d == forced_date
            for d in (s.split("|", 1)[0] for s in keys_final)
        ])
    elif latest_dates:
        keys_final = _composite_keys(combined)
        keep_mask = np.asarray([
            latest_dates.get(c) is None or d > latest_dates[c] or k in stale_keys
            for d, c, k in zip(
                (s.split("|", 1)[0] for s in keys_final),
                np.asarray(combined["code"]).tolist(),
                keys_final,
            )
        ])
    else:
        # --force: no DB rows → everything is new.
        keep_mask = np.ones(len(combined), dtype=bool)
    combined = combined[keep_mask].reset_index(drop=True)

    if verbose:
        print(f"    → {len(combined):,} new rows  ·  {combined['code'].nunique()} indexes", flush=True)
        if len(combined):
            # np datetime64[D] formatting — direct f-string of the proxied
            # scalar triggers a date.__format__ cudf fallback
            d_np = np.asarray(combined["date"]).astype("datetime64[D]")
            print(f"    → date range: {d_np.min()} → {d_np.max()}", flush=True)

    return combined


async def _fetch_pct_supplement(conn,
                                latest_dates: dict,
                                stale_codes: set,
                                grid_latest: str | None,
                                ) -> tuple[np.ndarray, list, np.ndarray] | None:
    """Fetch recent change_pct rows for codes NOT loaded (up to date).

    Proxy-based close estimation needs the proxy's changePct at the dates
    being estimated. Up-to-date codes contribute no CSV rows, so their
    recent pcts are read from the DB over a bounded window.

    Returns (dates_d64, codes, pcts) numpy/host arrays — a dict-built
    DataFrame ctor + pd.to_datetime on asyncpg date objects each emit a
    cudf fallback per call, and change_pct arrives as Decimal objects
    that pd.to_numeric cannot parse on GPU.
    """
    needed_known = [c for c in latest_dates if c not in stale_codes]
    if not needed_known or grid_latest is None:
        return None
    min_needed = min(latest_dates[c] for c in needed_known)
    window_start = max(
        min_needed,
        (datetime.date.fromisoformat(grid_latest)
         - datetime.timedelta(days=PCT_SUPPLEMENT_WINDOW_DAYS)).isoformat(),
    )
    rows = await conn.fetch(
        "SELECT date, code, change_pct FROM stats.index_basic_stats "
        "WHERE date > $1 AND date <= $2",
        datetime.date.fromisoformat(window_start),
        datetime.date.fromisoformat(grid_latest),
    )
    if not rows:
        return None
    # numpy casts Decimal/date objects natively — zero proxy calls
    s_d64 = np.asarray([r["date"] for r in rows], dtype="datetime64[D]")
    s_codes = [str(r["code"]) for r in rows]
    s_pcts = np.asarray([r["change_pct"] for r in rows], dtype=float)
    return s_d64, s_codes, s_pcts
