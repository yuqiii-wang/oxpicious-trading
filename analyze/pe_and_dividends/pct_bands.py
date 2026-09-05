"""Internal metric percentile band step for analyze.pe_and_dividends.

Monthly trailing percentile BANDS of the two valuation series carried by
analysis.pe_and_dividends — pe_ma20 and dividend_yield — one row per
(sec_type, code, date_year_month, metric, period, pct_type) in
analysis.pe_and_dividend_pct. The analysis.mov_ave_high_low_pct pattern
(high/low price bands) applied to the valuation metrics; the band-break
streaks audited against these bands live in pct_streaks.py.

=======================================================================
  FINANCIAL SEMANTICS
=======================================================================

For each calendar month m of a code's history, anchor the window at the
month's LAST observation row (the last row whose metric value is
non-NULL) and look BACK over a TRAILING window of `period` non-NULL
observations ending there, for each period in PD_PCT_PERIODS (255 / 500
/ 750 / 1275 = ~1 / 2 / 3 / 5 trading years — the ma255 yearly-window
precedent). Over that window:

    low_val[m, metric, period, pct]  = pct-th percentile (linear
                                       interpolation) of the window's
                                       metric values
    high_val[m, metric, period, pct] = (100 - pct)-th percentile of the
                                       window's metric values

Both legs use the SAME series — a valuation metric has one value per
day, so (unlike the price bands) there are no separate high/low legs.
pct_type in PD_PCT_TYPES (1 / 5 / 10) tightens the band: 1 = near-full
range of the window ([1st, 99th] percentile), 10 = core envelope
([10th, 90th]).

NULL-metric rows are excluded BEFORE the windows are built, so a
`period` window spans `period` genuine OBSERVATIONS (pe_ma20 is NULL
before the PE source warms up / on invalid-PE days; dividend_yield is
NULL until the first trailing-12m dividend window fills) and
min_periods counts observations, not calendar rows.

One row per (code, month, metric, period, pct_type) — 12 bands per
(code, month, metric), 24 per (code, month). Bands are sampled MONTHLY
(at the month-end observation anchor) and stored under the month's
FIRST day (date_year_month). Two consequences drive the rebuild
semantics (identical to the price bands):

1. TRAILING window (backward-only) — a completed month's trailing
   percentile is IMMUTABLE once computed. Per-(code, month, metric)
   INCREMENTAL upserts are therefore exact: missing triples are
   detected against the table's PK ((sec_type, code, date_year_month,
   metric, period, pct_type) — detected at (code, month, metric)
   granularity, where a triple counts as present only when all
   PD_PCT_ROWS_PER_TRIPLE (12) rows exist — a crash-consistency guard
   that recomputes partially-inserted triples), computed, and
   CSV-COPY inserted; no historical row ever needs refreshing.

2. IN-PROGRESS months are SKIPPED — a month is only banded once a
   LATER month with observations exists for that code (the month-end
   anchor must be the month's true last observation row). A month is
   banded on the first run after the next month's data arrives.

Detection also requires PD_PCT_MIN_PERIODS (255) cumulative non-NULL
observations of the metric through the month's end — fewer rows yield
no band (truncated window; high_val/low_val are NOT NULL). All periods
share the same 255-observation minimum, so triple expectation is
period-independent. Windows near a code's history start are naturally
truncated to the available observations. The code universe is the
run's processed scope (the parent's active universe), so delisted
codes' months never count as missing.

This module is an INTERNAL step of analyze.pe_and_dividends — invoked
from __main__.py after the daily detail rows are written, reusing the
in-memory detail DataFrame (the pe_ma20 / dividend_yield columns — no
second DB round-trip for source values; detection does read the detail
table to count observations per (code, month, metric)).

Insert path: PostgreSQL CSV COPY (csv_copy_from_frame_async), safe
because the inserted rows are pre-filtered to (code, month, metric)
triples the table does not completely have (incremental), or the scope
was deleted upfront (--force / single-code). Chunks are row-count
slices; each PK tuple appears exactly once in the frame, so no two
chunks can conflict.

GPU note: the trailing percentiles use pandas ``groupby(...).rolling
().quantile()`` via the shared _grouped_rolling_quantile helper (cuDF
lacks rolling-quantile support — CPU fallback under cudf.pandas, the
market_hypes / high_low_pct precedent; one CPU pass per (period,
pct_type, metric) = 24 per frame). The month keys, anchor detection
(shift-compare) and per-group max months stay on GPU-native column
ops; the final band frame is assembled from host numpy arrays — the
CSV COPY render downstream is host-side regardless.
"""
from __future__ import annotations

