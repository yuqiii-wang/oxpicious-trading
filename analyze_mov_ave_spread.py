"""
analyze_mov_ave_spread.py — Moving-Average Spread Analysis (ETF + Index)

Loads every business date's MA-gap values for every ETF and every index into
the wide-format detail table analysis.mov_ave_spreads_detail.

The `sec_type` column discriminates the source universe. The schema CHECK
allows three values ('etf' | 'index' | 'stock'); this script currently
computes only 'etf' and 'index' (stock requires stats.stock_tech_stats,
which does not yet exist):
  • ETF   — price = COALESCE(stats.etf_adjustment.adj_close,
                             stats.etf_basic_stats.close);
            MAs from stats.etf_tech_stats.
  • Index — price = stats.index_basic_stats.close (indices have no
            adjustment table); MAs from stats.index_tech_stats.
  • Stock — (reserved) price = stats.stock_basic_stats.close; MAs would
            come from stats.stock_tech_stats once that table is created.

9 gap pairs (canonical order):
  • 5 Price-vs-MA pairs:  gap = (price - maX) / maX,  X ∈ {5, 20, 60, 120, 255}
  • 4 MA5-vs-MA pairs:    gap = (ma5  - maX) / maX,  X ∈ {20, 60, 120, 255}

Detail table (WIDE):
  one row per (sec_type, code, date) with 9 gap_value columns.

Repopulated via TRUNCATE-then-INSERT on every run (full recompute).

Usage:
  python analyze_mov_ave_spread.py
"""
import os
import sys
import time
import asyncio
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.build_commons import (  # noqa: E402
    setup_utf8_stdout,
    get_db_connection_async,
    bulk_upsert_async,
    truncate_table_async,
    print_build_header,
    print_wall_time,
    fetch_codes_with_recent_data_async,
    RECENT_TRADING_DAYS,
    recent_trading_day_cutoff,
)

setup_utf8_stdout()

import pandas as pd  # noqa: E402
import numpy as np  # noqa: E402


# ----------------------------------------------------------------------------
#  Configuration
# ----------------------------------------------------------------------------

ANALYSIS_NAME = "mov_ave_spread"
DETAIL_TABLE = "analysis.mov_ave_spreads_detail"

DESCRIPTION = (
    "Moving-average spread analysis (ETF + Index). For each security (ETF or "
    "index) and business date, computes 9 gap pairs (5 Price/MA + 4 MA5/MA) "
    "as gap_value = (short_value - long_value) / long_value, plus 1st "
    "derivative (slope) and 2nd derivative (curvature) of price and each MA "
    "(ma5 / ma20 / ma60 / ma120 / ma255) computed per code ordered by date. "
    "The sec_type column discriminates the source universe; the schema CHECK "
    "allows 'etf' | 'index' | 'stock', but this script currently computes "
    "only 'etf' and 'index' (stock requires stats.stock_tech_stats, which "
    "does not yet exist). 'etf' uses COALESCE(etf_adjustment.adj_close, "
    "etf_basic_stats.close) for price and etf_tech_stats for MAs; 'index' "
    "uses index_basic_stats.close for price and index_tech_stats for MAs. "
    "Detail table stores one wide row per (sec_type, code, date) with all "
    "9 gap values + 12 slope/curvature columns (price + 5 MAs × slope/curv)."
)

# stats.*_tech_stats column names by MA window (identical for etf and index).
TECH_STATS_MA_COLUMNS = {
    5:   "ma5",
    20:  "ma20",
    60:  "ma60",
    120: "ma120",
    255: "ma255",
}

# MA windows for which slope (1st derivative) and curvature (2nd derivative)
# are computed. Matches the ma{W}_slope / ma{W}_curvature columns in the
# detail table.
MA_WINDOWS = (5, 20, 60, 120, 255)

# 9 (ma_short, ma_long, gap_column_name) tuples in canonical order.
# ma_short = 0 is the price sentinel (short_value = price); ma_short = 5 uses ma5.
# gap_column_name matches the column in analysis.mov_ave_spreads_detail.
PAIRS = [
    (0, 5,   "price_vs_ma5"),
    (0, 20,  "price_vs_ma20"),
    (0, 60,  "price_vs_ma60"),
    (0, 120, "price_vs_ma120"),
    (0, 255, "price_vs_ma255"),
    (5, 20,  "ma5_vs_ma20"),
    (5, 60,  "ma5_vs_ma60"),
    (5, 120, "ma5_vs_ma120"),
    (5, 255, "ma5_vs_ma255"),
]

