"""The registered standalone cells' run — the sourced mov_rsi layer:
one cell per component curve per sub type per side (pooled both
axes), each response axis the curve's OWN price forward blend. The
method is MERGED INTO the signal base class: StandaloneCellsMixin is
composed into HedgePairTrioEntry (via standalone.StandaloneSub)."""
from __future__ import annotations

import logging

import pandas as pd

from _common.labels import SIGNAL_TYPE_CODE

from analysis.analysis_signals import config as cfg
from analysis.analysis_signals.engines.rule_base import Candidate, RuleEntry
from analysis.analysis_signals.fetch.inputs._hedge_pairs import bar_suffix

from analysis.analysis_signals.engines.signal_families.hedge_pairs._commons.constants import (
    DEPTH_SCALE,
    PAIR_K,
    SOURCED_LEG_SUBS,
    SOURCED_MARKET_SUBS,
    WEIGHTING,
)

logger = logging.getLogger(__name__)


class StandaloneCellsMixin(RuleEntry):
    """The registered standalone cells' method. A partial
    HedgePairTrioEntry — never instantiated alone."""

    def _register_standalone(self) -> tuple[list[dict], list[dict]]:
        """The REGISTERED STANDALONE CELLS (2026-10-25, user-directed
        "the signals must register and retrieve under signal rules,
        not directly from analysis, so make sure register for is in
        portfolio signals"): the mov_rsi family's own window x tail
        vocabulary measured on the COMPONENT curves (the market +
        both legs — one cell per curve per sub type per side, pooled
        both axes) and registered as REAL mov_rsi rows with
        is_in_portfolio TRUE (the sourced layer's own tier). The
        portfolio's signal_rule_ids carry them (the shared market
        cells ride every pair's array, a leg cell its own pair's) and
        the expanded panel retrieves them from signal_rules — the
        response axis each curve's OWN price (its forward blend the
        mov_rsi contract)."""
        s_cells: list[dict] = []
        s_events: list[dict] = []
        comp = self._comp
        if comp is None or comp.empty:
            return s_cells, s_events
        self._attach_forward_for(comp, "price")
        for w in cfg.HP_STANDALONE_RSI_WINDOWS:
            suf = bar_suffix(w)
            rsi = comp[f"rsi_{w}days"]
            for p in cfg.HP_STANDALONE_TAIL_PCTS:
                sub = f"rsi{w}_{p}pct"
                for action, bar_col in (("buy", f"_bar_b{p}{suf}"),
                                        ("sell", f"_bar_s{p}{suf}")):
                    bar = comp[bar_col]
                    cand = Candidate(
                        key=sub,
                        mask=(rsi < bar) if action == "buy"
                        else (rsi > bar),
                        reference=bar,
                        depth=(bar - rsi) * (1.0 if action == "buy"
                                             else -1.0) / DEPTH_SCALE,
                        value_col=f"rsi_{w}days",
                        # the STORED RULE CONDITION line (the UI's
                        # Rule condition column renders it
                        # verbatim — the same engine-written
                        # convention as the trio's subs spec) +
                        # the row's RSI WINDOW (the UI maps the
                        # registered cells under their
                        # corresponding cell-signal groups)
                        params={"condition": (
                            f"RSI({w}) < own bottom {p}% tail"
                            if action == "buy" else
                            f"RSI({w}) > own top {p}% tail"),
                            "window": w,
                            "bar_def": {
                            "kind": "rolling_quantile",
                            "window_days":
                                cfg.HP_LEG_BAR_WINDOW_DAYS,
                            "tail_pct": p,
                            "shift": 1,
                        }},
                    )
                    r, h = self.run_cells(
                        comp, sub, action, [cand], k=PAIR_K,
                        value_col=f"rsi_{w}days", depth_factor=0.5,
                        signal_type_code=SIGNAL_TYPE_CODE["mov_rsi"],
                        portfolio_tier=True,
                        extra_params={
                            "portfolio_role": "component",
                            "pair_weighting": WEIGHTING,
                        },
                    )
                    s_cells += r
                    s_events += h
        # the sourced-member markers: which portfolio rules MAP each
        # registered cell through their signal_rule_ids (the cells
        # themselves are never portfolio rows) — the market cells
        # ride every pair's trio_wash rule, a leg cell its own
        # pair's rules that witness the legs (both today)
        for row in s_cells:
            row["_sourced_member"] = (
                {"market": True, "subs": list(SOURCED_MARKET_SUBS)}
                if row["code"] == cfg.HP_MARKET_CODE
                else {"leg": row["code"],
                      "subs": list(SOURCED_LEG_SUBS)})
        logger.info(f"[hedge_pairs] registered standalone cells: "
                    f"{len(s_cells)} mov_rsi rows (is_in_portfolio), "
                    f"{len(s_events)} events")
        return s_cells, s_events
