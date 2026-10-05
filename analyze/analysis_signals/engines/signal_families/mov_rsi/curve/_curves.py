"""mov_rsi CURVE construction — the continuous depth-response rows
(analyze.analysis_signals.engines.signal_families.mov_rsi.curve.
_curves).

THE formulation (the 2026-10 RSI depth-response study, 10 study
indices; see database/sql/analysis/analysis_signals/01_signals.sql's
signal_curves block for the SQL-side contract):

    s      = Phi^-1(1 - q)         the day's Gaussian depth score; q =
                                   its tail probability = the
                                   interpolated plotting-position
                                   percentile rank (rank + 0.5)/n of
                                   the day's RSI within the snapshot
                                   window (bottom q = p_hat, top
                                   q = 1 - p_hat)
    mu(s)  = mu_center + slope * (s - s_center)

  per (code, rsi_window, side, regime_state, snapshot):
    mu_center  the cell's sign-aligned blended mean forward change
               (the 0.25/0.65/0.10 next/5d/20d mix renormalized over
               valid legs — the threshold tier's dir_ave unit)
    s_center   the cell's OWN mean depth score (computed on demand —
               NEVER the theoretical uniform-tail constant 1.755: the
               streak-merged run-start population, regime selection
               and per-code trend shift every cell's real center)
    slope      the OLS slope POOLED per (side, regime) across the
               snapshot's codes x windows (per-cell slopes are too
               thin; the study found the gradient regime-dependent,
               window/code roughly invariant), buy side guarded >= 0
               (a negative fitted buy gradient is selection noise)
    tail_bars  the (code, window, side)'s interpolated RSI bars at
               pct 1/2/3/5/10 — the denormalized s -> RSI mapping

Study findings this encodes: the rebound is exponential-in-percentile
== linear-in-probit (NO quadratic term anywhere), the regime axis is
the first-order effect, and the sell side is adverse at every depth
(the fitted negative sell slope CARRIES that information — stored as
fitted).

Population: the q <= CURVE_TAIL_Q tail days, anchored on a valid
next-day forward change (the threshold tier's anchor rule), streak-
merged per (code, window, side, regime) — consecutive qualifying
union-calendar days collapse to ONE delay-0 trigger at the run's
first day. NO ownership calendar-year slice and NO gate: the curve is
fitted over the snapshot's whole trailing window (like the bars) and
registers on n >= CURVE_MIN_TRIGGERS only.

COMPUTE LAYOUT: the heavy frame phases (melt / rank / masks / streak-
merge / bars) are whole-frame cudf.pandas, chunked by the prepared
frame's code partitions exactly like BucketContext.buckets. The ONE
host boundary: the per-snapshot trigger + bar frames (a few thousand
rows) materialize once via engine.frame_records (the sanctioned
COPY-boundary scalar materialization); s = ndtri(1 - q), the per-cell
aggregates and the pooled (side, regime) OLS slopes run on plain host
numpy — the per-group regression core, documented here.
"""
from __future__ import annotations

import json
import logging

import numpy as np
import pandas as pd
from scipy.special import ndtri

from _common.labels import SIDE_CODE

from analyze.analysis_signals.config import (
    CURVE_MIN_TRIGGERS,
    CURVE_TAIL_PCTS,
    CURVE_TAIL_Q,
    FORWARD_HORIZONS,
    MIXED_HORIZON_WEIGHTS,
    RSI_SIDES,
    window_lower,
)
from analyze.analysis_signals.engines._base import SignalEngine
from analyze.analysis_signals.engines.signal_families.mov_rsi._buckets import (
    BucketContext,
    _finite_mask,
    _quantile_bars,
    _streak_merge,
)

logger = logging.getLogger(__name__)

_WEIGHTS = {n: MIXED_HORIZON_WEIGHTS[n] for n in FORWARD_HORIZONS}

_CURVE_CELL_COLS = [
    "code", "rsi_window", "side", "regime_state", "q", "y_dir",
]


