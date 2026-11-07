"""Candidate — data definition.

Migrated verbatim from :mod:`analysis.analysis_signals.engines.rule_base` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass, field
import pandas as pd


@dataclass(frozen=True)
class Candidate:
    """One bar/event variant of a (sub_type, action) rule the engine
    measures per cell — the self-adaptive loop picks the gate-passing
    winner. ``mask`` marks the QUALIFYING trigger-condition days on the
    prepared frame's index (the episode machinery takes the runs' first
    days); ``reference`` is the bar crossed (scalar, or the per-day
    adaptive bar Series on the same index); ``depth`` is the trigger
    day's normalized breach depth (the 0..2 confidence scale);
    ``params`` is the candidate's own rule_params fragment (the bar
    def etc.)."""
    key: str
    mask: pd.Series
    reference: float | pd.Series
    depth: pd.Series
    params: dict = field(default_factory=dict)
    # the candidate's own recorded-value column override (the history
    # rows' ``signal``): None = the run_cells call's ``value_col`` —
    # the hedge trio's window grid records the winning window's own
    # rsi_mean (rsi14 vs rsi10 means are different scales)
    value_col: str | None = None
