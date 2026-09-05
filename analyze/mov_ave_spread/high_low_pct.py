"""Internal high-low percentile band step for analyze.mov_ave_spread.

Multi-period high/low price percentile BANDS for ETF + Index + Stock:
one row per (sec_type, code, date_year_month, period, pct_type) in
analysis.mov_ave_high_low_pct — symmetric quantile envelopes of where
the security's daily prices traded over its recent history.

=======================================================================
  FINANCIAL SEMANTICS
=======================================================================

For each calendar month m of a code's history, anchor the window at the
month's LAST trading row and look BACK over a TRAILING window of
`period` trading rows ending there, for each period in
HIGH_LOW_PCT_PERIODS (255 / 500 / 750 / 1275 trading rows = ~1 / 2 / 3
/ 5 trading years — the ma255 yearly-window precedent). Over that
window:

    low_val[m, period, pct]  = pct-th percentile (linear
                               interpolation) of the window's daily
                               LOW prices
    high_val[m, period, pct] = (100 - pct)-th percentile of the
                               window's daily HIGH prices

pct_type in HIGH_LOW_PCT_TYPES (1 / 5 / 10) tightens the band: 1 =
near-full range of the window ([1st pct of lows, 99th pct of highs]);
10 = core envelope ([10th, 90th]). The band is SYMMETRIC — the same
pct_type trims both tails.

One row per (code, month, period, pct_type) — 12 bands per (code,
month). Bands are sampled MONTHLY (at the month-end anchor) and stored
under the month's FIRST day (date_year_month). Two consequences drive
the rebuild semantics:

1. TRAILING window (backward-only) — unlike the market-hypes
   thresholds, which are CENTERED and therefore shift when new data
   arrives, a completed month's trailing percentile is IMMUTABLE once
   computed. Per-(code, month) INCREMENTAL upserts are therefore exact:
   missing pairs are detected against the analysis table's PK
   ((sec_type, code, date_year_month, period, pct_type) — detected at
   (code, month) granularity, where a pair counts as present only when
   all HIGH_LOW_PCT_ROWS_PER_PAIR (12) rows exist — a crash-consistency
   guard that recomputes partially-inserted pairs), computed, and
   COPY-inserted; no historical row ever needs refreshing.

2. IN-PROGRESS months are SKIPPED — a month is only banded once a
   LATER month exists for that code (the month-end anchor must be the
   month's true last trading row; the current, still-accumulating month
   has none yet). The month is banded on the first run after the next
   month's data arrives. Detection mirrors this (h.ym < the code's
   last identity month), so no pair is ever expected before it is
   computable.

Detection also requires HIGH_LOW_PCT_MIN_PERIODS (255 = 1 trading
year) cumulative identity rows through the month's end — fewer rows
yield no band (truncated window; high_val/low_val are NOT NULL). All
periods share the same 255-row minimum, so pair expectation is
period-independent. Windows near a code's history start are naturally
truncated to the available rows (a 1275-period band over 300 rows of
history is a percentile of those 300 rows). The code universe is the
ACTIVE universe (codes with recent identity data), so delisted codes'
months never count as missing.

This module is an INTERNAL step of analyze.mov_ave_spread — invoked
from __main__.py after the market-hypes step, reusing the same DB
connection + source DataFrame (the ``high`` / ``low`` columns — no
second DB round-trip).

Insert path: PostgreSQL CSV COPY (csv_copy_from_frame_async), safe
because the inserted rows are pre-filtered to (code, month) pairs the
table does not completely have (incremental), or the scope was
truncated/deleted upfront (--force / single-code). Chunks are
row-count slices; each (sec_type, code, date_year_month, period,
pct_type) PK appears exactly once in the frame, so no two chunks can
conflict.

GPU note: the trailing percentiles use pandas ``groupby(...).rolling
().quantile()``. cuDF lacks rolling-quantile support, so when
cudf.pandas is active this op transparently falls back to the CPU
pandas implementation (same contract as the centered rolling-quantile
in market_hypes.py, whose helper is reused here; one CPU pass per
(period, pct_type, leg) = 24 per frame). The month keys, anchor
detection (shift-compare) and per-group max months stay on GPU-native
column ops; the final long-frame assembly unwraps to host numpy once
at the write boundary.
"""
from __future__ import annotations

import datetime
import time

import numpy as np
import pandas as pd

