"""Opposite industry correlations by benchmark offset (composite analysis).

Populates analysis_composites.industry_corr_benchmark_offsets — one row
per (industry_id, benchmark_industry_id, pool_size, benchmark_code,
start_date, interval) auditing, per 20/60/255-trading-day window:

  overall_corr_ma{W}_{W}d     RAW Pearson correlation of the two
                              industries' MA-{W} curves of mean_close
                              (same value as analysis.industry_correlations
                              — recomputed here so the audit row is
                              self-contained).
  offset_sub_corr_ma{W}_{W}d  Correlation of the benchmark-REMOVED trends
                              — the CONSOLIDATED offset primitive from
                              stats.cross_stats
                              (code_price_with_benchmark_offset): per
                              (industry, pool) the curve is the mean of
                              member indices' daily code-vs-benchmark
                              offsets, cumulated from the window start
                              (Pearson removes the per-window constant,
                              so this equals the correlation of the
                              offset LEVELS — the common benchmark move
                              subtracted out at k=1, no MA smoothing).
  opposite_score_ma{W}_{W}d   (1 - offset_sub_corr) / 2 in [0, 1] — the
                              opposite-correlation score (1 = perfectly
                              opposite once the benchmark is removed).

OFFSET SOURCE (consolidated 2026-09-07)
  The offset curves are NOT recomputed here anymore — they are the
  pair-grain code_price_with_benchmark_offset series of
  stats.cross_stats (built by builds.cross_stats from
  stats.index_basic_stats closes), averaged over the industry's member
  indices per pool. Pool buckets mirror builds.industry: member indices
  classified by their LATEST composition stock count (< 51 small,
  <= 180 mid, else large; compositionless members contribute to 'all'
  only). The former k-scaled MA-offset decomposition
  (k = MA_X[s]/MA_B[s], adj = MA_X − k·MA_B) is superseded by this
  read-from-source primitive.

COMPUTATION ARRANGEMENT (window component sums — no per-pair loops)
  Both stacks reuse the shared sliding-window Pearson kernel
  (_window_corr_stack): ONE sliding-window gather per (pool, benchmark,
  W) yields every pair's correlation at once. With N ≤ ~100 industries
  and ~87 grid starts per window the working set is ~10-100 MB —
  comfortably host-side.

Incremental mode (``target_dates`` non-empty — see
find_missing_offset_window_ends): only rows whose window END date
(start_date + W - 1 for some W with a non-NULL metric) is in
``target_dates`` are upserted. Full history is still loaded so the MA
curves are correct. No truncate.

Force mode (``force=True``): truncates the table first, then recomputes
and inserts all rows.

Filtered mode (``industry_ids`` non-empty): recomputes ALL windows for
the pairs among these industries only and upserts them (driven by the UI
refresh button). No truncate; incompatible with force.
"""
from __future__ import annotations

import datetime
import time
from typing import Optional, Sequence, Set

import numpy as np
import pandas as pd

from _common.build_commons import (
    copy_or_upsert_split_async,
    truncate_table_async,
    rec_col,
    rec_cols,
)
from _common.db_commons import batched_copy_by_key_async
from _common.df_utils import epoch_col_to_dt64
from analyze._common import upsert_analysis_identity
from analyze.industry_sentiments.correlations import (
    _grid_start_indices,
    _round_none,
    _window_col_ok,
    _window_corr_stack,
)
from analyze.analysis_composites.config import (
    ANALYSIS_DESCRIPTION_OFFSETS,
    ANALYSIS_NAME_OFFSETS,
    BASELINE_TABLE,
    INTERVAL_DAYS,
    MIN_OVERLAP,
    POOL_SIZES,
    TABLE_OFFSETS,
    WINDOWS,
)

import logging
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
#  Missing-window detection (incremental entry point)
# ---------------------------------------------------------------------------

