"""The NO-ACTION overlay tier's rule base (analysis.
analysis_signals.engines.no_action — 2026-11-18, user-directed:
"for no action and rejection for negative confidence, ensure they
sit in a diff independent module, every signal type computed as
independent from its regular positive confidence computation").

The SECOND negative-confidence layer beside engines/reject.py
(cell-level measured means): this one is DAY-level — per-family
trigger-time INVALIDATION conditions (the band walks, the squeeze
breakdown, the whipsaw chop, the PE dead zone, the macd chop) plus
the cross-family FATIGUE filter (too many same-type fired days in
the code's recent traded rows). Every rule marks FIRED POSITIVE
days only (overlay — a no-action row never exists without its
positive twin, and positive rows are never suppressed, rewritten,
or discounted); rows go to analysis_signals.no_action_signals, the
tier's OWN table, so the strategy/live seams that read
history_signals never see them by construction.

INDEPENDENCE CONTRACT — the module builds its own masks from the
prepared frame's raw columns (price / trend / gaps / pe / macd_hist)
with the shared _common.df_utils + analysis.trends LIBRARY helpers;
it never imports a family engine, CellGate, run_cells, or any
positive-path verdict/confidence code, and it must leave
signal_rules / history_signals / history_signal_performance
bit-identical (the A/B md5 bar). Shared libraries are allowed;
positive-path logic is not.

LAYOUT (cudf-dtypes): conditions are whole-frame grouped ops on the
GPU (grouped_rolling_agg / grouped_rolling_quantile / grouped_shift,
elementwise where-chains, fillna-before-compare). ONE sanctioned
host boundary: the rebound PE percentile (a per-code gather/index
algorithm over prior-only values — host_array in, host ndarray
back into the frame) and the driver's positional day-flag stamping;
both documented in their docstrings.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

import numpy as np
import pandas as pd

from _common.df_utils import host_array
from _common.labels import (
    ACTION_CODE,
    NO_ACTION_REASON_CODE,
    SIGNAL_SUB_TYPE_CODE,
)

from analysis.analysis_signals import config as cfg


class NoActionCtx:
    """The driver's per-run lookup context handed to every rule:

      pos_index   (code, date_epoch_us) -> frame row position — the
                  fired-row -> frame-row join (the frame is unique
                  per (code, date); built host-side once)
      fired_days  action code -> set of (code, date_epoch_us) — the
                  DISTINCT FIRED DAYS per action (the fatigue
                  filter's counting basis; distinct dates, so a
                  crisis day firing several sub types counts once)
    """

    def __init__(self, frame: pd.DataFrame) -> None:
        codes = host_array(frame["code"])
        dates_us = host_array(frame["date"]).astype("int64")
        self.pos_index: dict[tuple[str, int], int] = {
            (str(codes[i]), int(dates_us[i])): i
            for i in range(len(codes))
        }
        self.fired_days: dict[int, set[tuple[str, int]]] = {a: set()
                                                            for a in
                                                            (0, 1)}


def fired_day_key(code: str, day) -> tuple[str, int]:
    """The (code, date_epoch_us) key of a fired row's python date."""
    return code, int(np.datetime64(day, "us").astype("int64"))


