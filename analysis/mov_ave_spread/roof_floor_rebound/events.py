"""Roof/floor REBOUND AUDIT — event computation (host core).

Port of the 2026-10 retest study (temp_scripts/study_floor_roof_retest.py)
into the pipeline's compute form: for every (sec_type, code), detect the
recorded-extreme EVENTS at each scale window (episode extreme of fresh
W-day extremes — see config.roof_floor_rebound), evaluate the rebound /
retest / hold / break outcome of each, and build the LONG frame in the
analysis.mov_ave_spreads_roof_floor_rebounds schema.

Compute layout: ONE sanctioned host-numpy boundary per (sec_type, code)
group — the close/high/low/date columns are unwrapped once via
host_array, all event detection and outcome evaluation run on plain
host numpy (positional index arithmetic over the group's full history),
and the result frame is materialized through the REAL DataFrame ctor.
Event detection itself is vectorized (rolling extremes via host pandas);
only the per-event outcome scan is a small python loop (tens of events
per code — an event/gather algorithm, the class the skill sanctions for
host cores). sec_type / side / outcome ride the frame as their int16
label codes (the label-code contract); dates stay datetime64 until the
CSV COPY writer renders them.

Because outcome fields depend on data AFTER the event date (retest
dates, later breaks), the owning step rebuilds the table WHOLESALE per
sec_type — this module is stateless over the full per-code history.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from _common.df_utils import host_array
from _common.labels import RF_REBOUND_OUTCOME_CODE
from analysis.mov_ave_spread.config.roof_floor_rebound import (
    REBOUND_BREAK_TAU,
    REBOUND_CLEAN_BREAK_DAYS,
    REBOUND_CONFIRM_K,
    REBOUND_EPISODE_GAP,
    REBOUND_PCT,
    REBOUND_RESOLVE_PCT,
    REBOUND_RETEST_GAP_DAYS,
    REBOUND_SIDE_BOTTOM,
    REBOUND_SIDE_TOP,
    REBOUND_WITHIN,
    REBOUND_WINDOWS,
    REBOUND_ZONE_TAU,
)

GROUP_KEYS: list[str] = ["sec_type", "code"]

# Frame column order == the DB schema order (PK columns first).
_EVENT_COLUMNS = (
    "sec_type", "code", "side", "period", "event_date",
    "level", "outcome", "rebound_date", "retest_date", "days_to_retest",
    "rebound_pct", "bounce_pct", "break_depth_pct", "days_to_break",
    "clean_break", "later_break", "retest_episodes",
)


def _cluster_extremes(close: np.ndarray, w: int, side: int) -> list[tuple[int, float]]:
    """Episode-extreme events at scale w (side=+1 floor min, -1 roof max).

    A candidate t satisfies close[t] == extreme of close[t-w+1, t+K]
    (rolling extremes via host pandas — vectorized). Candidates within
    REBOUND_EPISODE_GAP trading days of the cluster's first candidate
    collapse into one episode whose extreme-close date is the event.
    Returns [(t, level)] ascending in t."""
    # REAL Series ctor: the proxied one rejects host ndarrays (the
    # "Unsupported type ndarray" fallback class) — this whole function
    # is host-side by design.
    real_pd_series = getattr(pd.Series, "_fsproxy_slow", pd.Series)
    s = real_pd_series(close)
    if side > 0:
        back = s.rolling(w, min_periods=w).min()
        fwd = s[::-1].rolling(REBOUND_CONFIRM_K + 1, min_periods=1).min()[::-1]
        is_ext = (close <= back.to_numpy()) & (close <= fwd.to_numpy())
    else:
        back = s.rolling(w, min_periods=w).max()
        fwd = s[::-1].rolling(REBOUND_CONFIRM_K + 1, min_periods=1).max()[::-1]
        is_ext = (close >= back.to_numpy()) & (close >= fwd.to_numpy())
    cand = np.flatnonzero(is_ext)
    events: list[tuple[int, float]] = []
    i = 0
    while i < len(cand):
        j = i
        while j + 1 < len(cand) and cand[j + 1] - cand[i] < REBOUND_EPISODE_GAP:
            j += 1
        seg = cand[i:j + 1]
        lvl = close[seg]
        t = int(seg[int(np.argmin(lvl))]) if side > 0 else int(seg[int(np.argmax(lvl))])
        events.append((t, float(close[t])))
        i = j + 1
    return events


def _first_idx(mask: np.ndarray, start: int) -> int | None:
    """First True index in ``mask`` at or after ``start``, or None."""
    hits = np.flatnonzero(mask[start:])
    return int(hits[0]) + start if len(hits) else None


def _audit_one(
    close: np.ndarray,
    low: np.ndarray,
    high: np.ndarray,
    w: int,
    side: int,
) -> list[dict]:
    """Outcome records for one (code-implicit, window, side) cohort."""
    out: list[dict] = []
    n = len(close)
    for t, level in _cluster_extremes(close, w, side):
        rec: dict = {
            "t": t, "level": level,
            "outcome": RF_REBOUND_OUTCOME_CODE["no_rebound"],
            "rebound_t": -1, "retest_t": -1,
            "days_to_retest": np.nan, "rebound_pct": np.nan,
            "bounce_pct": np.nan, "break_depth_pct": np.nan,
            "days_to_break": np.nan, "clean_break": None,
            "later_break": None, "retest_episodes": np.nan,
        }
        # ---- rebound: close at level*(1 +- 5%) within REBOUND_WITHIN --
        if side > 0:
            reb_mask = close >= level * (1 + REBOUND_PCT)
        else:
            reb_mask = close <= level * (1 - REBOUND_PCT)
        r = _first_idx(reb_mask, t + 1)
        if r is None or r > t + REBOUND_WITHIN:
            out.append(rec)  # no_rebound — the frequency denominator
            continue
        rec["rebound_t"] = r
        rec["rebound_pct"] = close[r] / level - 1.0
        # ---- retest: first zone touch after the rebound completes ------
        if side > 0:
            touch_mask = low <= level * (1 + REBOUND_ZONE_TAU)
        else:
            touch_mask = high >= level * (1 - REBOUND_ZONE_TAU)
        u = _first_idx(touch_mask, r + 1)
        if u is None:
            rec["outcome"] = RF_REBOUND_OUTCOME_CODE["never_retested"]
            out.append(rec)
            continue
        rec["retest_t"] = u
        rec["days_to_retest"] = float(u - t)
        # ---- resolve: break vs bounce-away, whichever closes first -----
        if side > 0:
            break_mask = close < level * (1 - REBOUND_BREAK_TAU)
            held_mask = close >= level * (1 + REBOUND_RESOLVE_PCT)
        else:
            break_mask = close > level * (1 + REBOUND_BREAK_TAU)
            held_mask = close <= level * (1 - REBOUND_RESOLVE_PCT)
        b = _first_idx(break_mask, u)
        h = _first_idx(held_mask, u)
        if b is not None and (h is None or b < h):
            rec["outcome"] = RF_REBOUND_OUTCOME_CODE["broken"]
            rec["days_to_break"] = float(b - u)
            seg_end = min(b + REBOUND_CLEAN_BREAK_DAYS, n - 1)
            ext = close[u:seg_end + 1]
            depth = ((level - ext.min()) / level) if side > 0 \
                else ((ext.max() - level) / level)
            rec["break_depth_pct"] = float(depth)
            close_after = close[min(b + REBOUND_CLEAN_BREAK_DAYS, n - 1)]
            rec["clean_break"] = bool(
                close_after < level if side > 0 else close_after > level
            )
        elif h is not None:
            rec["outcome"] = RF_REBOUND_OUTCOME_CODE["held"]
            seg = close[u:min(u + REBOUND_WITHIN, n - 1) + 1]
            bounce = (seg.max() - level) / level if side > 0 \
                else (level - seg.min()) / level
            rec["bounce_pct"] = float(bounce)
            # durability: does the level EVER break after the first hold?
            b2 = _first_idx(break_mask, h + 1)
            rec["later_break"] = bool(b2 is not None)
        else:
            rec["outcome"] = RF_REBOUND_OUTCOME_CODE["unresolved"]
        # ---- durability: distinct retest episodes before break / end ---
        touches = np.flatnonzero(touch_mask)
        eps = 0
        last_touch = None
        end = b if b is not None and rec["outcome"] == RF_REBOUND_OUTCOME_CODE["broken"] else n - 1
        for d in touches:
            if d <= r or d > end:
                continue
            if last_touch is None or d - last_touch >= REBOUND_RETEST_GAP_DAYS:
                eps += 1
            last_touch = int(d)
        rec["retest_episodes"] = float(eps)
        out.append(rec)
    return out


def compute_rebound_events(df: pd.DataFrame) -> pd.DataFrame:
    """Build the LONG rebound-audit frame for the whole source scope.

    Args:
        df: source frame sorted by [sec_type, code, date] with columns
            [sec_type, code, date, price, high, low] — the FULL
            per-code history (event outcomes need all post-event data).

    Returns:
        Frame with _EVENT_COLUMNS (the DB schema order). Empty frame
        with those columns when there is no source data or no events.
    """
    real_pd_df = getattr(pd.DataFrame, "_fsproxy_slow", pd.DataFrame)
    if df.empty:
        return real_pd_df(columns=list(_EVENT_COLUMNS))

    cols: dict[str, list] = {c: [] for c in _EVENT_COLUMNS}
    for (st_code, code), g in df.groupby(GROUP_KEYS, sort=False):
        close = host_array(g["price"].to_numpy()).astype("float64")
        high = host_array(g["high"].to_numpy()).astype("float64")
        low = host_array(g["low"].to_numpy()).astype("float64")
        # one unit for the whole column (generic NaT below is [ns])
        dates = host_array(g["date"].to_numpy()).astype("datetime64[ns]")
        st = int(st_code)
        code_str = str(code)
        for side, side_code in ((1, REBOUND_SIDE_BOTTOM), (-1, REBOUND_SIDE_TOP)):
            for w in REBOUND_WINDOWS:
                for rec in _audit_one(close, low, high, w, side):
                    cols["sec_type"].append(np.int16(st))
                    cols["code"].append(code_str)
                    cols["side"].append(np.int16(side_code))
                    cols["period"].append(np.int32(w))
                    cols["event_date"].append(dates[rec["t"]])
                    cols["level"].append(rec["level"])
                    cols["outcome"].append(np.int16(rec["outcome"]))
                    cols["rebound_date"].append(
                        dates[rec["rebound_t"]]
                        if rec["rebound_t"] >= 0 else np.datetime64("NaT")
                    )
                    cols["retest_date"].append(
                        dates[rec["retest_t"]]
                        if rec["retest_t"] >= 0 else np.datetime64("NaT")
                    )
                    cols["days_to_retest"].append(rec["days_to_retest"])
                    cols["rebound_pct"].append(rec["rebound_pct"])
                    cols["bounce_pct"].append(rec["bounce_pct"])
                    cols["break_depth_pct"].append(rec["break_depth_pct"])
                    cols["days_to_break"].append(rec["days_to_break"])
                    cols["clean_break"].append(rec["clean_break"])
                    cols["later_break"].append(rec["later_break"])
                    cols["retest_episodes"].append(rec["retest_episodes"])

    if not cols["sec_type"]:
        return real_pd_df(columns=list(_EVENT_COLUMNS))
    return real_pd_df(cols)