async def find_missing_offset_window_ends(
    conn,
    benchmark_code: str,
) -> Set[datetime.date]:
    """Return the set of source dates that are POTENTIAL window END dates
    on the calendar grid but not yet covered by a computed window end for
    ``benchmark_code``.

    Identical residue logic to
    analyze.industry_sentiments.correlations.find_missing_corr_window_ends
    (the table is keyed by window START dates which lag the source calendar
    by design), with the covered-ends union reading THIS table filtered to
    the benchmark — a window end is covered when the row for the window
    that ends on it carries ANY non-NULL metric for that W.
    """
    min_w_by_res: dict[int, int] = {}
    for w in WINDOWS:
        r = (w - 1) % INTERVAL_DAYS
        min_w_by_res[r] = min(min_w_by_res.get(r, w), w)
    pot_conds = " OR ".join(
        f"(idx % {INTERVAL_DAYS}) = {r} AND "
        f"idx >= {(-(-(w - 1) // INTERVAL_DAYS)) * INTERVAL_DAYS + w - 1}"
        for r, w in sorted(min_w_by_res.items())
    )
    # Covered end of a row = the calendar date at (calendar index of
    # start_date) + W - 1 — TRADING-day arithmetic (a plain start_date +
    # (W - 1) adds CALENDAR days and lands on the wrong date).
    cov_selects = " UNION ".join(
        f"SELECT c2.date AS d FROM {TABLE_OFFSETS} t "
        f"JOIN cal c1 ON c1.date = t.start_date "
        f"JOIN cal c2 ON c2.idx = c1.idx + {w - 1} "
        f"WHERE t.benchmark_code = $1 AND ("
        f"t.overall_corr_ma{w}_{w}d IS NOT NULL "
        f"OR t.offset_sub_corr_ma{w}_{w}d IS NOT NULL)"
        for w in WINDOWS
    )
    sql = f"""
        WITH cal AS (
            SELECT date, ROW_NUMBER() OVER (ORDER BY date) - 1 AS idx
            FROM (SELECT DISTINCT date FROM {BASELINE_TABLE}) s
        ),
        pot AS (
            SELECT DISTINCT date FROM cal WHERE {pot_conds}
        ),
        cov AS ({cov_selects})
        SELECT p.date
        FROM pot p
        LEFT JOIN cov c ON c.d = p.date
        WHERE c.d IS NULL
    """
    rows = await conn.fetch(sql, benchmark_code)
    return {r["date"] for r in rows}


# ---------------------------------------------------------------------------
#  Consolidated offset curves (per industry, pool — from stats.cross_stats)
# ---------------------------------------------------------------------------

_OFFSET_CURVES_SQL = """
WITH latest AS (
    SELECT code, MAX(snapshot_date) AS max_date
    FROM stats.sec_composition
    WHERE source_type = 'index' AND stock_code IS NOT NULL
    GROUP BY code
),
stock_num AS (
    SELECT h.code, COUNT(DISTINCT h.stock_code) AS n
    FROM stats.sec_composition h
    JOIN latest ld ON h.code = ld.code AND h.snapshot_date = ld.max_date
    WHERE h.source_type = 'index' AND h.stock_code IS NOT NULL
    GROUP BY h.code
),
cls AS (
    SELECT code, industry_id
    FROM stats.sec_classification
    WHERE type = 'index'
      AND is_active = TRUE
      AND industry_id IS NOT NULL AND industry_id <> ''
      AND is_industry_not_strategy = TRUE
),
member_pool AS (
    -- Pool buckets mirror builds.industry.classify_pool_vectorized:
    -- small < 51 constituent stocks, mid <= 180, large otherwise;
    -- compositionless members contribute to the 'all' pool only.
    SELECT cls.code, cls.industry_id,
           CASE
               WHEN sn.n IS NULL THEN NULL
               WHEN sn.n < 51 THEN 'small'
               WHEN sn.n <= 180 THEN 'mid'
               ELSE 'large'
           END AS bucket
    FROM cls cls
    LEFT JOIN stock_num sn ON sn.code = cls.code
),
pools(pool_size) AS (
    VALUES ('small'), ('mid'), ('large'), ('all')
)
SELECT mp.industry_id, p.pool_size,
       extract(epoch from cs.date)::float8 AS date,
       AVG(cs.code_price_with_benchmark_offset)::float8 AS offset_pts
FROM member_pool mp
JOIN stats.cross_stats cs
    ON cs.code = mp.code
   AND cs.benchmark_code = $1::text
   AND cs.sec_type = 'index'
   AND cs.code_price_with_benchmark_offset IS NOT NULL
JOIN pools p ON p.pool_size = 'all' OR p.pool_size = mp.bucket
GROUP BY mp.industry_id, p.pool_size, cs.date
"""


