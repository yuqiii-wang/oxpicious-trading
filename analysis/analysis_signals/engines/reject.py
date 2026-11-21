"""The signal rules tier's NEGATIVE-CONFIDENCE REJECT tier
(analysis.analysis_signals.engines.reject — 2026-11-16, user-directed:
"allow for negative confidence score which means REJECT the action
(however, does not mean the opposite action should take, e.g., a
negative SELL means at this price do not sell, but does not mean
buy)"). The logic lives here as a module fully SEPARATE from the
positive-confidence path — ``CellGate.confidence_pct`` keeps its
[0, CONFIDENCE_CAP_PCT] clamp untouched; a reject row is never a
sign-branch inside the trading tier:

  RejectEval.select          the emission SET for a run_cells call —
                             the whole reject decision consolidated
                             here (2026-11-18): cells with NO winning
                             candidate whose best failed candidate's
                             measured dir_ave clears the HIGH-
                             CONFIDENCE bar (strictly below config
                             REJECT_DIR_AVE_MAX — since 2026-11-18 a
                             DEEPLY negative level, −2.0%: only a
                             strongly losing measured mean — the sign-
                             aligned mean of the action's own episodes
                             AGAINST it, the action historically LOSING
                             at this trigger: tops-continue sells,
                             falling-knife buys — may claim against its
                             action; hairline negatives join the silent
                             band, user-directed "only reject on high
                             confidence, there should be very few no
                             action than action"),
                             MINUS the SIGNAL-CLUSTER PROBATION — a
                             cell whose SYSTEM BIRTH (its first-ever
                             history_signal_performance snapshot, the
                             never-purged record of when the cell
                             first measured anything) falls within
                             the past REJECT_FIRST_APPEARANCE_DAYS
                             (20) of the code's own traded rows is
                             still fresh and may not reject yet
  RejectEval.rejects         the bare emission verdict for a single
                             non-winning cell (select's per-cell leg).
                             The band [this bar, the side's gate bar)
                             stays SILENT — unproven or not strongly
                             negative, never rejected
  RejectEval.confidence_pct  the negative confidence in PERCENT: the
                             measured negative dir_ave amplified over
                             the breach depth and discounted by the
                             starvation sample_scale — the SAME scales
                             the positive path reads (a deeper pierce
                             into a losing population is a STRONGER
                             reject; thin evidence discounts it) —
                             clamped to [-CONFIDENCE_CAP_PCT, 0]

A reject row keeps the cell's REAL action code (buy/sell — the
direction is what is being negated) and carries NEGATIVE confidence on
the integer basis-point record columns: read it as "do NOT take this
action at this price", NEVER as the inverse action. The UI renders it
as the NO BUY / NO SELL chip — "no sell" is the SELL-side reject, not
a separate tier; the probation suppresses both sides symmetrically.
The rules row stamps is_valid FALSE (the denormalized gate verdict —
the cell did not pass) and rule_params.gate.reject_bar_pct; the
performance snapshot carries the negative dir_ave_pct as any cell's
would (the LIVE tier reads it as the confidence base, so a live reject
breach produces a negative-confidence live row — "do not sell/buy
NOW"). The probation needs no live-side counterpart: a suppressed cell
emits no rule row, so there is nothing to fire.

Exclusions (run_cells never consults this module there): the UNGATED
observational twins (their rejection is a trigger-time observation, the
VRP gate — not measured evidence) and the PORTFOLIO tier's sourced
calls (the portfolio mapping registers winners). Reject rows never feed
the POOLED evidence facts or the OHLCV-boost anchors — those stay the
trading tier's derivation populations. A POOLED-demoted BUY side keeps
its trading silence (insufficiency, not negativity) while its reject
cells still emit — the two claims are different populations.
"""
from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from analysis.analysis_signals import config as cfg

if TYPE_CHECKING:
    from analysis.analysis_signals.engines.data_definitions import (
        Candidate,
    )