# Security types computed by this script. The DB schema CHECK on
# analysis.mov_ave_spreads_detail.sec_type allows ('etf', 'index', 'stock'),
# but 'stock' is not yet computed here because stats.stock_tech_stats (the
# MA source table) does not exist. Add 'stock' to this tuple once that table
# is created and _fetch_sql_for_sec_type gains a 'stock' branch.
SEC_TYPES = ("etf", "index")

# Identity table per sec_type — used by the recent-data pre-filter
# (fetch_codes_with_recent_data_async) to find codes with at least one row
# in the last RECENT_TRADING_DAYS trading days. A code with no recent data
# (delisted / suspended / never-traded) is excluded from the analysis
# universe entirely so its full history is skipped.
SEC_TYPE_IDENTITY_TABLE = {
    "etf":   "stats.etf_identity",
    "index": "stats.index_identity",
}

# Every numeric column in analysis.mov_ave_spreads_detail is declared
# NUMERIC(10,6), whose absolute value must be < 10^4 (= 10000) after rounding
# to 6 decimal places — otherwise PostgreSQL raises
# NumericValueOutOfRangeError on insert. The gap columns (price_vs_maX,
# ma5_vs_maX) are ratios and stay well under this bound in practice; the
# slope/curvature columns are RAW differences (MA[t] - MA[t-1]) and can
# exceed 10000 for high-priced ETFs/indices at corporate-action or
# source-data-unit boundaries (e.g. 159943.SZ, 159909.SZ whose NAV is in the
# tens of thousands). Values at or beyond this bound are nulled before
# insert by _null_if_overflow rather than dropped, so the row is still
# written with its other (valid) columns.
NUMERIC_MAX_ABS = 10000.0


# ----------------------------------------------------------------------------
#  Helpers
# ----------------------------------------------------------------------------

def _null_if_overflow(series: pd.Series) -> pd.Series:
    """Return a copy of `series` with values that would overflow a
    NUMERIC(10,6) column replaced by NaN (later converted to None).

    NUMERIC(10,6) holds values with absolute value < 10^4 after rounding to
    6 decimal places. This helper nulls any value whose rounded absolute
    value >= NUMERIC_MAX_ABS, mirroring PostgreSQL's overflow check so the
    bulk upsert never fails. NaN/inf are also nulled.

    This is the safety net for:
      • slope/curvature columns (raw differences) — high-priced ETFs/indices
        can produce single-day MA changes exceeding 10000 at corporate-action
        or source-data-unit boundaries.
      • gap columns (ratios) — catches any near-zero-denominator ratio that
        slips through _gap_col's zero/near-zero check.
    """
    s = pd.to_numeric(series, errors="coerce")
    mask = s.isna() | ~np.isfinite(s) | (s.abs().round(6) >= NUMERIC_MAX_ABS)
    return s.where(~mask)


def _safe_ratio(num, den):
    """(num - den) / den — returns None for NaN/None/non-positive denominator.

    Mirrors the SQL NULL-on-bad-input semantics so that detail rows always
    match what a pure-SQL computation would produce. Also nulls results
    whose absolute value would overflow NUMERIC(10,6) (|result| >= 10000),
    which can occur when `den` is denormalized (near-zero but non-zero).
    """
    if num is None or den is None:
        return None
    try:
        n = float(num)
        d = float(den)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(n) or not np.isfinite(d) or d == 0:
        return None
    r = (n - d) / d
    if not np.isfinite(r) or abs(r) >= NUMERIC_MAX_ABS:
        return None
    return r


def _compute_slopes_curvatures(df: pd.DataFrame) -> pd.DataFrame:
    """Add 1st-derivative (slope) and 2nd-derivative (curvature) columns for
    price and each MA window, computed per (sec_type, code) ordered by date.

    slope[t]      = value[t] - value[t-1]   (NULL on first date of each code)
    curvature[t]  = slope[t] - slope[t-1]   (NULL on first two dates of each code)

    Adds columns price_slope / price_curvature (from `price`) and
    ma{W}_slope / ma{W}_curvature for W in MA_WINDOWS (from `ma{W}`).
    """
    df = df.sort_values(["sec_type", "code", "date"]).reset_index(drop=True)
    grp_keys = ["sec_type", "code"]
    # Price 1st + 2nd derivative.
    df["price_slope"] = df.groupby(grp_keys, sort=False)["price"].diff()
    df["price_curvature"] = df.groupby(grp_keys, sort=False)["price_slope"].diff()
    for w in MA_WINDOWS:
        ma_col = f"ma{w}"
        slope_col = f"ma{w}_slope"
        curv_col = f"ma{w}_curvature"
        df[slope_col] = df.groupby(grp_keys, sort=False)[ma_col].diff()
        df[curv_col] = df.groupby(grp_keys, sort=False)[slope_col].diff()
    return df