import datetime
import time

import numpy as np
import pandas as pd

from _common.db_commons import csv_copy_from_frame_async
from _common.df_utils import host_array
from analyze._common import upsert_analysis_identity
from analyze.mov_ave_spread.market_hypes import _grouped_rolling_quantile
from analyze.pe_and_dividends.config import (
    PD_PCT_COLUMNS,
    PD_PCT_DESCRIPTION,
    PD_PCT_METRICS,
    PD_PCT_MIN_PERIODS,
    PD_PCT_NAME,
    PD_PCT_PERIODS,
    PD_PCT_ROWS_PER_TRIPLE,
    PD_PCT_TABLE,
    PD_PCT_TYPES,
)

# Band rows per CSV COPY chunk (bounds the in-memory chunk sliced off
# the long frame before rendering — the high_low_pct.py precedent).
_BAND_CHUNK_ROWS = 200_000


# ---------------------------------------------------------------------------
#  Missing-(code, month, metric)-triple detection (PK-driven)
# ---------------------------------------------------------------------------

async def find_missing_pd_pct_triples(
    conn, sec_type: str, codes: list[str],
) -> set[tuple[str, str, datetime.date]]:
    """Return the (code, metric, month-first-day) triples of ``sec_type``
    whose band rows are missing or INCOMPLETE in
    analysis.pe_and_dividend_pct.

    A triple is EXPECTED — and therefore only counted when missing —
    when:

      - the code is in the given ``codes`` universe (the run's processed
        scope — the parent's active universe; delisted codes are outside
        the pipeline's universe and would never converge),
      - the code has >= PD_PCT_MIN_PERIODS (255) cumulative NON-NULL
        observations of the metric in analysis.pe_and_dividends through
        the month's end (fewer rows yield no band — the truncated window
        cannot satisfy min_periods; all periods share this minimum, so
        the expectation is period-independent), and
      - the month is COMPLETE: strictly earlier than the code's last
        month WITH observations of that metric (the in-progress month
        has no true month-end anchor yet — mirrors
        compute_metric_pct_bands).

    A present-but-INCOMPLETE triple (fewer than PD_PCT_ROWS_PER_TRIPLE
    rows — e.g. a chunk COPY aborted mid-run) is returned as missing
    too: the PK makes a re-computation of the whole triple conflict-free
    only if the stale rows are gone, and the caller wipes the triple's
    rows before insert (see _delete_incomplete_triples). COMPLETE
    triples are skipped.

    The check is one set-query (CTE window sums + LEFT JOIN + GROUP
    BY/HAVING) over the expected triples — no per-triple round-trips.
    """
    if not codes:
        return set()
    rows = await conn.fetch(
        f"""
        WITH ym AS (
            SELECT code,
                   date_trunc('month', date)::date AS ym,
                   COUNT(pe_ma20)        AS n_pe,
                   COUNT(dividend_yield) AS n_dy
            FROM analysis.pe_and_dividends
            WHERE sec_type = $1
              AND code = ANY($2::text[])
            GROUP BY 1, 2
        ),
        hist AS (
            SELECT code, ym, n_pe, n_dy,
                   SUM(n_pe) OVER (PARTITION BY code ORDER BY ym) AS pe_upto,
                   SUM(n_dy) OVER (PARTITION BY code ORDER BY ym) AS dy_upto
            FROM ym
        ),
        last_obs AS (
            SELECT code,
                   MAX(CASE WHEN n_pe > 0 THEN ym END) AS last_pe_ym,
                   MAX(CASE WHEN n_dy > 0 THEN ym END) AS last_dy_ym
            FROM ym
            GROUP BY code
        ),
        expected AS (
            SELECT h.code, h.ym, 'pe_ma20'::text AS metric
            FROM hist h JOIN last_obs lo ON lo.code = h.code
            WHERE lo.last_pe_ym IS NOT NULL
              AND h.pe_upto >= $3
              AND h.ym < lo.last_pe_ym
            UNION ALL
            SELECT h.code, h.ym, 'dividend_yield'::text AS metric
            FROM hist h JOIN last_obs lo ON lo.code = h.code
            WHERE lo.last_dy_ym IS NOT NULL
              AND h.dy_upto >= $3
              AND h.ym < lo.last_dy_ym
        )
        SELECT e.code, e.metric, e.ym
        FROM expected e
        LEFT JOIN {PD_PCT_TABLE} t
          ON t.sec_type = $1
         AND t.code = e.code
         AND t.date_year_month = e.ym
         AND t.metric = e.metric
        GROUP BY e.code, e.metric, e.ym
        HAVING COUNT(*) < $4
        """,
        sec_type, sorted(codes), PD_PCT_MIN_PERIODS, PD_PCT_ROWS_PER_TRIPLE,
    )
    return {(r["code"], r["metric"], r["ym"]) for r in rows}


