"""Comb weight statistics — the adaptive family weights.

Builds the per-date :class:`WeightTable` from trailing completed-event
quality moments. Exact ports of the 2026-11-17 study forms (skill
``singleton-strategy-tuning`` Parts 11-12):

- mean / mean_bar / tstat / tstat_bar1: difference arrays over the union
  trading calendar (+q/+q²/+1 at the completion position+1, −… at the
  event position+window+1, prefix-summed per family) — the scale-safe
  O(events + dates) form, exact for the trailing-``window_rows`` row
  window with completion-strictly-before eligibility;
- ewma: completion-ordered stream with gap-decayed Welford updates
  (halflife in union rows), state read at the last update strictly
  before the date.

Starved families (< ``floor_events`` completed trailing events — the
repo's K=5 sample floor) inherit the mean known weight; dates where
nothing is known fall back to equal. Never writes frame columns.

cudf boundary: this module is host-numpy only (difference arrays /
bisect over the event lists) — a sanctioned per-group gather/index core;
outputs are plain dicts.
"""
from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional, Tuple

import numpy as np

from strategy.factors_and_algos._comb.config import WeightsCfg
from strategy.factors_and_algos._comb.data_definitions import (
    SignalEvent,
    WeightTable,
)
from strategy.factors_and_algos._comb.events import EventDataset

#: (mean, std, n) per family per union date; (None, None, 0) when no
#: eligible completed events sit in the window.
Moments = Tuple[Optional[float], Optional[float], int]


def moments_rows(
    events: List[SignalEvent],
    dataset: EventDataset,
    window_rows: int,
) -> Dict[str, Dict[date, Tuple[float, float, int]]]:
    """Per-family trailing moments via difference arrays on q and q².

    Eligibility at union position ``i``: completion strictly before the
    date (start = pos(completion)+1) AND event within the last
    ``window_rows`` union rows (end = pos(event)+window+1). Exact port
    of the study's ``edge_moments_rows``.
    """
    n_pos = dataset.n_positions
    by_fam: Dict[str, List[SignalEvent]] = defaultdict(list)
    for e in events:
        by_fam[e.family].append(e)

    date_pos = dataset.date_pos
    series: Dict[str, Dict[date, Tuple[float, float, int]]] = {}
    for fam, evs in by_fam.items():
        s1 = np.zeros(n_pos + 1)
        s2 = np.zeros(n_pos + 1)
        cn = np.zeros(n_pos + 1)
        for e in evs:
            q = float(e.quality)
            start = min(date_pos.get(e.completion_date, n_pos - 1) + 1, n_pos)
            end = min(date_pos.get(e.event_date, n_pos) + window_rows + 1,
                      n_pos)
            s1[start] += q
            s1[end] -= q
            s2[start] += q * q
            s2[end] -= q * q
            cn[start] += 1.0
            cn[end] -= 1.0
        c1 = np.cumsum(s1)[:n_pos]
        c2 = np.cumsum(s2)[:n_pos]
        cc = np.cumsum(cn)[:n_pos]
        ser: Dict[date, Tuple[float, float, int]] = {}
        for d, i in date_pos.items():
            if cc[i] > 0:
                mean = c1[i] / cc[i]
                var = max(c2[i] / cc[i] - mean * mean, 0.0)
                ser[d] = (float(mean), float(np.sqrt(var)), int(cc[i]))
            else:
                ser[d] = (float("nan"), float("nan"), 0)
        series[fam] = ser
    return series


@dataclass
class EwmaStream:
    """Completion-ordered gap-decayed Welford moments for one family."""

    dates: List[date]
    mus: List[float]
    stds: List[float]
    n_effs: List[float]

    def state_before(self, day: date) -> Tuple[Optional[float], Optional[float], float]:
        """(mu, std, n_eff) at the last update strictly before ``day``."""
        i = bisect_left(self.dates, day) - 1
        if i < 0:
            return None, None, 0.0
        return self.mus[i], self.stds[i], self.n_effs[i]


