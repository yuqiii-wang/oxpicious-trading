"""macd — the classic MACD crossover family engine (analysis.
analysis_signals.engines.signal_families.macd.engine, 2026-10-26).

The typical-EMA study's product (user-directed "study what most typical
ema is, eg used 6d 12d 26d, etc, and implement it to replace the nearby
ema period if any" + "2nd task is macd signal in short term tech"): the
most typical EMA usage is the classic MACD set 12/26/9 (Gerald Appel's
original; the industry default on every charting platform), so the
tech_stats tables' nearby non-standard span 10 became ema12 + ema26 the
same day — and this family ships the classic MACD SIGNAL as a rule
cell: the signal-line CROSSOVER.

  macd_cross_up  BUY   the MACD line crosses ABOVE its EMA9 signal
                       line — the momentum turn. One episode per
                       cross run (K-absorbed: a re-cross within K=5
                       traded days is the same episode, the whipsaw
                       merge — the first day fired, delay 0).
  macd_cross_dn  SELL  the bearish crossover — EXIT TIMING for longs,
                       never a short entry (the equity asymmetry:
                       tops continue, the sell gate is 0).

Value source: the fetch reads the builds' PRECOMPUTED typical EMAs
(stats.{etf,index,stock}_tech_stats.ema12 / ema26 — LEFT JOINed; the
strategy-tier MacdAlgo's source verbatim, ONE EMA construction in the
repo) and assembles macd_line = ema12 - ema26, macd_signal = the EMA9
OF THE MACD LINE (inline — no precomputed column exists for an EMA of
the MACD line), macd_hist = the difference. The recorded signal value
is the histogram as a PCT OF CLOSE (the strategy MACD algo's
cross-security normalization — a 5000-point index and a 5-yuan ETF
compare); the reference is the cross line 0.0, and the confidence
depth is the cross day's |histogram| in its OWN trailing sigma (the
momentum depth convention — trailing 10y, one-row-shifted, no
look-ahead): a decisive cross carries more confidence than a marginal
one, capped at 2.

REGIME-FREE (the ma_cross precedent — cross-event families pool the
regime axis, per-regime cells measured thin and sign-flippy); the
TREND split stays (the momentum-turn population measures differently
per trend). Plain K-absorb (not trend-aware — the boll_std/momentum
precedent until the family's own A/B). OHLCV boost OFF for v1 (the
opt-in is a measured A/B away, the momentum 2026-10-16 precedent).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from _common.df_utils import grouped_rolling_agg, grouped_shift

from analysis.analysis_signals import config as cfg
from analysis.analysis_signals.engines.rule_base import Candidate, RuleEntry
from analysis.trends import REENTRY_EXIT_DAYS
from analysis.analysis_signals.fetch.inputs import fetch_macd_inputs


class MacdCrossEntry(RuleEntry):
    """macd — one entry, the two classic signal-line crossover sub
    types (macd_cross_up BUY / macd_cross_dn SELL) over the classic
    12/26/9 spans."""

    FAMILY = "macd"
    PACKAGE = "macd"
    SUB_TYPES = ("macd_cross_up", "macd_cross_dn")
    REGIME_FREE = True
    USES_TREND_SPLIT = True
    TREND_AWARE_REENTRY = False
    OHLCV_BOOST = False

    async def fetch_frame(self) -> pd.DataFrame:
        return await fetch_macd_inputs(self.conn, self.sec_type,
                                       self.codes)

    def run(self, frame: pd.DataFrame) -> tuple[list[dict], list[dict]]:
        rules: list[dict] = []
        history: list[dict] = []
        if frame.empty:
            return rules, history
        keys = ["code"]
        macd = frame["macd_line"].astype("float64")
        sig = frame["macd_signal"].astype("float64")
        prev_macd = grouped_shift(
            frame.assign(_m=macd), keys, ["_m"], out_names=["_pm"],
            periods=1,
        )["_pm"]
        prev_sig = grouped_shift(
            frame.assign(_s=sig), keys, ["_s"], out_names=["_ps"],
            periods=1,
        )["_ps"]

        # the recorded value axis: histogram as pct of close (the
        # strategy MACD algo's cross-security normalization)
        hist_pct = frame["macd_hist"].astype("float64") \
            / frame["price"].astype("float64") * 100.0
        frame["macd_hist_pct"] = hist_pct
        # the depth normalizer: the axis's OWN trailing sigma, shifted
        # one row (no look-ahead) — the momentum convention
        sd = grouped_shift(
            frame.assign(_raw=grouped_rolling_agg(
                frame, keys, "macd_hist_pct", cfg.MACD_SIGMA_WINDOW,
                min_periods=cfg.MACD_SIGMA_MIN_PERIODS, agg="std",
            )), keys, ["_raw"], out_names=["_sd"], periods=1,
        )["_sd"]
        sd = np.maximum(sd, 1e-6)
        depth = (hist_pct.abs() / sd).fillna(0.0)

        # the classic crossover edges (the strategy MACD algo's cross
        # detection): first bar has no prior -> no cross (NaN -> False
        # after the fill-BEFORE-compare convention)
        cross_up = ((macd > sig).fillna(False)
                    & (prev_macd <= prev_sig).fillna(False))
        cross_dn = ((macd < sig).fillna(False)
                    & (prev_macd >= prev_sig).fillna(False))

        bar_def = {
            "kind": "macd_cross",
            "ema_short": cfg.MACD_EMA_SHORT,
            "ema_long": cfg.MACD_EMA_LONG,
            "ema_signal": cfg.MACD_EMA_SIGNAL,
        }
        common = {
            "k": REENTRY_EXIT_DAYS,
            "value_col": "macd_hist_pct",
            "extra_params": {
                "bar_def": dict(bar_def),
                "depth_unit": "hist_sigma",
                "value_unit": "histogram, pct of close",
            },
        }
        r_b, h_b = self.run_cells(
            frame, "macd_cross_up", "buy",
            [Candidate(
                key="cross_up",
                mask=cross_up,
                reference=0.0,
                depth=depth,
                params={"kind": "macd_cross", "direction": "up"},
            )],
            depth_factor=0.5, **common,
        )
        r_s, h_s = self.run_cells(
            frame, "macd_cross_dn", "sell",
            [Candidate(
                key="cross_dn",
                mask=cross_dn,
                reference=0.0,
                depth=depth,
                params={"kind": "macd_cross", "direction": "down"},
            )],
            depth_factor=0.5, **common,
        )
        rules.extend(r_b)
        rules.extend(r_s)
        history.extend(h_b)
        history.extend(h_s)
        return rules, history