from _common.build_commons import (
    RECENT_TRADING_DAYS,
    fetch_codes_with_recent_data_async,
)
from _common.db_commons import csv_copy_from_frame_async
from _common.df_utils import column_subset, host_array
from analyze._common import upsert_analysis_identity
from analyze.mov_ave_spread.config import (
    HIGH_LOW_PCT_ANALYSIS_NAME,
    HIGH_LOW_PCT_COLUMNS,
    HIGH_LOW_PCT_DESCRIPTION,
    HIGH_LOW_PCT_MIN_PERIODS,
    HIGH_LOW_PCT_PERIODS,
    HIGH_LOW_PCT_ROWS_PER_PAIR,
    HIGH_LOW_PCT_TABLE,
    HIGH_LOW_PCT_TYPES,
    SEC_TYPE_IDENTITY_TABLE,
)
from analyze.mov_ave_spread.market_hypes import _grouped_rolling_quantile


# Band rows per CSV COPY chunk (bounds the in-memory chunk sliced off
# the long frame before rendering — same spirit as
# _EPISODE_CHUNK_ROWS in market_hypes.py).
_BAND_CHUNK_ROWS = 200_000


# ---------------------------------------------------------------------------
#  Missing-(code, month)-pair detection (PK-driven, per user convention)
# ---------------------------------------------------------------------------

async def find_missing_high_low_pct_pairs(
    conn, identity_table: str, sec_type: str,
    *, codes: list[str] | None = None,
) -> set[tuple[str, datetime.date]]:
    """Return the (code, month-first-day) pairs of ``sec_type`` whose band
    rows are missing or INCOMPLETE in analysis.mov_ave_high_low_pct.

    A pair is EXPECTED — and therefore only counted when missing — when:

      - the code is in the given ``codes`` universe (when None, the
        ACTIVE universe is fetched: codes with identity data in the
        last RECENT_TRADING_DAYS trading days — delisted codes are
        outside the pipeline's universe and would never converge),
      - the code has >= HIGH_LOW_PCT_MIN_PERIODS (255) cumulative
        identity rows through the month's end (fewer rows yield no
        band — the truncated window cannot satisfy min_periods; all
        periods share this minimum, so the expectation is
        period-independent), and
      - the month is COMPLETE: strictly earlier than the code's last
        identity month (the in-progress month has no true month-end
        anchor yet — mirrors compute_high_low_pct_bands).

    A present-but-INCOMPLETE pair (fewer than
    HIGH_LOW_PCT_ROWS_PER_PAIR rows — e.g. a chunk COPY aborted
    mid-run) is returned as missing too: the (code, month, period,
    pct_type) PK makes a re-computation of the whole pair conflict-free
    only if the stale rows are gone, and the caller wipes the pair's
    rows before insert (see _delete_pairs). COMPLETE pairs are skipped.

    The check is one LEFT JOIN + GROUP BY/HAVING over the expected
    pairs — no per-pair round-trips.
    """
    if codes is None:
        codes_set = await fetch_codes_with_recent_data_async(
            conn, identity_table, n_trading_days=RECENT_TRADING_DAYS,
        )
    else:
        codes_set = set(codes)
    if not codes_set:
        return set()

    rows = await conn.fetch(
        f"""
        WITH ym_counts AS (
            SELECT i.code,
                   date_trunc('month', i.date)::date AS ym,
                   COUNT(*) AS n_rows
            FROM {identity_table} i
            WHERE i.code = ANY($1::text[])
            GROUP BY 1, 2
        ),
        ym_hist AS (
            SELECT code, ym,
                   SUM(n_rows) OVER (
                       PARTITION BY code ORDER BY ym
                       ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                   ) AS rows_upto_month_end
            FROM ym_counts
        ),
        code_last AS (
            SELECT i.code,
                   date_trunc('month', MAX(i.date))::date AS last_ym
            FROM {identity_table} i
            WHERE i.code = ANY($1::text[])
            GROUP BY i.code
        ),
        expected AS (
            SELECT h.code, h.ym
            FROM ym_hist h
            JOIN code_last c ON c.code = h.code
            WHERE h.rows_upto_month_end >= $3
              AND h.ym < c.last_ym
        )
        SELECT e.code, e.ym
        FROM expected e
        LEFT JOIN {HIGH_LOW_PCT_TABLE} t
          ON t.sec_type = $2
         AND t.code = e.code
         AND t.date_year_month = e.ym
        GROUP BY e.code, e.ym
        HAVING COUNT(*) < $4
        """,
        sorted(codes_set), sec_type, HIGH_LOW_PCT_MIN_PERIODS,
        HIGH_LOW_PCT_ROWS_PER_PAIR,
    )
    return {(r["code"], r["ym"]) for r in rows}


