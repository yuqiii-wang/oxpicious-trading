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
                              (common market factor subtracted out).
  opposite_score_ma{W}_{W}d   (1 - offset_sub_corr) / 2 in [0, 1] — the
                              opposite-correlation score (1 = perfectly
                              opposite once the benchmark is removed).

OFFSET MATH (per window starting at grid date s)
  MA_X[t] = trailing W-day rolling mean of mean_close (industry trend —
            identical input to analyze.industry_sentiments.correlations).
  MA_B[t] = trailing W-day rolling mean of stats.index_basic_stats.close
            for benchmark_code, reindexed onto the pool calendar.
  k_X     = MA_X[s] / MA_B[s]      (benchmark rebased to the industry's
                                    MA level at the window start — the
                                    scaled benchmark moves in MA_X units).
  adj_X[t] = MA_X[t] - k_X * MA_B[t]        (benchmark-removed trend: an
                                             industry up while the
                                             benchmark is up MORE is DOWN
                                             after the offset).
  P_X[t]  = 100 + adj_X[t] - adj_X[s]       (recomputed price, starts at
                                             exactly 100 at s; Pearson is
                                             shift/scale-invariant, so the
                                             rebase is presentation only).

COMPUTATION ARRANGEMENT (window component sums — no per-pair loops)
  Pearson of linear combinations is expressible from the window sums of
  the components — for u_i = X_i - kx_i*B, v_j = Y_j - ky_j*B over w
  dates see _offset_corr_stack below — so ONE sliding-window gather +
  batched einsum/matmul per (pool, benchmark, W) yields every pair's
  overall / offset correlation at once. With N ≤ ~100 industries and
  ~87 grid starts per window the working set is ~10-100 MB — comfortably
  host-side (the correlations step's CuPy routing threshold is 64 MiB and
  is not needed here).

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
    BENCHMARK_TABLE,
    INTERVAL_DAYS,
    MIN_OVERLAP,
    POOL_SIZES,
    TABLE_OFFSETS,
    WINDOWS,
)


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
#  Windowed correlation stacks (overall / benchmark-offset)
# ---------------------------------------------------------------------------

def _bench_curve_on_calendar(
    bench_close: pd.Series, cal_index: pd.Index,
) -> np.ndarray:
    """Benchmark close reindexed onto the pool calendar (NaN off-calendar
    or missing) as a float64 numpy array."""
    s = bench_close.reindex(cal_index)
    return s.to_numpy(dtype=np.float64)


def _bench_window_ok(
    bench_ma: np.ndarray, starts: np.ndarray, w: int,
) -> np.ndarray:
    """Per grid start: is the benchmark MA-{w} defined on EVERY date of
    the window [s, s + w) AND at s itself (the k scale factor needs
    MA_B[s])? A window's first date IS s, so the window check covers it."""
    t_len = bench_ma.size
    nan_cs = np.concatenate(([0], np.cumsum(np.isnan(bench_ma))))
    idx_e = np.minimum(starts + w, t_len)
    nan_cnt = nan_cs[idx_e] - nan_cs[starts]
    return (nan_cnt == 0) & ((starts + w) <= t_len)


