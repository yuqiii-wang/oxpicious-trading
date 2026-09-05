"""opp_pair_state bucket monthly aggregation (analysis_forecasts) —
industry opposite-pair trend forecasts.

The PAIR buckets of analysis_composites.industry_corr_benchmark_offsets
(see database/sql/analysis/analysis_forecasts/07_opp_pair_state.sql):
by industry pair, when ONE side's benchmark-offset MA trend is dropping
the forecast RESULT is the future trend of the OTHER side industry.

All trend legs live on the OFFSET space the composites analysis defines.
With MA_W = the trailing-W-row rolling mean of the industry composite
mean_close (pool 'all') and MA_M = the benchmark's (000300) MA_W, the
W-day offset trend change of industry X ending at t — k rebased at the
lookback start, exactly the composites' window math (the adjusted trend
adj = MA_X − k·MA_M is identically 0 at the rebasing point) — is

    (MA_X[t] − k·MA_M[t]) − (MA_X[t−W] − k·MA_M[t−W]),
    k = MA_X[t−W] / MA_M[t−W]

which, normalized by the industry's own MA level, reduces to the
RELATIVE MA RETURN

    rel_X(t) = MA_X[t]/MA_X[t−W] − MA_M[t]/MA_M[t−W].

TRIGGER ("industry A is dropping"): rel_A(t) < 0 — A's W-day MA-trend
return is below the benchmark's (an industry whose trend grows while
the benchmark grows MORE is DROPPING after the offset). FORWARD TARGET:
the other side industry B's normalized offset change over [t, t+n],

    fwd_B(t,n) = MA_B[t+n]/MA_B[t] − MA_M[t+n]/MA_M[t].

Per (stat_month, W) the trigger cells are aggregated with the shared
sparse horizon machinery against the TARGET industry's adaptive
reversal bar (k_n·σ of B's window forward offset changes). side =
'bottom' reverses on change > +thr, so forecast_results.reverse_prob =
P(B rises beyond the bar) — the pair forecast's CONFIRMATION
probability, not a reversal. One bucket per directional pair; no hype
split, no cooldown (state buckets — industries have no hype source).

Yields (stat_month, rows) so __main__ can split each row into the
opp_pair_state motivation dicts and the forecast_results result dicts
and write month-major.
"""
from __future__ import annotations

import json
from datetime import date
from typing import Iterator

import numpy as np
import pandas as pd

from analyze.analysis_forecasts.config import (
    FORWARD_HORIZONS,
    OPP_PAIR_SIDE,
    OPP_PAIR_TREND_WINDOWS,
)
from analyze.analysis_forecasts.wide import (
    MonthWindow,
    aggregate_horizons_sparse,
    build_grid,
    build_result_rows,
    reverse_thresholds,
    round6,
    scatter_column,
    window_sigmas,
)


def _shift_rows(ma: np.ndarray, rows: int) -> np.ndarray:
    """Row-shift with NaN fill (positive = from the past)."""
    out = np.full_like(ma, np.nan)
    if rows > 0:
        out[rows:] = ma[:-rows]
    elif rows < 0:
        out[:rows] = ma[-rows:]
    else:
        out[:] = ma
    return out


def _bench_on_grid(bench: pd.Series, grid_ord: np.ndarray) -> np.ndarray:
    """Benchmark close reindexed onto the union day-ordinal grid (NaN on
    off-calendar dates — those days simply never trigger / never have a
    valid forward change)."""
    n = grid_ord.size
    if bench.empty:
        return np.full(n, np.nan)
    b_ord = (
        bench.index.to_numpy().astype("datetime64[D]").astype(np.int64)
    )
    pos = np.searchsorted(grid_ord, b_ord)
    ok = pos < n
    ok[ok] = grid_ord[pos[ok]] == b_ord[ok]
    out = np.full(n, np.nan)
    out[pos[ok]] = bench.to_numpy(dtype=np.float64)[ok]
    return out


