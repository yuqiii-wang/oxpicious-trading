"""AmplifierScaling — the SIGNAL AMPLIFICATION evidence sizing
(``strategy/factors_and_algos/_comb/amplifier.py``, 2026-11-18).

The analysis tier's SIGNAL AMPLIFICATION pair stamps each amplifier day
(``analysis_signals.signal_amplification_history``, one row per
(code, sec_type, amplifier_type, date)) with a ``bindings`` JSONB array
of per-bound-family ``{signal_type, action, amplify_bp, fired, ...}`` —
the OHLCV-anatomy amplifier's depth-scaled strength for that family on
that day (positive = amplifies the claim, negative = discounts it; the
tier's own evidence block is the measured grounding).

This module is the comb stack's consumer: the fetch carries each bar's
per-family amplifier sums as ``amp_bp_buy`` / ``amp_bp_sell`` (the
collector stashes them per family), and the transform scales the
family's OWN signed ``sig_<family>`` by

    factor(side) = 1 + clamp(Σ amplify_bp / AMP_FACTOR_DIVISOR,
                             AMP_FACTOR_FLOOR - 1, AMP_FACTOR_CAP - 1)

i.e. the bp sums map linearly into the U3 vol-state band convention:
+600bp (the tier's cap) → ×1.30, −600bp → ×0.70, non-amplifier days →
×1.0. Per-event evidence sizing (the U2 quantile-sizing sibling), never
family weighting (weights stay the ``weights`` group's territory).

Family-local by construction: a family's amplifier touches only that
family's own ``sig_<family>`` — it never touches another family's
signal and never invents a signal (the reject/no-action structural
rule inherited verbatim).
"""
from __future__ import annotations

from datetime import date
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from _common.df_utils import host_array

from strategy.factors_and_algos._comb.config import AmplifierCfg

__all__ = ["AmpStash", "AmplifierScaling"]

#: {family: {(code, date): (buy_bp_sum, sell_bp_sum)}} — Σ amplify_bp
#: over that (code, date)'s bound amplifiers for the family+side
#: (the collector builds it from the fetched ``amp_bp_buy`` /
#: ``amp_bp_sell`` columns; empty = no amplifier day in scope).
AmpStash = Dict[str, Dict[Tuple[str, date], Tuple[float, float]]]

#: the bp→factor divisor: the tier's |cap| 600bp maps to ×1.3 / ×0.7
#: (the U3 vol-state band convention — one repo convention, zero tuned
#: parameters)
AMP_FACTOR_DIVISOR = 2000.0
AMP_FACTOR_FLOOR = 0.5
AMP_FACTOR_CAP = 1.5


class AmplifierScaling:
    """Scale each family's own signed signal by its day's amplification
    factor (see the module docstring). BUY-side events take the family's
    buy-binding sum, SELL-side events the sell-binding sum; bars without
    an amplifier day scale nothing."""

    def __init__(self, cfg: AmplifierCfg, stash: Optional[AmpStash]) -> None:
        self.cfg = cfg
        self.stash: AmpStash = stash or {}

    @staticmethod
    def _factor(bp_sum: float) -> float:
        """Σ amplify_bp → the multiplicative factor (clamped to the
        U3 band)."""
        raw = 1.0 + float(bp_sum) / AMP_FACTOR_DIVISOR
        return max(AMP_FACTOR_FLOOR, min(AMP_FACTOR_CAP, raw))

    def apply(self, df: pd.DataFrame, families: List[str]) -> pd.DataFrame:
        for fam in families:
            fam_stash = self.stash.get(fam, {})
            # Lookup frame built FROM the big frame's own (code, date)
            # columns — python-date lists would be auto-converted to
            # datetime64[s] by cudf.pandas and fail the merge against the
            # object-date column (the QuantileSizing dtype convention).
            du = (df[["code", "date"]].drop_duplicates()
                  .reset_index(drop=True))
            codes_host = host_array(du["code"])
            dates_host = host_array(du["date"])
            buy_f = np.empty(len(du), dtype=np.float64)
            sell_f = np.empty(len(du), dtype=np.float64)
            for i in range(len(du)):
                day = dates_host[i]
                day = day.item() if hasattr(day, "item") else day
                pair = fam_stash.get((str(codes_host[i]), day))
                b, s = pair if pair is not None else (0.0, 0.0)
                buy_f[i] = self._factor(b)
                sell_f[i] = self._factor(s)
            du = du.assign(amp_buy=buy_f, amp_sell=sell_f)
            df = df.merge(du, on=["code", "date"], how="left")
            sig = df[f"sig_{fam}"].fillna(0.0)
            mult = pd.Series(1.0, index=df.index)
            mult = mult.mask(sig > 0, df["amp_buy"].fillna(1.0))
            mult = mult.mask(sig < 0, df["amp_sell"].fillna(1.0))
            df[f"sig_{fam}"] = sig * mult
            df = df.drop(columns=["amp_buy", "amp_sell"])
        return df
