"""MACD signal algorithm — signals PICKED UP from the analysis tier.

Refactored 2026-11-07 (user-directed "the signals could only get picked
up from analysis signal table"): the strategy NO LONGER computes the
MACD(12/26/9) crossover itself. The macd family's rule cells live in the
analysis tier (``analysis/analysis_signals`` — the MacdCrossEntry engine,
2026-10-26): the classic MACD line = ema12 − ema26 (the builds'
precomputed tech_stats EMAs) with its EMA9 signal line, trigger = the
signal-line crossover, and its replayed trigger events sit in
``analysis_signals.history_signals`` (signal_type = 10):

  - ``macd_cross_up`` (6000, BUY)  — MACD crosses ABOVE its signal line
    (the momentum turn);
  - ``macd_cross_dn`` (6001, SELL) — MACD crosses BELOW its signal line
    (exit timing — the equity asymmetry).

The recorded value is the histogram as a pct of close (cross-security
comparable); the crossed bar is 0.0; the recorded confidence is the
cell's measured evidence (dir_ave base × depth × starvation scales) in
basis points. Events are episode FIRST days (K=5 absorb, delay 0) — one
event per cross run, already de-whipsawed by the analysis tier.

This module is a thin :class:`AnalysisSignalAlgoBase` subclass: the
generic fetch (OHLC + the family's standalone-tier events) and the
generic event→confidence mapping are inherited; only the family identity
(SIGNAL_TYPE_CODE = 10) and the reason phrasing are macd-specific. There
are NO model params to tune — the signal math belongs to the analysis
tier; a ``--algo macd`` run backtests exactly the recorded events.
"""
from __future__ import annotations

from _common.labels import SIGNAL_TYPE_CODE
from strategy.factors_and_algos._algo.analysis_source import (
    AnalysisSignalAlgoBase,
)


class MacdAlgo(AnalysisSignalAlgoBase):
    """MACD crossover algo — picks up the macd family's recorded events.

    Signals come ONLY from ``analysis_signals.history_signals``
    (signal_type = 10, standalone tier, trading actions); the crossover
    math, episode shaping and confidence scaling all live in the
    analysis tier's rule cells.
    """

    ALGO_NAME = "macd"
    POSITION_AWARE = False
    # static.label_dim 'signal_type' — the macd detection family.
    SIGNAL_TYPE_CODE: int = SIGNAL_TYPE_CODE["macd"]

    # ------------------------------------------------------------------
    # Reason
    # ------------------------------------------------------------------
    def build_signal_reason(self, row, side: str, params: dict, confidence: float) -> str:
        """Report the picked-up analysis signal that fired this decision."""
        sub, value, reference, conf_bp = self.event_reason_parts(row)
        return (f"MACD {side}: analysis signal {sub} picked up from "
                f"analysis_signals.history_signals — value={value} vs "
                f"bar={reference}, cell confidence={conf_bp}bp "
                f"-> {confidence:.1f}/100")


# Singleton instance — the registry returns this via the algo package's
# __init__.py ``ALGO`` attribute.
ALGO = MacdAlgo()


__all__ = ["MacdAlgo", "ALGO"]