async def _delete_incomplete_triples(
    conn, sec_type: str,
    triples: set[tuple[str, str, datetime.date]],
) -> int:
    """DELETE the table rows of INCOMPLETE triples before their re-insert.

    Incremental mode only inserts rows for triples the detection found
    missing; for present-but-incomplete triples (partial chunk COPY from
    a crashed run) the stale rows must go first, or the re-inserted
    (period, pct_type) rows would PK-conflict. Deleting rows of a triple
    that will be fully re-inserted below is crash-safe: until the new
    COPY commits, the triple is merely still incomplete (what detection
    already treats as missing).
    """
    if not triples:
        return 0
    codes_l, metrics_l, ym_dates = map(list, zip(*triples))
    n = await conn.execute(
        f"DELETE FROM {PD_PCT_TABLE} t "
        f"WHERE t.sec_type = $1 "
        f"  AND (t.code, t.metric, t.date_year_month) IN ("
        f"SELECT * FROM unnest($2::text[], $3::text[], $4::date[]))",
        sec_type, codes_l, metrics_l, ym_dates,
    )
    return int(n.rsplit(" ", 1)[-1]) if n else 0


# ---------------------------------------------------------------------------
#  Compute (pure pandas / cuDF + host numpy at the write boundary)
# ---------------------------------------------------------------------------

def compute_metric_pct_bands(df: pd.DataFrame) -> pd.DataFrame:
    """Compute the month-end anchored multi-period percentile bands for
    BOTH metrics (pe_ma20 + dividend_yield) of one sec_type's detail
    frame.

    Args:
      df: the daily detail frame (build_detail_rows output) with columns
          [sec_type, code, date, pe_ma20, dividend_yield] — the FULL
          per-code history (the trailing windows need up to 1275 prior
          observations per anchor). NULL-metric rows are dropped per
          metric inside.

    Returns:
      Long frame with PD_PCT_COLUMNS — one row per (sec_type, code,
      complete month, metric, period, pct_type); month-anchors whose
      window has fewer than PD_PCT_MIN_PERIODS non-NULL observations are
      dropped for that (period, pct_type) band, as is the code's
      in-progress last month.

    The heavy percentiles run through _grouped_rolling_quantile (CPU
    fallback under cudf.pandas — see the module docstring); only the
    anchor rows' values are kept (a gather of ~1/21 of the rows per
    pass). The output frame is assembled from host numpy arrays — the
    CSV COPY render downstream is host-side regardless.
    """
    out_cols = list(PD_PCT_COLUMNS)
    if df.empty:
        return pd.DataFrame(columns=out_cols)

    frames: list[pd.DataFrame] = []
    for metric in PD_PCT_METRICS:
        mdf = _metric_frame(df, metric)
        if mdf.empty:
            continue
        frames.append(_bands_for_metric(mdf, metric))

    if not frames:
        return pd.DataFrame(columns=out_cols)
    return pd.concat(frames, ignore_index=True)[out_cols]


