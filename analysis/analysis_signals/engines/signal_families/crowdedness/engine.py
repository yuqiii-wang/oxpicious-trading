"""crowdedness — the pool-level crowding recent-extreme family
(analysis.analysis_signals.engines.signal_families.crowdedness.engine).

The v4 action logic (2026-11-16; user-directed: the turning dates
2020-05-29 / 2022-10-26 / 2025-06-03 / 2026-10-30 must fire BUYs and
2024-11-11 / 2026-06-24 SELLS — the v3 states missed each for a
different reason, diagnosed in temp_scripts/crowd_date_diagnosis.py
and studied in crowd_action_study4..7). The crowdedness stats are the
ASSISTANT metric: the trade is the INDEX itself (the cells measure the
index's own forward blend while the crowdedness columns carry the
trigger). The inventory, four layers deep:

  drain / pilein   the crowd at its trailing 60-row LOW/HIGH gated by
                   the underlying's own 10-row direction (drain needs
                   it UP — the crowd drained while price rose: the move
                   is not overcrowded, +0.39..+0.63% forward blend; the
                   pilein needs it DOWN — distribution churn in a
                   decline, exit timing). The v3 states, now with the
                   sparse-crowd-tolerant window (min_periods 20 — the
                   crowdedness build leaves gap days and the full-60
                   requirement blacked out whole stretches).
  drain_floor      the crowd composite at its trailing-250-row BOTTOM
                   (pos <= 0.10) while the index TICKS UP over 5 rows —
                   the year-scale crowd floor with the first up-tick
                   (the 2020-05-29 / 2025-06-03 signature). LOOSE GATE
                   (only a negative measured mean rejected — the
                   hedge_pairs precedent, run_cells overrides): the
                   pooled means sit below the global 0.25% buy bar.
  tech_washout     the hot set's median px-vs-MA60 percentile UNDER
                   WATER — the falling-knife capitulation catch, any
                   direction (+0.92% forward blend pooled, hit 61%):
                   the fresh-depth arm (<= 0.10 at a fresh 20-row low
                   of the percentile with volume picking up —
                   2026-10-30) OR the amount-climax arm (<= 0.20 at
                   amt >= 0.80 — 2022-10-26, the crowd under water plus
                   放量 climax).
  tech_euphoria    the crowd ma5 at its 60-row high or within 10% of
                   its trailing-year high while the hot set's
                   px-vs-MA20 percentile >= 0.85 — the blow-off
                   stretch, exit timing (2026-06-24). Sells are not
                   pooled-gated — the per-cell gate judges each code's
                   own episodes (000001's measure favorable, -0.32%
                   raw, while the 10-code pooled continues up — the
                   honest per-code split).
  tech_euphoria_div the euphoria stretch with the crowd composite
                   ALREADY turning down (score_chg5 < 0) — the
                   price/crowd divergence (2024-11-11's signature;
                   000001's own episodes -0.70% raw).

The reference is the PRIOR window extreme (one-row-shifted — the
level being broken, no look-ahead); the confidence depth is the
breach of that level in the axis's OWN trailing-window sigma
(trigger-time observable). Episodes are K=0 STRICT (the options_wall
precedent, 2026-11-16 — the measurement basis of the v4 studies):
consecutive state rows are one event firing its first day, and any
lapse re-arms — a state that returns after even one off-state row is
a fresh signal (one signal per K=0 episode). NULL crowdedness /
hot-tech days never trigger — the masks fill before compare and the
rolling windows skip NULLs against min_periods."""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from _common.df_utils import grouped_rolling_agg, grouped_shift

from analysis.analysis_signals import config as cfg
from analysis.analysis_signals.engines.rule_base import Candidate, RuleEntry
from analysis.analysis_signals.fetch.inputs import fetch_crowdedness_inputs

_CROWD_SIDES = (("drain", "buy"), ("pilein", "sell"))
_FLOOR_AXES = (("roll_score", "crowdedness_score", "crowd_roll_score"),
               ("year_score", "crowdedness_score_year",
                "crowd_year_score"))


