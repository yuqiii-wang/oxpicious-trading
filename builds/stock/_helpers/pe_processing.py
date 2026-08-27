"""SSE PE file reading, PE archive reading, PE estimation, and merge.

Contains:
- _read_sse_pe_files: read SSE PE files ({code}_pe.csv) with DB-first filtering
- _read_sse_archive_trend_files: read SSE per-stock {code}_trend.csv archive files
- fetch_pe_estimate_candidates: DB-driven estimation source of truth
- _read_sse_pe_files: independent SSE PE pass (latest-missing-dates gated)
- estimate_missing_pe_async: fill missing PE from last actual PE (constant-EPS)
"""
from __future__ import annotations

import bisect
import os
from collections import defaultdict
from datetime import date, datetime
from itertools import groupby
from operator import itemgetter

import numpy as np
import pandas as pd
from dateutil.relativedelta import relativedelta

from _common.build_commons import (
    glob_source_files,
    get_max_table_date_async,
    rec_col,
)

from downloads._common import read_csv_gpu_safe

from builds.stock._helpers.helpers import (
    _safe_to_datetime,
    _safe_to_numeric,
    _safe_columns,
    _peek_csv_max_date,
    _read_one,
)

# Maximum age (in calendar months) of a baseline actual-PE row usable for
# estimating PE on a missing-PE date.
PE_ESTIMATE_MAX_MONTHS: int = 3


def _file_mtime_date(path: str) -> date | None:
    """Local calendar date of a file's last modification, or None."""
    try:
        return datetime.fromtimestamp(os.path.getmtime(path)).date()
    except OSError:
        return None