def _metric_frame(df: pd.DataFrame, metric: str) -> pd.DataFrame:
    """Slice + clean one metric's observation frame.

    Returns [sec_type, code, date, value] with NULL-metric rows dropped,
    sorted by (sec_type, code, date) and positionally indexed — the
    rolling-quantile / anchor-machinery input contract.
    """
    mdf = df[["sec_type", "code", "date", metric]].copy()
    mdf = mdf.dropna(subset=[metric])
    if mdf.empty:
        return mdf
    return (
        mdf.sort_values(["sec_type", "code", "date"])
        .reset_index(drop=True)
        .rename(columns={metric: "value"})
    )


def _bands_for_metric(mdf: pd.DataFrame, metric: str) -> pd.DataFrame:
    """Compute one metric's month-anchored trailing percentile bands.

    mdf: the metric's observation frame (see _metric_frame) — non-NULL
    values only, sorted by (sec_type, code, date), reset index.
    """
    # ---- Month-end anchors: last observation row of each (sec_type,
    # code, month). Rows are sorted, so a row is its month's last
    # observation row iff the NEXT row changes group or month (the final
    # row is always an anchor — its shifted comparators are NULL).
    mdf["_pd_ym"] = (
        mdf["date"].dt.year.astype("int64") * 100
        + mdf["date"].dt.month.astype("int64")
    )
    grp_changed = (
        mdf["_pd_ym"].ne(mdf["_pd_ym"].shift(-1))
        | mdf["code"].ne(mdf["code"].shift(-1))
        | mdf["sec_type"].ne(mdf["sec_type"].shift(-1))
    )
    anchor_mask = grp_changed.fillna(True).astype(bool)
    mask_host = host_array(anchor_mask.to_numpy(dtype=bool))
    anchor_pos = np.flatnonzero(mask_host)
    if anchor_pos.size == 0:
        return pd.DataFrame(columns=list(PD_PCT_COLUMNS))

    # ---- Drop the in-progress last month: complete iff a LATER month
    # with observations exists for the code (anchor month < the code's
    # max-observation month).
    grp_max = mdf.groupby(["sec_type", "code"])["date"].transform("max")
    grp_max_host = host_array(grp_max.to_numpy())
    dates_host = host_array(mdf["date"].to_numpy())
    anchor_ym_m = dates_host[anchor_pos].astype("datetime64[M]")
    complete = anchor_ym_m < grp_max_host[anchor_pos].astype("datetime64[M]")
    pos = anchor_pos[complete]
    if pos.size == 0:
        return pd.DataFrame(columns=list(PD_PCT_COLUMNS))

    sec_vals = host_array(mdf["sec_type"].to_numpy())[pos]
    code_vals = host_array(mdf["code"].to_numpy())[pos]
    ym_start = dates_host[pos].astype("datetime64[M]").astype("datetime64[ns]")

    # ---- Trailing percentiles per (period, pct_type) -------------------
    # One CPU rolling-quantile pass per (period, pct_type) — the window
    # differs per period and the quantile level per pct_type (both legs
    # read the SAME column, so a leg pass collapses into the level).
    per_combo: list[pd.DataFrame] = []
    for period in PD_PCT_PERIODS:
        for pct in PD_PCT_TYPES:
            low_q = _grouped_rolling_quantile(
                mdf, "value",
                window=period,
                min_periods=PD_PCT_MIN_PERIODS,
                q=pct / 100.0,
            )
            high_q = _grouped_rolling_quantile(
                mdf, "value",
                window=period,
                min_periods=PD_PCT_MIN_PERIODS,
                q=(100 - pct) / 100.0,
            )
            low_v = host_array(low_q.to_numpy())[pos]
            high_v = host_array(high_q.to_numpy())[pos]
            valid = ~(np.isnan(low_v) | np.isnan(high_v))
            per_combo.append(pd.DataFrame({
                "sec_type": sec_vals[valid],
                "code": code_vals[valid],
                "date_year_month": ym_start[valid],
                "metric": np.full(int(valid.sum()), metric, dtype=object),
                "period": np.full(int(valid.sum()), period, dtype=np.int64),
                "pct_type": np.full(int(valid.sum()), pct, dtype=np.int64),
                # NUMERIC(12,6) target — round at the boundary so the
                # CSV render carries the stored precision (serves both
                # pe_ma20-scale and dividend_yield-scale metrics).
                "high_val": np.round(high_v[valid], 6),
                "low_val": np.round(low_v[valid], 6),
            }))

    return pd.concat(per_combo, ignore_index=True)


