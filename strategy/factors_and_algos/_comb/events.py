"""Comb event extraction — the statistics INPUTS layer.

Extracts the picked-up signal events from the collector's unified mixed
frame and enriches each with its realized forward quality (the repo's
dir_ave blend) — the input every adaptive statistic in the comb stack
consumes. Pure read: never writes frame columns.

cudf boundary: the per-code gather/index core (closes, dates, nonzero
positions, forward windows) runs on ONE host-numpy unwrap per code block
via ``host_array`` — the sanctioned gather/index boundary class; the
small event output materializes as plain python/dataclass rows (no
mid-flow ``pd.Series(host_ndarray, index=...)`` hybrids).
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from _common.df_utils import host_array

from strategy.factors_and_algos._comb.data_definitions import SignalEvent

#: The repo's dir_ave forward blend over 0/3/5/10/20 trading rows.
HORIZONS: Tuple[int, ...] = (0, 3, 5, 10, 20)
WEIGHTS: Tuple[float, ...] = (0.10, 0.15, 0.50, 0.15, 0.10)

BUY = "buy"
SELL = "sell"


def fwd_blend(closes: np.ndarray, idx: int) -> Optional[float]:
    """dir_ave forward blend anchored at ``idx`` (None unless the full
    20-row future is realized). Exact port of the study's convention."""
    n = len(closes)
    if idx + HORIZONS[-1] >= n:
        return None
    r = 0.0
    for h, w in zip(HORIZONS, WEIGHTS):
        r += w * (closes[idx + h] / closes[idx] - 1.0)
    return float(r)


def _host_dates(col: pd.Series) -> np.ndarray:
    """Unwrap a date column to a host ``datetime64[D]`` ndarray."""
    return host_array(col).astype("datetime64[D]")


class EventDataset:
    """Events + the union trading calendar of one mixed run.

    Built from the collector's unified frame (sorted by code, date): for
    every nonzero ``sig_<family>`` entry, one :class:`SignalEvent` with
    sign-aligned dir_ave quality and its 20-row completion date. The
    union calendar (sorted unique dates across ALL codes) is the time
    axis the edge statistics difference-arrays index into.
    """

    def __init__(
        self,
        events: List[SignalEvent],
        union_dates: List[date],
        date_pos: Dict[date, int],
    ) -> None:
        self.events = events
        self.union_dates = union_dates
        self.date_pos = date_pos
        self.n_positions = len(union_dates)

    @classmethod
    def from_frame(
        cls,
        df: pd.DataFrame,
        families: List[str],
        bp_stash: Optional[Dict[str, Dict[Tuple[str, date], float]]] = None,
    ) -> "EventDataset":
        """Extract events from the unified mixed frame.

        ``bp_stash`` is ``{family: {(code, date): recorded bp evidence}}``
        (the collector stashes each algo's ``hist_confidence`` at fetch
        time); missing evidence → NaN.
        """
        bp_stash = bp_stash or {}
        union_arr = np.unique(_host_dates(df["date"]))
        union_dates: List[date] = [d.astype("datetime64[D]").item()
                                   for d in union_arr]
        date_pos = {d: i for i, d in enumerate(union_dates)}

        events: List[SignalEvent] = []
        for code, block in df.groupby("code", sort=False):
            closes = host_array(block["close_price"]).astype(np.float64)
            dates = _host_dates(block["date"])
            n = len(closes)
            for family in families:
                sig = host_array(block[f"sig_{family}"]).astype(np.float64)
                sig = np.nan_to_num(sig, nan=0.0)
                fam_bp = bp_stash.get(family, {})
                for i in np.nonzero(sig)[0]:
                    d = dates[i].astype("datetime64[D]").item()
                    q_raw = fwd_blend(closes, int(i))
                    if q_raw is None:
                        quality: Optional[float] = None
                        comp: Optional[date] = None
                    else:
                        side = BUY if sig[i] > 0 else SELL
                        quality = q_raw if side == BUY else -q_raw
                        comp = dates[i + HORIZONS[-1]].astype("datetime64[D]").item()
                    bp = fam_bp.get((code, d), float("nan"))
                    events.append(SignalEvent(
                        family=family, code=code, row_idx=int(i),
                        event_date=d, side=BUY if sig[i] > 0 else SELL,
                        conf_bp=float(bp), quality=quality,
                        completion_date=comp,
                    ))
        return cls(events, union_dates, date_pos)

    def dated_events(self) -> List[SignalEvent]:
        """Only events with a completed forward window."""
        return [e for e in self.events
                if e.quality is not None and e.completion_date is not None]

    def events_with_bp(self) -> List[SignalEvent]:
        """Only events carrying recorded bp evidence."""
        return [e for e in self.events if e.conf_bp == e.conf_bp]


def calendar_cutoff(day: date, days: int) -> date:
    """Trailing calendar-window cutoff (``day - days``)."""
    return day - timedelta(days=days)


__all__ = ["BUY", "SELL", "EventDataset", "calendar_cutoff", "fwd_blend"]