def ewma_moments(
    events: List[SignalEvent],
    dataset: EventDataset,
    halflife_rows: int,
) -> Dict[str, EwmaStream]:
    """Streaming mean/variance per family (exact port of the study's
    ``ewma_moments``): events ordered by completion date, weight
    ``w = 1 − 0.5^(gap/halflife)`` on the union-row gap since the last
    update (w = 0 on the first event)."""
    by_fam: Dict[str, List[SignalEvent]] = defaultdict(list)
    for e in events:
        by_fam[e.family].append(e)

    date_pos = dataset.date_pos
    streams: Dict[str, EwmaStream] = {}
    for fam, evs in by_fam.items():
        evs = sorted(evs, key=lambda e: e.completion_date)
        mu, m2, n_eff = 0.0, 0.0, 0.0
        last_pos: Optional[int] = None
        ds: List[date] = []
        mus: List[float] = []
        stds: List[float] = []
        ns: List[float] = []
        for e in evs:
            pos = date_pos.get(e.completion_date)
            if pos is None:
                continue
            gap = (pos - last_pos) if last_pos is not None else 0
            last_pos = pos
            w = 1.0 - 0.5 ** (gap / halflife_rows) if gap else 0.0
            n_eff *= (1.0 - w) if gap else 1.0
            q = float(e.quality)
            old_mu = mu
            mu = mu + w * (q - mu) if n_eff > 0 else q
            m2 = m2 + w * ((q - old_mu) * (q - mu)) if n_eff > 0 else 0.0
            n_eff += 1.0
            ds.append(e.completion_date)
            mus.append(mu)
            stds.append(float(np.sqrt(max(m2, 0.0))))
            ns.append(n_eff)
        streams[fam] = EwmaStream(ds, mus, stds, ns)
    return streams


def _fill_known(w: Dict[str, Optional[float]]) -> Dict[str, float]:
    """Normalize: starved (None) families inherit the mean known weight;
    nothing known → equal; sums to 1 (scale is advisory — the blend
    re-normalizes over the selected families)."""
    known = [v for v in w.values() if v is not None]
    if not known or sum(known) <= 0:
        return {f: 1.0 for f in w}
    mean_known = float(np.mean(known))
    filled = {f: (v if v is not None else mean_known) for f, v in w.items()}
    s = sum(filled.values())
    if s <= 0:
        return {f: 1.0 for f in filled}
    return {f: v / s for f, v in filled.items()}


def _t_stat(mean: float, std: float, n: int) -> float:
    """t = μ·√n/σ with the degenerate-variance guard of the study."""
    if std <= 1e-12:
        return float(np.sqrt(n)) if mean > 0 else 0.0
    return max(mean * np.sqrt(n) / std, 0.0)


class EdgeWeightModel:
    """Builds the per-date weight table for one weight mode.

    Config-driven mode dispatch lives here (not in the pipeline):
    equal | mean | mean_bar | tstat | tstat_bar1 | ewma.
    """

    def __init__(self, cfg: WeightsCfg, dataset: EventDataset) -> None:
        self.cfg = cfg
        self.dataset = dataset

    def build(self) -> WeightTable:
        cfg = self.cfg
        dated = self.dataset.dated_events()
        if cfg.mode == "ewma":
            return self._build_ewma(dated)
        return self._build_moments(dated)

    # -- moments-based modes (difference arrays over union rows) -------
    def _build_moments(self, dated: List[SignalEvent]) -> WeightTable:
        cfg = self.cfg
        series = moments_rows(dated, self.dataset, cfg.window_rows)
        floor = cfg.floor_events
        weights: Dict[date, Dict[str, float]] = {}
        for d in self.dataset.union_dates:
            w: Dict[str, Optional[float]] = {}
            for fam, ser in series.items():
                mean, std, n = ser.get(d, (None, None, 0))
                if mean is None or mean != mean or n < floor:
                    w[fam] = None
                    continue
                if cfg.mode == "mean":
                    w[fam] = max(mean, 0.0)
                elif cfg.mode == "mean_bar":
                    w[fam] = max(mean - cfg.bar, 0.0)
                elif cfg.mode == "tstat":
                    w[fam] = _t_stat(mean, std, n)
                elif cfg.mode == "tstat_bar1":
                    w[fam] = max(_t_stat(mean, std, n) - 1.0, 0.0)
                else:  # pragma: no cover - validated vocabulary
                    w[fam] = None
            weights[d] = _fill_known(w)
        return WeightTable(weights)

    # -- ewma mode (streaming moments) --------------------------------
    def _build_ewma(self, dated: List[SignalEvent]) -> WeightTable:
        cfg = self.cfg
        streams = ewma_moments(dated, self.dataset, cfg.halflife_rows)
        floor = float(cfg.floor_events)
        weights: Dict[date, Dict[str, float]] = {}
        for d in self.dataset.union_dates:
            w: Dict[str, Optional[float]] = {}
            for fam, stream in streams.items():
                mu, std, n_eff = stream.state_before(d)
                if mu is None or n_eff < floor:
                    w[fam] = None
                    continue
                w[fam] = (max(mu / std, 0.0) if std and std > 1e-12
                          else (1.0 if mu > 0 else 0.0))
            weights[d] = _fill_known(w)
        return WeightTable(weights)


__all__ = ["EdgeWeightModel", "EwmaStream", "moments_rows", "ewma_moments"]