def curve_records(
    engine: SignalEngine,
    ctx: BucketContext,
    spec,
    sec_type: str,
    rsi_windows: tuple,
) -> list[dict]:
    """One snapshot's signal_curves rows — the fitted depth-response
    line per (code, rsi_window, side, regime_state) cell over the
    snapshot's trailing window (see module docstring)."""
    df = ctx._df
    cols = [
        "code", "date", "_t", "regime", "first_date",
    ] + [f"rsi_{w}days" for w in rsi_windows] \
      + [f"next_change_{n}d" for n in FORWARD_HORIZONS]
    lower = np.datetime64(spec.lower)
    upper = np.datetime64(spec.upper)
    trig_parts: list[pd.DataFrame] = []
    bar_parts: list[pd.DataFrame] = []
    for pc in sorted(df["_pc"].drop_duplicates().to_numpy().tolist()):
        mask = (
            (df["_pc"] == pc)
            & (df["first_date"] <= upper)
            & (df["date"] >= lower)
            & (df["date"] <= upper)
        )
        win = df.loc[mask, cols]
        if win.empty:
            continue
        cells = _curve_cells(win, rsi_windows)
        if not cells.empty:
            trig_parts.append(cells)
        bars = _tail_bars(win, rsi_windows)
        if not bars.empty:
            bar_parts.append(bars)
    if not trig_parts:
        return []

    # ---- the ONE host boundary: materialize once, aggregate on host ----
    trig = pd.concat(trig_parts, ignore_index=True)
    records = engine.frame_records(trig[_CURVE_CELL_COLS])
    cells: dict[tuple, list[float]] = {}
    pooled: dict[tuple, list[tuple[float, float]]] = {}
    for rec in records:
        s = float(ndtri(1.0 - rec["q"]))
        y = float(rec["y_dir"])
        key = (
            rec["code"], int(rec["rsi_window"]),
            int(rec["side"]), int(rec["regime_state"]),
        )
        acc = cells.setdefault(key, [0, 0.0, 0.0])
        acc[0] += 1
        acc[1] += y
        acc[2] += s
        pooled.setdefault((int(rec["side"]), int(rec["regime_state"])),
                          []).append((s, y))
    slopes = {
        pk: _pooled_slope(pts) for pk, pts in pooled.items()
    }
    bars = _bar_map(engine, bar_parts)

    buy_code = SIDE_CODE["bottom"]
    out: list[dict] = []
    for (code, rsi_window, side, regime), (n, y_sum, s_sum) in \
            cells.items():
        if n < CURVE_MIN_TRIGGERS:
            continue
        slope = slopes.get((side, regime), 0.0)
        if side == buy_code and slope < 0.0:
            slope = 0.0
        out.append({
            "code": code,
            "sec_type": sec_type,
            "signal_type": engine.signal_type,
            "rsi_window": rsi_window,
            "side": side,
            "regime_state": regime,
            "start_date": window_lower(spec.stat_date),
            "end_date": spec.stat_date,
            "n": n,
            "mu_center": round(y_sum / n, 8),
            "s_center": round(s_sum / n, 6),
            "slope": round(slope, 8),
            "tail_bars": json.dumps(bars.get(
                (code, rsi_window, side), {},
            )),
        })
    return out


# ---------------------------------------------------------------------------
#  Frame phases (whole-frame cudf — the threshold tier's patterns)
# ---------------------------------------------------------------------------

def _curve_cells(
    win: pd.DataFrame, rsi_windows: tuple,
) -> pd.DataFrame:
    """One window slice's delay-0 tail triggers with their tail
    probability q and the per-row blended sign-aligned forward change
    y_dir — the study's population, engine-grade: per-day
    plotting-position rank -> q <= CURVE_TAIL_Q tail per side ->
    anchor rule -> streak-merge (delay 0)."""
    id_vars = ["code", "date", "_t", "regime_state"]
    fwd = [f"next_change_{n}d" for n in FORWARD_HORIZONS]
    win = win.rename(columns={"regime": "regime_state"})
    long = win.melt(
        id_vars=id_vars + fwd,
        value_vars=[f"rsi_{w}days" for w in rsi_windows],
        var_name="_wcol", value_name="value",
    )
    long["rsi_window"] = long["_wcol"].map(
        {f"rsi_{w}days": w for w in rsi_windows}
    )
    long = long[_finite_mask(long["value"])].drop(columns=["_wcol"])
    if long.empty:
        return long

    # per-day percentile rank within (rsi_window, code) over the
    # window — the interpolated plotting position (rank + 0.5)/n,
    # never exactly 0/1 (an exact-0 rank-0 day would give
    # s = Phi^-1(1) = inf).
    keys = ["rsi_window", "code"]
    v = long.sort_values(keys + ["value"]).reset_index(drop=True)
    g = v.groupby(keys, sort=False)
    v["_r"] = g.cumcount()
    n_tbl = g.size().rename("_n").reset_index()
    v = v.merge(n_tbl, on=keys, how="left")
    v["p_hat"] = (v["_r"].astype("float64") + 0.5) \
        / v["_n"].astype("float64")
    v = v.drop(columns=["_r", "_n"])

    # tail membership + tail probability per side (side as its int16
    # label code — codes never enter frames as strings)
    parts: list[pd.DataFrame] = []
    bot = v[v["p_hat"] <= CURVE_TAIL_Q].copy()
    if not bot.empty:
        bot["side"] = np.int16(SIDE_CODE["bottom"])
        bot["q"] = bot["p_hat"]
        parts.append(bot)
    top = v[v["p_hat"] >= 1.0 - CURVE_TAIL_Q].copy()
    if not top.empty:
        top["side"] = np.int16(SIDE_CODE["top"])
        top["q"] = 1.0 - top["p_hat"]
        parts.append(top)
    if not parts:
        return pd.DataFrame()
    cells = pd.concat(parts, ignore_index=True)

    # the threshold tier's anchor rule: a valid next-day forward
    # change (guarantees the blend's denominator never vanishes)
    cells = cells[_finite_mask(cells["next_change_1d"])]
    if cells.empty:
        return cells
    cells = _blend_per_row(cells)
    return _streak_merge(
        cells, group_cols=["code", "rsi_window", "side", "regime_state"],
    )


