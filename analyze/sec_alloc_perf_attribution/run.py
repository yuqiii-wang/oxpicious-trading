"""Connection-reusable entry point for analyze.sec_alloc_perf_attribution.

``run_perf_attribution(conn, *, force)`` runs the full pipeline using a
caller-supplied asyncpg connection (the caller owns the connection lifecycle).
This makes the producer callable as an INTERNAL STEP of
``analyze.industry_sentiments`` (which passes its own connection down so the
sentiments + correlations + perf_attribution + attributions + etf_contribution
steps form a single atomic-ish batch on one connection).

Date management
---------------
Unlike the aggregation internal steps (correlations / attributions /
etf_contribution) — which receive ``target_dates`` from the parent — this
PRODUCER computes its OWN target_dates (missing from
``analysis.sec_alloc_perf_attribution`` vs ``stats.index_identity``). The
perf-attr table's missing dates can differ from ``industry_sentiments``'
missing dates (e.g. on first run after folding, or when index_exts gained
dates that sentiments already has), and the downstream attributions +
etf_contribution steps REQUIRE perf-attr to be fully current for their
target dates. Self-managing target_dates here guarantees correctness
regardless of the caller's state.

Pipeline
  0. Force: TRUNCATE analysis.sec_alloc_perf_attribution (+ dates map;
     drop the secondary (sec_type, date) index — post-created after the
     bulk COPY). Incremental: detect missing dates (index_identity vs
     the tiny dates-map table — `code` is the leading PK/HASH key so
     date-only scans on the main table are expensive); early-return if
     up to date.
  1. Fetch all index closes (benchmarks) + recent-data pre-filter.
  2. Fetch composition shared weights + codes-with-composition filter set.
  2b. Fetch aggregate ETF amount per (date, tracking_index) from index_exts.
  3a. Build + insert Index subjects (indices with composition vs all indices,
      excl. self-pairs) via ``build_and_insert`` (chunked COPY — corr OFF
      by default; see ``run_corr_update`` for the dedicated corr build).
  4. Register written dates in the dates map + post-create the secondary
     index. Upsert analysis.analysis_identity registry.

``run_corr_update(conn)`` (the ``--corr`` sub-command) recomputes
corr_20d/60d/255d for grid dates and upserts them onto existing rows.

``run_etf_backfill(conn)`` (the ``--etf`` sub-command) attaches
ETF amounts + ratio + MA5 from ``stats.index_exts`` onto EXISTING rows
in-place (year-chunked UPDATE) — used when rows were written before
``builds.index`` (exts phase) populated index_exts, avoiding a full --force
recompute of the 55M-row table.
"""
from __future__ import annotations

import datetime
import time
from typing import Optional, Set

# IMPORTANT: import ``compute`` BEFORE ``pandas`` so that
# cudf.pandas (activated by compute/__init__.py) patches pandas
# before any pandas operations run.
from analyze.sec_alloc_perf_attribution.compute import build_and_insert  # noqa: F401

import pandas as pd

from _common.build_commons import (
    truncate_table_async,
    fetch_codes_with_recent_data_async,
    RECENT_TRADING_DAYS,
    recent_trading_day_cutoff,
)

from analyze._common import upsert_analysis_identity
from analyze.sec_alloc_perf_attribution.config import (
    ANALYSIS_NAME,
    CORR_WINDOWS,
    DATES_MAP_TABLE,
    RATIO_CAP,
    SEC_TYPE_DATE_INDEX,
    SEC_TYPE_DATE_INDEX_SQL,
    TABLE,
    DESCRIPTION,
)
from analyze.sec_alloc_perf_attribution.compute._gpu_corr import (
    fetch_corr_grid_dates,
)
from analyze.sec_alloc_perf_attribution.fetch import (
    fetch_codes_with_composition,
    fetch_shared_weights,
    fetch_index_closes,
    fetch_index_subject_closes,
    fetch_etf_amount_by_index,
)