class RejectEval:
    """The REJECT tier's verdict + negative-confidence scale — the
    negative-evidence counterpart of ``evaluation.CellGate``, kept in
    this module only (constructed per run_cells call by rule_base;
    all inputs/outputs in PERCENT like the confidence scale)."""

    def __init__(self, dir_ave_max: float | None = None,
                 first_appearance_days: int | None = None) -> None:
        """``dir_ave_max`` — the reject bar in FRACTIONAL dir_ave (the
        config REJECT_DIR_AVE_MAX convention, mirroring GATE_DIR_AVE_MIN);
        a cell measures into the reject tier strictly BELOW it — since
        2026-11-18 a DEEPLY negative level (the high-confidence bar:
        hairline negatives stay silent, user-directed "only reject on
        high confidence").
        ``first_appearance_days`` — the SIGNAL-CLUSTER PROBATION window
        in the code's own traded rows (config
        REJECT_FIRST_APPEARANCE_DAYS by default): a cell whose system
        birth (first-ever measurement snapshot) falls within this
        window may not reject yet."""
        self._max_pct = (cfg.REJECT_DIR_AVE_MAX if dir_ave_max is None
                         else dir_ave_max) * 100.0
        self._first_days = (cfg.REJECT_FIRST_APPEARANCE_DAYS
                            if first_appearance_days is None
                            else first_appearance_days)

    def rejects(self, dir_ave_pct: float) -> bool:
        """The emission verdict for a non-winning cell (PERCENT input,
        the measure block's ``dir_ave_pct``): True = the cell's best
        candidate measured evidence strictly below the HIGH-CONFIDENCE
        bar — a strongly losing mean, worth claiming against (hairline
        negatives stay silent)."""
        return dir_ave_pct < self._max_pct

    def select(
        self,
        failed: dict[tuple, tuple["Candidate", dict]],
        chosen: dict[tuple, tuple["Candidate", dict]],
        births: dict[tuple, date],
        window_start: dict[str, date],
    ) -> dict[tuple, tuple["Candidate", dict]]:
        """The REJECT emission set for a run_cells call — the whole
        negative-confidence decision in one place (the caller hands the
        raw material over, this module owns the verdict):

        ``failed``        cell → the BEST failed candidate + its
                          measure block
        ``chosen``        the winning cells (any cell already carrying
                          a gate-passing candidate never rejects)
        ``births``        cell → the cell's SYSTEM BIRTH (its first-
                          ever history_signal_performance snapshot
                          date, read by the caller through the
                          cell_birth_dates SQL view — the never-purged
                          record of when the cell first MEASURED
                          anything). ``None``-missing = the cell has
                          never measured before this run = born NOW
        ``window_start``  code → the date of the code's
                          ``first_appearance_days``-most-recent traded
                          row of the prepared frame (the probation
                          window's opening boundary)

        A cell rejects iff it has NO winner, its best failed candidate
        measured strictly below the reject bar, AND its birth is OLDER
        than the window boundary — a cell born within the past
        ``first_appearance_days`` traded rows (or never measured
        before) is still FRESH: no reject claim yet, NO BUY / NO SELL
        alike. NOTE the in-frame first-trigger age needs no guard: the
        measurement itself requires a full 20d forward blend realized,
        so every reject's earliest trigger is already >= 20 traded
        rows old — the birth record is the only probation that can
        bind. Returned values keep the ``(candidate, measure)`` pair
        shape the caller's emission + history loops consume."""
        rejects: dict[tuple, tuple["Candidate", dict]] = {}
        for cell, (cand, meas) in failed.items():
            if cell in chosen:
                continue
            if not self.rejects(meas["measure"]["dir_ave_pct"]):
                continue
            # the SIGNAL-CLUSTER PROBATION (2026-11-18): a cell born
            # within the past REJECT_FIRST_APPEARANCE_DAYS traded rows
            # of its code (or never measured before this run) is still
            # fresh — no reject claim yet
            born = births.get(cell)
            if born is None:
                continue
            start = window_start.get(str(cell[0]))
            if start is not None and born > start:
                continue
            rejects[cell] = (cand, meas)
        return rejects

    def confidence_pct(self, base_pct: float, depth_norm: float,
                       factor: float,
                       sample_scale: float = 1.0) -> float:
        """Negative-scaled confidence in PERCENT: the cell's measured
        NEGATIVE base (its dir_ave pct) amplified linearly over the
        normalized breach depth (capped at 2) and multiplied by the
        starvation ``sample_scale`` (min(n/floor, 1)), clamped to
        [-CONFIDENCE_CAP_PCT, 0]. The record writers convert to the
        INTEGER basis-point scale (x100) — the mirror of
        ``CellGate.confidence_pct``; a non-negative base saturates to 0
        (never invents positivity here)."""
        scaled = base_pct * (1.0 + factor * min(max(depth_norm, 0.0), 2.0)) \
            * sample_scale
        return float(max(min(scaled, 0.0), -cfg.CONFIDENCE_CAP_PCT))

    def gate_bar_pct(self) -> float:
        """The reject bar as the PERCENT constant the reject rules rows
        stamp on rule_params.gate.reject_bar_pct (the constant-rule
        vocabulary — the trading twin of the gate block's min_pct)."""
        return round(self._max_pct, 4)


__all__ = ["RejectEval"]
