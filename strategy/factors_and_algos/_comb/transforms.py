"""Comb column transforms — one class per utility, each a pure
``apply(df, families) -> df`` stage on the pre-blend ``sig_<family>``
columns (BreadthFinisher is the one post-blend stage, on the blended
``signal_confidence``). No weight logic, no blend — orchestration lives
in :mod:`strategy.factors_and_algos._comb.pipeline`.

All forms are exact ports of the 2026-11-16/17 study implementations
(skill ``singleton-strategy-tuning`` Parts 3-7), at the measured
constants. Lookups that need per-event data (quantile sizing, breadth)
take the run's :class:`EventDataset` (plus the collector's bp stash);
the other two need only the frame.

cudf layout: whole-frame grouped rolling via ``grouped_rolling_agg`` and
elementwise ``where``-chains run on GPU; per-event lookups compute in a
host-numpy core and RE-MATERIALIZE as small (code, date)-keyed frames
merged back onto the big frame (cuDF-native hash join) — never a
mid-flow ``pd.Series(host_ndarray, index=...)`` hybrid.
"""
from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from _common.df_utils import grouped_rolling_agg, host_array

from strategy.factors_and_algos._comb.config import QuantileCfg, RegimeGateCfg
from strategy.factors_and_algos._comb.data_definitions import SignalEvent
from strategy.factors_and_algos._comb.events import EventDataset

#: Thesis sets for the regime gate (code constants, not config).
TREND_FAMILIES = frozenset({"ma_cross", "macd", "rf_break"})
REV_FAMILIES = frozenset(
    {"mov_rsi", "bollinger", "momentum", "rebound", "crowdedness"}
)

#: Vol-state constants (the stats.market_regimes convention).
VOL_EWM_SPAN = 20
VOL_REF_WINDOW = 255
VOL_REF_MIN_PERIODS = 120
VOL_STRESS_FACTOR = 0.5
VOL_CALM_FACTOR = 1.25

#: Breadth constants (Part 7 V1).
BREADTH_WEEK_DAYS = 6          # trailing window is [d-6, d] = 7 days
BREADTH_RANK_ENTRIES = 365     # trailing series entries (excluding today)
BREADTH_MIN_HISTORY = 20

BpStash = Dict[str, Dict[Tuple[str, date], float]]


class RegimeGate:
    """Thesis-matched BUY-only regime gate (Part 3's regime_matched).

    TREND families keep their BUYs only ABOVE the window-SMA, REV
    families only BELOW it; SELLs always pass; families outside both
    sets pass ungated. SMA warmup (NaN) gates nothing (comparison
    against NaN is False on both sides).
    """

    def __init__(self, cfg: RegimeGateCfg) -> None:
        self.cfg = cfg

    def apply(self, df: pd.DataFrame, families: List[str]) -> pd.DataFrame:
        sma = grouped_rolling_agg(
            df, "code", "close_price",
            window=self.cfg.window, min_periods=self.cfg.window, agg="mean",
        )
        close = df["close_price"]
        above = close > sma
        below = close < sma
        for fam in families:
            if fam not in TREND_FAMILIES and fam not in REV_FAMILIES:
                continue
            col = f"sig_{fam}"
            sig = df[col].fillna(0.0)
            buys = sig > 0
            mask = (above & buys) if fam in REV_FAMILIES else (below & buys)
            df[col] = sig.mask(mask, 0.0)
        return df