async def _delete_incomplete_pairs(
    conn, sec_type: str, pairs: set[tuple[str, datetime.date]],
) -> int:
    """DELETE the table rows of INCOMPLETE pairs before their re-insert.

    Incremental mode only inserts rows for pairs the detection found
    missing; for present-but-incomplete pairs (partial chunk COPY from
    a crashed run) the stale rows must go first, or the re-inserted
    (period, pct_type) rows would PK-conflict. Deleting rows of a pair
    that will be fully re-inserted below is crash-safe: until the new
    COPY commits, the pair is merely still incomplete (what detection
    already treats as missing).
    """
    if not pairs:
        return 0
    codes_l, ym_dates = zip(*pairs)
    n = await conn.execute(
        f"DELETE FROM {HIGH_LOW_PCT_TABLE} t "
        f"WHERE t.sec_type = $1 "
        f"  AND (t.code, t.date_year_month) IN ("
        f"SELECT * FROM unnest($2::text[], $3::date[]))",
        sec_type, list(codes_l), list(ym_dates),
    )
    return int(n.rsplit(" ", 1)[-1]) if n else 0


# ---------------------------------------------------------------------------
#  Compute (pure pandas / cuDF + host numpy at the write boundary)
# ---------------------------------------------------------------------------

def compute_high_low_pct_bands(df: pd.DataFrame) -> pd.DataFrame:
    """Compute the month-end anchored multi-period high/low percentile
    bands.

    Args:
      df: frame sorted by (sec_type, code, date) carrying columns
          [sec_type, code, date, high, low] — the FULL per-code history
          (the trailing windows need up to 1275 prior rows per anchor).
          The frame must have a reset (positional) index.

    Returns:
      Long frame with HIGH_LOW_PCT_COLUMNS — one row per (sec_type,
      code, complete month, period, pct_type); month-anchors whose
      window has fewer than HIGH_LOW_PCT_MIN_PERIODS non-NULL
      observations for a leg are dropped for that (period, pct_type)
      band, as is the code's in-progress last month.

    The heavy percentiles run through _grouped_rolling_quantile (CPU
    fallback under cudf.pandas — see the module docstring); only the
    anchor rows' values are kept (a gather of ~1/21 of the rows per
    pass). The output frame is assembled from host numpy arrays — the
    CSV COPY render downstream is host-side regardless.
    """
    out_cols = list(HIGH_LOW_PCT_COLUMNS)
    if df.empty:
        return pd.DataFrame(columns=out_cols)

    # ---- Month-end anchors: last row of each (sec_type, code, month) --
    # Rows are sorted by (sec_type, code, date), so a row is its month's
    # last row iff the NEXT row changes group or month (the final row is
    # always an anchor — its shifted comparators are NULL).
    df["_hlp_ym"] = (
        df["date"].dt.year.astype("int64") * 100
        + df["date"].dt.month.astype("int64")
    )
    grp_changed = (
        df["_hlp_ym"].ne(df["_hlp_ym"].shift(-1))
        | df["code"].ne(df["code"].shift(-1))
        | df["sec_type"].ne(df["sec_type"].shift(-1))
    )
    anchor_mask = grp_changed.fillna(True).astype(bool)
    mask_host = host_array(anchor_mask.to_numpy(dtype=bool))
    anchor_pos = np.flatnonzero(mask_host)
    if anchor_pos.size == 0:
        return pd.DataFrame(columns=out_cols)

    # ---- Drop the in-progress last month: complete iff a LATER month
    # exists for the code (anchor month < the code's max-data month).
    grp_max = df.groupby(["sec_type", "code"])["date"].transform("max")
    grp_max_host = host_array(grp_max.to_numpy())
    dates_host = host_array(df["date"].to_numpy())
    anchor_ym_m = dates_host[anchor_pos].astype("datetime64[M]")
    complete = anchor_ym_m < grp_max_host[anchor_pos].astype("datetime64[M]")
    pos = anchor_pos[complete]
    if pos.size == 0:
        return pd.DataFrame(columns=out_cols)

    sec_vals = host_array(df["sec_type"].to_numpy())[pos]
    code_vals = host_array(df["code"].to_numpy())[pos]
    ym_start = dates_host[pos].astype("datetime64[M]").astype("datetime64[ns]")

    # ---- Trailing percentiles per (period, pct_type) -------------------
    # One CPU rolling-quantile pass per (period, pct_type, leg) — the
    # window differs per period and the quantile level per pct_type /
    # leg, so the passes cannot be merged.
    frames: list[pd.DataFrame] = []
    for period in HIGH_LOW_PCT_PERIODS:
        for pct in HIGH_LOW_PCT_TYPES:
            low_q = _grouped_rolling_quantile(
                df, "low",
                window=period,
                min_periods=HIGH_LOW_PCT_MIN_PERIODS,
                q=pct / 100.0,
            )
            high_q = _grouped_rolling_quantile(
                df, "high",
                window=period,
                min_periods=HIGH_LOW_PCT_MIN_PERIODS,
                q=(100 - pct) / 100.0,
            )
            low_v = host_array(low_q.to_numpy())[pos]
            high_v = host_array(high_q.to_numpy())[pos]
            valid = ~(np.isnan(low_v) | np.isnan(high_v))
            frames.append(pd.DataFrame({
                "sec_type": sec_vals[valid],
                "code": code_vals[valid],
                "date_year_month": ym_start[valid],
                "period": np.full(int(valid.sum()), period, dtype=np.int64),
                "pct_type": np.full(int(valid.sum()), pct, dtype=np.int64),
                # NUMERIC(10,2) target — round at the boundary so the
                # CSV render carries the stored precision.
                "high_val": np.round(high_v[valid], 2),
                "low_val": np.round(low_v[valid], 2),
            }))

    out = pd.concat(frames, ignore_index=True)
    return out[out_cols]