# ---------------------------------------------------------------------------
#  Dates-map helpers (fast date existence — `code` is the leading PK/HASH
#  key, so date-only scans on the 40M-row main table are expensive)
# ---------------------------------------------------------------------------
async def _backfill_dates_map_if_stale(conn) -> None:
    """One-time guard: if the map is empty but the main table has rows
    (migration from the pre-map schema), populate the map from the main
    table's DISTINCT dates so missing-date detection stays correct."""
    n_map = await conn.fetchval(f"SELECT count(*) FROM {DATES_MAP_TABLE}")
    if n_map:
        return
    n_main = await conn.fetchval(f"SELECT count(*) FROM {TABLE}")
    if not n_main:
        return
    print(f"    -> dates map empty but {TABLE} has {n_main:,} rows; "
          f"backfilling map (one-time)...", flush=True)
    await conn.execute(
        f"INSERT INTO {DATES_MAP_TABLE} (date) "
        f"SELECT DISTINCT date FROM {TABLE} "
        f"ON CONFLICT (date) DO NOTHING"
    )


async def _fetch_map_dates(conn) -> Set[datetime.date]:
    rows = await conn.fetch(f"SELECT date FROM {DATES_MAP_TABLE}")
    return {r["date"] for r in rows}


async def _register_dates(conn, dates: Set[datetime.date]) -> None:
    """Record written dates in the map (idempotent)."""
    if not dates:
        return
    rows = [(d,) for d in sorted(dates)]
    await conn.executemany(
        f"INSERT INTO {DATES_MAP_TABLE} (date) VALUES ($1) "
        f"ON CONFLICT (date) DO NOTHING",
        rows,
    )


async def run_corr_update(conn) -> None:
    """Dedicated corr build (``--corr`` sub-command): recompute
    corr_20d/60d/255d for grid dates and upsert them onto EXISTING rows.

    The main pipeline runs with corr OFF (the GPU tensor pass is the
    most expensive step and corr only changes on the stride-20 grid);
    this sub-command refreshes it independently. The upsert payload
    carries ONLY the 4 PK columns + the 3 corr columns, so base
    columns (weights, ETF amounts, ratio) written by the main run are
    never touched — the corr-only fast path skips the per-subject
    merge/MA5 pipeline entirely.
    """
    t0 = time.time()
    print("\n" + "=" * 78, flush=True)
    print("  SEC ALLOC PERF ATTRIBUTION — CORR BUILD (stride grid only)",
          flush=True)
    print("=" * 78, flush=True)

    await _backfill_dates_map_if_stale(conn)

    # Grid dates that EXIST in the table (via the map — no main-table scan).
    grid_dates = await fetch_corr_grid_dates(conn)
    grid_set: Set[datetime.date] = set(pd.to_datetime(grid_dates).date)
    present = await _fetch_map_dates(conn)
    target_dates = grid_set & present
    print(f"    -> {len(target_dates)} grid dates present in {TABLE}",
          flush=True)
    if not target_dates:
        print("    -> no grid dates to update; run the main pipeline first.",
              flush=True)
        return

    # Full fetch: the earliest grid date reaches far back, so the lookback
    # window effectively keeps the full history (corr windows need it).
    index_closes = await fetch_index_closes(conn)
    if index_closes.empty:
        print("    -> no index data; exiting.", flush=True)
        return
    shared_weights = await fetch_shared_weights(conn)
    etf_amount_by_index = await fetch_etf_amount_by_index(conn)
    index_subject_closes = await fetch_index_subject_closes(conn)

    # Same recent-data pre-filter as the main run — keeps the subject and
    # benchmark universes identical so corr mode never INSERTS rows the
    # main pipeline deliberately skipped (delisted/suspended indices).
    active_index_codes = await fetch_codes_with_recent_data_async(
        conn, "stats.index_identity", n_trading_days=RECENT_TRADING_DAYS,
    )
    index_closes = index_closes[
        index_closes["benchmark_code"].isin(active_index_codes)
    ].copy()
    if index_closes.empty:
        print("    -> no indices with recent data; exiting.", flush=True)
        return
    broad_subjects = index_closes[
        ~index_closes["benchmark_code"].isin(index_subject_closes["code"])
    ].rename(columns={
        "benchmark_code": "code",
        "benchmark_close": "subject_close",
    })
    if not broad_subjects.empty:
        index_subject_closes = pd.concat(
            [index_subject_closes, broad_subjects], ignore_index=True
        )
    index_subject_closes = index_subject_closes[
        index_subject_closes["code"].isin(active_index_codes)
    ].copy()

    if index_subject_closes.empty:
        print("    -> no subjects; exiting.", flush=True)
        return

    n = await build_and_insert(
        conn, index_subject_closes, index_closes, shared_weights,
        etf_amount_by_index, sec_type="index",
        target_dates=target_dates, with_corr=True,
    )
    print(f"    -> corr build total: {n:,} rows", flush=True)
    print(f"\n  corr build wall time: {time.time() - t0:.1f}s", flush=True)