class CrowdTailEntry(RuleEntry):
    """The crowdedness recent-extreme rule — the v4 inventory:
    (hot set, axis, drain/pilein) per the four crowding axes plus the
    drain_floor / tech_washout / tech_euphoria(_div) turning-date
    states. The family's universe is data-driven (the codes with
    crowdedness rows at the sec_type — index; etf/stock resolve empty
    and skip)."""

    FAMILY = "crowdedness"
    PACKAGE = "crowdedness"
    SUB_TYPES = tuple(
        [f"{prefix}_{side}"
         for _, _, prefix in cfg.CROWD_AXES for side, _ in _CROWD_SIDES]
        + [f"{prefix}_drain_floor" for _, _, prefix in _FLOOR_AXES]
        + ["crowd_tech_washout", "crowd_tech_euphoria",
           "crowd_tech_euphoria_div"]
    )
    REGIME_FREE = False
    USES_TREND_SPLIT = True
    # plain K-absorb until the family's own trend-aware A/B is measured
    # (the bollinger/momentum precedent)
    TREND_AWARE_REENTRY = False
    OHLCV_BOOST = False

    async def fetch_frame(self) -> pd.DataFrame:
        return await fetch_crowdedness_inputs(
            self.conn, self.sec_type, self.codes,
        )

    def run(self, frame: pd.DataFrame) -> tuple[list[dict], list[dict]]:
        rules: list[dict] = []
        history: list[dict] = []
        if frame.empty:
            return rules, history
        for axis, col, prefix in cfg.CROWD_AXES:
            rules_a, history_a = self._run_axis(frame, axis, col, prefix)
            rules.extend(rules_a)
            history.extend(history_a)
        for axis, col, prefix in _FLOOR_AXES:
            rules_f, history_f = self._run_floor(frame, axis, col, prefix)
            rules.extend(rules_f)
            history.extend(history_f)
        rules_t, history_t = self._run_tech(frame)
        rules.extend(rules_t)
        history.extend(history_t)
        return rules, history

    def _direction(self, frame: pd.DataFrame, window: int) -> pd.Series:
        """The underlying index's own trailing `window`-row return
        (trigger-time observable — backward-looking; the price is the
        response axis, the crowd columns the trigger)."""
        price_prior = grouped_shift(
            frame, ["code"], ["price"], out_names=["_p"], periods=window,
        )["_p"]
        return frame["price"] / price_prior - 1.0

    def _run_axis(self, frame: pd.DataFrame, axis: str, col: str,
                  prefix: str) -> tuple[list[dict], list[dict]]:
        value = frame[col].astype("float64")
        keys = ["code"]
        w = cfg.CROWD_STATE_WINDOW
        mp = cfg.CROWD_STATE_MIN_PERIODS
        tw = cfg.CROWD_TREND_WINDOW

        # the PRIOR window extremes (one-row-shifted — the level the
        # current row breaks; no look-ahead) + the axis's own trailing
        # sigma (the depth normalizer). The rolling windows skip NULL
        # crowdedness days against min_periods (the sparse-crowd
        # tolerance — gap days no longer blackout the state).
        prior_low = grouped_shift(
            frame.assign(_raw=grouped_rolling_agg(
                frame, keys, col, w, min_periods=mp, agg="min",
            )), keys, ["_raw"], out_names=["_s"], periods=1,
        )["_s"]
        prior_high = grouped_shift(
            frame.assign(_raw=grouped_rolling_agg(
                frame, keys, col, w, min_periods=mp, agg="max",
            )), keys, ["_raw"], out_names=["_s"], periods=1,
        )["_s"]
        sigma = grouped_shift(
            frame.assign(_raw=grouped_rolling_agg(
                frame, keys, col, w, min_periods=mp, agg="std",
            )), keys, ["_raw"], out_names=["_s"], periods=1,
        )["_s"]
        sigma = np.maximum(sigma, 1e-6)
        ret = self._direction(frame, tw)

        # the recent-extreme states: the row BREAKS the prior window
        # extreme (at/below the prior low, at/above the prior high —
        # warmup NaN fills False)
        is_low = (value <= prior_low).fillna(False)
        is_high = (value >= prior_high).fillna(False)

        def extra_params(side: str) -> dict:
            return {
                "axis": axis,
                "trend_gate": {"window": tw},
                "depth_unit": "axis_sigma",
                "bar_def": {
                    "kind": "recent_extreme",
                    "side": side,
                    "window_days": w,
                    "trend_window": tw,
                },
            }

        rules: list[dict] = []
        history: list[dict] = []

        # drain BUY: the crowd at its recent low while the index rose
        r_buy, h_buy = self.run_cells(
            frame, f"{prefix}_drain", "buy",
            [Candidate(
                key=f"newlow{w}",
                mask=is_low & (ret > 0).fillna(False),
                reference=prior_low,
                depth=(prior_low - value) / sigma,
            )],
            depth_factor=0.5,
            k=0,  # K=0 strict episodes (the options_wall precedent): each lapse-and-re-entry is a fresh event — the measurement basis of studies crowd_action_study4..7
            value_col=col,
            extra_params=extra_params("low"),
        )
        rules.extend(r_buy)
        history.extend(h_buy)

        # pilein SELL: the crowd at its recent high while the index
        # fell — distribution churn in a decline (exit timing)
        r_sell, h_sell = self.run_cells(
            frame, f"{prefix}_pilein", "sell",
            [Candidate(
                key=f"newhigh{w}",
                mask=is_high & (ret < 0).fillna(False),
                reference=prior_high,
                depth=(value - prior_high) / sigma,
            )],
            depth_factor=0.5,
            k=0,  # K=0 strict episodes (the options_wall precedent): each lapse-and-re-entry is a fresh event — the measurement basis of studies crowd_action_study4..7
            value_col=col,
            extra_params=extra_params("high"),
        )
        rules.extend(r_sell)
        history.extend(h_sell)
        return rules, history

    def _run_floor(self, frame: pd.DataFrame, axis: str, col: str,
                   prefix: str) -> tuple[list[dict], list[dict]]:
        """drain_floor BUY: the crowd at its trailing-250-row bottom
        (pos <= the cut) while the index ticks up over 5 rows — the
        year-scale crowd floor with the first up-tick. LOOSE GATE (the
        hedge_pairs precedent): only a negative measured mean is
        rejected — the pooled means sit below the global 0.25% buy
        bar."""
        value = frame[col].astype("float64")
        keys = ["code"]
        w = cfg.CROWD_FLOOR_WINDOW
        mp = cfg.CROWD_FLOOR_MIN_PERIODS
        cut = cfg.CROWD_FLOOR_POS_CUT
        tw = cfg.CROWD_FLOOR_TREND_WINDOW

        lo = grouped_shift(
            frame.assign(_raw=grouped_rolling_agg(
                frame, keys, col, w, min_periods=mp, agg="min",
            )), keys, ["_raw"], out_names=["_s"], periods=1,
        )["_s"]
        hi = grouped_shift(
            frame.assign(_raw=grouped_rolling_agg(
                frame, keys, col, w, min_periods=mp, agg="max",
            )), keys, ["_raw"], out_names=["_s"], periods=1,
        )["_s"]
        sigma = grouped_shift(
            frame.assign(_raw=grouped_rolling_agg(
                frame, keys, col, w, min_periods=mp, agg="std",
            )), keys, ["_raw"], out_names=["_s"], periods=1,
        )["_s"]
        sigma = np.maximum(sigma, 1e-6)
        pos = ((value - lo) / (hi - lo).replace(0, np.nan)).clip(0, 1)
        ret5 = self._direction(frame, tw)

        at_floor = (pos <= cut).fillna(False)
        # the depth: how far INTO the floor band (below lo + cut*span)
        depth = ((lo + cut * (hi - lo) - value) / sigma).clip(lower=0)

        r, h = self.run_cells(
            frame, f"{prefix}_drain_floor", "buy",
            [Candidate(
                key=f"floor{cut}",
                mask=at_floor & (ret5 > 0).fillna(False),
                reference=lo,
                depth=depth,
            )],
            depth_factor=0.5,
            k=0,  # K=0 strict episodes (the options_wall precedent): each lapse-and-re-entry is a fresh event — the measurement basis of studies crowd_action_study4..7
            value_col=col,
            # the family-scoped LOOSE gate (the hedge_pairs precedent):
            # only obviously bad (negative-mean) populations rejected
            gate_min={"buy": 0.0, "sell": 0.0},
            pooled_dir_ave_min=0.0,
            extra_params={
                "axis": axis,
                "trend_gate": {"window": tw},
                "depth_unit": "axis_sigma",
                "bar_def": {
                    "kind": "crowd_floor",
                    "pos_cut": cut,
                    "window_days": w,
                    "trend_window": tw,
                },
            },
        )
        return r, h

    def _run_tech(self, frame: pd.DataFrame) -> tuple[list[dict],
                                                      list[dict]]:
        """The hot-set TECH triggers (analysis.market_crowdedness_hot_
        tech median percentiles, LEFT JOINed at the fetch): the
        washout capitulation catch (BUY) and the euphoria blow-off
        stretch (+ its price/crowd divergence arm) as exit timing
        (SELL)."""
        keys = ["code"]
        fw = cfg.CROWD_HOT_FRESH_WINDOW
        px60 = frame["median_px_vs_ma60_pct"].astype("float64")
        px20 = frame["median_px_vs_ma20_pct"].astype("float64")
        amt = frame["median_amt_vs_ma20_pct"].astype("float64")

        # the fresh-depth-extreme cue: the percentile makes a new
        # 20-row low (the under-water state DEEPENING — a fresh event,
        # not a state that lapsed weeks ago)
        px60_fresh = (px60 <= grouped_shift(
            frame.assign(_raw=grouped_rolling_agg(
                frame, keys, "median_px_vs_ma60_pct", fw,
                min_periods=10, agg="min",
            )), keys, ["_raw"], out_names=["_s"], periods=1,
        )["_s"]).fillna(False)

        # the crowd ma5's blow-off states: the 60-row break OR within
        # 10% of the trailing-year high
        ma5 = frame["crowdedness_score_ma5"].astype("float64")
        ma5_newhigh60 = (ma5 >= grouped_shift(
            frame.assign(_raw=grouped_rolling_agg(
                frame, keys, "crowdedness_score_ma5",
                cfg.CROWD_STATE_WINDOW, min_periods=20, agg="max",
            )), keys, ["_raw"], out_names=["_s"], periods=1,
        )["_s"]).fillna(False)
        ma5_year_hi = grouped_shift(
            frame.assign(_raw=grouped_rolling_agg(
                frame, keys, "crowdedness_score_ma5",
                cfg.CROWD_FLOOR_WINDOW, min_periods=60, agg="max",
            )), keys, ["_raw"], out_names=["_s"], periods=1,
        )["_s"]
        ma5_pos250hi = (ma5 >= ma5_year_hi
                        * cfg.CROWD_EUPHORIA_YEAR_TOP).fillna(False)
        # the crowd composite turning down (the divergence cue)
        score_prior = grouped_shift(
            frame, keys, ["crowdedness_score"], out_names=["_s"],
            periods=5,
        )["_s"]
        score_chg5 = frame["crowdedness_score"].astype("float64") \
            - score_prior

        stretched = (ma5_newhigh60 | ma5_pos250hi) \
            & (px20 >= cfg.CROWD_HOT_PX20_STRETCH).fillna(False)

        rules: list[dict] = []
        history: list[dict] = []

        # tech_washout BUY: the hot set under water — two arms OR'd
        washout = (
            ((px60 <= cfg.CROWD_HOT_PX60_DEEP)
             & px60_fresh
             & (amt >= cfg.CROWD_HOT_AMT_MIN).fillna(False))
            | ((px60 <= cfg.CROWD_HOT_PX60_WIDE)
               & (amt >= cfg.CROWD_HOT_AMT_CLIMAX).fillna(False))
        )
        r_w, h_w = self.run_cells(
            frame, "crowd_tech_washout", "buy",
            [Candidate(
                key="underwater",
                mask=washout.fillna(False),
                reference=pd.Series(cfg.CROWD_HOT_PX60_DEEP,
                                    index=frame.index),
                depth=((cfg.CROWD_HOT_PX60_DEEP - px60) / 0.10)
                    .clip(lower=0),
            )],
            depth_factor=0.5,
            k=0,  # K=0 strict episodes (the options_wall precedent): each lapse-and-re-entry is a fresh event — the measurement basis of studies crowd_action_study4..7
            value_col="median_px_vs_ma60_pct",
            extra_params={
                "depth_unit": "hot_pctile",
                "bar_def": {
                    "kind": "hot_tech_washout",
                    "px60_deep": cfg.CROWD_HOT_PX60_DEEP,
                    "px60_wide": cfg.CROWD_HOT_PX60_WIDE,
                    "amt_min": cfg.CROWD_HOT_AMT_MIN,
                    "amt_climax": cfg.CROWD_HOT_AMT_CLIMAX,
                },
            },
        )
        rules.extend(r_w)
        history.extend(h_w)

        # tech_euphoria SELL: the blow-off stretch, exit timing
        euphoria_depth = ((px20 - cfg.CROWD_HOT_PX20_STRETCH) / 0.15) \
            .clip(lower=0)
        for sub, div in (("crowd_tech_euphoria", False),
                         ("crowd_tech_euphoria_div", True)):
            mask = (stretched & (score_chg5 < 0).fillna(False)) if div \
                else stretched
            r_e, h_e = self.run_cells(
                frame, sub, "sell",
                [Candidate(
                    key="stretched",
                    mask=mask.fillna(False),
                    reference=pd.Series(cfg.CROWD_HOT_PX20_STRETCH,
                                        index=frame.index),
                    depth=euphoria_depth,
                )],
                depth_factor=0.5,
                k=0,  # K=0 strict episodes (the options_wall precedent): each lapse-and-re-entry is a fresh event — the measurement basis of studies crowd_action_study4..7
                value_col="median_px_vs_ma20_pct",
                extra_params={
                    "depth_unit": "hot_pctile",
                    "bar_def": {
                        "kind": "hot_tech_euphoria",
                        "px20_cut": cfg.CROWD_HOT_PX20_STRETCH,
                        "div": div,
                    },
                },
            )
            rules.extend(r_e)
            history.extend(h_e)
        return rules, history