class QuantileSizing:
    """Evidence sizing (Part 6 U2): an event's size = 0.5 + 0.5 × the
    percentile of its recorded bp confidence within its OWN
    (family, code) trailing-``window_days`` events (calendar window,
    today excluded). Floor of ``floor_events`` events, else size 1.0;
    events without recorded evidence keep size 1.0."""

    def __init__(
        self,
        cfg: QuantileCfg,
        dataset: EventDataset,
        bp_stash: BpStash,
    ) -> None:
        self.cfg = cfg
        self.bp_stash = bp_stash
        by_fc: Dict[Tuple[str, str], List[SignalEvent]] = defaultdict(list)
        for e in dataset.events_with_bp():
            by_fc[(e.family, e.code)].append(e)
        self._by_fc: Dict[Tuple[str, str], Tuple[List[date], List[float]]] = {}
        for key, evs in by_fc.items():
            evs.sort(key=lambda e: e.event_date)
            self._by_fc[key] = ([e.event_date for e in evs],
                                [e.conf_bp for e in evs])

    def _size(self, fam: str, code: str, day: date, bp: float) -> float:
        if bp != bp:  # NaN — no recorded evidence
            return 1.0
        entry = self._by_fc.get((fam, code))
        if not entry:
            return 1.0
        dates, confs = entry
        hi = bisect_left(dates, day)
        lo = bisect_left(dates, day - timedelta(days=self.cfg.window_days))
        win = confs[lo:hi]
        if len(win) < self.cfg.floor_events:
            return 1.0
        pct = sum(1 for v in win if v <= bp) / len(win)
        return 0.5 + 0.5 * pct

    def apply(self, df: pd.DataFrame, families: List[str]) -> pd.DataFrame:
        for fam in families:
            fam_bp = self.bp_stash.get(fam, {})
            # Lookup frame built FROM the big frame's own (code, date)
            # columns — constructing it from python-date lists would be
            # auto-converted to datetime64[s] by cudf.pandas and fail the
            # merge against the object-date column (the documented ctor
            # accident; the skill's to_dt64 alignment convention).
            du = (df[["code", "date"]].drop_duplicates()
                  .reset_index(drop=True))
            codes_host = host_array(du["code"])
            dates_host = host_array(du["date"])
            sizes = np.empty(len(du), dtype=np.float64)
            for i in range(len(du)):
                day = dates_host[i]
                day = day.item() if hasattr(day, "item") else day
                code = str(codes_host[i])
                bp = fam_bp.get((code, day), float("nan"))
                sizes[i] = self._size(fam, code, day, bp)
            du = du.assign(size=sizes)
            df = df.merge(du, on=["code", "date"], how="left")
            sig = df[f"sig_{fam}"].fillna(0.0)
            df[f"sig_{fam}"] = sig * df["size"].fillna(1.0)
            df = df.drop(columns=["size"])
        return df


class VolStateScaling:
    """Vol-state exposure (Part 6 U3): per code, the EWMA(20) vol of
    daily returns z-scored against its own trailing 255-row window
    (min_periods 120), shifted 1 row — BUY entries ×0.5 in stress
    (z ≥ +1), ×1.25 in calm (z ≤ −1), ×1.0 inside the band. SELLs never
    scaled."""

    def apply(self, df: pd.DataFrame, families: List[str]) -> pd.DataFrame:
        # Lookup frame FROM the big frame's own columns (see QuantileSizing
        # for the dtype rationale) — (code, date) is unique on the unified
        # mixed frame, so carrying close_price through costs nothing. Sorted
        # by (code, date) so per-code blocks are contiguous and the offset
        # cursor maps computed multipliers back to du rows exactly.
        du = (df[["code", "date", "close_price"]]
              .drop_duplicates(subset=["code", "date"])
              .sort_values(["code", "date"]).reset_index(drop=True))
        mults = np.ones(len(du), dtype=np.float64)
        cursor = 0
        for _, block in du.groupby("code", sort=False):
            cl = block["close_price"].reset_index(drop=True)
            ret = cl.pct_change()
            vol20 = ret.ewm(span=VOL_EWM_SPAN, adjust=False).std()
            mean255 = vol20.rolling(VOL_REF_WINDOW,
                                    min_periods=VOL_REF_MIN_PERIODS).mean()
            std255 = vol20.rolling(VOL_REF_WINDOW,
                                   min_periods=VOL_REF_MIN_PERIODS).std()
            z = ((vol20 - mean255) / std255).shift(1)
            mult = pd.Series(1.0, index=cl.index)
            mult[z >= 1.0] = VOL_STRESS_FACTOR
            mult[z <= -1.0] = VOL_CALM_FACTOR
            mults[cursor:cursor + len(block)] = (
                host_array(mult).astype(np.float64))
            cursor += len(block)
        du = du.drop(columns=["close_price"]).assign(vol_mult=mults)
        df = df.merge(du, on=["code", "date"], how="left")
        mult = df["vol_mult"].fillna(1.0)
        for fam in families:
            sig = df[f"sig_{fam}"].fillna(0.0)
            df[f"sig_{fam}"] = sig.where(sig <= 0, sig * mult)
        return df.drop(columns=["vol_mult"])