# ---------------------------------------------------------------------------
#  ETF-only backfill (--etf sub-command)
# ---------------------------------------------------------------------------
# Extended lookback (calendar days) before each chunk start so the MA5
# window (5 trading dates per (code, sec_type, benchmark_code)) sees the
# trailing 4 rows from the previous chunk. 20 calendar days covers the
# longest CN market holiday (Spring Festival ~8 days) with margin.
_MA5_LOOKBACK_CALENDAR_DAYS: int = 20

# Per-chunk ETF backfill: attach index_exts amounts + capped ratio + MA5.
# Mirrors compute/_etf.py EXACTLY (attach_etf_amounts + compute_ma5_ratio):
#   * benchmark_etf_trading_amount = index_exts.amount keyed on
#     (date, benchmark_code); NULL when no ETF tracks the benchmark.
#   * code_etf_trading_amount      = index_exts.amount keyed on
#     (date, code) — index subjects only (mirrors the sec_type='index'
#     guard in attach_etf_amounts; the table currently holds only
#     sec_type='index' rows anyway).
#   * ratio = bench/code, NULL when either is NULL/0 or |ratio| >= RATIO_CAP
#     (the cap is applied BEFORE the MA5 window, like compute_ma5_ratio).
#   * ma5 = avg(ratio) over the trailing 5 DATES per
#     (code, sec_type, benchmark_code), NULL-skipping (pandas
#     rolling(5, min_periods=1) semantics). The pipeline's grouped
#     rolling is per row-window within benchmark group sorted stably by
#     benchmark_code over date-major rows == trailing 5 dates per
#     (code, sec_type, benchmark_code).
# Chunked by calendar year (restartable, observable progress); each
# chunk joins an extra 20-day lookback before chunk_start so rows at
# the chunk head get correct trailing windows. Idempotent: re-running
# rewrites the same values.
# NOTE: the UPDATE joins on the PK (code, date, sec_type, benchmark_code),
# NOT ctid — ctid is only unique WITHIN each hash partition, so on the
# partitioned parent rows in different partitions can share a ctid and
# a ctid join cross-matches tuples between partitions (silently swapped
# ETF amounts).
ETF_BACKFILL_CHUNK_SQL = """
WITH joined AS (
    SELECT p.code, p.date, p.sec_type, p.benchmark_code,
           b.total_etf_trading_amount AS bench_amt,
           CASE WHEN p.sec_type = 'index'
                THEN c.total_etf_trading_amount END AS code_amt
    FROM {table} p
    LEFT JOIN stats.index_exts b
           ON b.date = p.date AND b.code = p.benchmark_code
    LEFT JOIN stats.index_exts c
           ON c.date = p.date AND c.code = p.code
    WHERE p.sec_type = 'index'
      AND p.date <= $2::date
      AND p.date >= $3::date
),
calc AS (
    SELECT j.code, j.date, j.sec_type, j.benchmark_code,
           j.bench_amt, j.code_amt,
           CASE WHEN j.bench_amt IS NULL OR j.code_amt IS NULL
                     OR j.bench_amt = 0 OR j.code_amt = 0
                     OR abs(j.bench_amt / NULLIF(j.code_amt, 0)) >= {ratio_cap}
                THEN NULL
                ELSE j.bench_amt / NULLIF(j.code_amt, 0) END AS ratio
    FROM joined j
),
win AS (
    SELECT code, date, sec_type, benchmark_code,
           bench_amt, code_amt, ratio,
           avg(ratio) OVER (
               PARTITION BY code, sec_type, benchmark_code
               ORDER BY date
               ROWS BETWEEN 4 PRECEDING AND CURRENT ROW
           ) AS ma5
    FROM calc
)
UPDATE {table} p
SET benchmark_etf_trading_amount               = win.bench_amt,
    code_etf_trading_amount                    = win.code_amt,
    etf_trading_amount_ratio_benchmark_to_code = win.ratio,
    etf_trading_amount_ratio_benchmark_to_code_ma5 = win.ma5
FROM win
WHERE p.code = win.code
  AND p.date = win.date
  AND p.sec_type = win.sec_type
  AND p.benchmark_code = win.benchmark_code
  AND p.date >= $1::date
""".format(table=TABLE, ratio_cap=RATIO_CAP)


