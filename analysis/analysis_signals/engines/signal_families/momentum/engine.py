"""momentum — the log-return momentum family (analysis.analysis_signals.
engines.signal_families.momentum.engine).

A BUY fires when a lookback axis's LOG-RETURN ln(1 + momentum) falls
below the code's OWN bottom tail quantile over its trailing 10y (the
capitulation crash — deep negative cumulative momentum reverts); a SELL
when it exceeds the top tail (the exhaustion run-up — exit timing for
longs). The family is self-adaptive at TWO levels:

  - the LOOKBACKS are vol-adaptive upstream (analysis.mov_ave_momentum
    picks short/mid/long n per (code, regime) by equal-expected-move
    targeting), with the fixed 5/20d anchors alongside;
  - the BARS are per-code trailing percentile quantiles of the
    axis's own log-return — the multi-level threshold grid is the
    nested tail carving 3% > 2% > 1% (a deep breach counts once, at
    its deepest claim).

The log transform is the self-adaptivity enabler: raw ROC is bounded
below (-100%) and unbounded above, so percentile tails in raw space
are dominated by that asymmetry; ln(1 + momentum) is the symmetric
additive space where a trailing quantile bar tracks the code's own
volatility level, comparable across codes and regimes. The confidence
depth is likewise normalized by the axis's OWN trailing sigma (a 5d
and a 100d breach compare in sigma units).

Regime + trend splits stay (the mean-reversion premise is the mov_rsi
one — reversal after tail breaches concentrates in hot/panic; the same
tail measures asymmetric populations in different trends). The
re-entry absorb is the PLAIN K=5 (trend-aware splitting is a measured
A/B away, the boll_std precedent)."""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from _common.df_utils import grouped_rolling_agg, grouped_rolling_quantile, grouped_shift

from analysis.analysis_signals import config as cfg
from analysis.analysis_signals.engines.rule_base import Candidate, RuleEntry
from analysis.analysis_signals.fetch.inputs import fetch_momentum_inputs
from analysis.trends import REENTRY_EXIT_DAYS

# sub_type prefix per axis (momentum_5days -> mom5, momentum_short ->
# momshort) — the label_dim signal_sub_type vocabulary
_AXIS_PREFIX = {"5days": "mom5", "20days": "mom20", "short": "momshort",
                "mid": "mommid", "long": "momlong"}


class MomentumTailEntry(RuleEntry):
    """The momentum log-return tail-episode rule — one entry, (axis,
    tail pct) are the sub signal types (mom5_1pct .. momlong_3pct over
    MOMENTUM_AXES x MOMENTUM_TAIL_PCTS)."""

    FAMILY = "momentum"
    PACKAGE = "momentum"
    SUB_TYPES = tuple(
        f"{_AXIS_PREFIX[axis]}_{p}pct"
        for axis in cfg.MOMENTUM_AXES for p in cfg.MOMENTUM_TAIL_PCTS
    )
    REGIME_FREE = False
    USES_TREND_SPLIT = True
    # plain K-absorb until the family's own trend-aware A/B is measured
    # (the boll_std precedent)
    TREND_AWARE_REENTRY = False
    # the trust-score pass opts in (2026-10-16): the day-0 anatomy now
    # carries the turnover-climax grade — widening the boosted
    # population (NEVER a gate; failing sides boost nothing)
    OHLCV_BOOST = True

    async def fetch_frame(self) -> pd.DataFrame:
        return await fetch_momentum_inputs(
            self.conn, self.sec_type, self.codes, since=date(1990, 1, 1),
        )

    def run(self, frame: pd.DataFrame) -> tuple[list[dict], list[dict]]:
        rules: list[dict] = []
        history: list[dict] = []
        if frame.empty:
            return rules, history
        for axis in cfg.MOMENTUM_AXES:
            rules_a, history_a = self._run_axis(frame, axis)
            rules.extend(rules_a)
            history.extend(history_a)
        return rules, history

    def _run_axis(self, frame: pd.DataFrame, axis: str,
                  ) -> tuple[list[dict], list[dict]]:
        col = f"logret_{axis}"
        logret = frame[col].astype("float64")

        keys = ["code"]
        # the per-code adaptive bars + the sigma depth normalizer,
        # deepest tail first; the trailing stats are SHIFTED one row
        # (no look-ahead)
        bars: dict[int, tuple[pd.Series, pd.Series]] = {}
        for pct in sorted(cfg.MOMENTUM_TAIL_PCTS):
            q = pct / 100.0
            frame[f"_bar_b{pct}"] = grouped_shift(
                frame.assign(_raw=grouped_rolling_quantile(
                    frame, keys, col, cfg.MOMENTUM_BAR_WINDOW_DAYS,
                    cfg.MOMENTUM_BAR_MIN_PERIODS, q,
                )), keys, ["_raw"], out_names=["_s"], periods=1,
            )["_s"]
            frame[f"_bar_s{pct}"] = grouped_shift(
                frame.assign(_raw=grouped_rolling_quantile(
                    frame, keys, col, cfg.MOMENTUM_BAR_WINDOW_DAYS,
                    cfg.MOMENTUM_BAR_MIN_PERIODS, 1.0 - q,
                )), keys, ["_raw"], out_names=["_s"], periods=1,
            )["_s"]
            bars[pct] = (frame[f"_bar_b{pct}"], frame[f"_bar_s{pct}"])
        # the axis's own trailing sigma — the depth normalizer that
        # makes a 5d and a 100d breach comparable (self-adaptive
        # across axes and codes)
        sd = grouped_shift(
            frame.assign(_raw=grouped_rolling_agg(
                frame, keys, col, cfg.MOMENTUM_BAR_WINDOW_DAYS,
                min_periods=cfg.MOMENTUM_BAR_MIN_PERIODS, agg="std",
            )), keys, ["_raw"], out_names=["_s"], periods=1,
        )["_s"]
        sd = np.maximum(sd, 1e-6)

        # nested widest-first carving: each tail claims only what the
        # DEEPER tail has not already claimed
        deeper_buy = pd.Series(False, index=frame.index, dtype="bool")
        deeper_sell = pd.Series(False, index=frame.index, dtype="bool")
        rules: list[dict] = []
        history: list[dict] = []
        for pct in sorted(cfg.MOMENTUM_TAIL_PCTS):
            bar_b, bar_s = bars[pct]
            buy_deep = (logret < bar_b).fillna(False)
            sell_deep = (logret > bar_s).fillna(False)
            sub = f"{_AXIS_PREFIX[axis]}_{pct}pct"
            common = {
                "k": REENTRY_EXIT_DAYS,
                "value_col": col,
                "extra_params": {
                    "axis": axis,
                    "bar_def": {
                        "kind": "rolling_quantile",
                        "value": "logret",
                        "tail_pct": pct,
                        "window_days": cfg.MOMENTUM_BAR_WINDOW_DAYS,
                        "shift": 1,
                    },
                    "depth_unit": "axis_sigma",
                },
            }
            r_buy, h_buy = self.run_cells(
                frame, sub, "buy",
                [Candidate(
                    key=f"tail{pct}",
                    mask=buy_deep & ~deeper_buy,
                    reference=bar_b,
                    depth=(bar_b - logret) / sd,
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
                    depth=(logret - bar_s) / sd,
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
