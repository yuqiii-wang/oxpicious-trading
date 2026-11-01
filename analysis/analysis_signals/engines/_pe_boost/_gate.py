"""The PE-boost variant gate (analysis.analysis_signals.engines.
_pe_boost._gate, 2026-10-27 — the class OOP restructure of the flat
engines/variants.py): the shared is_pe_boosted axis the family
engines import (dormant — see below).

DORMANT since the 2026-11-01 variant-axes UNLINK (user-directed
"keep the pe and ohlcv boosted logic in py, however, offload to
unlink from the computation ... delete is pe and ohlcv boosted col
as from pk"): the is_pe_boosted column left the DB PKs and no engine
imports this package — the logic is kept for a future re-link.

The gate AND-gates a family's plain candidate masks by the code's own
trailing PE-percentile condition — today's PE among the code's own
PRIOR PE readings (strictly before t, min PE_MIN_PERIODS — no
look-ahead; stats.{sec}_valuation.pe joined at the family fetch, NULL
where the store has no row — every non-index sec_type today, whose PE
variants then legitimately starve). The gate vocabulary is the
rebound study's measured TP1/TP2 cross tables, shared since
2026-10-26 (user-directed "create shared pe and ohlcv logic"):

  extremes   pe_pct <= cheap_below or >= rich_above — the
             capitulation barbell (cheap strongest, rich
             delayed-but-real, middle dead); the capitulation-class
             BUYs (rebound_capit, rf_break's floor_deep /
             floor_bounce);
  not_cheap  pe_pct >= the floor — the outside-cheap claim class
             (stalled rebounds recover at cheap valuations; reclaims
             never fire there in the study); rebound's fail/reclaim.

COMPUTE LAYOUT: the per-code trailing rank is the SANCTIONED HOST
BOUNDARY (a trailing rank has no vectorized form): host_array-wrapped
per-code slices through a sorted-insertion rank — O(n^2) on ~1.6k
rows per code, trivial; NaN before the warmup or where the valuation
store has no row. attach() writes the shared ``_pe_pct`` column ONCE
(every PeBoostGate instance of a run reads the same column — the
per-kind gates only derive their own boolean conditions from it).
"""
from __future__ import annotations

from typing import ClassVar

import numpy as np
import pandas as pd

from _common.df_utils import host_array

from analysis.analysis_signals import config as cfg
from analysis.analysis_signals.engines._variant_base import VariantGate

__all__ = ["PE_PCT_COL", "PeBoostGate", "trailing_pct_rank"]

# the shared attached-rank column (internal — never emitted; every
# PeBoostGate.attach of a run is idempotent over it)
PE_PCT_COL = "_pe_pct"


def trailing_pct_rank(vals: np.ndarray, min_periods: int) -> np.ndarray:
    """Per-day percentile of vals[t] within the PRIOR values (strictly
    before t; 0..1). HOST-NUMPY BOUNDARY (see the module docstring):
    sorted-insertion buffer; NaN before min_periods observations or
    where the value itself is NaN."""
    n = len(vals)
    out = np.full(n, np.nan)
    hist = np.empty(n, dtype=np.float64)
    nh = 0
    for t in range(n):
        v = vals[t]
        if not np.isfinite(v):
            continue
        if nh >= min_periods:
            out[t] = float(np.searchsorted(hist[:nh], v,
                                           side="left")) / nh
        pos = int(np.searchsorted(hist[:nh], v, side="right"))
        hist[pos + 1:nh + 1] = hist[pos:nh]
        hist[pos] = v
        nh += 1
    return out


class PeBoostGate(VariantGate):
    """The PE-conditioning variant axis (is_pe_boosted): the plain
    candidates' masks AND-gated by the code's own trailing
    PE-percentile condition per the shared gate vocabulary.

    ``rule`` picks the gate ("extremes" the capitulation barbell,
    "not_cheap" the outside-cheap claim class); ``min_periods``
    overrides the trailing rank's warmup (default PE_MIN_PERIODS)."""

    bar_key: ClassVar[str] = "pe_gate"
    flag_kwarg: ClassVar[str] = "pe_boosted"
    suffix: ClassVar[str] = "_pe"

    def __init__(self, rule: str = "extremes",
                 min_periods: int | None = None) -> None:
        super().__init__()
        if rule not in ("extremes", "not_cheap"):
            raise ValueError(f"unknown PE gate rule: {rule!r}")
        self.rule = rule
        self.min_periods = cfg.PE_MIN_PERIODS if min_periods is None \
            else min_periods

    def available(self, frame: pd.DataFrame) -> bool:
        """Whether the valuation store contributed ANY PE row (the
        non-index sec_types' saver — the variants would only ever
        starve)."""
        return not frame.empty and "pe" in frame.columns \
            and bool(frame["pe"].notna().any())

    def attach(self, frame: pd.DataFrame) -> None:
        """The per-code trailing PE rank onto the shared column
        (HOST-NUMPY BOUNDARY, computed once — idempotent)."""
        if PE_PCT_COL in frame.columns:
            return
        codes = host_array(frame["code"])
        vals = host_array(frame["pe"])
        bnd = np.flatnonzero(np.r_[True, codes[1:] != codes[:-1]])
        bnd = np.r_[bnd, len(codes)]
        rank = np.full(len(frame), np.nan)
        for i in range(len(bnd) - 1):
            s, e = int(bnd[i]), int(bnd[i + 1])
            rank[s:e] = trailing_pct_rank(vals[s:e], self.min_periods)
        frame[PE_PCT_COL] = rank

    def _condition(self, frame: pd.DataFrame) -> pd.Series:
        """The gate condition per the rule (fill-BEFORE-compare — NaN
        pe (warmup / missing valuation rows) maps to -1 and can never
        pass a gate)."""
        p = frame[PE_PCT_COL].fillna(-1.0)
        if self.rule == "extremes":
            lo, hi = cfg.PE_GATE_EXTREMES
            return (p <= lo) | (p >= hi)
        return p >= cfg.PE_GATE_NOT_CHEAP

    def bar_def(self) -> dict:
        """The stored bar fragment (the UI bar word renders it)."""
        if self.rule == "extremes":
            lo, hi = cfg.PE_GATE_EXTREMES
            return {"axis": "pe_pct", "rule": "extremes",
                    "cheap_below": lo, "rich_above": hi}
        return {"axis": "pe_pct", "rule": "not_cheap",
                "min": cfg.PE_GATE_NOT_CHEAP}
