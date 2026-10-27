"""The hp_exit_trio blow-off mirror — the trio's MEASURED ASYMMETRY
CHECK. Never shipped: its rows are discarded every run and the result
only logged. The method is MERGED INTO the signal base class:
TrioMirrorMixin is composed into HedgePairTrioEntry (via trio.TrioSub)."""
from __future__ import annotations

import logging

import pandas as pd

from analysis.analysis_signals import config as cfg
from analysis.analysis_signals.engines.rule_base import Candidate, RuleEntry
from analysis.analysis_signals.fetch.inputs._hedge_pairs import bar_suffix

from analysis.analysis_signals.engines.signal_families.hedge_pairs._commons.bar_defs import (
    trio_bar_def,
)
from analysis.analysis_signals.engines.signal_families.hedge_pairs._commons.constants import (
    DEPTH_SCALE,
    PAIR_K,
    WEIGHTING,
)

logger = logging.getLogger(__name__)


class TrioMirrorMixin(RuleEntry):
    """The blow-off mirror's method. A partial HedgePairTrioEntry —
    never instantiated alone."""

    def _check_sell_mirror(self, frame: pd.DataFrame) -> None:
        """The equity-asymmetry CHECK (measured, never shipped): the
        blow-off mirror — all three curves at their RSI exhaustion
        tails — runs through the same candidate machinery and the
        result is only LOGGED. The recorded finding (measured here
        2026-10-24 and in the 2026-10-23 coordinated-exit study):
        the mirror is structurally thin exit timing (~a quarter of the
        buy side's episodes) — coordinated tops CONTINUE. Its rows are
        discarded, so hp_exit_trio (5829) stays a registered label no
        writer emits."""
        candidates: list[Candidate] = []
        for w in cfg.HP_LEG_RSI_WINDOWS:
            suf = bar_suffix(w)
            for pct in sorted(cfg.HP_LEG_TAIL_PCTS):
                raw = (frame[f"bs{pct}{suf}_a"] & frame[f"bs{pct}{suf}_b"]
                       & frame[f"bs{pct}{suf}_m"])
                bar = frame[f"trio_bar_s{pct}{suf}"]
                rsi_mean = frame[f"rsi_mean{suf}"]
                candidates.append(Candidate(
                    key=f"tail{pct}" if suf == "" else f"w{w}_tail{pct}",
                    # the same at-least populations as the shipped entry
                    mask=raw,
                    reference=bar,
                    depth=(rsi_mean - bar) / DEPTH_SCALE,
                    value_col=f"rsi_mean{suf}",
                    params={"bar_def": trio_bar_def(
                        "trio_rsi_tail_mirror", pct, window=w)},
                ))
        r, h = self.run_cells(
            frame, "hp_exit_trio", "sell", candidates, k=PAIR_K,
            value_col="rsi_mean", depth_factor=0.5,
            extra_params={
                "witnesses": ["leg_a", "leg_b", "market"],
                "market": cfg.HP_MARKET_CODE,
                "pair_weighting": WEIGHTING,
            },
        )
        logger.info(
            f"[hedge_pairs] hp_exit_trio/sell (ASYMMETRY CHECK, not "
            f"shipped): the mirror measured {len(r)} cells / {len(h)} "
            f"events — exit-timing thin (tops continue; rows discarded)"
        )
