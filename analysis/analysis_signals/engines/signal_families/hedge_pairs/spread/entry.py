"""The hp_entry_spread run — the pair's long-short SPREAD curve at its
own RSI BOTTOM tail: the classic pairs CONVERGENCE trade (BUY the
laggard leg A, SELL the leader leg B; no benchmark curve). The method
is MERGED INTO the signal base class: SpreadEntryMixin is composed
into HedgePairTrioEntry (via spread.SpreadSub)."""
from __future__ import annotations

import logging

import pandas as pd

from analysis.analysis_signals import config as cfg
from analysis.analysis_signals.engines.rule_base import Candidate, RuleEntry
from analysis.analysis_signals.fetch.inputs._hedge_pairs import bar_suffix

from analysis.analysis_signals.engines.signal_families.hedge_pairs._commons.bar_defs import (
    spread_bar_def,
)
from analysis.analysis_signals.engines.signal_families.hedge_pairs._commons.constants import (
    DEPTH_SCALE,
    PAIR_K,
    WEIGHTING,
)

logger = logging.getLogger(__name__)


class SpreadEntryMixin(RuleEntry):
    """The spread entry sub's method. A partial HedgePairTrioEntry —
    never instantiated alone."""

    def _run_spread_entry(self, frame: pd.DataFrame,
                          ) -> tuple[list[dict], list[dict]]:
        """The SPREAD convergence entry (2026-10-25, user-directed "one
        buy one sell by the spread between two negative corr curves,
        disregarding the benchmark curve"; study temp_scripts/
        spread_pair_study.py): the pair's long-short SPREAD curve
        (sr = w_a*r_a - w_b*r_b — the fetch's own lagged inverse-vol
        weights, leg A LONG / leg B SHORT, NO market curve) in its
        own RSI bottom tail vs its own adaptive percentile bar — the
        classic pairs CONVERGENCE trade: BUY the laggard leg A, SELL
        the leader leg B. The candidates span the same WINDOW x TAIL
        at-least grid (window-major, deepest-first); the response
        axis is the SPREAD value's forward blend (the long-A/short-B
        PnL), the recorded value the winning window's spread RSI, the
        winner the same frequency-floor policy under the family's own
        loose gate. Measured: the raw population is NEGATIVE (the
        2026-10-20 ratio-z negative reproduces on the RSI axis), the
        edge lives in the per-pair winners (pooled +0.55% t=4.70 hit
        61% over the study's 25/48 winner pairs)."""
        candidates: list[Candidate] = []
        for w in cfg.HP_LEG_RSI_WINDOWS:
            suf = bar_suffix(w)
            for pct in sorted(cfg.HP_LEG_TAIL_PCTS):
                raw = frame[f"sb{pct}{suf}"]
                bar = frame[f"sbar_b{pct}{suf}"]
                srsi = frame[f"srsi{suf}"]
                candidates.append(Candidate(
                    key=f"tail{pct}" if suf == "" else f"w{w}_tail{pct}",
                    mask=raw,
                    reference=bar,
                    depth=(bar - srsi) / DEPTH_SCALE,
                    value_col=f"srsi{suf}",
                    params={"bar_def": spread_bar_def(
                        "spread_rsi_tail", pct, window=w)},
                ))
        rules, history = self.run_cells(
            frame, "hp_entry_spread", "buy", candidates, k=PAIR_K,
            value_col="srsi", depth_factor=0.5,
            response_col="spread",
            freq_floor_per_year=cfg.HP_MIN_EVENTS_PER_YEAR,
            gate_min=cfg.HP_GATE_DIR_AVE_MIN,
            pooled_dir_ave_min=cfg.HP_POOLED_DIR_AVE_MIN,
            extra_params={
                "witnesses": ["leg_a", "leg_b"],
                "pair_weighting": WEIGHTING,
                "response": "spread_long_short",
            },
        )
        logger.info(f"[hedge_pairs] hp_entry_spread/buy: {len(rules)} "
                    f"orchestrating cells, {len(history)} events")
        return rules, history
