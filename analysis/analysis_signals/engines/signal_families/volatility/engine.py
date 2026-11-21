"""volatility — the realized-volatility leverage-effect tail family
(analysis.analysis_signals.engines.signal_families.volatility.engine,
2026-11-18).

The analysis.volatility panel as rule cells. The research premise (the
user's "local top volatility signals local low price"): the LEVERAGE
EFFECT (Black 1976; French, Schwert & Stambaugh 1987, JF — negative
returns raise future volatility more than positive ones) puts realized-
vol PEAKS at price TROUGHS, and GARCH clustering + mean reversion
make a top-tail vol breach an EPISODE, not a regime — the same
capitulation-washout population the repo measured repeatedly (rf
floor_deep, rebound_capit, ovix panic_peak; the equity asymmetry:
sharp falls revert, tops continue).

  spike  BUY   the vol axis pierces ABOVE its own trailing top-q
               adaptive tail — the panic washout. GATED by the FEAR
               LEG: price below its 5-rows-ago close (the ovix
               panic/euphoria direction split — keeps upside euphoria
               like 2024-09-30 out of the buy population).
  crush  SELL  the vol axis falls below its own bottom-q adaptive
               tail while price sits within 2% of its trailing-60-row
               high — COMPLACENCY AT THE TOP, exit timing (a naked
               low-vol sell would fire through every calm uptrend;
               the low-volatility anomaly is a cross-sectional
               result, never a time-series sell; the sell gate 0 lets
               the per-cell evidence judge the exit).

Axes (config VOL_AXES, read from the panel — the bars compute
in-engine): hv20 = hv_20 (the canonical 20-row close-to-close
realized vol), ewma = ewma_vol (the RiskMetrics fast vol, λ=0.94 —
reacts in days where hv_20 needs the window to turn). The sub types
are the {axis}_{tail}pct grid over the LOCAL tail pcts {5,10,15,20}
with nested widest-first carving — a deep breach counts once, at its
deepest claim.

Bars are PER-CODE ADAPTIVE percentile quantiles over the trailing
YEAR (252 rows, min 60 — the panel's own pct_rank_252 convention —
SHIFTED one row; LOCAL, not all-history: the 2026-11-18 first build's
trailing-2520d top-{1,2,3}% tails were DEGENERATE on the panel's ~7y
history, only the 2024-10 mega-cluster ever breaching them — 000001
fired 4 events where the visible local spikes number ~two dozen;
user-directed fix, study temp_scripts/vol_spike_count_study.py). The
confidence depth is the breach in the axis's OWN trailing sigma (same
local window, shifted 1 — the momentum convention), factor 0.5.

REGIME-FREE (the ma_cross/macd/rf_break precedent): the vol tail
breach already carries the stress state — a regime split would
fragment the sparse tail populations onto their near-deterministic
regime corners. The TREND split stays (a fall-trend capitulation and
a rise-trend vol burst measure differently). Plain K=5 absorb
(trend-aware splitting is a measured A/B away, the bollinger/
momentum precedent). OHLCV boost OFF for v1. NOT live-wired (the
macd precedent).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from _common.df_utils import (
    grouped_rolling_quantile,
    grouped_rolling_agg,
    grouped_shift,
)

from analysis.analysis_signals import config as cfg
from analysis.analysis_signals.engines.rule_base import Candidate, RuleEntry
from analysis.trends import REENTRY_EXIT_DAYS
from analysis.analysis_signals.fetch.inputs import fetch_volatility_inputs


class VolatilityTailEntry(RuleEntry):
    """volatility — one entry, the (axis, local tail pct) grid with
    the two direction-gated actions: spike BUY (vol above its own
    trailing-year top tail + the fear leg) / crush SELL (vol below its
    bottom tail + price at its trailing high). The family's universe
    is data-driven (the codes with volatility rows at the sec_type —
    index; etf/stock resolve empty and skip)."""

    FAMILY = "volatility"
    PACKAGE = "volatility"
    SUB_TYPES = tuple(
        f"{prefix}_{p}pct"
        for prefix, _ in cfg.VOL_AXES for p in cfg.VOL_TAIL_PCTS
    )
    REGIME_FREE = True
    USES_TREND_SPLIT = True
    # plain K-absorb until the family's own trend-aware A/B is measured
    # (the bollinger/momentum/macd precedent)
    TREND_AWARE_REENTRY = False
    OHLCV_BOOST = False

    async def fetch_frame(self) -> pd.DataFrame:
        return await fetch_volatility_inputs(self.conn, self.sec_type,
                                             self.codes)

    def run(self, frame: pd.DataFrame) -> tuple[list[dict], list[dict]]:
        rules: list[dict] = []
        history: list[dict] = []
        if frame.empty:
            return rules, history
        # the direction gates' price legs (trigger-time observable —
        # backward windows only; the price is the response axis, the
        # vol columns the trigger)
        keys = ["code"]
        price = frame["price"].astype("float64")
        # the fear leg: price below its N-rows-ago close (the ovix
        # panic/euphoria direction split)
        price_leg = grouped_shift(
            frame, keys, ["price"], out_names=["_pl"],
            periods=cfg.VOL_SPIKE_LEG_WINDOW,
        )["_pl"]
        fear = (price < price_leg).fillna(False)
        # the crush sell's stretch context: within FRAC of the prior
        # W-row high (the window shifted 1 — the level being neared,
        # never a window including itself)
        prior_high = grouped_shift(
            frame.assign(_raw=grouped_rolling_agg(
                frame, keys, "price", cfg.VOL_CRUSH_HIGH_WINDOW,
                min_periods=cfg.VOL_CRUSH_HIGH_WINDOW, agg="max",
            )), keys, ["_raw"], out_names=["_ph"], periods=1,
        )["_ph"]
        stretched = (
            price >= prior_high * cfg.VOL_CRUSH_HIGH_FRAC
        ).fillna(False)

        for prefix, col in cfg.VOL_AXES:
            rules_a, history_a = self._run_axis(
                frame, prefix, col, fear, stretched,
            )
            rules.extend(rules_a)
            history.extend(history_a)
        return rules, history

    def _run_axis(
        self, frame: pd.DataFrame, prefix: str, col: str,
        fear: pd.Series, stretched: pd.Series,
    ) -> tuple[list[dict], list[dict]]:
        value = frame[col].astype("float64")
        keys = ["code"]

        # the per-code adaptive bars, deepest tail first; the trailing
        # quantile is SHIFTED one row (no look-ahead). BUY breaches the
        # TOP tail (the vol spike), SELL the BOTTOM tail (the vol
        # crush) — the sides are NOT mirror populations (the equity
        # asymmetry rides the direction gates, not the bars)
        bars: dict[int, tuple[pd.Series, pd.Series]] = {}
        for pct in sorted(cfg.VOL_TAIL_PCTS):
            q = pct / 100.0
            frame[f"_bar_t{pct}"] = grouped_shift(
                frame.assign(_raw=grouped_rolling_quantile(
                    frame, keys, col, cfg.VOL_BAR_WINDOW_DAYS,
                    cfg.VOL_BAR_MIN_PERIODS, 1.0 - q,
                )), keys, ["_raw"], out_names=["_s"], periods=1,
            )["_s"]
            frame[f"_bar_b{pct}"] = grouped_shift(
                frame.assign(_raw=grouped_rolling_quantile(
                    frame, keys, col, cfg.VOL_BAR_WINDOW_DAYS,
                    cfg.VOL_BAR_MIN_PERIODS, q,
                )), keys, ["_raw"], out_names=["_s"], periods=1,
            )["_s"]
            bars[pct] = (frame[f"_bar_t{pct}"], frame[f"_bar_b{pct}"])
        # the depth normalizer: the axis's OWN trailing sigma, shifted
        # one row (the momentum convention)
        sigma = grouped_shift(
            frame.assign(_raw=grouped_rolling_agg(
                frame, keys, col, cfg.VOL_BAR_WINDOW_DAYS,
                min_periods=cfg.VOL_BAR_MIN_PERIODS, agg="std",
            )), keys, ["_raw"], out_names=["_sd"], periods=1,
        )["_sd"]
        sigma = np.maximum(sigma, 1e-6)

        # nested widest-first carving: each tail claims only what the
        # DEEPER tail has not already claimed
        deeper_buy = pd.Series(False, index=frame.index, dtype="bool")
        deeper_sell = pd.Series(False, index=frame.index, dtype="bool")
        rules: list[dict] = []
        history: list[dict] = []
        for pct in sorted(cfg.VOL_TAIL_PCTS):
            bar_t, bar_b = bars[pct]
            buy_deep = (value > bar_t).fillna(False)
            sell_deep = (value < bar_b).fillna(False)
            sub = f"{prefix}_{pct}pct"
            common = {
                "k": REENTRY_EXIT_DAYS,
                "value_col": col,
                "extra_params": {
                    "axis": prefix,
                    "spike_leg_window": cfg.VOL_SPIKE_LEG_WINDOW,
                    "crush_high_window": cfg.VOL_CRUSH_HIGH_WINDOW,
                    "crush_high_frac": cfg.VOL_CRUSH_HIGH_FRAC,
                    "depth_unit": "axis_sigma",
                    "value_unit": "vol, % annualized",
                    "bar_def": {
                        "kind": "rolling_quantile",
                        "tail_pct": pct,
                        "window_days": cfg.VOL_BAR_WINDOW_DAYS,
                        "shift": 1,
                    },
                },
            }
            # spike BUY: the vol top-tail breach with the fear leg
            r_buy, h_buy = self.run_cells(
                frame, sub, "buy",
                [Candidate(
                    key=f"tail{pct}",
                    mask=buy_deep & ~deeper_buy & fear,
                    reference=bar_t,
                    depth=(value - bar_t) / sigma,
                )],
                depth_factor=0.5,
                **common,
            )
            # crush SELL: the vol bottom-tail breach at the price high
            r_sell, h_sell = self.run_cells(
                frame, sub, "sell",
                [Candidate(
                    key=f"tail{pct}",
                    mask=sell_deep & ~deeper_sell & stretched,
                    reference=bar_b,
                    depth=(bar_b - value) / sigma,
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