async def fetch_offset_curves(conn, benchmark_code: str) -> pd.DataFrame:
    """(industry_id, pool_size, date, offset_pts) rows for one benchmark.

    The consolidated per-member daily offsets
    (stats.cross_stats.code_price_with_benchmark_offset) averaged over
    each industry's member indices per pool bucket. Always full history —
    the sliding windows need it; filtered (industry_ids) mode slices in
    pandas.
    """
    rows = await conn.fetch(_OFFSET_CURVES_SQL, benchmark_code)
    return pd.DataFrame(
        rec_cols(rows),
        columns=["industry_id", "pool_size", "date", "offset_pts"],
    )


# ---------------------------------------------------------------------------
#  Pipeline
# ---------------------------------------------------------------------------

async def run_opposite_correlations(
    conn,
    *,
    target_dates: Optional[Set[datetime.date]] = None,
    force: bool = False,
    industry_ids: Optional[Set[str]] = None,
    benchmarks: Sequence[str] = ("000300",),
) -> None:
    """Run the benchmark-offset correlation pipeline.

    Reuses the caller's DB connection.

    Pipeline
      1. Load all (date, industry_id, pool_size, mean_close) rows from
         stats.industry_basic_stats (non-NULL mean) — full history so the
         MA curves are correct.
      2. Load benchmark closes from stats.index_basic_stats.
      3. Per pool: pivot to a (date x industry) matrix, compute the
         MA-{W} curves + grid starts + full-window validity (shared with
         the correlations step), then per benchmark reindex the benchmark
         close onto the pool calendar and emit the audit stacks:
         overall (reused _window_corr_stack) and the benchmark-removed
         offset (component-sum algebra), plus the derived
         opposite score = (1 - offset) / 2.
      4. Truncate (force) + ONE key-batched write; upsert analysis_identity.
    """
    t0 = time.time()
    logger.info("\n" + "=" * 78)
    logger.info("  OPPOSITE INDUSTRY CORRELATIONS BY BENCHMARK OFFSET "
          "(analysis_composites)")
    logger.info("=" * 78)

    benchmarks = tuple(dict.fromkeys(benchmarks))
    filtered = industry_ids is not None and len(industry_ids) > 0
    if filtered and force:
        raise ValueError(
            "run_opposite_correlations: industry_ids filter cannot be "
            "combined with force=True (filtered runs must never truncate "
            "the whole table)"
        )
    incremental = (not force and not filtered
                   and target_dates is not None
                   and len(target_dates) > 0)
    if force:
        logger.info("    mode: FORCE (full recompute)")
    elif filtered:
        logger.info(f"    mode: FILTERED ({len(industry_ids)} industries — "
              f"recompute all their windows, upsert)")
    elif incremental:
        logger.info(f"    mode: incremental ({len(target_dates)} target "
              f"window-end dates)")
    logger.info(f"    benchmarks: {', '.join(benchmarks)}")

    # ---- Step 1: load mean_close series (same query as correlations) ----
    logger.info("\n[o1/4] Loading (date, industry_id, pool_size, mean_close) "
          f"from {BASELINE_TABLE} (non-NULL mean only)...")
    if filtered:
        rows = await conn.fetch(f"""
            SELECT extract(epoch from date)::float8 AS date,
                   industry_id, pool_size, mean_close
            FROM {BASELINE_TABLE}
            WHERE mean_close IS NOT NULL
              AND industry_id = ANY($1)
            ORDER BY industry_id, pool_size, date
        """, sorted(industry_ids))
    else:
        rows = await conn.fetch(f"""
            SELECT extract(epoch from date)::float8 AS date,
                   industry_id, pool_size, mean_close
            FROM {BASELINE_TABLE}
            WHERE mean_close IS NOT NULL
            ORDER BY industry_id, pool_size, date
        """)
    if not rows:
        logger.info("      -> no industry data; skipping.")
        return
    df = pd.DataFrame(rec_cols(rows))
    df["date"] = epoch_col_to_dt64(df["date"], index=df.index)
    df["mean_close"] = df["mean_close"].astype(float)
    logger.info(f"      -> {len(rows):,} rows")

    # ---- Step 2: load consolidated offset curves (stats.cross_stats) ----
    # Per benchmark: (industry, pool, date) mean-of-member daily offsets.
    offset_curves: dict[str, pd.DataFrame] = {}
    for bench_code in benchmarks:
        odf = await fetch_offset_curves(conn, bench_code)
        if odf.empty:
            logger.warning(f"      -> WARNING: no cross_stats offset rows "
                           f"for benchmark '{bench_code}' — all its offset "
                           f"columns will be NULL; skipping it.")
            continue
        odf["date"] = epoch_col_to_dt64(odf["date"], index=odf.index)
        odf["offset_pts"] = odf["offset_pts"].astype(float)
        offset_curves[bench_code] = odf
    benchmarks = tuple(b for b in benchmarks if b in offset_curves)
    if not benchmarks:
        logger.info("      -> no benchmarks with offset data; skipping.")
        return
    logger.info(f"      -> offset curves for {len(benchmarks)} benchmark(s)")

    # ---- Steps 3: per-pool stacks + emit rows ----------------------------
    logger.info("\n[o3/4] Per-pool windowed audit stacks "
          f"(windows={WINDOWS}, stride={INTERVAL_DAYS}d)...")

    out_rows: list[dict] = []
    tgt64: np.ndarray = (
        np.asarray(sorted(target_dates), dtype="datetime64[D]")
        if incremental else np.array([], dtype="datetime64[D]")
    )

    for pool in POOL_SIZES:
        sub = df[df["pool_size"] == pool]
        if sub.empty:
            continue
        wide = sub.pivot(
            index="date", columns="industry_id", values="mean_close"
        ).sort_index()
        t_len, n_ind = wide.shape
        if n_ind < 2:
            continue
        ids: np.ndarray = np.asarray(wide.columns)
        valid: np.ndarray = wide.notna().to_numpy()      # (T, N) bool
        sd_d: np.ndarray = np.asarray(wide.index).astype("datetime64[D]")
        overlap: np.ndarray = (
            valid.astype(np.int64).T @ valid.astype(np.int64)
        )
        starts: np.ndarray = _grid_start_indices(t_len)
        pair_ok: np.ndarray = np.triu(overlap >= MIN_OVERLAP, k=1)

        ma: dict[int, np.ndarray] = {
            w: wide.rolling(w, min_periods=w).mean().to_numpy()
            for w in WINDOWS
        }
        col_ok: dict[int, np.ndarray] = {
            w: _window_col_ok(ma[w], starts, w) for w in WINDOWS
        }

        for bench_code in benchmarks:
            # Consolidated offset matrix on the pool calendar: rows =
            # pool dates, cols = industries (aligned to the mean_close
            # pivot), values = mean-of-member daily offsets.
            odf = offset_curves[bench_code]
            if filtered:
                odf = odf[odf["industry_id"].isin(industry_ids)]
            sub_off = odf[odf["pool_size"] == pool]
            if sub_off.empty:
                offset_mat = np.full((t_len, n_ind), np.nan)
            else:
                off_wide = (
                    sub_off.pivot(index="date", columns="industry_id",
                                  values="offset_pts")
                    .reindex(index=wide.index, columns=ids)
                )
                offset_mat = off_wide.to_numpy(dtype=np.float64)
            col_ok_off: dict[int, np.ndarray] = {
                w: _window_col_ok(offset_mat, starts, w) for w in WINDOWS
            }

            # 3D emit mask (S, N, N): upper-triangle pairs where BOTH
            # industries' windows are valid (benchmark-gated offset /
            # score cells are NaN-masked inside _offset_corr_stack).
            emit3: np.ndarray = np.zeros(
                (starts.size, n_ind, n_ind), dtype=bool
            )
            for w in WINDOWS:
                ok = col_ok[w]                           # (S, N)
                emit3 |= ok[:, :, None] & ok[:, None, :]
            emit3 &= pair_ok[None, :, :]
            if incremental:
                touch3: np.ndarray = np.zeros_like(emit3)
                for w in WINDOWS:
                    ends = starts + w - 1
                    in_cal = ends < t_len
                    end_dates = sd_d[np.minimum(ends, t_len - 1)]
                    end_in_target = in_cal & np.isin(end_dates, tgt64)
                    ok = col_ok[w]
                    touch3 |= (
                        (ok[:, :, None] & ok[:, None, :])
                        & end_in_target[:, None, None]
                    )
                emit3 &= touch3
            s_idx, ai_idx, bi_idx = np.nonzero(emit3)
            pool_rows = int(s_idx.size)
            n_pairs = int(pair_ok.sum())
            if s_idx.size == 0:
                logger.info(f"      [{pool:5s}|{bench_code}] {t_len:,} dates x "
                      f"{n_ind} industries -> {n_pairs:,} pairs "
                      f"(overlap >= {MIN_OVERLAP}), {starts.size:,} grid "
                      f"starts, {pool_rows:,} rows")
                continue

            overall_vals: dict[int, np.ndarray] = {}
            sub_vals: dict[int, np.ndarray] = {}
            score_vals: dict[int, np.ndarray] = {}
            for w in WINDOWS:
                stack = _window_corr_stack(ma[w], starts, w, col_ok[w])
                overall_vals[w] = stack[s_idx, ai_idx, bi_idx]
                # Offset-sub: windowed Pearson of the CONSOLIDATED offset
                # curves (cumulated from the window start — equivalent to
                # levels under Pearson). Cells whose offset window is
                # invalid stay NaN (the shared kernel NaN-masks them).
                sub_stack = _window_corr_stack(
                    offset_mat, starts, w, col_ok_off[w],
                )
                sub_vals[w] = sub_stack[s_idx, ai_idx, bi_idx]
                # Opposite score = (1 - offset) / 2 on the FINITE offsets.
                s = sub_vals[w]
                score = (1.0 - s) / 2.0
                score = np.where(np.isfinite(s), score, np.nan)
                score_vals[w] = score

            order = np.lexsort((s_idx, bi_idx, ai_idx))
            ind_l = ids[ai_idx[order]].tolist()
            bench_ind_l = ids[bi_idx[order]].tolist()
            sd_l = sd_d[starts[s_idx[order]]].astype(object).tolist()
            ov_l = [_round_none(overall_vals[w][order], 4) for w in WINDOWS]
            sb_l = [_round_none(sub_vals[w][order], 4) for w in WINDOWS]
            sc_l = [_round_none(score_vals[w][order], 4) for w in WINDOWS]

            out_rows.extend(
                {
                    "industry_id": a,
                    "benchmark_industry_id": b,
                    "pool_size": pool,
                    "benchmark_code": bench_code,
                    "start_date": d,
                    "interval": INTERVAL_DAYS,
                    "overall_corr_ma20_20d": o20,
                    "overall_corr_ma60_60d": o60,
                    "overall_corr_ma255_255d": o255,
                    "offset_sub_corr_ma20_20d": u20,
                    "offset_sub_corr_ma60_60d": u60,
                    "offset_sub_corr_ma255_255d": u255,
                    "opposite_score_ma20_20d": c20,
                    "opposite_score_ma60_60d": c60,
                    "opposite_score_ma255_255d": c255,
                }
                for a, b, d,
                    o20, o60, o255,
                    u20, u60, u255,
                    c20, c60, c255
                in zip(
                    ind_l, bench_ind_l, sd_l,
                    ov_l[0], ov_l[1], ov_l[2],
                    sb_l[0], sb_l[1], sb_l[2],
                    sc_l[0], sc_l[1], sc_l[2],
                )
            )
            logger.info(f"      [{pool:5s}|{bench_code}] {t_len:,} dates x "
                  f"{n_ind} industries -> {n_pairs:,} pairs "
                  f"(overlap >= {MIN_OVERLAP}), {starts.size:,} grid "
                  f"starts, {pool_rows:,} rows")

    total_rows = len(out_rows)
    logger.info(f"      -> {total_rows:,} audit rows emitted"
          f"{' (target window-end dates filtered)' if incremental else ''}")
    if not out_rows:
        logger.info("      -> no rows to write; skipping.")
        return

    # ---- Step 4: truncate (force only) + write ---------------------------
    if force:
        logger.info(f"\n[o4/4] Truncating {TABLE_OFFSETS} and key-batched-COPY-"
              f"inserting {total_rows:,} rows (batch key = industry_id)...")
        await truncate_table_async(conn, TABLE_OFFSETS)
        n = await batched_copy_by_key_async(
            conn, TABLE_OFFSETS, out_rows, key="industry_id",
            label="offset-corr",
        )
        via = "key-batched COPY (force)"
    else:
        logger.info(f"\n[o4/4] Upserting {total_rows:,} rows into "
              f"{TABLE_OFFSETS}...")
        n_copied, n_upserted = await copy_or_upsert_split_async(
            conn, TABLE_OFFSETS, out_rows,
            key_columns=[
                "industry_id",
                "benchmark_industry_id",
                "pool_size",
                "benchmark_code",
                "start_date",
                "interval",
            ],
            date_column="start_date",
        )
        n = n_copied + n_upserted
        via = "COPY" if n_copied > 0 and n_upserted == 0 else \
            f"COPY+upsert ({n_copied}+{n_upserted})" if n_copied > 0 else \
            "upsert"
    logger.info(f"      -> inserted {n:,} rows via {via}")

    # ---- Register in analysis.analysis_identity --------------------------
    await upsert_analysis_identity(
        conn,
        name=ANALYSIS_NAME_OFFSETS,
        detail_name=ANALYSIS_NAME_OFFSETS,
        description=ANALYSIS_DESCRIPTION_OFFSETS,
    )

    summary = await conn.fetch(f"""
        SELECT pool_size AS pool, benchmark_code,
               COUNT(*) AS n_rows,
               COUNT(DISTINCT (industry_id, benchmark_industry_id))
                   AS n_pairs,
               MIN(start_date) AS first_date,
               MAX(start_date) AS last_date
        FROM {TABLE_OFFSETS}
        GROUP BY pool_size, benchmark_code
        ORDER BY benchmark_code, pool_size
    """)
    logger.info("\n      Summary by (benchmark, pool_size):")
    for r in summary:
        logger.info(f"        {r['benchmark_code']} {r['pool']:6s}: "
              f"{r['n_rows']:>8,} rows . {r['n_pairs']:>4} pairs . "
              f"{r['first_date']} -> {r['last_date']}")

    logger.info(f"\n  opposite correlations wall time: {time.time() - t0:.1f}s")