def _offset_corr_stack(
    ma: np.ndarray,
    bench_ma: np.ndarray,
    starts: np.ndarray,
    w: int,
) -> np.ndarray:
    """Per grid start: the full (N, N) Pearson-correlation matrix of the
    benchmark-removed trends u_i = MA_i - k_i * MA_B (k_i = MA_i[s] /
    MA_B[s]) over the window [s, s + w).

    The adjusted series is a linear combination, so its Pearson matrix is
    computed from the window component sums (SX / SXX / SXY / Sxb / Sb /
    Sbb) in ONE sliding-window gather + einsum/matmul pass — no per-pair
    loops, no materialized (S, W, N) adjusted matrices.

    NaN cells are zero-filled before the sums; validity is enforced by
    the caller's emit mask (valid pairs never touch a filled cell).
    Entries for start rows where the benchmark window is invalid are
    NaN-masked here.
    """
    n_starts = starts.size
    _, n_ind = ma.shape
    stack = np.full((n_starts, n_ind, n_ind), np.nan, dtype=np.float64)
    full = np.nonzero((starts + w) <= ma.shape[0])[0]
    if full.size == 0:
        return stack
    s_full = starts[full]

    x0 = np.ascontiguousarray(
        np.lib.stride_tricks.sliding_window_view(ma, w, axis=0)[s_full]
        .transpose(0, 2, 1)
    )                                                    # (F, w, N)
    b0 = np.lib.stride_tricks.sliding_window_view(bench_ma, w)[s_full]
    b0 = np.ascontiguousarray(b0)                        # (F, w)
    np.copyto(x0, 0.0, where=np.isnan(x0))
    np.copyto(b0, 0.0, where=np.isnan(b0))

    # Component sums over the window.
    sx: np.ndarray = x0.sum(axis=1)                      # (F, N)
    sxx: np.ndarray = np.einsum("fwi,fwi->fi", x0, x0)   # (F, N)
    sxy: np.ndarray = x0.transpose(0, 2, 1) @ x0         # (F, N, N)
    sxb: np.ndarray = np.einsum("fwi,fw->fi", x0, b0)    # (F, N)
    sb: np.ndarray = b0.sum(axis=1)                      # (F,)
    sbb: np.ndarray = np.einsum("fw,fw->f", b0, b0)      # (F,)

    # Per-(start, industry) scale factor k = MA[s] / MA_B[s].
    ma_s = ma[s_full]                                    # (F, N)
    bb_s = bench_ma[s_full]                              # (F,)
    with np.errstate(divide="ignore", invalid="ignore"):
        k = ma_s / bb_s[:, None]                         # (F, N)
    k = np.where(np.isfinite(k), k, np.nan)

    kx = k[:, :, None]                                   # subject scale
    ky = k[:, None, :]                                   # benchmark-pair scale
    sb_b = sb[:, None, None]

    # Sums of u_i = MA_i - k_i*B and v_j = MA_j - k_j*B.
    su = sx[:, :, None] - kx * sb_b                      # (F, N, 1)
    sv = sx[:, None, :] - ky * sb_b                      # (F, 1, N)
    suu = sxx[:, :, None] \
        - 2.0 * kx * sxb[:, :, None] \
        + kx * kx * sbb[:, None, None]                   # (F, N, 1)
    svv = sxx[:, None, :] \
        - 2.0 * ky * sxb[:, None, :] \
        + ky * ky * sbb[:, None, None]                   # (F, 1, N)
    suv = sxy \
        - ky * sxb[:, :, None] \
        - kx * sxb[:, None, :] \
        + kx * ky * sbb[:, None, None]                   # (F, N, N)

    cov = suv - su * sv / w
    var_u = suu - su * su / w
    var_v = svv - sv * sv / w
    with np.errstate(divide="ignore", invalid="ignore"):
        corr = cov / np.sqrt(var_u * var_v)

    # Start rows where the benchmark MA window is invalid -> all NaN.
    bok = _bench_window_ok(bench_ma, starts, w)
    corr[~bok[full]] = np.nan
    stack[full] = corr
    return stack


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
    print("\n" + "=" * 78, flush=True)
    print("  OPPOSITE INDUSTRY CORRELATIONS BY BENCHMARK OFFSET "
          "(analysis_composites)", flush=True)
    print("=" * 78, flush=True)

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
        print("    mode: FORCE (full recompute)", flush=True)
    elif filtered:
        print(f"    mode: FILTERED ({len(industry_ids)} industries — "
              f"recompute all their windows, upsert)", flush=True)
    elif incremental:
        print(f"    mode: incremental ({len(target_dates)} target "
              f"window-end dates)", flush=True)
    print(f"    benchmarks: {', '.join(benchmarks)}", flush=True)

    # ---- Step 1: load mean_close series (same query as correlations) ----
    print("\n[o1/4] Loading (date, industry_id, pool_size, mean_close) "
          f"from {BASELINE_TABLE} (non-NULL mean only)...", flush=True)
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
        print("      -> no industry data; skipping.", flush=True)
        return
    df = pd.DataFrame(rec_cols(rows))
    df["date"] = epoch_col_to_dt64(df["date"], index=df.index)
    df["mean_close"] = df["mean_close"].astype(float)
    print(f"      -> {len(rows):,} rows", flush=True)

    # ---- Step 2: load benchmark closes ----------------------------------
    print(f"\n[o2/4] Loading benchmark closes from {BENCHMARK_TABLE}...",
          flush=True)
    bench_rows = await conn.fetch(f"""
        SELECT extract(epoch from date)::float8 AS date, code, close
        FROM {BENCHMARK_TABLE}
        WHERE code = ANY($1) AND close IS NOT NULL
        ORDER BY code, date
    """, list(benchmarks))
    bdf = pd.DataFrame(rec_cols(bench_rows))
    bdf["date"] = epoch_col_to_dt64(bdf["date"], index=bdf.index)
    bdf["close"] = bdf["close"].astype(float)
    bench_series: dict[str, pd.Series] = {
        code: g.set_index("date")["close"].sort_index()
        for code, g in bdf.groupby("code")
    }
    for code in benchmarks:
        if code not in bench_series:
            print(f"      -> WARNING: no close rows for benchmark "
                  f"'{code}' — all its offset columns will be NULL; "
                  f"skipping it.", flush=True)
    benchmarks = tuple(b for b in benchmarks if b in bench_series)
    if not benchmarks:
        print("      -> no benchmarks with data; skipping.", flush=True)
        return
    print(f"      -> {len(bench_rows):,} rows for "
          f"{len(benchmarks)} benchmark(s)", flush=True)

    # ---- Steps 3: per-pool stacks + emit rows ----------------------------
    print("\n[o3/4] Per-pool windowed audit stacks "
          f"(windows={WINDOWS}, stride={INTERVAL_DAYS}d)...", flush=True)

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
            bench_close = _bench_curve_on_calendar(
                bench_series[bench_code], wide.index,
            )
            bench_ma: dict[int, np.ndarray] = {
                w: pd.Series(bench_close).rolling(
                    w, min_periods=w
                ).mean().to_numpy()
                for w in WINDOWS
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
                print(f"      [{pool:5s}|{bench_code}] {t_len:,} dates x "
                      f"{n_ind} industries -> {n_pairs:,} pairs "
                      f"(overlap >= {MIN_OVERLAP}), {starts.size:,} grid "
                      f"starts, {pool_rows:,} rows", flush=True)
                continue

            overall_vals: dict[int, np.ndarray] = {}
            sub_vals: dict[int, np.ndarray] = {}
            score_vals: dict[int, np.ndarray] = {}
            for w in WINDOWS:
                stack = _window_corr_stack(ma[w], starts, w, col_ok[w])
                overall_vals[w] = stack[s_idx, ai_idx, bi_idx]
                sub_stack = _offset_corr_stack(
                    ma[w], bench_ma[w], starts, w,
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
            print(f"      [{pool:5s}|{bench_code}] {t_len:,} dates x "
                  f"{n_ind} industries -> {n_pairs:,} pairs "
                  f"(overlap >= {MIN_OVERLAP}), {starts.size:,} grid "
                  f"starts, {pool_rows:,} rows", flush=True)

    total_rows = len(out_rows)
    print(f"      -> {total_rows:,} audit rows emitted"
          f"{' (target window-end dates filtered)' if incremental else ''}",
          flush=True)
    if not out_rows:
        print("      -> no rows to write; skipping.", flush=True)
        return

    # ---- Step 4: truncate (force only) + write ---------------------------
    if force:
        print(f"\n[o4/4] Truncating {TABLE_OFFSETS} and key-batched-COPY-"
              f"inserting {total_rows:,} rows (batch key = industry_id)...",
              flush=True)
        await truncate_table_async(conn, TABLE_OFFSETS)
        n = await batched_copy_by_key_async(
            conn, TABLE_OFFSETS, out_rows, key="industry_id",
            label="offset-corr",
        )
        via = "key-batched COPY (force)"
    else:
        print(f"\n[o4/4] Upserting {total_rows:,} rows into "
              f"{TABLE_OFFSETS}...", flush=True)
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
    print(f"      -> inserted {n:,} rows via {via}", flush=True)

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
    print("\n      Summary by (benchmark, pool_size):", flush=True)
    for r in summary:
        print(f"        {r['benchmark_code']} {r['pool']:6s}: "
              f"{r['n_rows']:>8,} rows . {r['n_pairs']:>4} pairs . "
              f"{r['first_date']} -> {r['last_date']}", flush=True)

    print(f"\n  opposite correlations wall time: {time.time() - t0:.1f}s",
          flush=True)