async def _read_sse_pe_files(
    sse_pe_dir: str,
    conn=None,
    force: bool = False,
    verbose: bool = True,
    code_filter: str | None = None,
) -> pd.DataFrame:
    """Read SSE PE files ({code}_pe.csv) and return a DataFrame with
    (date, code, name, pe) columns.

    Decoupled PE pass — independent from the OHLCV combined frame.
    Latest-missing-dates semantics at TWO levels:
      file gate (existing):
      1. the stock code comes from the FILENAME ({bare}_pe.csv → {bare}.SS)
      2. ONE DB query returns MAX(date) with non-null pe per code
      3. file mtime on/before the DB max date → skip outright (the file
         cannot hold PE data newer than what is already loaded)
      4. otherwise tail-peek the file's max date; read only when it holds
         data beyond the DB max
      row gate (new): within each read file, only rows with
         date > db_max[code] survive — a full-history CSV is PARSED but
         only its missing tail is returned for upsert (no history rewrite).
    """
    pe_files = glob_source_files(sse_pe_dir, "*_pe.csv")
    if code_filter:
        bare = code_filter.split(".")[0]
        pe_files = [
            f for f in pe_files
            if os.path.basename(f) == f"{bare}_pe.csv"
        ]
    if not pe_files:
        if verbose:
            print("    [PE] No PE files found", flush=True)
        return pd.DataFrame(columns=["date", "code", "name", "pe"])

    row_gate_max: dict[str, date] = {}
    files_to_read = pe_files

    if conn is not None and not force:
        # {bare}_pe.csv → {bare}.SS (filenames carry zero-padded 6-digit codes)
        file_codes: dict[str, str] = {
            path: os.path.basename(path)[: -len("_pe.csv")] + ".SS"
            for path in pe_files
        }
        codes_list = sorted(set(file_codes.values()))
        max_dates_in_db: dict[str, date] = {}
        try:
            rows = await conn.fetch(
                """
                SELECT code, MAX(date) AS max_date
                FROM stats.stock_basic_stats
                WHERE code = ANY($1) AND pe IS NOT NULL
                GROUP BY code
                """,
                codes_list,
            )
            # Whole-column zip pairing (no per-row dict access)
            max_dates_in_db.update(zip(
                rec_col(rows, "code"), rec_col(rows, "max_date")))
        except Exception:
            max_dates_in_db = {}

        files_to_read = []
        skipped_count = 0
        for path in pe_files:
            db_max = max_dates_in_db.get(file_codes[path])
            if db_max is None:
                files_to_read.append(path)  # new code — load full history
                continue
            mtime_d = _file_mtime_date(path)
            if mtime_d is not None and mtime_d <= db_max:
                skipped_count += 1
                continue
            result = _peek_csv_max_date(path, "日期", "证券代码")
            if result is None:
                files_to_read.append(path)  # unparseable tail — read to be safe
                continue
            file_max, _first_code = result
            if date.fromisoformat(file_max) > db_max:
                files_to_read.append(path)
                # row gate: keep only the missing tail of this full-history
                # file (rows at/below the DB max are already loaded)
                row_gate_max[file_codes[path]] = db_max
            else:
                skipped_count += 1

        if verbose:
            print(f"    [PE] {len(pe_files)} PE files → "
                  f"{len(files_to_read)} files to read "
                  f"({skipped_count} skipped by mtime/tail peek)",
                  flush=True)
    elif force:
        if verbose:
            print(f"    [PE] Force mode: reading all {len(pe_files)} PE files", flush=True)

    if not files_to_read:
        if verbose:
            # Distinguish "dir/files absent" from "all snapshots already
            # loaded": skipped_count>0 means the incremental gate worked.
            if skipped_count > 0:
                print(f"    [PE] All {skipped_count} PE snapshots already "
                      f"loaded — nothing new to merge", flush=True)
            else:
                print("    [PE] No PE files with new data to read", flush=True)
        return pd.DataFrame(columns=["date", "code", "name", "pe"])

    frames: list[pd.DataFrame] = []
    n_rows_gated_out = 0
    for path in files_to_read:
        # One-pass read, no exception handling — read_csv_gpu_safe returns
        # an empty frame (never raises) for unreadable/empty files
        df = read_csv_gpu_safe(path, dtype={"证券代码": str, "sec_type": str})
        src_cols = _safe_columns(df)
        if "证券代码" not in src_cols or "静态市盈率(倍)" not in src_cols:
            continue
        df = df[df["证券代码"].notna()].copy()
        # canonical CSV: sec_type == "stock" by plain column equality;
        # 证券代码 already carries the ".SS" suffix
        df = df[df["sec_type"] == "stock"]
        df["code"] = df["证券代码"].astype(str)
        if df.empty:
            continue
        df["exchange"] = "SS"
        df["pe"] = _safe_to_numeric(df["静态市盈率(倍)"])
        df["pe"] = df["pe"].where(df["pe"] != 0, float("nan"))
        df["date"] = _safe_to_datetime(df["日期"])
        df = df.dropna(subset=["date"])
        # row gate (latest-missing-dates): single-code frame — one scalar
        # cutoff per file keeps only rows beyond the DB max
        gate_code = os.path.basename(path)[: -len("_pe.csv")] + ".SS"
        gate_max = row_gate_max.get(gate_code)
        if gate_max is not None and len(df) > 0:
            n_before = len(df)
            df = df[df["date"] > pd.Timestamp(gate_max)]
            n_rows_gated_out += n_before - len(df)
            if df.empty:
                continue
        cols = ["date", "code", "exchange", "pe"]
        if "证券简称" in src_cols:
            df["name"] = df["证券简称"].astype(str)
            cols.append("name")
        df = df[cols].copy()
        frames.append(df)

    if verbose and n_rows_gated_out > 0:
        print(f"    [PE] Row gate: {n_rows_gated_out:,} historical rows "
              f"already in DB excluded (latest-missing-dates)", flush=True)

    if not frames:
        return pd.DataFrame(columns=["date", "code", "name", "pe"])

    combined = pd.concat(frames, ignore_index=True)
    # ONE strip pass over the merged frame (per-file strips were ~1,700
    # redundant kernel launches); downloads conversion writes verbatim
    # xlsx strings so padding is rare — this is defensive hygiene only.
    if "name" in _safe_columns(combined):
        combined["name"] = combined["name"].str.strip()
    combined = combined.drop_duplicates(subset=["date", "code"], keep="last")
    return combined


async def fetch_pe_estimate_candidates(
    conn,
    batch_dates: list,
) -> list[tuple]:
    """DB-driven estimation source of truth: (date, code, close) tuples for
    today's ingested dates whose basic_stats row still lacks ANY pe.

    Replaces the old frame-based collect_missing_pe_tuples(missing_pe_df):
    with PE decoupled from the OHLCV frame, actual PE values may arrive via
    the independent PE pass AFTER the OHLCV rows are written, so candidates
    must be read back from the DB — a row that just received an actual PE is
    correctly excluded from estimation."""
    if not batch_dates:
        return []
    rows = await conn.fetch(
        """
        SELECT date, code, close::float8 AS close
        FROM stats.stock_basic_stats
        WHERE date = ANY($1::date[])
          AND pe IS NULL
          AND is_pe_estimated = false
          AND close IS NOT NULL
        """,
        batch_dates,
    )
    # Whole-column zip pairing; close is float8 in SQL so already Python floats
    return list(zip(rec_col(rows, "date"), rec_col(rows, "code"),
                    rec_col(rows, "close")))