async def run_etf_backfill(conn) -> None:
    """ETF-only backfill (``--etf`` sub-command): populate
    benchmark_etf_trading_amount, code_etf_trading_amount,
    etf_trading_amount_ratio_benchmark_to_code (+ _ma5) on EXISTING rows
    from ``stats.index_exts`` — without recomputing weights or corr.

    Rationale: rows written by the main pipeline BEFORE
    ``builds.index`` (exts phase) populated ``stats.index_exts`` carry NULL ETF
    amounts. A full ``--force`` recompute of the 55M-row table just to
    attach amounts is wasteful — the amounts are a pure function of
    (date, code) from index_exts, so they can be attached in-place with
    one UPDATE per year chunk. MA5 is recomputed per (code, sec_type,
    benchmark_code) over a trailing-5-date window with a 20-calendar-day
    lookback before each chunk (see ETF_BACKFILL_CHUNK_SQL).
    """
    t0 = time.time()
    print("\n" + "=" * 78, flush=True)
    print("  SEC ALLOC PERF ATTRIBUTION — ETF-ONLY BACKFILL (from index_exts)",
          flush=True)
    print("=" * 78, flush=True)

    bounds = await conn.fetchrow(
        f"SELECT min(date) AS min_date, max(date) AS max_date "
        f"FROM {DATES_MAP_TABLE}"
    )
    min_date: Optional[datetime.date] = bounds["min_date"]
    max_date: Optional[datetime.date] = bounds["max_date"]
    if min_date is None or max_date is None:
        print("    -> dates map empty (no rows in the main table); "
              "run the main pipeline first.", flush=True)
        return

    print(f"    -> backfilling ETF columns for {min_date} .. {max_date} "
          f"(year chunks, {_MA5_LOOKBACK_CALENDAR_DAYS}d MA5 lookback)",
          flush=True)

    for year in range(min_date.year, max_date.year + 1):
        chunk_start = max(datetime.date(year, 1, 1), min_date)
        chunk_end = min(datetime.date(year, 12, 31), max_date)
        ext_start = chunk_start - datetime.timedelta(
            days=_MA5_LOOKBACK_CALENDAR_DAYS
        )
        t_chunk = time.time()
        status = await conn.execute(
            ETF_BACKFILL_CHUNK_SQL, chunk_start, chunk_end, ext_start
        )
        print(f"    -> {year}: {status} ({time.time() - t_chunk:.1f}s)",
              flush=True)

    # Verification: populated counts + PK-join mismatch audit (a row is a
    # mismatch when the index_exts join hits but the stored value differs).
    ver = await conn.fetchrow(
        f"SELECT count(*) AS total, "
        f"count(benchmark_etf_trading_amount) AS n_bench, "
        f"count(code_etf_trading_amount) AS n_code, "
        f"count(etf_trading_amount_ratio_benchmark_to_code) AS n_ratio, "
        f"count(etf_trading_amount_ratio_benchmark_to_code_ma5) AS n_ma5 "
        f"FROM {TABLE}"
    )
    print(f"    -> verification: {dict(ver)}", flush=True)
    n_mismatch: int = int(await conn.fetchval(
        f"SELECT count(*) FROM {TABLE} p "
        f"LEFT JOIN stats.index_exts b "
        f"       ON b.date = p.date AND b.code = p.benchmark_code "
        f"LEFT JOIN stats.index_exts c "
        f"       ON c.date = p.date AND c.code = p.code "
        f"WHERE p.benchmark_etf_trading_amount "
        f"      IS DISTINCT FROM b.total_etf_trading_amount "
        f"   OR p.code_etf_trading_amount IS DISTINCT FROM "
        f"      CASE WHEN p.sec_type = 'index' "
        f"           THEN c.total_etf_trading_amount END"
    ))
    print(f"    -> PK-join mismatch audit: {n_mismatch} rows differ "
          f"from index_exts", flush=True)
    print(f"\n  etf backfill wall time: {time.time() - t0:.1f}s", flush=True)


