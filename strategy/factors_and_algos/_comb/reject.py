"""Reject-tier offset — the family-local self-suppression stage.

The analysis tier's REJECT rows ("do NOT take this action at this
price", ``analysis_signals.engines.reject``) arrive at the strategy
tier as the fetch's ``rej_action`` / ``rej_confidence`` evidence
columns, stashed by the collector per family as
``{family: {(code, date): (action_code, bp)}}``. This stage is their
ONLY consumer in the blend: when :class:`RejectCfg` is enabled, a
family's reject on (code, date, side) zeroes that family's OWN
same-side ``sig_<family>`` on that bar — the family's failed-cell
evidence suppressing its own signal (a BUY reject zeroes a positive
contribution; a SELL reject zeroes a negative one). The opposite side
is untouched and no signal is ever invented: a reject can only REMOVE
the family's own same-side voice from the bar's netting.

Design boundaries (2026-11-18 study, skill singleton-strategy-tuning
Part 16 — measured, not stylistic):

- CROSS-FAMILY offsets are STRUCTURALLY ABSENT. A reject never reads
  or writes another family's column: the transfer study measured other
  families' negative state coinciding with BETTER same-side events
  (buys +2.90% @71.5% under ≥2 negative others vs +1.89% @68.1%
  unexposed; era-stable) — a cross-family veto would cancel exactly
  the best buys. There is no mode, weight, or threshold through which
  this class can express one.
- The stage runs PRE-BLEND and PRE-GATE (pipeline stage 0), AFTER the
  EventDataset build — the same convention as every other transform:
  the edge statistics learn from the UNSUPPRESSED event population, so
  weight-fading (family-level, trailing) and rejection (family-local,
  per-bar) remain independent signals, never double-counted.
- Lookahead-free by construction: a reject row is recorded by the
  analysis tier on the SIGNAL bar (its evidence is the cell's trailing
  measured dir_ave) — the same bar the suppression applies to.

cudf boundary: the reject population is small (starts at 0 — the tier
is in its birth probation until ~2026-12-31) and each entry is one
vectorized boolean-mask write on the big frame (cuDF-native column
ops); the per-entry loop over the stash is the sanctioned small-list
iteration, not a per-row frame compute.
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

import pandas as pd

from _common.df_utils import safe_columns
from _common.labels import ACTION_CODE

from strategy.factors_and_algos._comb.config import RejectCfg

#: {family: {(code, date): (rej_action_code, rej_confidence_bp)}}
RejStash = Dict[str, Dict[Tuple[str, object], Tuple[float, float]]]


class RejectOffset:
    """Zero a family's own same-side signal on its reject bars."""

    def __init__(self, cfg: RejectCfg) -> None:
        self.cfg = cfg

    def apply(
        self,
        df: pd.DataFrame,
        families,
        rej_stash: Optional[RejStash] = None,
    ) -> pd.DataFrame:
        """Apply the family-local suppression to the ``sig_<family>``
        columns. A no-op when the stash is empty (the default OFF /
        soft-launch state) — the frame is returned untouched, not
        copied."""
        if not self.cfg.enabled or not rej_stash:
            return df
        buy_code = float(ACTION_CODE["buy"])
        cols = safe_columns(df)
        for fam in families:
            entries = rej_stash.get(fam)
            if not entries:
                continue
            col = f"sig_{fam}"
            if col not in cols:
                continue
            sig = df[col].fillna(0.0)
            for (code, day), (rej_action, _bp) in entries.items():
                side_pos = rej_action == buy_code
                hit = (
                    (df["code"] == code) & (df["date"] == day)
                    & (sig > 0 if side_pos else sig < 0)
                )
                if hit.any():
                    df.loc[hit, col] = 0.0
        return df


__all__ = ["RejectOffset", "RejStash"]
