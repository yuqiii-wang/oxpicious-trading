"""The signal rules tier's shared EVALUATION CONTRACT
(analysis.analysis_signals.engines.evaluation) — the primitives every
family's cell loop runs on, split out of rule_base (the
architecture-consolidation refactor, 2026-10-15: the ABC stays the
ABSTRACTION at engines/ top; this module and boost.py hold the
implementation machinery):

  CellGate         the plain gate + the depth-scaled confidence — the
                   equity asymmetry IS the design (sell gate 0 @ floor
                   2 = exit timing; buy 0.25% @ floor 3 = the measured
                   edge — the 2026-10-16 moderate loosening from
                   0.75% @ 4 / 0 @ 3)
  cell_stats       the per-cell measurement over trigger days (n,
                   sign-aligned dir_ave/std, hit)
  pooled_evidence  the TWO-TIER gate's evidence tier (2026-10-16):
                   the per-cell floors cannot reach statistical
                   significance (blend sigma ~2.6% -> n=3 SE ~1.5%,
                   the 0.25% bar is ~0.16 SE), so the BUY edge claim
                   lives at the (sub_type, action) POOLED population —
                   t-stat + expanding-year sign consistency, the
                   graduation studies' discipline formalized

The episode machinery (EpisodeDetector — FIRST days of breach runs
with the K-absorb, optionally trend-aware) moved 2026-10-15 into the
consolidated analysis.trends component (analysis.trends.episodes) —
rule_base imports it from there. cell_stats is a whole-frame grouped
op (the cudf layout); pooled_evidence is a HOST-numpy boundary (the
facts are host dicts past run_cells' row-materialization boundary
already — the sanctioned boundary-core pattern). rule_base.run_cells
and any study script consume them through these objects.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from analysis.analysis_signals import config as cfg


class CellGate:
    """The plain gate + the depth-scaled confidence — the shared
    evaluation contract's decision object (constructed from the config
    bars; the equity asymmetry is deliberate, see the biz skill):
    buy needs a measured edge (>= 0.25% blended dir_ave at floor 3
    episodes — the 2026-10-16 moderate loosening from 0.75% @ 4);
    sell is EXIT TIMING — any non-negative edge at floor 2 (sell
    evidence is structurally thinner under positive drift)."""

    def __init__(self, gate_min: dict[str, float] | None = None,
                 floors: dict[str, int] | None = None) -> None:
        self.gate_min = gate_min if gate_min is not None \
            else dict(cfg.GATE_DIR_AVE_MIN)
        self.floors = floors if floors is not None \
            else dict(cfg.SAMPLE_FLOOR)

    def passes(self, action: str, n: int, dir_ave: float) -> bool:
        return (n >= self.floors[action]
                and dir_ave >= self.gate_min[action])

    def confidence_pct(self, base_pct: float, depth_norm: float,
                       factor: float) -> float:
        """Depth-scaled confidence in PERCENT: the cell's measured
        on-bar base (its dir_ave pct) amplified linearly over the
        normalized breach depth (capped at 2), clamped to
        [0, CONFIDENCE_CAP_PCT]. The record writers convert to the
        INTEGER basis-point scale (×100) for the DB columns."""
        scaled = base_pct * (1.0 + factor * min(max(depth_norm, 0.0), 2.0))
        return float(min(max(scaled, 0.0), cfg.CONFIDENCE_CAP_PCT))


def cell_stats(trig: pd.DataFrame, action: str,
               keys: list[str]) -> pd.DataFrame:
    """Per measured-cell measurement over the trigger days: n episodes,
    the sign-aligned dir_ave (favorable direction: +blend for buy,
    -blend for sell), its std (the dispersion the t-stat needs —
    2026-10-16) and the hit rate."""
    fav = -trig["_blend"] if action == "sell" else trig["_blend"]
    t = trig.assign(
        _fav=fav.astype("float64"),
        _win=(fav > 0).astype("float64"),
    )
    return t.groupby(keys).agg(
        n=("_fav", "size"), dir_ave=("_fav", "mean"),
        dir_std=("_fav", "std"), hit=("_win", "mean"),
    ).reset_index()


def pooled_evidence(facts: list[dict]) -> dict | None:
    """The (sub_type, action) POOLED evidence tier — the shared
    evaluation contract's statistical layer (2026-10-16). ``facts`` =
    one dict per MEASURABLE trigger of the side's WINNING cells across
    codes: {year, fav, f3, f5, f10, f20} with fav and the fwd legs
    SIGN-ALIGNED in PERCENT (+ for buy, - for sell — cell_stats's
    convention). Returns the pooled block stamped onto the winning
    rules rows (``rule_params.pooled``):

      n / dir_ave_pct / t_stat / hit_rate   the pooled measurement
      decay_pct                             the per-horizon mean
                                            response (the decay curve
                                            that justifies the blend
                                            weights)
      years.legs                            per year: n, that year's
                                            dir_ave_pct, and the
                                            EXPANDING train_pct (the
                                            mean over strictly-earlier
                                            years — the measured-vs-
                                            realized view, the boost
                                            G2's lift to the plain
                                            tier)
      passed                                the BUY verdict: pooled
                                            n >= POOLED_MIN_N AND
                                            dir_ave >= POOLED_DIR_AVE_MIN
                                            AND t >= POOLED_T_STAT_MIN
                                            AND a majority of
                                            measurable years (n >=
                                            POOLED_YEAR_MIN_N)
                                            positive — no measurable
                                            years skips the last leg.

