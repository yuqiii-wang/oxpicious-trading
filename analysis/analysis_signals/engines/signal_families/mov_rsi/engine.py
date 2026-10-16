"""mov_rsi — the RSI percentile family (analysis.analysis_signals.
engines.signal_families.mov_rsi.engine).

A BUY fires when Wilder RSI(W) falls below the code's OWN bottom tail
quantile over its trailing 10y (the capitulation); a SELL when it
exceeds the top tail (the exhaustion exit). The entry grid is
config.RSI_ENTRY_WINDOWS — 14 the Wilder classic + 10 the measured
best buy window (the 2026-10-15 window study). Bars are PER-CODE
ADAPTIVE percentile quantiles — never fixed 30/70 (a fixed level
either never triggers or triggers constantly). The tail pcts are the
nested carving grid: a deep breach counts once, at its DEEPEST claim
(the 1% tail carves out of 2%, 2% out of 3%).

Regime splits stay (the mean reversion concentrates in hot/panic);
trend splits added by the redesign (the same tail measures asymmetric
populations in different trends). The re-entry absorb is TREND-AWARE
here (2026-10-15): the K=5 window merges only SAME-trend re-entries —
a re-entry into a different trend is a FRESH episode, measured in the
trend it re-entered in.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from _common.df_utils import grouped_rolling_quantile, grouped_shift

from analysis.analysis_signals import config as cfg
from analysis.analysis_signals.engines.rule_base import Candidate, RuleEntry
from analysis.analysis_signals.fetch.inputs import fetch_rsi_inputs
from analysis.trends import REENTRY_EXIT_DAYS

_DEPTH_SCALE = 10.0  # RSI points per unit of confidence depth (0..2 cap)


class RsiTailEntry(RuleEntry):
    """The RSI tail-episode rule — one entry, (window, tail pct) are
    the sub signal types (rsi14_1pct .. rsi10_3pct over
    RSI_ENTRY_WINDOWS × RSI_TAIL_PCTS). The family the ohlcv boost
    opted in FIRST (2026-10-15): the day-0 anatomy trust score rides
    the fetched axes and re-derives its coefficients on this run's own
    anchor population (see engines/boost.OhlcvBoost)."""

    FAMILY = "mov_rsi"
    PACKAGE = "mov_rsi"
    SUB_TYPES = tuple(
        f"rsi{w}_{p}pct"
        for w in cfg.RSI_ENTRY_WINDOWS for p in cfg.RSI_TAIL_PCTS
    )
    REGIME_FREE = False
    USES_TREND_SPLIT = True
    # the K-absorb merges only SAME-trend re-entries (2026-10-15) — a
    # re-entry into a different trend is a fresh episode in that
    # trend's cell
    TREND_AWARE_REENTRY = True
    OHLCV_BOOST = True

    async def fetch_frame(self) -> pd.DataFrame:
        return await fetch_rsi_inputs(
            self.conn, self.sec_type, self.codes, since=date(1990, 1, 1),
        )

    def run(self, frame: pd.DataFrame) -> tuple[list[dict], list[dict]]:
        rules: list[dict] = []
        history: list[dict] = []
        if frame.empty:
            return rules, history
        for w in cfg.RSI_ENTRY_WINDOWS:
            rules_w, history_w = self._run_window(frame, w)
            rules.extend(rules_w)
            history.extend(history_w)
        return rules, history

    def _run_window(self, frame: pd.DataFrame, w: int,
                    ) -> tuple[list[dict], list[dict]]:
        col = f"rsi_{w}days"
        rsi = frame[col].astype("float64")

        # the per-code adaptive bars, deepest tail first; the trailing
        # quantile is SHIFTED one row (no look-ahead)
        keys = ["code"]
        bars: dict[int, tuple[pd.Series, pd.Series]] = {}
        for pct in sorted(cfg.RSI_TAIL_PCTS):
            q = pct / 100.0
            frame[f"_bar_b{pct}"] = grouped_shift(
                frame.assign(_raw=grouped_rolling_quantile(
                    frame, keys, col, cfg.RSI_BAR_WINDOW_DAYS,
                    cfg.RSI_BAR_MIN_PERIODS, q,
                )), keys, ["_raw"], out_names=["_s"], periods=1,
            )["_s"]
            frame[f"_bar_s{pct}"] = grouped_shift(
                frame.assign(_raw=grouped_rolling_quantile(
                    frame, keys, col, cfg.RSI_BAR_WINDOW_DAYS,
                    cfg.RSI_BAR_MIN_PERIODS, 1.0 - q,
                )), keys, ["_raw"], out_names=["_s"], periods=1,
            )["_s"]
            bars[pct] = (frame[f"_bar_b{pct}"], frame[f"_bar_s{pct}"])

        # nested widest-first carving: each tail claims only what the
        # DEEPER tail has not already claimed
        deeper_buy = pd.Series(False, index=frame.index, dtype="bool")
        deeper_sell = pd.Series(False, index=frame.index, dtype="bool")
        rules: list[dict] = []
        history: list[dict] = []
        for pct in sorted(cfg.RSI_TAIL_PCTS):
            bar_b, bar_s = bars[pct]
            buy_deep = (rsi < bar_b).fillna(False)
            sell_deep = (rsi > bar_s).fillna(False)
            sub = f"rsi{w}_{pct}pct"
            common = {
                "k": REENTRY_EXIT_DAYS,
                "value_col": col,
                "is_typical": frame[f"is_typical_{w}days"],
                "extra_params": {
                    "window": w,
                    "bar_def": {
                        "kind": "rolling_quantile",
                        "tail_pct": pct,
                        "window_days": cfg.RSI_BAR_WINDOW_DAYS,
                        "shift": 1,
                    },
                },
            }
            r_buy, h_buy = self.run_cells(
                frame, sub, "buy",
                [Candidate(
                    key=f"tail{pct}",
                    mask=buy_deep & ~deeper_buy,
                    reference=bar_b,
                    depth=(bar_b - rsi) / _DEPTH_SCALE,
                )],
                depth_factor=0.5,
                **common,
            )
            r_sell, h_sell = self.run_cells(
                frame, sub, "sell",
                [Candidate(
                    key=f"tail{pct}",
                    mask=sell_deep & ~deeper_sell,
                    reference=bar_s,
                    depth=(rsi - bar_s) / _DEPTH_SCALE,
                )],
                depth_factor=0.5,
                **common,
            )
            rules.extend(r_buy)
            rules.extend(r_sell)
            history.extend(h_buy)
            history.extend(h_sell)
            deeper_buy = deeper_buy | buy_deep
            deeper_sell = deeper_sell | sell_deep
        return rules, history