def _fetch_sql_for_sec_type(sec_type: str) -> str:
    """Build the per-(code, date) source-fetch SQL for the given sec_type,
    scoped to a caller-supplied code list via the ``$1`` placeholder
    (``WHERE i.code = ANY($1::text[])``).

    ETFs JOIN etf_identity + etf_basic_stats + LEFT JOIN etf_adjustment
    (for adj_close) + JOIN etf_tech_stats.

    Indices JOIN index_identity + index_basic_stats + JOIN index_tech_stats
    (no adjustment table — indices have no corporate actions).

    Stock is not yet supported (no stats.stock_tech_stats table); add a
    'stock' branch here once that table exists.
    """
    if sec_type == "etf":
        return """
            SELECT
                i.code,
                i.date,
                COALESCE(a.adj_close, b.close) AS price,
                t.ma5, t.ma20, t.ma60, t.ma120, t.ma255
            FROM stats.etf_identity i
            JOIN stats.etf_basic_stats b ON b.date = i.date AND b.code = i.code
            LEFT JOIN stats.etf_adjustment a ON a.date = i.date AND a.code = i.code
            JOIN stats.etf_tech_stats t   ON t.date = i.date AND t.code = i.code
            WHERE i.code = ANY($1::text[])
            ORDER BY i.code, i.date ASC
        """
    if sec_type == "index":
        return """
            SELECT
                i.code,
                i.date,
                b.close AS price,
                t.ma5, t.ma20, t.ma60, t.ma120, t.ma255
            FROM stats.index_identity i
            JOIN stats.index_basic_stats b ON b.date = i.date AND b.code = i.code
            JOIN stats.index_tech_stats  t ON t.date = i.date AND t.code = i.code
            WHERE i.code = ANY($1::text[])
            ORDER BY i.code, i.date ASC
        """
    raise ValueError(f"Unknown sec_type: {sec_type!r}")


# ----------------------------------------------------------------------------
#  Step 1 — fetch source data (price + MAs joined from stats schema)
# ----------------------------------------------------------------------------

