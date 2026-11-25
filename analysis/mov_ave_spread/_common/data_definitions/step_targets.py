"""StepTargets — data definition.

A step's incremental write scope (built by
``analysis.mov_ave_spread._common.steps.resolve_step_targets``)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Set


@dataclass(frozen=True)
class StepTargets:
    """The dates one TableStep must (re)compute, split by write path.

    ``dates`` is the whole incremental scope: the step's own missing
    dates, the parent's core target dates (the family moves in lockstep
    — a date reopened in the detail / trailing tables is reopened in
    every same-grain step) and the step's repair dates, inside the
    default increment bound.

    ``refresh`` is the subset of ``dates`` the step's table ALREADY
    holds. The incremental write is a date-skip + COPY (it drops every
    row whose date is present), so rows on those dates cannot take it:
    they are upserted instead. That is what lets a whole date be
    recomputed over stale rows — a per-code gap, a corrected source bar.
    """

    dates: Set = field(default_factory=set)
    refresh: Set = field(default_factory=set)
