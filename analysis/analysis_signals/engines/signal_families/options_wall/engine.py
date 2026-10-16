"""options_wall — the VRP-gated OI-wall family (analysis.
analysis_signals.engines.signal_families.options_wall.engine).

"Dominant OI walls repel spot": the chain-dominant wall per side from
analysis.options_walls (call_wall_high the outer cap, put_wall_low the
outer edge); a close MEANINGFULLY past a wall is a reversion trade
back toward it.

  put breach  (buy)  — close <= put_wall_low x (1 − 0.25%),
      VRP-GATED: implied vol30 must sit in its trailing top quintile
      vs realized (the variance-risk-premium confirmation — a breach
      with RICH IV is the capitulation rebound; with cheap IV it is
      the falling knife). The gate is realized as
      vrp_ratio >= its own trailing-255d 80th-percentile bar (shifted
      — no look-ahead); no per-row rank needed.
  _rej twin   (wait) — the same breach with the VRP gate FAILED:
      observational only, never in a trading set.
  call breach (sell) — close >= call_wall_high x (1 + 0.25%), ungated
      (the sell is exit timing for longs).

Sparse event population: POOLED (trend 4 / regime 4 — per-code
occurrence is 1-6/yr, per-axis splits starve below the floors);
strict consecutive-day episodes (K=0 — any day back inside the edge
starts a fresh episode).
"""
from __future__ import annotations

import pandas as pd

from _common.df_utils import (
    grouped_rolling_agg,
    grouped_rolling_quantile,
    grouped_shift,
)

from analysis.analysis_signals import config as cfg
from analysis.analysis_signals.engines.rule_base import Candidate, RuleEntry
from analysis.analysis_signals.fetch.inputs import (
    fetch_options_wall_inputs,
)

_TRADING_DAYS = 252.0


class WallBreachEntry(RuleEntry):
    """The OI-wall breach rule — one entry, the sides are the sub signal
    types (ow_put_025pct / ow_put_025pct_rej / ow_call_025pct)."""

    FAMILY = "options_wall"
    PACKAGE = "options_wall"
    SUB_TYPES = ("ow_put_025pct", "ow_put_025pct_rej", "ow_call_025pct")
    REGIME_FREE = True
    USES_TREND_SPLIT = False  # pooled: trend 4 (the no-trend-claim bucket)

    async def fetch_frame(self) -> pd.DataFrame:
        return await fetch_options_wall_inputs(self.conn, self.codes)

    def run(self, frame: pd.DataFrame) -> tuple[list[dict], list[dict]]:
        rules: list[dict] = []
        history: list[dict] = []
        if frame.empty:
            return rules, history
        keys = ["code"]

        # the VRP gate: implied (vol30) rich vs realized (rolling 20d
        # spot-return σ, annualized) — the ratio beyond its own trailing
        # 80th percentile bar, the bar SHIFTED one row (no look-ahead)
        ret = frame["price"] / grouped_shift(
            frame, keys, ["price"], out_names=["_p"], periods=1,
        )["_p"] - 1.0
        realized = grouped_rolling_agg(
            frame.assign(_ret=ret), keys, "_ret",
            cfg.OW_VRP_REALIZED_WINDOW, min_periods=cfg.OW_VRP_REALIZED_WINDOW,
            agg="std", ddof=0,
        ) * (_TRADING_DAYS ** 0.5) * 100.0
        frame["vrp_ratio"] = frame["vol30"] / realized
        vrp_bar = grouped_shift(
            frame.assign(_raw=grouped_rolling_quantile(
                frame, keys, "vrp_ratio", cfg.OW_VRP_PCTL_WINDOW,
                cfg.OW_VRP_PCTL_WINDOW, cfg.OW_VRP_PCTL_MIN,
            )), keys, ["_raw"], out_names=["_s"], periods=1,
        )["_s"]
        vrp_gate = (frame["vrp_ratio"] >= vrp_bar).fillna(False)

        frame["put_dist"] = (frame["price"] / frame["put_wall_low"]
                             - 1.0) * 100.0
        frame["call_dist"] = (frame["price"] / frame["call_wall_high"]
                              - 1.0) * 100.0
        put_breach = (frame["put_wall_low"].notna()
                      & (frame["put_dist"] <= -cfg.OW_BREACH_WIDTH_PCT))
        call_breach = (frame["call_wall_high"].notna()
                       & (frame["call_dist"] >= cfg.OW_BREACH_WIDTH_PCT))

        common = {
            "k": cfg.OW_REENTRY_EXIT_DAYS,
            "depth_factor": 0.0,  # confidence FLAT — the sparse family's scale
            "extra_params": {
                "walls": "analysis.options_walls zone, dominant per side",
                "vrp_gate": {
                    "kind": "trailing_pctl_bar",
                    "pctl": cfg.OW_VRP_PCTL_MIN,
                    "window_days": cfg.OW_VRP_PCTL_WINDOW,
                    "realized_window": cfg.OW_VRP_REALIZED_WINDOW,
                },
            },
        }
        r_buy, h_buy = self.run_cells(
            frame, "ow_put_025pct", "buy",
            [Candidate(
                key="put_breach_vrp",
                mask=put_breach & vrp_gate,
                reference=-cfg.OW_BREACH_WIDTH_PCT,
                depth=(-frame["put_dist"] - cfg.OW_BREACH_WIDTH_PCT) / 1.0,
                params={"kind": "wall_breach", "side": "put",
                        "bar_def": {"kind": "wall_dist_pct",
                                    "below": -cfg.OW_BREACH_WIDTH_PCT}},
            )],
            value_col="put_dist", **common,
        )
        # the VRP-fail twin: observational, never a trading row
        r_rej, h_rej = self.run_cells(
            frame, "ow_put_025pct_rej", "wait",
            [Candidate(
                key="put_breach_vrp_fail",
                mask=put_breach & ~vrp_gate,
                reference=-cfg.OW_BREACH_WIDTH_PCT,
                depth=(-frame["put_dist"] - cfg.OW_BREACH_WIDTH_PCT) / 1.0,
                params={"kind": "wall_breach", "side": "put",
                        "bar_def": {"kind": "wall_dist_pct",
                                    "below": -cfg.OW_BREACH_WIDTH_PCT},
                        "vrp_gate_failed": True},
            )],
            value_col="put_dist", ungated=True, **common,
        )
        r_sell, h_sell = self.run_cells(
            frame, "ow_call_025pct", "sell",
            [Candidate(
                key="call_breach",
                mask=call_breach,
                reference=cfg.OW_BREACH_WIDTH_PCT,
                depth=(frame["call_dist"] - cfg.OW_BREACH_WIDTH_PCT) / 1.0,
                params={"kind": "wall_breach", "side": "call",
                        "bar_def": {"kind": "wall_dist_pct",
                                    "above": cfg.OW_BREACH_WIDTH_PCT}},
            )],
            value_col="call_dist", **common,
        )
        rules.extend(r_buy + r_rej + r_sell)
        history.extend(h_buy + h_rej + h_sell)
        return rules, history
