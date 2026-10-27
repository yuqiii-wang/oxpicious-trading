"""The shared VARIANT-GATE base class (analysis.analysis_signals.
engines._variant_base, 2026-10-27 — the class OOP restructure of the
flat engines/variants.py): the abstract behind the engines/_pe_boost
and engines/_ohlcv_boost packages.

A VariantGate owns ONE boosted-variant axis of the rule tier — the
is_pe_boosted / is_ohlcv_boosted PK axes (a variant row is a SEPARATE
row from the plain one; the two populations are measured independently
by the SAME gate machinery, so a variant that cannot clear the
dir_ave bar emits nothing — the negative-optimization self-prune).
The axis contract (subclasses implement):

  available   whether the frame carries the axis's data at all (the
              families skip the passes entirely when it does not — the
              non-index sec_types' PE saver, the fetch-dependent amt)
  attach      compute + store the axis column on the frame (idempotent
              — the expensive shared layers, e.g. the per-code
              trailing PE rank, compute once for every gate of the
              axis)
  _condition  the gate condition over the attached column (a boolean
              Series on the frame's index; NaN-bearing states can
              never pass — fill-BEFORE-compare with the -1 sentinel)
  bar_def     the stored bar fragment (nested under ``bar_key`` in the
              variant candidates' rule_params.bar_def — the UI's bar
              word reads it)

and the pass machinery lives HERE: ``prepare`` attaches + caches the
condition once per frame, ``gated`` AND-gates the engine's own plain
candidates, ``run_pass`` emits one variant pass through run_cells with
the axis's row-flag kwarg stamped (the rebound PE pattern
generalized). Engines build their PLAIN candidates once and hand the
SAME list to the plain run_cells call and every gate's run_pass.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar, Sequence

import pandas as pd

if TYPE_CHECKING:  # no runtime import — rule_base imports this stack
    from ..rule_base import Candidate, RuleEntry

__all__ = ["VariantGate"]


class VariantGate(ABC):
    """One boosted-variant axis (the PE-conditioning / OHLCV-boost
    population gates the family engines apply beside their plain
    cells)."""

    # -- the row identity (the PK axes' mirrors) -------------------------
    bar_key: ClassVar[str] = ""      # bar_def fragment key ('pe_gate' |
    #                                  'ohlcv_gate' — the UI renders it)
    flag_kwarg: ClassVar[str] = ""   # the run_cells row-flag kwarg
    suffix: ClassVar[str] = ""       # the variant candidate-key suffix

    def __init__(self) -> None:
        # the gate condition cached by prepare() — one host-safe
        # boolean Series per frame (the engines call prepare ONCE and
        # the pass calls reuse it; instances are per-run, never shared
        # across frames)
        self._cond: pd.Series | None = None

    # -- the axis contract (subclasses implement) ------------------------
    @abstractmethod
    def available(self, frame: pd.DataFrame) -> bool:
        """Whether the frame carries the axis's data at all."""

    @abstractmethod
    def attach(self, frame: pd.DataFrame) -> None:
        """Compute + store the axis column(s) on the frame
        (idempotent)."""

    @abstractmethod
    def _condition(self, frame: pd.DataFrame) -> pd.Series:
        """The gate condition over the attached column."""

    @abstractmethod
    def bar_def(self) -> dict:
        """The stored bar fragment (nested under ``bar_key``)."""

    # -- the shared pass machinery ----------------------------------------
    def prepare(self, frame: pd.DataFrame) -> None:
        """Attach the axis column + cache the condition — call ONCE
        per prepared frame, before the run_pass calls (``active`` then
        reports whether the axis has data this scope)."""
        if not self.available(frame):
            self._cond = None
            return
        self.attach(frame)
        self._cond = self._condition(frame)

    @property
    def active(self) -> bool:
        """Whether the axis carries data for the prepared frame (the
        condition is cached and the passes can run)."""
        return self._cond is not None

    def gated(self, candidates: Sequence[Candidate]) -> list[Candidate]:
        """The variant pass's candidate set: the plain candidates'
        masks AND-gated by the axis condition + the bar fragment
        nested under ``bar_key``. Constructed through ``type(c)``
        (frozen dataclasses — no runtime Candidate import needed)."""
        if self._cond is None:
            return []
        frag = self.bar_def()
        out: list[Candidate] = []
        for c in candidates:
            params = dict(c.params)
            bar = dict(params.get("bar_def", {}))
            bar[self.bar_key] = frag
            params["bar_def"] = bar
            out.append(type(c)(
                key=c.key + self.suffix,
                mask=(c.mask & self._cond).fillna(False),
                reference=c.reference, depth=c.depth,
                params=params, value_col=c.value_col,
            ))
        return out

    def run_pass(self, entry: RuleEntry, frame: pd.DataFrame,
                 sub_type: str, action: str,
                 candidates: Sequence[Candidate],
                 call: dict) -> tuple[list[dict], list[dict]]:
        """One variant pass — the gated candidates through the entry's
        run_cells with the axis's row-flag stamped; ([], []) when the
        axis has no data this scope. ``call`` is the PLAIN pass's
        run_cells keyword arguments (k / value_col / depth_factor /
        extra_params / ...) — the variant reuses them verbatim."""
        if not self.active:
            return [], []
        return entry.run_cells(
            frame, sub_type, action, self.gated(candidates),
            **{**call, self.flag_kwarg: True},
        )
