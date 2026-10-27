"""hedge_pairs pair lookup grids + drawdown lenses — the per-pair host
arrays the event-member builders and the benefit audit read.

BOUNDARY (the sanctioned host core): ``pair_grids`` unwraps each
lookup column ONCE via ``host_array`` into per-code host arrays (one
per column), and everything downstream (the event loops'
``searchsorted`` date lookups, the trough lenses, the drawdown gauges)
is plain host numpy over those slices — python loops only over the
structural event list. No mid-flow hybrids: the grids are never fed
back into a DataFrame ctor."""
from __future__ import annotations

import numpy as np

from _common.df_utils import host_array

from analysis.analysis_signals import config as cfg
from analysis.analysis_signals.fetch.inputs._hedge_pairs import bar_suffix


def pair_grids(frame) -> dict[str, dict]:
    """Per pair code the host lookup grid: sorted dates + the
    event-resolution columns as plain host arrays (ONE unwrap per
    column; the event loop's date lookups are host searchsorted on
    the sorted array). Carries the dd gauges, the trough lens, and
    the per-role RSI / bars / breach bools the witness records
    read."""
    roles = ("a", "b", "m")
    cols = ["date", "trough20", "dd_value", "dd_a", "dd_b"]
    for w in cfg.HP_LEG_RSI_WINDOWS:
        suf = bar_suffix(w)
        cols += [f"rsi{suf}_{r}" for r in roles]
        cols += [f"{side}{pct}{suf}_{r}" for r in roles
                 for side in ("bb", "bs")
                 for pct in sorted(cfg.HP_LEG_TAIL_PCTS)]
        cols += [f"bar_{side}{pct}{suf}_{r}" for r in roles
                 for side in ("b", "s")
                 for pct in sorted(cfg.HP_LEG_TAIL_PCTS)]
    grids: dict[str, dict] = {}
    parts = {c: host_array(frame[c]) for c in cols}
    codes = host_array(frame["code"]).tolist()
    dates = parts["date"].astype("datetime64[D]")
    # frame sorted by (code, date) — each code's block contiguous
    bounds: dict[str, tuple[int, int]] = {}
    for i, c in enumerate(codes):
        b = bounds.get(c)
        if b is None:
            bounds[c] = (i, i + 1)
        else:
            bounds[c] = (b[0], i + 1)
    for c, (s, e) in bounds.items():
        grids[c] = {col: parts[col][s:e] for col in cols}
        grids[c]["date_d"] = dates[s:e]
    return grids


def dd_lens(grid: dict, dates: list) -> dict | None:
    """One cell's coordinated-event drawdown lens: the triggers'
    forward worst-trough within 20 pair-days (mean + p10) vs the
    response path's all-days baseline."""
    trough = grid["trough20"]
    if len(trough) == 0:
        return None
    gd = np.asarray(dates, dtype="datetime64[D]")
    pos = np.searchsorted(grid["date_d"], gd, side="right") - 1
    vals = trough[pos]
    vals = vals[np.isfinite(vals)]
    baseline = trough[np.isfinite(trough)]
    if len(vals) == 0 or len(baseline) == 0:
        return None
    return {
        "min20_pct": round(float(vals.mean()) * 100.0, 4),
        "p10_pct": round(float(np.percentile(vals, 10)) * 100.0, 4),
        "baseline_pct": round(float(baseline.mean()) * 100.0, 4),
        "n": int(len(vals)),
    }


def combine_lens(lenses: list[dict | None]) -> dict | None:
    """The portfolio-level lens: the per-cell lenses n-weighted
    (mean/p10 troughs), the shallowest baseline, the total n."""
    live = [l for l in lenses if l]
    if not live:
        return None
    n = sum(l["n"] for l in live)
    if n <= 0:
        return None
    return {
        "min20_pct": round(
            sum(l["min20_pct"] * l["n"] for l in live) / n, 4),
        "p10_pct": round(
            sum(l["p10_pct"] * l["n"] for l in live) / n, 4),
        "baseline_pct": min(l["baseline_pct"] for l in live),
        "n": n,
    }