class NoActionRule(ABC):
    """One invalidation condition. Subclasses declare their scope
    (family / declined actions / sub types) and implement ``attach``
    (add the rule's CONTEXT_COLS to the frame — whole-frame grouped
    ops, in place) and ``matches`` (mark this fired row given its
    frame-row context values). The driver materializes the marked
    rows — rules never touch the write path."""

    REASON: ClassVar[str]
    #: the family label this rule scopes to (None = cross-family)
    FAMILY: ClassVar[str | None] = None
    #: the declined action LABELS ("buy"/"sell")
    ACTIONS: ClassVar[tuple[str, ...]] = ()
    #: the declined sub type LABELS (None = every sub type)
    SUB_TYPES: ClassVar[tuple[str, ...] | None] = None
    #: the frame columns ``attach`` adds (the driver extracts them
    #: host-side and hands their values to ``matches``)
    CONTEXT_COLS: ClassVar[tuple[str, ...]] = ()

    def action_codes(self) -> tuple[int, ...]:
        return tuple(ACTION_CODE[a] for a in self.ACTIONS)

    def sub_type_codes(self) -> tuple[int, ...] | None:
        if self.SUB_TYPES is None:
            return None
        return tuple(SIGNAL_SUB_TYPE_CODE[s] for s in self.SUB_TYPES)

    @abstractmethod
    def attach(self, frame: pd.DataFrame, ctx: NoActionCtx) -> None:
        """Add self.CONTEXT_COLS to ``frame`` in place (never remove
        or reorder rows; every column the rule adds starts with
        ``_na_`` and is dropped by the driver after evaluation)."""

    def matches(self, row: dict, ctxv: dict) -> bool:
        """The per-fired-row verdict: ``row`` is a positive fired
        history row (dict), ``ctxv`` maps this rule's CONTEXT_COLS to
        the fired day's frame-row values. The base implementation
        scopes on action/sub type and delegates the condition to
        ``condition``."""
        if row["action"] not in self.action_codes():
            return False
        subs = self.sub_type_codes()
        if subs is not None and row["signal_sub_type"] not in subs:
            return False
        return self.condition(row, ctxv)

    def condition(self, row: dict, ctxv: dict) -> bool:
        """The condition leg — override when the verdict needs the
        row (e.g. a per-sub-type context column); the default reads
        the rule's single boolean CONTEXT_COLS[0]."""
        return bool(ctxv.get(self.CONTEXT_COLS[0], False))

    def confidence_bp(self) -> int:
        """The rule's recorded negative confidence (bp) — a small
        DELIBERATE magnitude: the marker is the product, the
        magnitude a severity hint (config NO_ACTION_CONF_BP)."""
        return int(cfg.NO_ACTION_CONF_BP[self.REASON])

    def params(self) -> dict:
        """The rule_params fragment stamped on every emitted row:
        the reason vocabulary + the condition constants (the
        constant-rule convention — the UI renders them verbatim)."""
        return {"reason": self.REASON,
                "family": self.FAMILY,
                "actions": list(self.ACTIONS),
                "sub_types": list(self.SUB_TYPES or []),
                "condition": self.condition_params()}

    def condition_params(self) -> dict:
        return {}


def materialize(row: dict, rule: NoActionRule) -> dict:
    """One no_action_signals row: the fired row's own identity +
    values (overlay — the row re-states what fired), the declined
    action, the reason code, and the rule's negative confidence."""
    return {
        "code": row["code"], "sec_type": row["sec_type"],
        "signal_type": row["signal_type"],
        "signal_sub_type": row["signal_sub_type"],
        "action": row["action"],
        "reason_code": NO_ACTION_REASON_CODE[rule.REASON],
        "date": row["date"], "time": row["time"],
        "signal": row["signal"],
        "signal_reference": row["signal_reference"],
        "confidence": rule.confidence_bp(),
        "regime_state": row.get("regime_state"),
        "rule_params": rule.params(),
    }


def positive_fired_rows(history_rows: list[dict]) -> list[dict]:
    """The OVERLAY BASIS: the run's POSITIVE trading rows only —
    confidence >= 0 (the reject tier's negative rows are advice,
    not actions), real buy/sell actions (never the wait twin), the
    standalone tier (never the portfolio-sourced events)."""
    return [r for r in history_rows
            if r.get("confidence", 0) >= 0
            and r.get("action") in (ACTION_CODE["buy"],
                                    ACTION_CODE["sell"])
            and not r.get("is_in_portfolio", False)]


__all__ = ["NoActionCtx", "NoActionRule", "fired_day_key",
           "materialize", "positive_fired_rows"]