def _pairs_frame(
    pairs_by_st: dict[str, set[tuple[str, datetime.date]]],
) -> pd.DataFrame:
    """Build the (sec_type, code, date_year_month) key frame for a set of
    expected missing pairs per sec_type.

    Host-pure numpy date array — np.array() converts datetime.date
    objects to datetime64[ns] natively (no proxied .values / .astype
    dispatch, which falls back under cudf.pandas).
    """
    frames: list[pd.DataFrame] = []
    for st, pairs in pairs_by_st.items():
        if not pairs:
            continue
        codes_l, ym_dates = zip(*pairs)
        frames.append(pd.DataFrame({
            "sec_type": np.full(len(pairs), st, dtype=object),
            "code": np.asarray(codes_l, dtype=object),
            "date_year_month": np.array(ym_dates, dtype="datetime64[ns]"),
        }))
    if not frames:
        return pd.DataFrame(
            columns=["sec_type", "code", "date_year_month"],
        )
    return pd.concat(frames, ignore_index=True)


def _filter_to_missing_pairs(
    bands: pd.DataFrame,
    missing_by_st: dict[str, set[tuple[str, datetime.date]]],
) -> pd.DataFrame:
    """Inner-join the bands frame to the expected missing (code, month)
    pairs per sec_type.

    The join key is the full (sec_type, code, date_year_month) — codes
    can collide across sec_types (e.g. index 000001 vs stock 000001),
    so sec_type MUST be part of the key.
    """
    miss_df = _pairs_frame(missing_by_st)
    if miss_df.empty:
        return bands.iloc[0:0]
    return bands.merge(
        miss_df, on=["sec_type", "code", "date_year_month"], how="inner",
    )