async def run_perf_attribution(
    conn,
    *,
    force: bool = False,
) -> None:
    """Run the sec_alloc_perf_attribution pipeline on a caller-supplied
    connection.

    Reuses the caller's DB connection (does not open/close its own) so this
    can run as an internal step of ``analyze.industry_sentiments``. Manages
    its OWN target_dates detection — see the module docstring for why.

    Args:
      conn: asyncpg connection (caller owns lifecycle).
      force: when True, truncate ``analysis.sec_alloc_perf_attribution`` first
        and recompute all rows. When False, detect + compute only missing
        dates (early-return if up to date).
    """
    t0 = time.time()
    print("\n" + "=" * 78, flush=True)
    print("  SEC ALLOC PERF ATTRIBUTION (INDEX x INDEX) "
          "— internal step of industry_sentiments", flush=True)
    print("=" * 78, flush=True)
    if force:
        print("    mode: FORCE (full recompute)", flush=True)

    # ---- Step 0: determine target dates -------------------------------
    if force:
        print(f"\n[0/6] Force mode: truncating {TABLE}...", flush=True)
        await truncate_table_async(conn, TABLE)
        await truncate_table_async(conn, DATES_MAP_TABLE)
        # Drop the secondary index for the load — post-created after the
        # bulk COPY (maintaining it live during a 40M-row load costs far
        # more than one rebuild at the end).
        await conn.execute(
            f"DROP INDEX IF EXISTS analysis.{SEC_TYPE_DATE_INDEX}"
        )
        target_dates: Optional[Set[datetime.date]] = None
        print("    -> truncated (+ dates map); dropped secondary index; "
              "will recompute all rows", flush=True)
    else:
        print("\n[0/6] Detecting missing dates "
              "(source: index_identity vs dates map)...",
              flush=True)
        await _backfill_dates_map_if_stale(conn)
        source_rows = await conn.fetch(
            "SELECT DISTINCT date FROM stats.index_identity ORDER BY date"
        )
        source_dates = {r["date"] for r in source_rows}
        existing = await _fetch_map_dates(conn)
        target_dates = source_dates - existing
        print(f"    -> {len(target_dates)} dates missing from {TABLE} "
              f"(map has {len(existing)} of {len(source_dates)} source dates)",
              flush=True)
        if not target_dates:
            print("    -> perf_attribution up to date; nothing to do.",
                  flush=True)
            print(f"\n  perf_attribution wall time: {time.time() - t0:.1f}s",
                  flush=True)
            return

    # ---- Compute lookback start_date for incremental mode ---------
    # When target_dates is set, only fetch data within the lookback window
    # (max corr window + MA5 buffer) to reduce DB I/O and memory usage.
    # This is an additional optimization on top of the pandas-level filter
    # in build_and_insert's _filter_dataframes_for_lookback.
    _MAX_CORR: int = max(CORR_WINDOWS)  # 255 trading days
    _MA5_BUF: int = 5                   # for etf_trading_amount_ratio MA5
    _LOOKBACK: int = _MAX_CORR + _MA5_BUF  # 260 trading days
    start_date: Optional[datetime.date] = None
    if target_dates is not None and len(target_dates) > 0:
        min_target: datetime.date = min(target_dates)
        start_date = recent_trading_day_cutoff(_LOOKBACK, ref=min_target)
        print(f"    -> lookback window: {start_date} to {min_target} "
              f"({_LOOKBACK} trading days)", flush=True)

    # ---- Step 1: fetch ALL index closes (used as benchmarks) -----
    print("\n[1/6] Fetching all index closes (benchmarks)...", flush=True)
    index_closes = await fetch_index_closes(conn, start_date=start_date)
    n_indices = index_closes["benchmark_code"].nunique() if not index_closes.empty else 0
    print(f"    -> {len(index_closes):,} index rows across {n_indices} indices",
          flush=True)

    if index_closes.empty:
        print("    -> no index data; exiting.", flush=True)
        return

    # ---- Step 1b: recent-data pre-filter ----------------------------
    # Drop any index (benchmark OR subject candidate) whose latest
    # stats.index_identity row is older than the cutoff — i.e. NO data
    # in the last RECENT_TRADING_DAYS trading days. Such indices are
    # delisted / suspended / never-traded and would contribute empty
    # subject rows. Filtering here covers BOTH the benchmark universe
    # and index subjects (subjects are derived from this same
    # index_closes frame below).
    cutoff = recent_trading_day_cutoff(RECENT_TRADING_DAYS)
    active_index_codes = await fetch_codes_with_recent_data_async(
        conn, "stats.index_identity", n_trading_days=RECENT_TRADING_DAYS,
    )
    before = int(index_closes["benchmark_code"].nunique())
    index_closes = index_closes[
        index_closes["benchmark_code"].isin(active_index_codes)
    ].copy()
    after = int(index_closes["benchmark_code"].nunique())
    print(f"    -> recent-data pre-filter (cutoff={cutoff.isoformat()}, "
          f"{RECENT_TRADING_DAYS} trading days): kept {after} of {before} "
          f"indices (dropped {before - after} with no recent data)", flush=True)
    if index_closes.empty:
        print("    -> no indices with recent data; exiting.", flush=True)
        return

    # ---- Step 2: fetch composition shared weights + codes-with-comp --
    print("\n[2/6] Fetching composition shared weights (ALL pairs) + "
          "codes-with-composition filter set...", flush=True)
    shared_weights = await fetch_shared_weights(conn)
    print(f"    -> {len(shared_weights):,} (subject, benchmark) pairs with shared weights",
          flush=True)
    codes_with_comp = await fetch_codes_with_composition(conn)
    print(f"    -> {len(codes_with_comp):,} codes have composition data "
          f"(used to filter subjects)", flush=True)

    # ---- Step 2b: fetch aggregate ETF amount per (date, index) ----
    # Reads precomputed total_etf_trading_amount from stats.index_exts (built by
    # build_index_exts.py). Used to populate benchmark_etf_trading_amount AND
    # code_etf_trading_amount for index subjects (both keyed on the tracked index
    # code via stats.sec_classification.parent_index_code).
    print("\n[2b/6] Fetching total_etf_trading_amount from stats.index_exts per "
          "(date, tracking_index)...", flush=True)
    etf_amount_by_index = await fetch_etf_amount_by_index(
        conn, start_date=start_date
    )
    if not etf_amount_by_index.empty:
        n_idx_with_etf = etf_amount_by_index["index_code"].nunique()
        print(f"    -> {len(etf_amount_by_index):,} rows across "
              f"{n_idx_with_etf} indices that have tracking ETFs", flush=True)
    else:
        print("    -> no ETF->index mapping data; benchmark_etf_trading_amount "
              "and code_etf_trading_amount (for index subjects) will be NULL.",
              flush=True)

    total = 0

    # ---- Step 3a: Index subjects ---------------------------------
    # Subject pool: ALL compositioned non-broad non-debt indices
    # (for Intraday Attribution to show all industries).
    # Benchmark pool (index_closes): top-N per industry by ETF turnover
    # + all broad-market indices (for the Market Movements top plot).
    print("\n[3a/6] Building Index subjects (ALL compositioned indices vs "
          "top-N benchmark pool)...", flush=True)
    index_subject_closes = await fetch_index_subject_closes(
        conn, start_date=start_date
    )

    # Also include broad-market indices in the subject pool (they should
    # appear as subjects in the Attribution view alongside all others).
    broad_subjects = index_closes[
        ~index_closes["benchmark_code"].isin(index_subject_closes["code"])
    ].rename(columns={
        "benchmark_code": "code",
        "benchmark_close": "subject_close",
    })
    if not broad_subjects.empty:
        index_subject_closes = pd.concat(
            [index_subject_closes, broad_subjects], ignore_index=True
        )

    # Recent-data pre-filter also applies to subjects.
    before_idx = int(index_subject_closes["code"].nunique())
    index_subject_closes = index_subject_closes[
        index_subject_closes["code"].isin(active_index_codes)
    ].copy()
    after_idx = int(index_subject_closes["code"].nunique())
    print(f"    -> {after_idx} of {before_idx} subjects "
          f"(recent-data filter + broad-include)", flush=True)

    if not index_subject_closes.empty:
        n = await build_and_insert(conn, index_subject_closes, index_closes,
                                   shared_weights,
                                   etf_amount_by_index,
                                   sec_type="index",
                                   target_dates=target_dates)
        total += n
        print(f"    -> Index total: {n:,} rows", flush=True)

    print(f"\n    -> grand total: {total:,} rows", flush=True)

    # ---- Step 4: register written dates + post-create index ---------
    print(f"\n[4/6] Registering written dates in {DATES_MAP_TABLE} + "
          f"post-creating secondary index...", flush=True)
    if target_dates is not None:
        written = set(target_dates)
    else:
        # Force mode: all source dates were recomputed.
        rows_d = await conn.fetch(
            "SELECT DISTINCT date FROM stats.index_identity"
        )
        written = {r["date"] for r in rows_d}
    await _register_dates(conn, written)
    print(f"    -> map now covers {len(written)} written dates", flush=True)
    t_idx = time.time()
    await conn.execute(SEC_TYPE_DATE_INDEX_SQL)
    print(f"    -> index {SEC_TYPE_DATE_INDEX} ready "
          f"({time.time() - t_idx:.1f}s)", flush=True)

    # ---- Step 4b: upsert analysis_identity -------------------------
    print(f"\n[4b/6] Upserting analysis.analysis_identity registry...",
          flush=True)
    await upsert_analysis_identity(
        conn,
        name=ANALYSIS_NAME,
        detail_name="sec_alloc_perf_attribution",
        description=DESCRIPTION,
    )

    print(f"\n  perf_attribution wall time: {time.time() - t0:.1f}s",
          flush=True)