async def fetch_source_data(conn, sec_type: str) -> pd.DataFrame:
    """Fetch per-(code, date) price + MAs from the stats schema for the given
    sec_type ('etf' or 'index'), then compute per-(code) slope and curvature
    for each MA window.

    Pre-filter: only codes with at least one identity-table row in the last
    RECENT_TRADING_DAYS trading days are loaded. A code with no recent data
    (delisted / suspended / never-traded) is excluded from the analysis
    universe entirely — its full history is skipped — so the detail table
    only carries the active universe.

    Returns a DataFrame with columns:
        sec_type, code, date, price, ma5, ma20, ma60, ma120, ma255,
        price_slope, price_curvature,
        ma5_slope, ma20_slope, ma60_slope, ma120_slope, ma255_slope,
        ma5_curvature, ma20_curvature, ma60_curvature, ma120_curvature,
        ma255_curvature

    Uses INNER JOINs on both basic_stats and tech_stats so the resulting rows
    satisfy the detail table's data-integrity expectation (every row has both
    OHLCV and MA source data).
    """
    # ---- Pre-filter: keep only codes with data in the recent trading window.
    identity_table = SEC_TYPE_IDENTITY_TABLE[sec_type]
    cutoff = recent_trading_day_cutoff(RECENT_TRADING_DAYS)
    active_codes = await fetch_codes_with_recent_data_async(
        conn, identity_table, n_trading_days=RECENT_TRADING_DAYS,
    )
    print(f"      pre-filter: {len(active_codes):,} {sec_type} codes have "
          f"data in the last {RECENT_TRADING_DAYS} trading days "
          f"(cutoff={cutoff.isoformat()})", flush=True)
    if not active_codes:
        return pd.DataFrame(columns=["sec_type", "code", "date", "price",
                                     "ma5", "ma20", "ma60", "ma120", "ma255"])

    sql = _fetch_sql_for_sec_type(sec_type)
    rows = await conn.fetch(sql, sorted(active_codes))
    if not rows:
        return pd.DataFrame(columns=["sec_type", "code", "date", "price",
                                     "ma5", "ma20", "ma60", "ma120", "ma255"])
    # asyncpg.Record → dict so pandas picks up column names (not integer indices).
    df = pd.DataFrame([dict(r) for r in rows])
    n_loaded_codes = df["code"].nunique() if "code" in df.columns else 0
    if n_loaded_codes < len(active_codes):
        # Some active codes had identity rows but no joined basic_stats /
        # tech_stats rows — they are dropped by the INNER JOINs.
        print(f"      note: {len(active_codes) - n_loaded_codes:,} {sec_type} "
              f"codes had recent identity data but no joined basic/tech stats "
              f"rows → dropped by INNER JOIN", flush=True)
    # Tag every row with its sec_type so downstream detail/summary rows
    # carry the discriminant column required by the new schema.
    df["sec_type"] = sec_type
    # Ensure date column is python date (not datetime) for clean serialization
    df["date"] = pd.to_datetime(df["date"]).dt.date
    # Coerce numeric columns to float (asyncpg returns Decimal for NUMERIC)
    for col in ("price", "ma5", "ma20", "ma60", "ma120", "ma255"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    # Compute slope (1st derivative) and curvature (2nd derivative) per code.
    df = _compute_slopes_curvatures(df)
    # Reorder columns for readability.
    df = df[["sec_type", "code", "date", "price",
             "ma5", "ma20", "ma60", "ma120", "ma255",
             "price_slope", "price_curvature",
             "ma5_slope", "ma20_slope", "ma60_slope", "ma120_slope", "ma255_slope",
             "ma5_curvature", "ma20_curvature", "ma60_curvature",
             "ma120_curvature", "ma255_curvature"]]
    return df


# ----------------------------------------------------------------------------
#  Step 2 — compute 9 wide gap columns per (sec_type, code, date)
# ----------------------------------------------------------------------------

def _gap_col(df: pd.DataFrame, num_col: str, den_col: str) -> pd.Series:
    """Vectorized (num - den) / den with NULL semantics matching _safe_ratio.

    Returns None where num/den is NaN, where the denominator is zero or
    denormalized (|den| < 1e-12, which would produce a huge or non-finite
    ratio), or where the result is non-finite. The _null_if_overflow pass
    in build_detail_rows is the final safety net for any ratio that still
    exceeds the NUMERIC(10,6) range.
    """
    num = df[num_col]
    den = df[den_col]
    out = (num - den) / den
    mask = (num.isna() | den.isna()
            | (den.abs() < 1e-12)
            | ~np.isfinite(out))
    return out.where(~mask, other=None)


def build_detail_rows(df: pd.DataFrame):
    """For each (sec_type, code, date) row, compute all 9 gap values and
    emit a wide-format dict suitable for bulk_upsert into
    analysis.mov_ave_spreads_detail.

    Includes the precomputed price_slope / price_curvature and
    ma{W}_slope / ma{W}_curvature columns.

    Uses vectorized pandas ops + to_dict(orient='records') for speed on large
    DataFrames (millions of rows).
    """
    if df.empty:
        return []

    out_df = pd.DataFrame({
        "sec_type":    df["sec_type"],
        "code":          df["code"],
        "date":          df["date"],
        "price_vs_ma5":   _gap_col(df, "price", "ma5"),
        "price_vs_ma20":  _gap_col(df, "price", "ma20"),
        "price_vs_ma60":  _gap_col(df, "price", "ma60"),
        "price_vs_ma120": _gap_col(df, "price", "ma120"),
        "price_vs_ma255": _gap_col(df, "price", "ma255"),
        "ma5_vs_ma20":    _gap_col(df, "ma5",   "ma20"),
        "ma5_vs_ma60":    _gap_col(df, "ma5",   "ma60"),
        "ma5_vs_ma120":   _gap_col(df, "ma5",   "ma120"),
        "ma5_vs_ma255":   _gap_col(df, "ma5",   "ma255"),
        "price_slope":     df["price_slope"],
        "price_curvature": df["price_curvature"],
        "ma5_slope":       df["ma5_slope"],
        "ma20_slope":      df["ma20_slope"],
        "ma60_slope":      df["ma60_slope"],
        "ma120_slope":     df["ma120_slope"],
        "ma255_slope":     df["ma255_slope"],
        "ma5_curvature":   df["ma5_curvature"],
        "ma20_curvature":  df["ma20_curvature"],
        "ma60_curvature":  df["ma60_curvature"],
        "ma120_curvature": df["ma120_curvature"],
        "ma255_curvature": df["ma255_curvature"],
    })
    # Null any value whose absolute value would overflow NUMERIC(10,6)
    # (|value| >= 10000 after rounding to 6 decimals). This is the exception
    # case → NULL: rows are kept (with the offending column set to NULL)
    # rather than dropping the whole row or failing the bulk upsert. It
    # mainly affects the raw-difference slope/curvature columns of
    # high-priced ETFs/indices at corporate-action boundaries; gap ratios
    # are also guarded here as a final safety net for near-zero denominators.
    numeric_cols = [c for c in out_df.columns
                    if c not in ("sec_type", "code", "date")]
    nulled_counts = {}
    for c in numeric_cols:
        before_na = int(out_df[c].isna().sum())
        out_df[c] = _null_if_overflow(out_df[c])
        n = int(out_df[c].isna().sum()) - before_na
        if n > 0:
            nulled_counts[c] = n
    if nulled_counts:
        total = sum(nulled_counts.values())
        per_col = ", ".join(f"{c}={n}" for c, n in nulled_counts.items())
        print(f"    → NUMERIC(10,6) overflow-guard nulled {total:,} value(s) "
              f"across {len(nulled_counts)} column(s): {per_col}", flush=True)
    # Replace NaN with None so asyncpg serializes them as SQL NULL.
    out_df = out_df.where(pd.notna(out_df), None)
    return out_df.to_dict(orient="records")


# ----------------------------------------------------------------------------
#  Main
# ----------------------------------------------------------------------------

async def main():
    t0 = time.time()
    print_build_header(
        "ANALYZE MA-SPREADS (ETF + INDEX)",
        detail_table=DETAIL_TABLE,
        pairs=f"{len(PAIRS)} (5 Price/MA + 4 MA5/MA)",
        sec_types=", ".join(SEC_TYPES),
    )

    conn = await get_db_connection_async()
    try:
        # ---- Step 1: fetch source data for every sec_type ---------------
        print("\n[1/3] Fetching per-(sec_type, code, date) price + MAs "
              "from stats schema...", flush=True)
        frames = []
        for at in SEC_TYPES:
            print(f"    → fetching {at}...", flush=True)
            df_at = await fetch_source_data(conn, at)
            print(f"      {len(df_at):,} {at} (code, date) source rows",
                  flush=True)
            if not df_at.empty:
                frames.append(df_at)
        df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        print(f"    → {len(df):,} total (sec_type, code, date) source rows",
              flush=True)
        if df.empty:
            print("    → no source data; exiting.", flush=True)
            return

        # ---- Step 2: build detail rows -------------------------------------
        print("\n[2/3] Computing 9 wide gap columns + 12 slope/curvature "
              "columns per (sec_type, code, date)...", flush=True)
        detail = build_detail_rows(df)
        print(f"    → {len(detail):,} detail rows", flush=True)

        # ---- Step 3: truncate + insert detail + upsert identity -----------
        print(f"\n[3/3] Truncating {DETAIL_TABLE} and inserting detail rows...",
              flush=True)
        await truncate_table_async(conn, DETAIL_TABLE)
        n_detail = await bulk_upsert_async(
            conn, DETAIL_TABLE, detail,
            key_columns=["sec_type", "code", "date"],
            batch_size=1000,
        )
        print(f"    → inserted {n_detail:,} rows", flush=True)

        # ---- Upsert analysis_identity ------------------------------------
        print(f"    → Upserting analysis.analysis_identity registry...",
              flush=True)
        await conn.execute(
            """
            INSERT INTO analysis.analysis_identity
                (name, detail_name, last_run_datetime, description)
            VALUES ($1, $2, NOW(), $3)
            ON CONFLICT (name) DO UPDATE SET
                detail_name       = EXCLUDED.detail_name,
                last_run_datetime = NOW(),
                description       = EXCLUDED.description
            """,
            ANALYSIS_NAME,
            "mov_ave_spreads_detail",
            DESCRIPTION,
        )
        print(f"    → upserted analysis_identity: name={ANALYSIS_NAME!r}, "
              f"detail_name='mov_ave_spreads_detail'", flush=True)

        print_wall_time(t0)
    finally:
        try:
            await conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())