def _blend_per_row(cells: pd.DataFrame) -> pd.DataFrame:
    """The engine's horizon blend applied per-row: y = Σ w_n·c_n /
    Σ_{valid legs} w_n (renormalized over the row's own valid legs —
    _mixed_blend's formula at row granularity), sign-aligned by the
    side code (bottom as-is, top negated)."""
    from analyze.analysis_signals.config import SELL_SIDE_CODES

    denom = None
    numer = None
    for n, w in _WEIGHTS.items():
        col = f"next_change_{n}d"
        fin = _finite_mask(cells[col])
        term = (cells[col] * w).where(fin, 0.0)
        wf = fin.astype("float64") * w
        denom = wf if denom is None else denom + wf
        numer = term if numer is None else numer + term
    sign = pd.Series(1.0, index=cells.index, dtype="float64")
    sign = sign.mask(cells["side"].isin(SELL_SIDE_CODES), -1.0)
    cells["y_dir"] = (numer / denom) * sign
    return cells


def _tail_bars(
    win: pd.DataFrame, rsi_windows: tuple,
) -> pd.DataFrame:
    """The (rsi_window, code, side, pct) linearly-interpolated RSI
    bars at CURVE_TAIL_PCTS over the window's values — the SAME
    _quantile_bars machinery the threshold tier's bars use, at the
    curve's wider pct grid (the denormalized s -> RSI mapping)."""
    id_vars = ["code", "date", "_t", "regime"]
    long = win.melt(
        id_vars=id_vars,
        value_vars=[f"rsi_{w}days" for w in rsi_windows],
        var_name="_wcol", value_name="value",
    )
    long["rsi_window"] = long["_wcol"].map(
        {f"rsi_{w}days": w for w in rsi_windows}
    )
    long = long[_finite_mask(long["value"])].drop(columns=["_wcol"])
    if long.empty:
        return pd.DataFrame()
    q_small = pd.DataFrame({
        "rsi_window": [w for w in rsi_windows
                       for _ in CURVE_TAIL_PCTS for _ in RSI_SIDES],
        "side": [np.int16(SIDE_CODE[s]) for _ in rsi_windows
                 for _ in CURVE_TAIL_PCTS for s in RSI_SIDES],
        "pct": [p for _ in rsi_windows for p in CURVE_TAIL_PCTS
                for _ in RSI_SIDES],
        "q": [(1.0 - p / 100.0 if s == "top" else p / 100.0)
              for _ in rsi_windows for p in CURVE_TAIL_PCTS
              for s in RSI_SIDES],
    })
    groups = long[["rsi_window", "code"]].drop_duplicates()
    q_spec = groups.merge(q_small, on="rsi_window", how="inner")
    bars = _quantile_bars(
        long[["rsi_window", "code", "value"]],
        group_cols=["rsi_window", "code"],
        q_spec=q_spec,
    )
    return bars[bars["bar"].notna()]


def _bar_map(
    engine: SignalEngine, bar_parts: list[pd.DataFrame],
) -> dict[tuple, dict[str, float]]:
    """(code, rsi_window, side) -> {"1": bar, "2": bar, ...} — the
    tail_bars JSON payload, materialized through the same sanctioned
    records boundary."""
    out: dict[tuple, dict[str, float]] = {}
    if not bar_parts:
        return out
    bars = pd.concat(bar_parts, ignore_index=True)
    for rec in engine.frame_records(
        bars[["code", "rsi_window", "side", "pct", "bar"]],
    ):
        key = (rec["code"], int(rec["rsi_window"]), int(rec["side"]))
        out.setdefault(key, {})[str(int(rec["pct"]))] = round(
            float(rec["bar"]), 6,
        )
    return out


def _pooled_slope(points: list[tuple[float, float]]) -> float:
    """The OLS slope of y on [1, s] over the pooled points (host
    numpy — the per-group regression core of the module's ONE host
    boundary). 0.0 when the pooled s-spread is degenerate."""
    if len(points) < 2:
        return 0.0
    s = np.array([p[0] for p in points], dtype="float64")
    y = np.array([p[1] for p in points], dtype="float64")
    s_mu = s.mean()
    denom = float(((s - s_mu) ** 2).sum())
    if denom <= 1e-12:
        return 0.0
    return float(((s - s_mu) * (y - y.mean())).sum() / denom)