async def _read_sse_archive_trend_files(
    sse_pe_dir: str,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int | None = None,
    conn=None,
    force: bool = False,
    verbose: bool = True,
    code_filter: str | None = None,
) -> pd.DataFrame:
    """Read SSE per-stock {code}_trend.csv archive files (historical OHLCV).

    Incremental by file mtime + per-code DB max date (no full-archive load):
      1. the stock code comes from the FILENAME ({bare}_trend.csv →
         {bare}.SS) — no file I/O needed to know which stock a file holds
      2. ONE DB query returns MAX(date) per code from stats.stock_identity
      3. file mtime on/before the DB max date for that code → skip outright
         (the file cannot contain rows newer than what is already loaded)
      4. otherwise tail-peek the file's max date; read only when it holds
         data beyond the DB max
      5. rows read are filtered to date > DB max per code (vectorized,
         after concat), so NO anti-join against stock_identity is needed
         downstream

    force=True reads every file in full with no filtering. Interior
    per-code gaps at or below the DB max date are NOT backfilled by this
    path (they are normally trading suspensions); whole-market missing
    dates are covered by the daily-file gap detection in the pipeline.
    """
    archive_files = glob_source_files(sse_pe_dir, "*_trend.csv")
    if code_filter:
        bare = code_filter.split(".")[0]
        archive_files = [
            f for f in archive_files
            if os.path.basename(f) == f"{bare}_trend.csv"
        ]
    if limit:
        archive_files = archive_files[:limit]
    if not archive_files:
        if verbose:
            print("    [ARCHIVE] No archive trend files found", flush=True)
        return pd.DataFrame()

    # {bare}_trend.csv → {bare}.SS (filenames carry zero-padded 6-digit codes)
    file_codes: dict[str, str] = {
        path: os.path.basename(path)[: -len("_trend.csv")] + ".SS"
        for path in archive_files
    }

    apply_db_filter: bool = conn is not None and not force
    files_to_read: list[str] = archive_files
    db_max_by_code: dict[str, date] = {}

    if apply_db_filter:
        codes_list = sorted(set(file_codes.values()))
        try:
            rows = await conn.fetch(
                """
                SELECT code, MAX(date) AS max_date
                FROM stats.stock_identity
                WHERE code = ANY($1) AND exchange = 'SS'
                GROUP BY code
                """,
                codes_list,
            )
            # Whole-column zip pairing (no per-row dict access)
            db_max_by_code.update(zip(
                rec_col(rows, "code"), rec_col(rows, "max_date")))
        except Exception:
            db_max_by_code = {}

        files_to_read = []
        n_skip_mtime = 0
        n_skip_peek = 0
        for path in archive_files:
            db_max = db_max_by_code.get(file_codes[path])
            if db_max is None:
                files_to_read.append(path)  # new code — load full history
                continue
            mtime_d = _file_mtime_date(path)
            if mtime_d is not None and mtime_d <= db_max:
                n_skip_mtime += 1
                continue
            result = _peek_csv_max_date(path, "交易日期", "证券代码")
            if result is None:
                files_to_read.append(path)  # unparseable tail — read to be safe
                continue
            file_max, _first_code = result
            if date.fromisoformat(file_max) > db_max:
                files_to_read.append(path)
            else:
                n_skip_peek += 1

        if verbose:
            print(f"    [ARCHIVE] {len(archive_files)} archive files → "
                  f"{len(files_to_read)} to read "
                  f"({n_skip_mtime} skipped by mtime, {n_skip_peek} by tail peek)",
                  flush=True)
    elif force and verbose:
        print(f"    [ARCHIVE] Force mode: reading all {len(archive_files)} archive files", flush=True)

    if not files_to_read:
        if verbose:
            print(f"    [ARCHIVE] No archive files with new data to read", flush=True)
        return pd.DataFrame()

    sd = pd.to_datetime(start_date) if start_date else None
    ed = pd.to_datetime(end_date) if end_date else None

    frames: list[pd.DataFrame] = []
    for path in files_to_read:
        df = _read_one(path)
        if df is None or df.empty:
            continue
        if apply_db_filter:
            # tag with the per-code DB max; ONE vectorized filter after
            # concat (per-file date conversion pays cudf kernel overhead)
            db_max = db_max_by_code.get(file_codes[path])
            # Timestamp for the datetime64 column comparison (host-side
            # constructor — not the GPU Timestamp method fallback path)
            df["_db_max"] = pd.Timestamp(db_max) if db_max is not None else pd.NaT
        frames.append(df)

    if not frames:
        return pd.DataFrame()

    # concat first, then one vectorized date pass over the whole frame —
    # per-file datetime conversion pays cudf kernel overhead 1733 times
    combined = pd.concat(frames, ignore_index=True)
    combined["date"] = _safe_to_datetime(combined["date"])
    combined = combined.dropna(subset=["date"])
    if apply_db_filter:
        # keep only rows newer than each code's DB max; sentinel fill
        # avoids cudf Kleene-logic null-comparison traps (null > x → null)
        n_before = len(combined)
        combined["_db_max"] = combined["_db_max"].fillna(pd.Timestamp("1900-01-01"))
        combined = combined[combined["date"] > combined["_db_max"]]
        combined = combined.drop(columns=["_db_max"])
        if verbose and n_before > len(combined):
            print(f"    [ARCHIVE] Per-code DB-max filter: "
                  f"{n_before:,} → {len(combined):,} rows", flush=True)
    if sd is not None:
        combined = combined[combined["date"] >= sd]
    if ed is not None:
        combined = combined[combined["date"] <= ed]
    if combined.empty:
        return pd.DataFrame()

    combined = combined.drop_duplicates(subset=["date", "code"], keep="last")
    return combined.reset_index(drop=True)


