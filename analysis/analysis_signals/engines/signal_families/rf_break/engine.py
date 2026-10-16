"""rf_break — the roof/floor breakout family engine (analysis.
analysis_signals.engines.signal_families.rf_break.engine, 2026-10-16).

The TURTLE breakout on this repo's own roof/floor machinery: the
trigger is the close crossing the PRIOR day's level from
analysis.mov_ave_spreads_roof_floor_estimates — per the (METHOD,
WINDOW) grid:

  method   donchian (1) — the window's max intraday high / min low;
           the raw structural extreme.
           median   (5) — the NaN-skipping median of the five estimate
           methods per (date, period): the cross-method CONSENSUS
           level (smoother than the raw extreme; catches breaks the
           lone donchian wick-extreme misses).
  window   {60, 120, 255, 500} trading days.

  rf{W}_roof      BUY  — the first close ABOVE the prior day's W-day
      donchian roof: the significant-high continuation (turtle
      System-1 style). The 2024-09-24/30/10-08 rally days are new
      multi-month-high closes by construction.
  rf{W}_floor     SELL — the first close BELOW the prior day's floor:
      the breakdown exit timing. The sell is EXIT TIMING for longs,
      never a short entry (the equity asymmetry).
  rf{W}_med_roof / rf{W}_med_floor — the same sides on the median
      consensus level.

NO cross-window carving: each (method, window) breach fires its own
sub type — the windows are different CLAIM STRENGTHS (a new 60d high
and the simultaneous new 255d high are different facts), and the
first-build A/B measured the carve-out variant STARVING the mid
windows (the 255d cells lost their strongest days to the 500d claim
and fell below the gate floor — the 2024-09-30 events vanished with
them), so every window keeps its own full population and the gate
decides. The trigger day's own level never sets its own bar (the
level is shifted one row on the code's own rows). REGIME-FREE with a
trend split (the ma_cross precedent — per-regime cells measured thin
and sign-flippy, and the studies' starving corners were regime-corner
artifacts). The ohlcv boost opts in (the turnover-climax axis widens
the trust-weighted population).
"""
from __future__ import annotations

import pandas as pd

from _common.df_utils import grouped_shift

from analysis.analysis_signals import config as cfg
from analysis.analysis_signals.engines.rule_base import Candidate, RuleEntry
from analysis.trends import REENTRY_EXIT_DAYS
from analysis.analysis_signals.fetch.inputs import fetch_rf_inputs

# the breach depth normalizer: 2% beyond the level = depth 1.0 (the
# confidence scale's unit, capped at 2)
_RF_DEPTH_UNIT = 0.02

# the estimates-table level prefix per config method infix (donchian
# reads roof{W}/floor{W} columns, median reads mroof{W}/mfloor{W})
_METHOD_PREFIX = {"": "", "_med": "m"}


class RoofFloorEntry(RuleEntry):
    """roof/floor breakout — one entry, the (method, window, side)
    grid."""

    FAMILY = "rf_break"
    PACKAGE = "rf_break"
    SUB_TYPES = tuple(
        f"rf{w}{infix}_{side}"
        for infix, _ in cfg.RF_BREAK_METHODS
        for side in ("roof", "floor")
        for w in cfg.RF_BREAK_WINDOWS
    )
    REGIME_FREE = True
    USES_TREND_SPLIT = True
    OHLCV_BOOST = True

    async def fetch_frame(self) -> pd.DataFrame:
        return await fetch_rf_inputs(self.conn, self.sec_type, self.codes)

    def run(self, frame: pd.DataFrame) -> tuple[list[dict], list[dict]]:
        rules: list[dict] = []
        history: list[dict] = []
        if frame.empty:
            return rules, history
        keys = ["code"]
        for infix, method_code in cfg.RF_BREAK_METHODS:
            p = _METHOD_PREFIX[infix]
            method_label = "donchian" if infix == "" else "median"
            for side, action in (("roof", "buy"), ("floor", "sell")):
                # the breach width per window: today's close vs the
                # PRIOR day's level (the trigger day's own level never
                # sets its own bar) — one shifted column pass per
                # window
                breach: dict[int, pd.Series] = {}
                for w in cfg.RF_BREAK_WINDOWS:
                    level = frame[f"{p}{side}{w}"].astype("float64")
                    prev_level = grouped_shift(
                        frame.assign(_l=level), keys, ["_l"],
                        out_names=["_pl"], periods=1,
                    )["_pl"]
                    breach[w] = (
                        frame["price"] / prev_level - 1.0) * 100.0
                # NO cross-window carving — every window's breach is
                # that sub type's own population (the carve-out
                # variant starved the mid windows; see the docstring)
                for w in cfg.RF_BREAK_WINDOWS:
                    br = breach[w]
                    mask = (br > 0.0).fillna(False)
                    col = f"_rf{p}{w}_{side}_pct"
                    frame[col] = br
                    r, h = self.run_cells(
                        frame, f"rf{w}{infix}_{side}", action,
                        [Candidate(
                            key=f"{p}{side}{w}",
                            mask=mask,
                            reference=0.0,
                            depth=(br / _RF_DEPTH_UNIT)
                            .clip(lower=0.0),
                            params={
                                "kind": f"{side}_break", "window": w,
                                "method": method_label,
                                "bar_def": {
                                    "kind": "prior_day_level",
                                    "level": f"{p}{side}{w}",
                                    "method": method_label,
                                    "method_code": method_code,
                                    "source": "analysis.mov_ave_spreads_"
                                             "roof_floor_estimates",
                                    "shift": 1,
                                },
                            },
                        )],
                        k=REENTRY_EXIT_DAYS, value_col=col,
                        depth_factor=0.5,
                        extra_params={
                            "side_policy": (
                                "buy_only" if side == "roof"
                                else "exit_timing"),
                            "value_unit":
                                "pct beyond the prior day's level",
                        },
                    )
                    rules.extend(r)
                    history.extend(h)
        return rules, history
