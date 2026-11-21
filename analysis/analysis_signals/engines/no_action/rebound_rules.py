"""rebound's no-action rule (engines/no_action/rebound_rules.py).

`rebound_pe_deadzone` (NO BUY) — MEASURED (the 2026-10-26 barbell
study): the capitulation bounce is cheap20 +6.64% fwd20 (the
valuation floor) and rich20 +2.18% but +6.46% fwd60 (the knife
takes a month to stop, the snapback real) — while MIDDLE PE has NO
60d edge at all. A capit trigger at middle PE is the dead zone:
not cheap enough to be the floor, not rich enough for the violent
snapback. The pe-boost PK axis that productized this was UNLINKED
2026-11-01 (the variant rows are gone) — the MEASUREMENT stands,
unencoded; this rule re-expresses it as overlay advice.

The PE percentile is re-derived independently: today's PE among
the code's own PRIOR PE readings (>= NO_ACTION_PE_MIN_PERIODS
observations, strictly prior values — no look-ahead). The frame
still carries the `pe` column (the rebound fetch LEFT JOINs
stats.index_valuation). This is the module's ONE sanctioned host
boundary (a per-code gather/index algorithm — sorted insertion
over prior-only values, host_array in, host ndarray back into the
frame); a NaN PE (no valuation row) never marks — no evidence, no
claim.
"""
from __future__ import annotations

from bisect import insort, bisect_left
from typing import ClassVar

import numpy as np
import pandas as pd

from _common.df_utils import host_array, safe_columns

from analysis.analysis_signals import config as cfg
from analysis.analysis_signals.engines.no_action.base import NoActionRule


def pe_percentile(codes: np.ndarray, dates_us: np.ndarray,
                  pe: np.ndarray, min_periods: int) -> np.ndarray:
    """The prior-only PE percentile per (code, row): pe[t] ranked
    among the code's STRICTLY PRIOR valid PE readings, 0..1, NaN
    before ``min_periods`` prior observations or at a NaN PE. Rows
    are walked in (code, date) order regardless of the frame's own
    ordering (a lexsort permutation) — the host boundary core."""
    n = len(codes)
    code_id = np.empty(n, dtype=np.int64)
    ids: dict[str, int] = {}
    for i in range(n):
        c = str(codes[i])
        code_id[i] = ids.setdefault(c, len(ids))
    order = np.lexsort((dates_us, code_id))
    out = np.full(n, np.nan, dtype="float64")
    prior: list[list[float]] = [[] for _ in range(len(ids))]
    for i in order:
        v = pe[i]
        if np.isnan(v):
            continue  # no valuation row — no claim either way
        lst = prior[code_id[i]]
        if len(lst) >= min_periods:
            out[i] = bisect_left(lst, v) / len(lst)
        insort(lst, float(v))
    return out


class ReboundPeDeadzone(NoActionRule):
    """NO BUY at a capitulation trigger in the middle-PE dead zone."""

    REASON: ClassVar[str] = "rebound_pe_deadzone"
    FAMILY: ClassVar[str | None] = "rebound"
    ACTIONS: ClassVar[tuple[str, ...]] = ("buy",)
    SUB_TYPES: ClassVar[tuple[str, ...]] = ("rebound_capit_60",
                                            "rebound_capit_252")
    CONTEXT_COLS: ClassVar[tuple[str, ...]] = ("_na_pe_pct",)

    def attach(self, frame: pd.DataFrame, ctx) -> None:
        if "_na_pe_pct" in set(safe_columns(frame)):
            return
        codes = host_array(frame["code"])
        dates_us = host_array(frame["date"]).astype("int64")
        pe = host_array(frame["pe"].to_numpy(dtype="float64",
                                             na_value=np.nan))
        frame["_na_pe_pct"] = pe_percentile(
            codes, dates_us, pe, cfg.NO_ACTION_PE_MIN_PERIODS)

    def condition(self, row: dict, ctxv: dict) -> bool:
        p = ctxv.get("_na_pe_pct")
        if p is None or np.isnan(p):
            return False
        lo, hi = cfg.NO_ACTION_PE_DEADZONE
        return lo <= p < hi

    def condition_params(self) -> dict:
        lo, hi = cfg.NO_ACTION_PE_DEADZONE
        return {
            "pe_pct": f"today's PE among the code's own prior "
                      f"readings (min {cfg.NO_ACTION_PE_MIN_PERIODS})",
            "deadzone": [lo, hi],
            "evidence": "measured 2026-10-26: cheap20 +6.64% fwd20 / "
                        "rich20 +6.46% fwd60 / middle +-1% no edge",
        }


__all__ = ["ReboundPeDeadzone", "pe_percentile"]