def build_opp_pair_matrices(
    df_ind: pd.DataFrame,
    bench: pd.Series,
    windows: tuple[int, ...] = OPP_PAIR_TREND_WINDOWS,
) -> tuple[np.ndarray, list[str], np.ndarray, np.ndarray, dict]:
    """Wide (T, C) industry offset-trend matrices for the opp_pair family.

    Args:
        df_ind: long industry frame (code = industry_id, date, close),
              sorted by (code, date) — fetch_industry_closes output.
        bench: the offset benchmark's close Series (fetch_benchmark_closes).
        windows: trend windows W (trading-day rows) of the MA curves.

    Returns:
        (grid_ord, industries, didx, cidx, mats) with
        mats[W] = {"trig": (T, C) normalized offset trend change
                   rel_X(t) (NaN where undefined — never a trigger),
                   "NC0": {n: (T, C) target forward offset change, 0.0
                           on invalid days},
                   "FIN": {n: (T, C) validity bool}}.
    """
    grid_ord, industries, didx, cidx = build_grid(df_ind)
    shape = (len(grid_ord), len(industries))
    close = scatter_column(df_ind, "close", shape, didx, cidx)
    b_close = _bench_on_grid(bench, grid_ord)

    mats: dict[int, dict] = {}
    for w in windows:
        ma = pd.DataFrame(close).rolling(w, min_periods=w).mean().to_numpy()
        b_ma = pd.Series(b_close).rolling(w, min_periods=w).mean().to_numpy()
        with np.errstate(divide="ignore", invalid="ignore"):
            rel = (
                ma / _shift_rows(ma, w)
                - (b_ma / _shift_rows(b_ma, w))[:, None]
            )
            trig = np.where(np.isfinite(rel), rel, np.nan)
            nc0: dict[int, np.ndarray] = {}
            fin: dict[int, np.ndarray] = {}
            for n in FORWARD_HORIZONS:
                fwd = (
                    ma / _shift_rows(ma, -n)
                    - (b_ma / _shift_rows(b_ma, -n))[:, None]
                )
                ok = np.isfinite(fwd)
                nc0[n] = np.where(ok, fwd, 0.0)
                fin[n] = ok
        mats[w] = {"trig": trig, "NC0": nc0, "FIN": fin}
    return grid_ord, industries, didx, cidx, mats


def _pair_axis(
    pairs: pd.DataFrame, industries: list[str],
) -> tuple[np.ndarray, np.ndarray, list[str], list[str],
           np.ndarray, np.ndarray, np.ndarray]:
    """Pairs → aligned axis arrays over the industry column space.

    Returns (a_idx, b_idx, a_ids, b_ids, scores, corrs, score_dates) for
    the pairs whose BOTH endpoints are in ``industries`` (skipped
    otherwise), pair-order preserved.
    """
    pos = {c: i for i, c in enumerate(industries)}
    known = set(pos)
    p = pairs[
        pairs["industry_id"].isin(known)
        & pairs["pair_industry_id"].isin(known)
    ]
    a_idx = p["industry_id"].map(pos).to_numpy(dtype=np.int64)
    b_idx = p["pair_industry_id"].map(pos).to_numpy(dtype=np.int64)
    scores = pd.to_numeric(p["pair_score"], errors="coerce").to_numpy(
        dtype=np.float64)
    corrs = pd.to_numeric(p["pair_corr"], errors="coerce").to_numpy(
        dtype=np.float64)
    return (
        a_idx, b_idx,
        p["industry_id"].tolist(), p["pair_industry_id"].tolist(),
        scores, corrs, p["score_date"].to_numpy(),
    )


def _iso(d) -> str | None:
    """date-like → ISO string (asyncpg DATE columns arrive as
    datetime.date; NULL → None)."""
    return d.isoformat() if d is not None and hasattr(d, "isoformat") \
        else (str(d) if d is not None else None)