async def estimate_missing_pe_async(
    conn,
    missing_pe_rows: list[tuple],
    history_start: date | None = None,
    history_end: date | None = None,
) -> dict[tuple, float]:
    """Estimate PE for rows where it's missing, using the last actual PE.

    For each (date, code, close), looks up the most recent row in
    stats.stock_basic_stats with actual PE (is_pe_estimated=false,
    pe IS NOT NULL, pe > 0) for the same code and an earlier date, then
    computes: estimated_pe = today_close * last_pe / last_close.
    """
    if not missing_pe_rows:
        return {}

    codes = sorted(set(c for (_, c, _) in missing_pe_rows))
    if not codes:
        return {}

    query = """
        SELECT code, date, pe::float8 AS pe, close::float8 AS close
        FROM stats.stock_basic_stats
        WHERE code = ANY($1::text[])
          AND pe IS NOT NULL
          AND pe > 0
          AND close IS NOT NULL
          AND is_pe_estimated = false
    """
    params: list = [codes]
    if history_start is not None:
        query += "  AND date >= $2\n"
        params.append(history_start)
    if history_end is not None:
        param_idx = len(params) + 1
        query += f"  AND date <= ${param_idx}\n"
        params.append(history_end)
    query += "  ORDER BY code, date"
    rows = await conn.fetch(query, *params)

    # Whole-column extraction; SQL orders by code so itertools.groupby
    # partitions without per-row dict access. pe/close are float8 in SQL.
    codes_c = rec_col(rows, "code")
    dates_c = rec_col(rows, "date")
    pes_c = rec_col(rows, "pe")
    closes_c = rec_col(rows, "close")

    pe_history: dict[str, list[tuple]] = defaultdict(list)
    for code, grp in groupby(zip(dates_c, codes_c, pes_c, closes_c),
                             key=itemgetter(1)):
        pe_history[code].extend((d, p, c) for d, _, p, c in grp)

    result: dict[tuple, float] = {}
    for (d, code, close) in missing_pe_rows:
        history = pe_history.get(code)
        if not history:
            continue
        dates = [h[0] for h in history]
        idx = bisect.bisect_left(dates, d) - 1
        if idx < 0:
            continue
        last_date, last_pe, last_close = history[idx]
        cutoff = d - relativedelta(months=PE_ESTIMATE_MAX_MONTHS)
        if last_date < cutoff:
            continue
        if last_close > 0 and close is not None and close > 0:
            result[(d, code)] = (close * last_pe) / last_close

    return result