async def _copy_bands_chunked(conn, bands: pd.DataFrame) -> int:
    """CSV-COPY the bands frame in row-count chunks.

    Sequential on ``conn``: band rows are monthly (<= ~1/21 of the
    daily universe x periods x pct_types), so even a full
    stock-universe rebuild is a few million narrow rows — sequential
    CSV COPY is fast enough and keeps the step free of pool-acquire
    bookkeeping. The ``pool`` / ``max_concurrent`` run-parameters are
    accepted for API compatibility with the sibling steps but unused
    here.

    Conflict-free by construction: the caller pre-wiped the incomplete
    pairs and pre-filtered to missing (code, month) pairs
    (incremental), or the scope was truncated/deleted upfront (--force
    / single-code), and each PK tuple appears exactly once.
    """
    n_total = len(bands)
    columns = list(HIGH_LOW_PCT_COLUMNS)
    n_chunks = (n_total + _BAND_CHUNK_ROWS - 1) // _BAND_CHUNK_ROWS
    total = 0
    for i in range(n_chunks):
        lo = i * _BAND_CHUNK_ROWS
        chunk = bands.iloc[lo:lo + _BAND_CHUNK_ROWS]
        n = await csv_copy_from_frame_async(
            conn, HIGH_LOW_PCT_TABLE, chunk, columns=columns,
        )
        total += n
        print(f"      bands chunk {i + 1}/{n_chunks}: COPY {n:,} rows "
              f"(cumulative {total:,})", flush=True)
    return total


# ---------------------------------------------------------------------------
#  Pipeline (internal step — invoked from mov_ave_spread.__main__)
# ---------------------------------------------------------------------------