class BreadthFinisher:
    """Cross-code capitulation breadth (Part 7 V1), post-blend form:
    the blended BUY side × (0.5 + 0.5·pct) where pct is the day's
    same-side breadth percentile within the trailing 365 series entries
    (excluding today; <20 entries → 0.5), the blended SELL side ×
    (1.5 − 0.5·pct). Market-wide capitulation = the strongest buys; late
    mega-cluster sells = the weakest exits.

    NOTE (documented deviation): the study measured breadth on the full
    8-family census; production counts the SELECTED families' events
    (the collector only fetches what it blends). Breadth ships OFF by
    default — enable only with this caveat in mind."""

    def __init__(self, dataset: EventDataset) -> None:
        self.dataset = dataset
        self.buy_pct = self._pct_lookup(self._breadth("buy"))
        self.sell_pct = self._pct_lookup(self._breadth("sell"))

    def _breadth(self, side: str) -> Dict[date, int]:
        per_code: Dict[str, set] = defaultdict(set)
        for e in self.dataset.events:
            if e.side == side:
                per_code[e.code].add(e.event_date)
        union = set(self.dataset.union_dates)
        out: Dict[date, int] = {}
        for d in self.dataset.union_dates:
            lo = d - timedelta(days=BREADTH_WEEK_DAYS)
            n = sum(1 for ds in per_code.values()
                    if any(lo <= x <= d for x in ds if x in union))
            out[d] = n
        return out

    def _pct_lookup(self, series: Dict[date, int]):
        dates = sorted(series)

        def lookup(d: date) -> float:
            i = bisect_left(dates, d)
            if i >= len(dates) or dates[i] != d:
                return 0.5
            lo = max(0, i - BREADTH_RANK_ENTRIES)
            hist = [series[dates[j]] for j in range(lo, i)]
            if len(hist) < BREADTH_MIN_HISTORY:
                return 0.5
            v = series[d]
            return sum(1 for x in hist if x <= v) / len(hist)
        return lookup

    def apply(self, df: pd.DataFrame) -> pd.DataFrame:
        du = df[["date"]].drop_duplicates().sort_values("date").reset_index(drop=True)
        dates_host = host_array(du["date"])
        buy_arr = np.array([
            self.buy_pct(d if isinstance(d, date) else
                         d.astype("datetime64[D]").item())
            for d in dates_host
        ], dtype=np.float64)
        sell_arr = np.array([
            self.sell_pct(d if isinstance(d, date) else
                          d.astype("datetime64[D]").item())
            for d in dates_host
        ], dtype=np.float64)
        du = du.assign(buy_m=0.5 + 0.5 * buy_arr)
        du = du.assign(sell_m=1.5 - 0.5 * sell_arr)
        df = df.merge(du, on="date", how="left")
        # pct-0.5 equivalents as pure defense (the lookup never misses)
        buy_m = df["buy_m"].fillna(0.75)
        sell_m = df["sell_m"].fillna(1.25)
        conf = df["signal_confidence"]
        is_buy = conf > 0
        is_sell = conf < 0
        scaled = conf.mask(is_buy, conf * buy_m)
        scaled = scaled.mask(is_sell, conf * sell_m)
        df["signal_confidence"] = scaled.clip(-100.0, 100.0)
        df["signal_value"] = df["signal_confidence"].abs()
        # The per-family weight columns ride the SAME side multipliers
        # so the __MIX__ contributions (n = w × c) still sum to the
        # final blended confidence.
        w_cols = [c for c in df.columns if c.startswith("algo_w_")]
        for col in w_cols:
            w = df[col]
            df[col] = w.mask(is_buy, w * buy_m).mask(is_sell, w * sell_m)
        return df.drop(columns=["buy_m", "sell_m"])


__all__ = [
    "REV_FAMILIES",
    "TREND_FAMILIES",
    "BreadthFinisher",
    "QuantileSizing",
    "RegimeGate",
    "VolStateScaling",
]
