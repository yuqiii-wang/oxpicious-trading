"""options_vix — the vol-spike-in-fear-regime family (analysis.
analysis_signals.engines.signal_families.options_vix.engine).

BUY THE PANIC, conditionally. The axes: corr20 (the PUBLISHED 20-
session corr of spot return vs vol change — analysis.
options_vol_index_vs_spot_price_corr, linearly interpolated per code
onto every session IN-ENGINE; below −0.8 is roughly the 5th-percentile
deep-fear tail), slope5 (the 5-day vol slope), z252 (the vol level
z). The measured premise: corr alone ≈ baseline, spike alone weak —
the INTERACTION is the edge.

  OvixSpikeEntry (buy)   ovix_spike      — corr20 < −0.8 & slope5 >
      +10% (the panic spike); ovix_spike_hivol — spike & z252 > 1
      (the interaction at its strongest).
  OvixPanicPeakEntry (buy) — ovix_panic_peak, the vol30 value space:
      vol escalates ≥ 5% past its prior 120-session max while slope5
      is still > +10% AND spot sits below its 5-sessions-ago close —
      the PANIC-PEAK escalation leg. The corr axis stays moderate in
      V-spike crises (000852's Feb-2024 interp corr bottomed −0.74 vs
      the −0.80 bar) and the K=8 episode absorb fires any level-based
      condition at the crisis ONSET — this leg re-arms at the NEW
      extreme (2024-02-05 for 000852/000300; pooled n=14 dir_ave
      +3.55% hit 86%), and the downside leg separates fear from
      upside euphoria (2024-10-08 stimulus top excluded).
  OvixFearUnwindEntry (sell) — corr20 crosses back above −0.5 with a
      40-session deep-fear witness: "the panic premium is gone and the
      fear trade is over" (exit timing).

corr_20d is the published series interpolated to daily IN-FRAME (the
stored table is a 5-session stride grid through history + the daily
tail — day-over-day crossing logic needs the daily series; nothing is
written back to the DB); slope/z are computed in-engine on vol30.
Sparse crisis population: POOLED (trend 4 / regime 4); episodes
deduped at the crisis scale (K=8).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from _common.df_utils import (
    grouped_rolling_agg,
    grouped_shift,
)

from analysis.analysis_signals import config as cfg
from analysis.analysis_signals.engines.rule_base import Candidate, RuleEntry
from analysis.analysis_signals.fetch.inputs import fetch_options_vix_inputs


class _OvixBase(RuleEntry):
    """The shared fetch + vol axes for the options_vix entries."""

    FAMILY = "options_vix"
    PACKAGE = "options_vix"
    REGIME_FREE = True
    USES_TREND_SPLIT = False  # pooled: trend 4 (the no-trend-claim bucket)

    async def fetch_frame(self) -> pd.DataFrame:
        return await fetch_options_vix_inputs(self.conn, self.codes)

    def _attach_vol_axes(self, frame: pd.DataFrame) -> None:
        """The daily axes on the joined frame: slope5/z252 computed
        in-engine on vol30; corr_20d = the PUBLISHED series linearly
        interpolated per code onto every session — the stored table
        rides a 5-session stride grid through history (+ the daily
        tail), and the day-over-day crossing logic needs the daily
        series. Whole-frame grouped ops (ffill/bfill/cumsum/cumcount
        over segment ids — session-ordinal space, inside the stored
        span only; leading/trailing gaps stay NaN); nothing is written
        back to the DB."""
        keys = ["code"]
        vol = frame["vol30"].astype("float64")
        prev5 = grouped_shift(
            frame.assign(_v=vol), keys, ["_v"], out_names=["_p5"],
            periods=5,
        )["_p5"]
        frame["slope5"] = vol / prev5 - 1.0
        mu252 = grouped_rolling_agg(
            frame, keys, "vol30", cfg.OVIX_Z_WINDOW,
            min_periods=cfg.OVIX_Z_WINDOW,
        )
        sd252 = grouped_rolling_agg(
            frame, keys, "vol30", cfg.OVIX_Z_WINDOW,
            min_periods=cfg.OVIX_Z_WINDOW, agg="std",
        )
        frame["z252"] = (vol - mu252) / sd252

        # corr_20d: the published fear-gauge series -> daily (linear,
        # per code, between the stored stride samples)
        v = frame["corr_20d_pub"].astype("float64")
        work = pd.DataFrame({"code": frame["code"], "_v": v},
                            index=frame.index)
        work["_prev"] = work.groupby(keys, sort=False)["_v"].ffill()
        work["_nxt"] = work.groupby(keys, sort=False)["_v"].bfill()
        work["_valid"] = v.notna().astype("int64")
        work["_seg"] = work.groupby(keys, sort=False)["_valid"].cumsum()
        seg_keys = keys + ["_seg"]
        work["_pos"] = work.groupby(seg_keys, sort=False).cumcount()
        work["_one"] = np.int64(1)
        work["_len"] = work.groupby(seg_keys, sort=False)["_one"] \
            .transform("sum")
        t = work["_pos"] / work["_len"]
        interp = work["_prev"] + t * (work["_nxt"] - work["_prev"])
        # restore the raw samples at valid rows (the code's LAST
        # stored sample has no next: 0 * NaN would NaN it otherwise)
        frame["corr_20d"] = v.where(v.notna(), interp)


class OvixSpikeEntry(_OvixBase):
    """ovix_spike / ovix_spike_hivol — the corr×spike interaction."""

    SUB_TYPES = ("ovix_spike", "ovix_spike_hivol")

    def run(self, frame: pd.DataFrame) -> tuple[list[dict], list[dict]]:
        rules: list[dict] = []
        history: list[dict] = []
        if frame.empty:
            return rules, history
        self._attach_vol_axes(frame)
        corr = frame["corr_20d"].astype("float64")

        spike = (corr < cfg.OVIX_CORR_BAR) \
            & (frame["slope5"] > cfg.OVIX_SPIKE_SLOPE)
        spike = spike.fillna(False)
        common = {
            "k": cfg.OVIX_REENTRY_EXIT_DAYS,
            "value_col": "corr_20d",
            "depth_factor": 0.0,  # confidence FLAT — the sparse family's scale
            "extra_params": {
                "corr_source": "published corr_20d (analysis."
                               "options_vol_index_vs_spot_price_corr), "
                               "linearly interpolated per code onto every "
                               "session in-engine (the stored table is a "
                               "5-session stride grid + the daily tail)",
                "axes": {
                    "corr_20d": {"bar": cfg.OVIX_CORR_BAR},
                    "slope5": {"bar_pct": cfg.OVIX_SPIKE_SLOPE * 100},
                },
            },
        }
        r1, h1 = self.run_cells(
            frame, "ovix_spike", "buy",
            [Candidate(
                key="spike",
                mask=spike,
                reference=cfg.OVIX_CORR_BAR,
                depth=(-corr - abs(cfg.OVIX_CORR_BAR)) / 0.1,
                params={"kind": "ovix_spike",
                        "bar_def": {"kind": "corr_slope_interaction",
                                    "corr_below": cfg.OVIX_CORR_BAR,
                                    "slope5_gt": cfg.OVIX_SPIKE_SLOPE}},
            )],
            **common,
        )
        r2, h2 = self.run_cells(
            frame, "ovix_spike_hivol", "buy",
            [Candidate(
                key="spike_hivol",
                mask=spike & (frame["z252"] > cfg.OVIX_HIVOL_Z).fillna(False),
                reference=cfg.OVIX_CORR_BAR,
                depth=(frame["z252"] - cfg.OVIX_HIVOL_Z).clip(lower=0.0),
                params={"kind": "ovix_spike_hivol",
                        "bar_def": {"kind": "corr_slope_z_interaction",
                                    "corr_below": cfg.OVIX_CORR_BAR,
                                    "slope5_gt": cfg.OVIX_SPIKE_SLOPE,
                                    "z252_gt": cfg.OVIX_HIVOL_Z}},
            )],
            **common,
        )
        rules.extend(r1 + r2)
        history.extend(h1 + h2)
        return rules, history


class OvixPanicPeakEntry(_OvixBase):
    """ovix_panic_peak — the vol30-space panic-PEAK escalation leg.

    vol escalates past its prior 120-session max by >= 5% while slope5
    is still > +10% AND spot sits below its 5-sessions-ago close. The
    re-arm at the NEW extreme is the point: any level-based condition
    fires at the crisis ONSET and the K=8 absorb eats the rest of the
    crisis, so the peak (the day the user sees max VIX + min spot)
    never gets its own marker under the corr-gated configs — the corr
    axis itself stays moderate through V-spike crises. The downside
    leg separates downside fear from upside euphoria (the stimulus-day
    vol spikes buy the top otherwise)."""

    SUB_TYPES = ("ovix_panic_peak",)

    def run(self, frame: pd.DataFrame) -> tuple[list[dict], list[dict]]:
        rules: list[dict] = []
        history: list[dict] = []
        if frame.empty:
            return rules, history
        self._attach_vol_axes(frame)
        keys = ["code"]
        vol = frame["vol30"].astype("float64")
        margin = cfg.OVIX_PEAK_ATH_MARGIN

        # the escalation bar: the prior ATH-window max (shifted 1 — the
        # trigger day's own vol never sets its own bar) x (1 + margin)
        prev_vol = grouped_shift(
            frame.assign(_v=vol), keys, ["_v"], out_names=["_pv"],
            periods=1,
        )["_pv"]
        rmax = grouped_rolling_agg(
            frame.assign(_pv=prev_vol), keys, "_pv",
            cfg.OVIX_PEAK_ATH_WINDOW,
            min_periods=cfg.OVIX_PEAK_ATH_WINDOW // 2, agg="max",
        )
        bar = rmax * (1.0 + margin)

        # the downside leg: close below the slope window's own anchor
        px_prev = grouped_shift(
            frame.assign(_p=frame["price"].astype("float64")), keys,
            ["_p"], out_names=["_pp"], periods=cfg.OVIX_PEAK_DN_WINDOW,
        )["_pp"]
        dn = frame["price"] < px_prev

        mask = (vol > bar) \
            & (frame["slope5"] > cfg.OVIX_SPIKE_SLOPE) \
            & dn
        mask = mask.fillna(False)

        r, h = self.run_cells(
            frame, "ovix_panic_peak", "buy",
            [Candidate(
                key="panic_peak",
                mask=mask,
                reference=bar,
                depth=((vol / bar - 1.0) / margin).clip(lower=0.0),
                params={"kind": "ovix_panic_peak",
                        "value_space": "vol_index_30d",
                        "bar_def": {"kind": "vol_ath_escalation",
                                    "ath_window":
                                        cfg.OVIX_PEAK_ATH_WINDOW,
                                    "ath_margin_pct": margin * 100,
                                    "slope5_gt": cfg.OVIX_SPIKE_SLOPE,
                                    "downside_window":
                                        cfg.OVIX_PEAK_DN_WINDOW}},
            )],
            k=cfg.OVIX_REENTRY_EXIT_DAYS, value_col="vol30",
            depth_factor=0.0,
            extra_params={
                "value_source": "analysis.options_vol_index.vol_index_30d",
                "axes": {
                    "vol_ath_escalation": {
                        "window": cfg.OVIX_PEAK_ATH_WINDOW,
                        "margin_pct": margin * 100,
                    },
                    "slope5": {"bar_pct": cfg.OVIX_SPIKE_SLOPE * 100},
                    "downside": {"window": cfg.OVIX_PEAK_DN_WINDOW},
                },
            },
        )
        rules.extend(r)
        history.extend(h)
        return rules, history


class OvixAtrBreakEntry(_OvixBase):
    """ovix_atr_break — the volatility-EXPANSION breakout leg (buy).

    The 2026-10-15 grind-down class the corr/ATH legs never cover: a
    fast vol repricing from a LOW base — slope5 fires (+28% that day)
    while corr20 sits far above −0.80 and vol30 far below its 120-
    session max, so ovix_spike AND ovix_panic_peak both stay dark. The
    price leg is the day's own return in close-to-close ATR units
    (options_spot_daily has no OHLC — |ret| rolling mean is the ATR
    analog): a >= k-ATR DOWN day while the vol index is repricing is
    the expansion breakout, BUY the panic continuation-reversal. The
    k bar is SELF-ADAPTIVE per cell from the {2, 2.5, 3} grid; the
    ATR never includes the trigger day's own return (rolling, the
    day rides the window but a 2-3x multiple dominates it)."""

    SUB_TYPES = ("ovix_atr_break",)

    def run(self, frame: pd.DataFrame) -> tuple[list[dict], list[dict]]:
        rules: list[dict] = []
        history: list[dict] = []
        if frame.empty:
            return rules, history
        self._attach_vol_axes(frame)
        keys = ["code"]
        price = frame["price"].astype("float64")
        prev_px = grouped_shift(
            frame.assign(_p=price), keys, ["_p"], out_names=["_pp"],
            periods=1,
        )["_pp"]
        ret = price / prev_px - 1.0
        atr = grouped_rolling_agg(
            frame.assign(_a=ret.abs()), keys, "_a",
            cfg.OVIX_ATR_WINDOW, min_periods=cfg.OVIX_ATR_WINDOW,
        )
        frame["atr_mult"] = ret / atr.where(atr > 0)
        mult = frame["atr_mult"]

        candidates = [
            Candidate(
                key=f"atr{k:g}",
                mask=(mult <= -k)
                & (frame["slope5"] > cfg.OVIX_SPIKE_SLOPE).fillna(False),
                reference=-k,
                depth=((-mult - k) / 0.5).clip(lower=0.0),
                params={"kind": "atr_break",
                        "value_space": "spot_return_in_atr",
                        "bar_def": {"kind": "atr_multiple",
                                    "atr_window": cfg.OVIX_ATR_WINDOW,
                                    "multiple": k,
                                    "slope5_gt": cfg.OVIX_SPIKE_SLOPE}},
            )
            for k in cfg.OVIX_ATR_KS
        ]
        r, h = self.run_cells(
            frame, "ovix_atr_break", "buy", candidates,
            k=cfg.OVIX_REENTRY_EXIT_DAYS, value_col="atr_mult",
            depth_factor=0.0,
            extra_params={
                "value_source": "analysis.options_spot_daily close "
                                "(close-to-close ATR)",
                "axes": {
                    "atr": {"window": cfg.OVIX_ATR_WINDOW,
                            "ks": list(cfg.OVIX_ATR_KS)},
                    "slope5": {"bar_pct": cfg.OVIX_SPIKE_SLOPE * 100},
                },
            },
        )
        rules.extend(r)
        history.extend(h)
        return rules, history


class OvixFearUnwindEntry(_OvixBase):
    """ovix_fear_unwind — the confirmed fear-regime exit (sell)."""

    SUB_TYPES = ("ovix_fear_unwind",)

    def run(self, frame: pd.DataFrame) -> tuple[list[dict], list[dict]]:
        rules: list[dict] = []
        history: list[dict] = []
        if frame.empty:
            return rules, history
        self._attach_vol_axes(frame)
        corr = frame["corr_20d"].astype("float64")
        keys = ["code"]

        prev_corr = grouped_shift(
            frame.assign(_c=corr), keys, ["_c"], out_names=["_pc"],
            periods=1,
        )["_pc"]
        # the deep-fear witness: the 40-session trailing corr minimum
        # was inside the deep-fear tail
        corr_min = grouped_rolling_agg(
            frame.assign(_c=corr), keys, "_c", cfg.OVIX_UNWIND_WITNESS,
            min_periods=cfg.OVIX_UNWIND_WITNESS, agg="min",
        )
        mask = (corr >= cfg.OVIX_UNWIND_CORR) \
            & (prev_corr < cfg.OVIX_UNWIND_CORR) \
            & (corr_min < cfg.OVIX_CORR_BAR)
        mask = mask.fillna(False)

        r, h = self.run_cells(
            frame, "ovix_fear_unwind", "sell",
            [Candidate(
                key="fear_unwind",
                mask=mask,
                reference=cfg.OVIX_UNWIND_CORR,
                depth=(corr - cfg.OVIX_UNWIND_CORR).clip(lower=0.0) / 0.1,
                params={"kind": "ovix_fear_unwind",
                        "bar_def": {"kind": "corr_cross_up",
                                    "through": cfg.OVIX_UNWIND_CORR,
                                    "witness_sessions":
                                        cfg.OVIX_UNWIND_WITNESS}},
            )],
            k=cfg.OVIX_REENTRY_EXIT_DAYS, value_col="corr_20d",
            depth_factor=0.0,
        )
        rules.extend(r)
        history.extend(h)
        return rules, history
