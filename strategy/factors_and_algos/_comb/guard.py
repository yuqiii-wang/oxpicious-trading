"""Equity-curve guard (Part 6 U4) — the position-aware util done right.

2-pass protocol (orchestrated by the collector, which owns the engine):

1. pass 1 runs the full comb WITHOUT the guard → the strategy's pooled
   daily PnL path across all codes in the run;
2. :meth:`EquityGuard.build_state` turns that path into a per-date BUY
   multiplier — ``factor`` while the trailing ``lookback_rows``-row
   pooled-PnL slope is negative, 1.0 otherwise (shifted one trading
   day — only yesterday's slope is knowable today);
3. pass 2 re-blends from the ORIGINAL per-family signals with the
   multiplier applied to BUY entries pre-blend.

Pure module: consumes daily-row ``total_pnl`` dicts, never imports the
engine (skill Part 6's guard-ordering caution: a guard scaling family
columns AFTER the blend is a no-op — the engine reads the blended
column).
"""
from __future__ import annotations

from datetime import date
from typing import Dict, List

import numpy as np

from _common.df_utils import host_array

from strategy.factors_and_algos._comb.config import EquityGuardCfg
from strategy.factors_and_algos._comb.data_definitions import GuardState


class EquityGuard:
    """Builds/derives the guard state; applies it pre-blend."""

    def __init__(self, cfg: EquityGuardCfg) -> None:
        self.cfg = cfg

    def build_state(
        self, pnl_by_date: Dict[date, float],
    ) -> GuardState:
        """``{date: buy_multiplier}`` from the pooled cumulative-PnL path.

        The slope at row ``i`` uses the path's OWN trading rows: the
        multiplier effective on date ``i`` reads the slope at ``i−1``
        (one-day shift), and only once at least ``lookback_rows`` rows
        precede it.
        """
        dates = sorted(pnl_by_date)
        pnl = np.array([pnl_by_date[d] for d in dates], dtype=np.float64)
        multipliers: Dict[date, float] = {}
        lookback = self.cfg.lookback_rows
        for i, d in enumerate(dates):
            j = i - 1  # shifted: the slope known at the previous day
            if j >= lookback:
                multipliers[d] = (self.cfg.factor
                                  if (pnl[j] - pnl[j - lookback]) < 0 else 1.0)
            else:
                multipliers[d] = 1.0
        return GuardState(multipliers)

    def apply(
        self, df: pd.DataFrame, families: List[str], state: GuardState,
    ) -> pd.DataFrame:
        """Scale BUY entries of every family column by the per-date
        multiplier (SELLs never scaled). The multiplier column arrives
        via a (date → mult) lookup frame merged onto the big frame —
        cudf-native, no positional host writes."""
        du = df[["date"]].drop_duplicates().sort_values("date").reset_index(drop=True)
        dates_host = host_array(du["date"])
        mult_arr = np.array([
            state.at(d if isinstance(d, date) else
                     d.astype("datetime64[D]").item())
            for d in dates_host
        ], dtype=np.float64)
        du = du.assign(guard_mult=mult_arr)
        df = df.merge(du, on="date", how="left")
        mult = df["guard_mult"].fillna(1.0)
        for fam in families:
            sig = df[f"sig_{fam}"].fillna(0.0)
            df[f"sig_{fam}"] = sig.where(sig <= 0, sig * mult)
        return df.drop(columns=["guard_mult"])


__all__ = ["EquityGuard"]