def _triples_frame(
    triples_by_st: dict[str, set[tuple[str, str, datetime.date]]],
) -> pd.DataFrame:
    """Build the (sec_type, code, date_year_month, metric) key frame for
    a set of expected missing triples per sec_type.

    Host-pure numpy date array — np.array() converts datetime.date
    objects to datetime64[ns] natively (no proxied .values / .astype
    dispatch, which falls back under cudf.pandas).
    """
    frames: list[pd.DataFrame] = []
    for st, triples in triples_by_st.items():
        if not triples:
            continue
        codes_l, metrics_l, ym_dates = map(list, zip(*triples))
        frames.append(pd.DataFrame({
            "sec_type": np.full(len(triples), st, dtype=object),
            "code": np.asarray(codes_l, dtype=object),
            "date_year_month": np.array(ym_dates, dtype="datetime64[ns]"),
            "metric": np.asarray(metrics_l, dtype=object),
        }))
    if not frames:
        return pd.DataFrame(
            columns=["sec_type", "code", "date_year_month", "metric"],
        )
    return pd.concat(frames, ignore_index=True)


def _filter_to_missing_triples(
    bands: pd.DataFrame,
    missing_by_st: dict[str, set[tuple[str, str, datetime.date]]],
) -> pd.DataFrame:
    """Inner-join the bands frame to the expected missing (code, month,
    metric) triples per sec_type.

    The join key is the full (sec_type, code, date_year_month, metric) —
    codes can collide across sec_types (e.g. index 000001 vs stock
    000001.SZ stripped), so sec_type MUST be part of the key.
    """
    miss_df = _triples_frame(missing_by_st)
    if miss_df.empty:
        return bands.iloc[0:0]
    return bands.merge(
        miss_df,
        on=["sec_type", "code", "date_year_month", "metric"],
        how="inner",
    )


async def _copy_bands_chunked(conn, bands: pd.DataFrame) -> int:
    """CSV-COPY the bands frame in row-count chunks.

    Sequential on ``conn``: band rows are monthly (<= ~1/21 of the daily
    universe x metrics x periods x pct_types), so even a full
    stock-universe rebuild is a few million narrow rows — sequential
    CSV COPY is fast enough (the high_low_pct.py precedent).
    Conflict-free by construction: the caller pre-wiped the incomplete
    triples and pre-filtered to missing triples (incremental), or the
    scope was deleted upfront (--force / single-code), and each PK tuple
    appears exactly once.
    """
    n_total = len(bands)
    columns = list(PD_PCT_COLUMNS)
    n_chunks = (n_total + _BAND_CHUNK_ROWS - 1) // _BAND_CHUNK_ROWS
    total = 0
    for i in range(n_chunks):
        lo = i * _BAND_CHUNK_ROWS
        chunk = bands.iloc[lo:lo + _BAND_CHUNK_ROWS]
        n = await csv_copy_from_frame_async(
            conn, PD_PCT_TABLE, chunk, columns=columns,
        )
        total += n
        print(f"      bands chunk {i + 1}/{n_chunks}: COPY {n:,} rows "
              f"(cumulative {total:,})", flush=True)
    return total


