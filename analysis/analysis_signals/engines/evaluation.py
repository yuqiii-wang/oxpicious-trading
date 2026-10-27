"""The signal rules tier's shared EVALUATION CONTRACT
(analysis.analysis_signals.engines.evaluation) — the primitives every
family's cell loop runs on, split out of rule_base (the
architecture-consolidation refactor, 2026-10-15: the ABC stays the
ABSTRACTION at engines/ top; this module and boost.py hold the
implementation machinery):

  CellGate         the evidence bar + the confidence scales — the
                   equity asymmetry IS the design (sell bar 0 = exit
                   timing; buy 0.25% = the measured edge — the
                   2026-10-16 moderate loosening from 0.75%). The
                   sample floor stopped BLOCKING 2026-10-19
                   (user-directed): a cell under its floor trades at
                   the sample_scale fraction of the confidence instead
                   of replaying as wait (the starvation logic retired)
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
    """The evidence bar + the confidence scales — the shared evaluation
    contract's decision object (constructed from the config bars; the
    equity asymmetry is deliberate, see the biz skill): buy needs a
    measured edge (>= 0.25% blended dir_ave — the 2026-10-16 moderate
    loosening from 0.75%); sell is EXIT TIMING — any non-negative edge
    (sell evidence is structurally thinner under positive drift). The
    sample floor is NOT a gate any more (2026-10-19, user-directed):
    ``passes`` judges the dir_ave bar only, and a cell holding fewer
    episodes than its floor trades at the ``sample_scale`` fraction of
    the confidence — thin evidence discounts the claim instead of
    silencing it (the starvation wait logic retired)."""

    def __init__(self, gate_min: dict[str, float] | None = None,
                 floors: dict[str, int] | None = None) -> None:
        self.gate_min = gate_min if gate_min is not None \
            else dict(cfg.GATE_DIR_AVE_MIN)
        self.floors = floors if floors is not None \
            else dict(cfg.SAMPLE_FLOOR)

    def passes(self, action: str, n: int, dir_ave: float) -> bool:
        """The evidence bar: the side's dir_ave bar only. ``n`` rides
        the signature for the study scripts' call surface — it shapes
        ``sample_scale`` now, never the verdict."""
        return dir_ave >= self.gate_min[action]

    def starved(self, action: str, n: int) -> bool:
        """True when the cell holds episodes but sits under its sample
        floor — it trades (the bar passed) at a reduced confidence."""
        return n < self.floors.get(action, 0)

    def sample_scale(self, action: str, n: int) -> float:
        """The starvation confidence scale: the cell's sample
        completeness min(n / floor, 1) — n = floor−1 buys at 2/3
        confidence, n = 1 at 1/3 (sell floor 2: n = 1 at 1/2); at or
        past the floor the scale is 1 and the confidence is the plain
        depth-scaled value. Linear in n so the UI's confidence bp
        reads directly as evidence weight."""
        floor = self.floors.get(action, 0)
        if floor <= 0:
            return 1.0
        return float(min(n / floor, 1.0))

    def confidence_pct(self, base_pct: float, depth_norm: float,
                       factor: float,
                       sample_scale: float = 1.0) -> float:
        """Depth-scaled confidence in PERCENT: the cell's measured
        on-bar base (its dir_ave pct) amplified linearly over the
        normalized breach depth (capped at 2) and multiplied by the
        starvation ``sample_scale`` (min(n/floor, 1) — 1.0 for
        fully-sampled cells), clamped to [0, CONFIDENCE_CAP_PCT]. The
        record writers convert to the INTEGER basis-point scale (×100)
        for the DB columns; the live tier's rules_confidence_bp is the
        identical formula."""
        scaled = base_pct * (1.0 + factor * min(max(depth_norm, 0.0), 2.0)) \
            * sample_scale
        return float(min(max(scaled, 0.0), cfg.CONFIDENCE_CAP_PCT))


def cell_stats(trig: pd.DataFrame, action: str,
               keys: list[str], blend_col: str = "_blend") -> pd.DataFrame:
    """Per measured-cell measurement over the trigger days: n episodes,
    the sign-aligned dir_ave (favorable direction: +blend for buy,
    -blend for sell), its std (the dispersion the t-stat needs —
    2026-10-16) and the hit rate. ``blend_col`` selects the response
    axis (the multi-response extension — run_cells passes the named
    FORWARD_PRICE_COLS column's blend; default the primary ``_blend``
    every pre-multi-response caller reads)."""
    fav = -trig[blend_col] if action == "sell" else trig[blend_col]
    t = trig.assign(
        _fav=fav.astype("float64"),
        _win=(fav > 0).astype("float64"),
    )
    return t.groupby(keys).agg(
        n=("_fav", "size"), dir_ave=("_fav", "mean"),
        dir_std=("_fav", "std"), hit=("_win", "mean"),
    ).reset_index()


def pooled_evidence(facts: list[dict],
                    dir_ave_min: float | None = None) -> dict | None:
    """The (sub_type, action) POOLED evidence tier — the shared
    evaluation contract's statistical layer (2026-10-16). ``facts`` =
    one dict per MEASURABLE trigger of the side's WINNING cells across
    codes: {year, fav, f3, f5, f10, f20} with fav and the fwd legs
    SIGN-ALIGNED in PERCENT (+ for buy, - for sell — cell_stats's
    convention). ``dir_ave_min`` optionally overrides the pooled mean
    bar for this call (the family-scoped loosening — None = the global
    POOLED_DIR_AVE_MIN); the t bar and the expanding-year majority
    stay the shared contract. Returns the pooled block stamped onto
    the winning rules rows' performance snapshots
    (``_performance.pooled``, the history_signal_performance row):

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
      passed                                the BUY verdict: the pooled
                                            population TRADES — n >=
                                            POOLED_MIN_N (full
                                            confidence) or n >=
                                            POOLED_TRADE_FLOOR at the
                                            starvation conf_scale (the
                                            2026-10-19 pooled retreat),
                                            in both cases with dir_ave
                                            >= POOLED_DIR_AVE_MIN AND
                                            t >= POOLED_T_STAT_MIN AND
                                            a majority of measurable
                                            years (n >=
                                            POOLED_YEAR_MIN_N)
                                            positive — no measurable
                                            years skips the last leg.
      starved / conf_scale                  the starvation ride-along:
                                            True + min(n /
                                            POOLED_MIN_N, 1) when the
                                            side trades under the full
                                            n floor; False + 1.0 at or
                                            past it (the caller folds
                                            conf_scale into the cell's
                                            confidence sample_scale).

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
    # the statistics legs stay verdict legs (mean bar, t bar, the
    # measurable-year sign majority); n is the CONFIDENCE reference
    # (the 2026-10-19 pooled starvation retreat): n >= POOLED_MIN_N
    # trades at full confidence, POOLED_TRADE_FLOOR <= n < it trades
    # at min(n / POOLED_MIN_N, 1), below the trade floor no claim at all
    stat_ok = bool(
        mean_pct >= (cfg.POOLED_DIR_AVE_MIN if dir_ave_min is None
                     else dir_ave_min) * 100.0
        and t_stat is not None
        and t_stat >= cfg.POOLED_T_STAT_MIN
        and (not meas or pos * 2 > len(meas))
    )
    full = n >= cfg.POOLED_MIN_N and stat_ok
    starved = bool(stat_ok and cfg.POOLED_TRADE_FLOOR <= n
                   < cfg.POOLED_MIN_N)
    conf_scale = float(min(n / cfg.POOLED_MIN_N, 1.0)) if starved else 1.0
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
        "trade_floor": cfg.POOLED_TRADE_FLOOR,
        "starved": starved,
        "conf_scale": round(conf_scale, 4),
        "passed": bool(full or starved),
    }


__all__ = ["EpisodeDetector", "CellGate", "cell_stats", "pooled_evidence"]
