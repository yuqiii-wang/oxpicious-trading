"""The hp_exit_spread run — the same long-short SPREAD curve at its
RSI EXHAUSTION tail: the rotation blow-off exit (SELL the leader leg
A, BUY the laggard leg B — exit timing, never pooled-gated). The
method is MERGED INTO the signal base class: SpreadExitMixin is
composed into HedgePairTrioEntry (via spread.SpreadSub)."""
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


class SpreadExitMixin(RuleEntry):
    """The spread exit sub's method. A partial HedgePairTrioEntry —
    never instantiated alone."""

    def _run_spread_exit(self, frame: pd.DataFrame,
                         ) -> tuple[list[dict], list[dict]]:
        """The TOP-TAIL EXIT (hp_exit_spread, 2026-10-25, user-directed
        "2024-04-18 can also be a good event date to pick; re-study
        the rule why failed, and fix it"; study temp_scripts/
        spread_peryear_study.py): the same spread curve at its RSI
        EXHAUSTION tail — the rotation blow-off — SELL the leader
        leg A / BUY the laggard leg B (the one-sell-one-buy mirror of
        the entry; exit timing, sells never pooled-gated). The
        diagnosis: the prior era shipped bottom-only, so the showcase
        pair's 2024-04-18-class tops — the dividend-over-tech
        rotation exhausting at the 2024-04-16..19 microcap-crash
        peak, srsi10 75..82 breaching its own top t5/t2 bars — could
        never fire (the mirror check measured them and DISCARDED the
        rows). The measured record under the per-year floor policy:
        44 winners / 850 events, pooled +0.20% t=3.41 hit 56% 6/6
        measurable years — SHIPPED (the raw-grid negative stands as
        the honest background: relative strength persists; the edge
        lives in the per-pair winners the policy picks, exactly the
        entry sub's evidence structure)."""
        x_candidates: list[Candidate] = []
        for w in cfg.HP_LEG_RSI_WINDOWS:
            suf = bar_suffix(w)
            for pct in sorted(cfg.HP_LEG_TAIL_PCTS):
                raw = frame[f"ss{pct}{suf}"]
                bar = frame[f"sbar_s{pct}{suf}"]
                srsi = frame[f"srsi{suf}"]
                x_candidates.append(Candidate(
                    key=f"tail{pct}" if suf == "" else f"w{w}_tail{pct}",
                    mask=raw,
                    reference=bar,
                    depth=(srsi - bar) / DEPTH_SCALE,
                    value_col=f"srsi{suf}",
                    params={"bar_def": spread_bar_def(
                        "spread_rsi_tail_mirror", pct, window=w)},
                ))
        rules, history = self.run_cells(
            frame, "hp_exit_spread", "sell", x_candidates, k=PAIR_K,
            value_col="srsi", depth_factor=0.5,
            response_col="spread",
            freq_floor_per_year=cfg.HP_MIN_EVENTS_PER_YEAR,
            gate_min=cfg.HP_GATE_DIR_AVE_MIN,
            extra_params={
                "witnesses": ["leg_a", "leg_b"],
                "pair_weighting": WEIGHTING,
                "response": "spread_long_short",
            },
        )
        logger.info(f"[hedge_pairs] hp_exit_spread/sell: {len(rules)} "
                    f"orchestrating cells, {len(history)} events")
        return rules, history