# ---------------------------------------------------------------------------
#  Pipeline (internal step — invoked from pe_and_dividends.__main__)
# ---------------------------------------------------------------------------

async def run_pd_pct_bands(
    conn,
    detail_df: pd.DataFrame,
    *,
    sec_type: str,
    force: bool = False,
    code_filter: str | None = None,
) -> None:
    """Run the metric percentile band pipeline against the detail frame
    already computed by the parent pe_and_dividends run.

    Reuses the caller's DB connection and the in-memory detail DataFrame
    (the pe_ma20 / dividend_yield columns — no second DB fetch of source
    values). The frame must contain the FULL per-code history so each
    month-end anchor's trailing windows see up to 1275 prior
    observations.

    Pipeline
      0. Scope wipe / missing-triple detection. --force: DELETE the
         sec_type's rows upfront (the parent's --force only truncates
         the detail + stats tables, so this step wipes its own scope);
         single-code: DELETE that code's rows; incremental: detect
         missing / incomplete (code, month, metric) triples (PK-driven,
         scoped to the frame's codes) — when nothing is missing, return
         before any compute.
      1. Compute the month-end anchored bands over the FULL history
         (trailing windows are immutable — only missing triples are
         inserted, but the rolling windows need the prior observations).
      2. Wipe incomplete triples' stale rows, filter to the missing
         triples, CSV-COPY insert in chunks.
      3. Upsert analysis.analysis_identity registry.

    Args:
      conn: asyncpg connection (reused from parent).
      detail_df: the daily detail frame (build_detail_rows output) with
          at least [sec_type, code, date, pe_ma20, dividend_yield] — the
          FULL per-code history of ONE sec_type.
      sec_type: the frame's sec_type (parent loop passes one at a time
          to bound memory).
      force: full rebuild of the sec_type's bands (scope deleted
          upfront, detection bypassed).
      code_filter: single-code mode (--code): recompute ALL of this
          code's months (its rows are deleted upfront).
    """
    t0 = time.time()
    print("\n" + "=" * 78, flush=True)
    print("  PE_AND_DIVIDEND_PCT (internal step of pe_and_dividends)",
          flush=True)
    print("=" * 78, flush=True)

    if detail_df.empty:
        print("    -> no detail data; skipping bands step.", flush=True)
        return

    if code_filter is not None:
        print(f"    mode: SINGLE-CODE (full band rebuild for "
              f"{code_filter})", flush=True)
    elif force:
        print(f"    mode: FORCE (full band rebuild for {sec_type})",
              flush=True)
    else:
        print(f"    mode: incremental (missing (code, month, metric) "
              f"triples only)", flush=True)

    codes = sorted(set(host_array(
        detail_df["code"].to_numpy()
    )))

    # ---- Step 0: scope wipe / missing-triple detection -----------------
    if code_filter is not None:
        await conn.execute(
            f"DELETE FROM {PD_PCT_TABLE} WHERE sec_type = $1 AND code = $2",
            sec_type, code_filter,
        )
        missing_by_st = None
    elif force:
        await conn.execute(
            f"DELETE FROM {PD_PCT_TABLE} WHERE sec_type = $1", sec_type,
        )
        missing_by_st = None
    else:
        triples = await find_missing_pd_pct_triples(conn, sec_type, codes)
        missing_by_st = {sec_type: triples}
        n_codes_hit = len({c for c, _, _ in triples})
        print(f"    -> {sec_type}: {len(triples):,} missing (code, month, "
              f"metric) triples across {n_codes_hit:,} of "
              f"{len(codes):,} codes", flush=True)
        if not triples:
            print("    -> DB is up to date; nothing to do.", flush=True)
            return

    # ---- Step 1: compute bands over the full history -------------------
    print(f"\n[b1/3] Computing month-end trailing metric percentile bands "
          f"for metrics {', '.join(PD_PCT_METRICS)}, periods "
          f"{', '.join(str(p) for p in PD_PCT_PERIODS)} observations at "
          f"pct_type {', '.join(str(p) for p in PD_PCT_TYPES)} "
          f"(min {PD_PCT_MIN_PERIODS} window observations)...",
          flush=True)
    bands = compute_metric_pct_bands(detail_df)
    n_band_codes = (
        bands[["sec_type", "code"]].drop_duplicates().shape[0]
        if not bands.empty else 0
    )
    print(f"    -> {len(bands):,} band rows across {n_band_codes:,} "
          f"(sec_type, code) groups", flush=True)

    # ---- Step 2: filter to missing triples + COPY insert ---------------
    if missing_by_st is not None:
        n_expected = sum(len(p) for p in missing_by_st.values())
        n_before = len(bands)
        bands = _filter_to_missing_triples(bands, missing_by_st)
        print(f"    -> missing-triple filter: {len(bands):,} of "
              f"{n_before:,} band rows kept ({n_expected:,} expected "
              f"triples x {PD_PCT_ROWS_PER_TRIPLE} rows)", flush=True)
        n_band_triples = (
            bands[["sec_type", "code", "date_year_month", "metric"]]
            .drop_duplicates().shape[0]
            if not bands.empty else 0
        )
        if n_band_triples < n_expected:
            print(f"    WARNING: {n_expected - n_band_triples:,} expected "
                  f"triples produced no band (detail rows exist but the "
                  f"in-memory frame has < {PD_PCT_MIN_PERIODS} non-NULL "
                  f"observations — restated source data); they will be "
                  f"re-flagged on the next run.", flush=True)

    if bands.empty:
        print("    -> no band rows to insert; skipping COPY.", flush=True)
        return

    # ---- Wipe incomplete triples' stale rows (incremental mode) --------
    # Detection flagged both fully-missing and present-but-incomplete
    # triples; deleting the detected triples' rows (a no-op for the
    # fully missing ones) makes the re-insert PK-conflict-free. Must run
    # AFTER the bands computed cleanly — a compute failure then leaves
    # the triples merely incomplete (re-flagged next run), never empty.
    if missing_by_st is not None:
        n_del = await _delete_incomplete_triples(
            conn, sec_type, missing_by_st.get(sec_type, set()),
        )
        if n_del:
            print(f"    -> deleted {n_del:,} stale rows of incomplete "
                  f"triples ({sec_type})", flush=True)

    n_pairs = (
        bands[["sec_type", "code", "date_year_month"]]
        .drop_duplicates().shape[0]
    )
    print(f"\n[b2/3] COPY-inserting {len(bands):,} band rows "
          f"({n_pairs:,} code-months x {len(PD_PCT_METRICS)} metrics x "
          f"{len(PD_PCT_PERIODS)} periods x {len(PD_PCT_TYPES)} "
          f"pct_types)...", flush=True)
    n = await _copy_bands_chunked(conn, bands)
    del bands
    print(f"    -> inserted {n:,} band rows", flush=True)

    # ---- Step 3: register in analysis_identity --------------------------
    print(f"\n[b3/3] Upserting analysis.analysis_identity registry...",
          flush=True)
    await upsert_analysis_identity(
        conn,
        name=PD_PCT_NAME,
        detail_name="pe_and_dividend_pct",
        description=PD_PCT_DESCRIPTION,
    )

    print(f"\n  pe_and_dividend_pct wall time: {time.time() - t0:.1f}s",
          flush=True)