HOST-numpy boundary: the facts are host dicts past run_cells'
row-materialization boundary; everything below is plain host numpy
(zero cudf dispatch). All values are cast to python scalars for the
JSONB ride."""
    if not facts:
        return None
    fav = np.array([f["fav"] for f in facts], dtype="float64")
    n = int(fav.size)
    mean_pct = float(fav.mean())
    std = float(fav.std(ddof=1)) if n >= 2 else float("nan")
    t_stat = (mean_pct / (std / math.sqrt(n))
              if n >= 2 and math.isfinite(std) and std > 1e-12
              else None)
    by_year: dict[int, list[float]] = {}
    for f in facts:
        by_year.setdefault(int(f["year"]), []).append(float(f["fav"]))
    legs: list[dict] = []
    for yr in sorted(by_year):
        ys = np.array(by_year[yr], dtype="float64")
        train = np.array([float(f["fav"]) for f in facts
                          if int(f["year"]) < yr], dtype="float64")
        legs.append({
            "year": yr, "n": int(ys.size),
            "dir_ave_pct": round(float(ys.mean()), 4),
            "train_pct": round(float(train.mean()), 4)
            if train.size >= cfg.POOLED_YEAR_MIN_N else None,
            "measurable": bool(ys.size >= cfg.POOLED_YEAR_MIN_N),
        })
    meas = [l for l in legs if l["measurable"]]
    pos = sum(1 for l in meas if l["dir_ave_pct"] > 0.0)
    passed = bool(
        n >= cfg.POOLED_MIN_N
        and mean_pct >= cfg.POOLED_DIR_AVE_MIN * 100.0
        and t_stat is not None
        and t_stat >= cfg.POOLED_T_STAT_MIN
        and (not meas or pos * 2 > len(meas))
    )
    return {
        "n": n,
        "dir_ave_pct": round(mean_pct, 4),
        "t_stat": round(t_stat, 4) if t_stat is not None else None,
        "hit_rate": round(float((fav > 0.0).mean()), 4),
        "decay_pct": {
            f"{h}d": round(float(np.mean(
                [f[f"f{h}"] for f in facts])), 4)
            for h in cfg.FORWARD_BLEND_DAYS if h > 0
        },
        "years": {
            "min_n": cfg.POOLED_YEAR_MIN_N,
            "legs": legs,
            "positive": f"{pos}/{len(meas)}",
        },
        "bar_pct": round(cfg.POOLED_DIR_AVE_MIN * 100.0, 4),
        "t_min": cfg.POOLED_T_STAT_MIN,
        "floor": cfg.POOLED_MIN_N,
        "passed": passed,
    }


__all__ = ["EpisodeDetector", "CellGate", "cell_stats", "pooled_evidence"]
