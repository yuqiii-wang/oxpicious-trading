"""No-action overlay offset — the family-local suppression stage (the
reject tier's day-level mirror).

The analysis tier's NO-ACTION marks ("do NOT take this action at this
price", ``analysis_signals.engines.no_action`` →
``analysis_signals.no_action_signals``) arrive at the strategy tier
as the fetch's ``na_action`` / ``na_confidence`` evidence columns,
stashed by the collector per family as
``{family: {(code, date): (action_code, bp)}}``. This stage is their
ONLY consumer in the blend: when :class:`NoActionCfg` is enabled, a
mark on (code, date, side) zeroes that family's OWN same-side
``sig_<family>`` on that bar. Unlike a reject (a CELL-level verdict
whose history rows never coexist with that cell's trading rows), a
mark BY CONSTRUCTION sits on a FIRED positive day — the suppression
cancels the marked fired event itself, which is exactly the tier's
claim (e.g. the calibrated boll_squeeze_breakdown: marked fired buys
measured −0.27% vs kept +2.24% aligned blend). The opposite side is
untouched and no signal is ever invented: a mark can only REMOVE the
family's own same-side voice from the bar's netting.

Design boundaries (the reject tier's Part-16 shapes, verbatim — skill
singleton-strategy-tuning Parts 16/17):

- CROSS-FAMILY offsets are STRUCTURALLY ABSENT. A mark never reads or
  writes another family's column: the reject transfer study measured
  cross-family vetoes cancelling exactly the best buys, and the
  no-action tier is family-local by design (a mark names its own
  fired event). There is no mode, weight, or threshold through which
  this class can express one.
- The stage runs PRE-BLEND and PRE-GATE (pipeline stage 0, beside the
  reject offset), AFTER the EventDataset build — the edge statistics
  learn from the UNSUPPRESSED event population, so weight-fading
  (family-level, trailing) and no-action suppression (family-local,
  per-bar) remain independent signals, never double-counted.
- Lookahead-free by construction: a mark is recorded by the analysis
  tier on the SIGNAL bar (its evidence is trigger-time-observable
  context) — the same bar the suppression applies to.

cudf boundary: the mark population is small (the tier's rarity
budget: ≤ ~10% of a family's fired days) and each entry is one
vectorized boolean-mask write on the big frame (cuDF-native column
ops); the per-entry loop over the stash is the sanctioned small-list
iteration, not a per-row frame compute.
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

import pandas as pd

from _common.df_utils import safe_columns
from _common.labels import ACTION_CODE

from strategy.factors_and_algos._comb.config import NoActionCfg

#: {family: {(code, date): (na_action_code, na_confidence_bp)}}
NaStash = Dict[str, Dict[Tuple[str, object], Tuple[float, float]]]


class NoActionOffset:
    """Zero a family's own same-side signal on its marked bars."""

    def __init__(self, cfg: NoActionCfg) -> None:
        self.cfg = cfg

    def apply(
        self,
        df: pd.DataFrame,
        families,
        na_stash: Optional[NaStash] = None,
    ) -> pd.DataFrame:
        """Apply the family-local suppression to the ``sig_<family>``
        columns. A no-op when the stash is empty (the default OFF
        state) — the frame is returned untouched, not copied."""
        if not self.cfg.enabled or not na_stash:
            return df
        buy_code = float(ACTION_CODE["buy"])
        cols = safe_columns(df)
        for fam in families:
            entries = na_stash.get(fam)
            if not entries:
                continue
            col = f"sig_{fam}"
            if col not in cols:
                continue
            sig = df[col].fillna(0.0)
            for (code, day), (na_action, _bp) in entries.items():
                side_pos = na_action == buy_code
                hit = (
                    (df["code"] == code) & (df["date"] == day)
                    & (sig > 0 if side_pos else sig < 0)
                )
                if hit.any():
                    df.loc[hit, col] = 0.0
        return df


__all__ = ["NaStash", "NoActionOffset"]