async def run_high_low_pct(
    conn,
    df: pd.DataFrame,
    *,
    force: bool = False,
    pool=None,
    max_concurrent: int = 20,
    sec_type: str | None = None,
    code_filter: str | None = None,
) -> None:
    """Run the high-low percentile band pipeline against the source data
    already loaded by the parent mov_ave_spread.

    Reuses the caller's DB connection and source DataFrame (the
    ``high`` / ``low`` columns — no second DB fetch). The DataFrame
    must contain the FULL per-code history so each month-end anchor's
    trailing windows see up to 1275 prior rows.

    Pipeline
      0. Detect missing / incomplete (code, month) pairs per sec_type
         (PK-driven; scoped to the frame's codes — the exact compute
         universe). When nothing is missing, return before any compute.
         Incomplete pairs have their stale rows deleted first so the
         re-insert is conflict-free.
      1. Compute the month-end anchored bands over the FULL history
         (trailing windows are immutable — only missing months are
         inserted, but the rolling windows need the prior rows).
      2. Filter to the missing pairs, CSV-COPY insert in chunks.
      3. Upsert analysis.analysis_identity registry.

    Args:
      conn: asyncpg connection (reused from parent).
      df: source DataFrame with at least columns [sec_type, code, date,
          high, low] — the FULL per-code history.
      force: the parent's --force truncated / deleted the scope upfront
          (step 0 of __main__), so every month is (re)computed and
          inserted; detection is bypassed.
      pool: accepted for API compatibility with the sibling steps
          (unused — the COPY is sequential; see _copy_bands_chunked).
      max_concurrent: accepted for API compatibility (unused).
      sec_type: when provided, process only this sec_type (parent loop
                passes one sec_type at a time to bound memory). When
                None, infers sec_types from the DataFrame.
      code_filter: single-code mode (--code): recompute ALL of this
                   code's months (the caller already deleted its rows).
    """
    t0 = time.time()
    print("\n" + "=" * 78, flush=True)
    print("  MOV_AVE_HIGH_LOW_PCT (internal step of mov_ave_spread)",
          flush=True)
    print("=" * 78, flush=True)

    if code_filter is not None:
        print(f"    mode: SINGLE-CODE (full band rebuild for "
              f"{code_filter})", flush=True)
    elif force:
        print("    mode: FORCE (full band rebuild; the parent wiped the "
              "scope upfront)", flush=True)
    else:
        print("    mode: incremental (missing (code, month) pairs only)",
              flush=True)

    needed_cols = ["sec_type", "code", "date", "high", "low"]
    available = column_subset(df, needed_cols)
    hlp_df = df[available].copy()

    if hlp_df.empty:
        print("    -> no source data; skipping high-low-pct step.",
              flush=True)
        return

    if sec_type is not None:
        sec_types = (sec_type,)
    else:
        sec_types = tuple(sorted(hlp_df["sec_type"].unique()))

    # ---- Step 0: missing-pair detection (skip compute when current) --
    if code_filter is not None or force:
        # Single-code: rows pre-deleted by the caller. Force: the
        # parent truncated (full-universe) or deleted the scoped rows
        # (--sec-type) in its step 0 — recompute everything.
        missing_by_st: dict[str, set] | None = None
    else:
        missing_by_st = {}
        for st in sec_types:
            st_codes = sorted(set(host_array(
                hlp_df.loc[hlp_df["sec_type"] == st, "code"].to_numpy()
            )))
            pairs = await find_missing_high_low_pct_pairs(
                conn, SEC_TYPE_IDENTITY_TABLE[st], st, codes=st_codes,
            )
            missing_by_st[st] = pairs
            print(f"    -> {st}: {len(pairs):,} missing (code, month) "
                  f"pairs across {len(st_codes):,} codes", flush=True)
        if not any(missing_by_st.values()):
            print("    -> DB is up to date; nothing to do.", flush=True)
            return

    # ---- Step 1: compute bands over the full history -----------------
    print(f"\n[h1/3] Computing month-end trailing high/low percentile "
          f"bands for periods "
          f"{', '.join(str(p) for p in HIGH_LOW_PCT_PERIODS)} rows at "
          f"pct_type {', '.join(str(p) for p in HIGH_LOW_PCT_TYPES)} "
          f"(min {HIGH_LOW_PCT_MIN_PERIODS} window rows)...",
          flush=True)
    hlp_df = hlp_df.sort_values(
        ["sec_type", "code", "date"]
    ).reset_index(drop=True)
    bands = compute_high_low_pct_bands(hlp_df)
    del hlp_df
    n_codes = (
        bands[["sec_type", "code"]].drop_duplicates().shape[0]
        if not bands.empty else 0
    )
    print(f"    -> {len(bands):,} band rows across {n_codes:,} "
          f"(sec_type, code) groups", flush=True)

    # ---- Step 2: filter to missing pairs + COPY insert ---------------
    if missing_by_st is not None:
        n_expected = sum(len(p) for p in missing_by_st.values())
        n_before = len(bands)
        bands = _filter_to_missing_pairs(bands, missing_by_st)
        print(f"    -> missing-pair filter: {len(bands):,} of "
              f"{n_before:,} band rows kept ({n_expected:,} expected "
              f"pairs x {HIGH_LOW_PCT_ROWS_PER_PAIR} rows)", flush=True)
        n_band_pairs = (
            bands[["sec_type", "code", "date_year_month"]]
            .drop_duplicates().shape[0]
            if not bands.empty else 0
        )
        if n_band_pairs < n_expected:
            print(f"    WARNING: {n_expected - n_band_pairs:,} expected "
                  f"pairs produced no band (identity rows exist but the "
                  f"joined source has < {HIGH_LOW_PCT_MIN_PERIODS} "
                  f"non-NULL high/low observations, or the month's rows "
                  f"were dropped by the source INNER JOINs); they will "
                  f"be re-flagged on the next run.", flush=True)

    if bands.empty:
        print("    -> no band rows to insert; skipping COPY.", flush=True)
        return

    # ---- Wipe incomplete pairs' stale rows (incremental mode) --------
    # Detection flagged both fully-missing and present-but-incomplete
    # pairs; deleting the detected pairs' rows (a no-op for the fully
    # missing ones) makes the re-insert PK-conflict-free. Must run
    # AFTER the bands computed cleanly — a compute failure then leaves
    # the pairs merely incomplete (re-flagged next run), never empty.
    if missing_by_st is not None:
        for st in sec_types:
            n_del = await _delete_incomplete_pairs(
                conn, st, missing_by_st.get(st, set()),
            )
            if n_del:
                print(f"    -> deleted {n_del:,} stale rows of "
                      f"incomplete pairs ({st})", flush=True)

    print(f"\n[h2/3] COPY-inserting {len(bands):,} band rows "
          f"({len(bands) // HIGH_LOW_PCT_ROWS_PER_PAIR:,} code-months x "
          f"{len(HIGH_LOW_PCT_PERIODS)} periods x "
          f"{len(HIGH_LOW_PCT_TYPES)} pct_types)...", flush=True)
    n = await _copy_bands_chunked(conn, bands)
    del bands
    print(f"    -> inserted {n:,} band rows", flush=True)

    # ---- Step 3: register in analysis_identity ----------------------
    print(f"\n[h3/3] Upserting analysis.analysis_identity registry...",
          flush=True)
    await upsert_analysis_identity(
        conn,
        name=HIGH_LOW_PCT_ANALYSIS_NAME,
        detail_name="mov_ave_high_low_pct",
        description=HIGH_LOW_PCT_DESCRIPTION,
    )

    print(f"\n  mov_ave_high_low_pct wall time: "
          f"{time.time() - t0:.1f}s", flush=True)