def compute_opp_pair_results(
    mats: dict[int, dict],
    windows: list[MonthWindow],
    industries: list[str],
    sec_type: str,
    first_ord: np.ndarray,
    pairs: pd.DataFrame,
    *,
    benchmark_code: str,
    pool_size: str,
) -> Iterator[tuple[date, list[dict]]]:
    """Yield (stat_month, bucket rows) per stat month — opp_pair family.

    Args:
        mats: build_opp_pair_matrices output.
        windows: resolved MonthWindow list for the target months.
        industries: sorted industry_id list (matrix column order).
        sec_type: emitted into every row (the opp_pair constant).
        first_ord: (C,) per-industry first composite-close date as
              ABSOLUTE epoch-day ordinals — a pair's month window is
              live only when BOTH endpoints' history strictly precedes
              the window start (DATE-space full-window gate).
        pairs: fetch_opp_pair_pairs output (unordered pair set + latest
              offsets-table score context).
        benchmark_code / pool_size: recorded build parameters.
    """
    a_idx, b_idx, a_ids, b_ids, scores, corrs, score_dates = _pair_axis(
        pairs, industries)
    P = a_idx.size
    if P == 0:
        return

    for mw in windows:
        lo, hi = mw.lo, mw.hi
        if lo >= hi:
            continue
        live = first_ord < mw.lo_ord
        pair_live = live[a_idx] & live[b_idx]
        if not pair_live.any():
            continue

        rows: list[dict] = []
        for w in sorted(mats):
            M = mats[w]
            TR = M["trig"][lo:hi]
            NC0s = {n: M["NC0"][n][lo:hi] for n in FORWARD_HORIZONS}
            FINs = {n: M["FIN"][n][lo:hi] for n in FORWARD_HORIZONS}
            # Per-(industry, horizon) adaptive reversal bar for this
            # window — the TARGET industry's own σ, gathered per pair.
            sigma, cnts = window_sigmas(NC0s, FINs)
            thr_n = reverse_thresholds(sigma, cnts)
            thr_pair = {n: thr_n[n][b_idx] for n in FORWARD_HORIZONS}

            with np.errstate(invalid="ignore"):
                tp = (TR[:, a_idx] < 0) & pair_live[None, :]
            st, pc = np.nonzero(tp)
            if st.size == 0:
                continue
            # np.nonzero is row-major → pc non-decreasing (the group-
            # ascending order aggregate_horizons_sparse requires).
            trig_vals = TR[st, a_idx[pc]]

            # Pair-gathered target matrices: column b of each pair — the
            # aggregates run with ONE "code" axis (all pairs are
            # configs) via flat = pair position.
            nc0p = {n: NC0s[n][:, b_idx] for n in FORWARD_HORIZONS}
            finp = {n: FINs[n][:, b_idx] for n in FORWARD_HORIZONS}
            agg = aggregate_horizons_sparse(
                st, pc, pc, 1, P, OPP_PAIR_SIDE, nc0p, finp, thr_pair,
            )
            # Reshape the (1, P) bundles to (P, 1): pairs become the
            # "code" axis (indexed by ii), the single config by kk — so
            # build_result_rows gathers/thr-indexes per pair unchanged.
            agg = {
                n: tuple(None if m is None else m.reshape(P, 1)
                         for m in bundle)
                for n, bundle in agg.items()
            }

            trig_cnt = np.bincount(pc, minlength=P)
            ii = np.nonzero(trig_cnt > 0)[0]
            if ii.size == 0:
                continue
            kk = np.zeros(ii.size, dtype=np.int64)
            mean_rel = np.divide(
                np.bincount(pc, weights=trig_vals, minlength=P),
                trig_cnt, out=np.full(P, np.nan), where=trig_cnt > 0,
            )

            base: list[dict] = []
            for i in ii.tolist():
                base.append({
                    "sec_type": sec_type,
                    "industry_id": a_ids[i],
                    "pair_industry_id": b_ids[i],
                    "stat_month": mw.stat_month,
                    "trend_window": w,
                    "side": OPP_PAIR_SIDE,
                    "benchmark_code": benchmark_code,
                    "pool_size": pool_size,
                    # config JSONB — asyncpg COPY needs a JSON text
                    # string (compute_std / compute_px_vol precedent).
                    "config": json.dumps({
                        "mean_rel": round6(float(mean_rel[i])),
                        "pair_score": round6(float(scores[i]))
                        if np.isfinite(scores[i]) else None,
                        "pair_corr": round6(float(corrs[i]))
                        if np.isfinite(corrs[i]) else None,
                        "score_date": _iso(score_dates[i]),
                    }),
                })
            rows.extend(build_result_rows(agg, kk, ii, base, thr_pair))

        if rows:
            yield mw.stat_month, rows
