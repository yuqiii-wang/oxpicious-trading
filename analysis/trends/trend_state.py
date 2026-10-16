"""The per-(code, date) price TREND classification
(analysis.trends.trend_state — moved verbatim from the retired
analysis.analysis_signals.trends; the consolidation keeps analysis.*
free of any analysis_signals import while the signals tier imports
this as just another consumer).

``attach_trend`` stamps each (code, date) row of a prepared frame with
the code's OWN trend state — an int8 ``trend`` column decoding through
_common.labels.TREND_CODE (0 top / 1 bottom / 2 rise / 3 fall / 5
unknown):

  top/bottom = the price_vs_ma255 gap beyond its own TRAILING 255d
    q90/q10 with a matching 20d MA slope (the overextended extremes of
    rise/fall — the trend-split cells' strongest populations)
  rise/fall  = the RSI RE-ENTRY episodes (2026-10-15, replacing the
    retired price-vs-MA60 states — a 60d MA identifies a turn months
    after it happened): Wilder RSI(TREND_RSI_WINDOW) vs the FIXED
    momentum-zone edges 50 +- TREND_RSI_ZONE_SPREAD (gains dominate
    losses / losses dominate gains — see the constants block for why
    these edges are fixed, not per-code percentiles).
    A band touch opens a momentum episode that stays ALIVE through
    <= REENTRY_EXIT_DAYS traded rows out of band (the shared K-absorb
    re-entry semantics — the band walk: a pullback that re-enters
    within K rows is the SAME episode, so the trend does not flicker
    on every dip); the day is rise/fall while the up/down episode is
    alive AND its touch is the LATEST of the two bands (a touch of the
    opposite band ends the old episode immediately — the turn is
    identified in days, not months) AND the EFFICIENCY GUARD agrees —
    the sign of the Kaufman net change over TREND_GUARD_WINDOW rows
    (the direction the recent path actually traveled; RSI alone is
    scale-invariant and crosses the zones on a quiet market's
    direction-run wiggles — the 2026-10-15 online-candidates study
    measured this guard strictly better than the earlier MA20-slope
    agreement: faster turns, higher coverage, same quiet discipline).
    THE DEEP-ANCHOR TURN (2026-10-15): a DEEP RSI touch
    (<= TREND_RSI_DEEP / >= 100 - TREND_RSI_DEEP) opens a
    TREND_RSI_TURN_WINDOW-row recovery window in which the midline
    (50) re-cross with the guard's agreement flips the trend — after a
    capitulation RSI recovers proportionally to CONTINUED gains, so
    the opposite zone sits weeks away and the plain zones would label
    the whole rebound fall/unknown (000001's Apr-2020 recovery: the
    midline re-cross flipped it 2020-04-07 under the guard; the plain
    zones and the retired MA60 rule NEVER rose that April)
  unknown    = neither episode alive: RSI mid-band, momentum
    dissolved beyond the K window, or the code's warmup head (the
    first ~20 rows before the MA20 slope exists; the top/bottom gap
    extremes additionally need their 255-row trailing quantiles)

The trailing quantiles are SHIFTED one row (no look-ahead — a day is
never compared against a band that includes itself), and every rolling
input runs on the code's OWN traded rows (whole-frame grouped ops —
the cudf layout; no per-code loops).

The RSI is computed INLINE (the analysis.mov_ave_spread.steps.rsi
contract — Wilder ewm alpha=1/W, adjust=False, min_periods=W,
ignore_na=True) through the SAME fused CUDA kernel
(_common.df_utils.kernels.ewm): attach_trend is family-agnostic (the
options/ma_cross frames carry no RSI columns), so it cannot lean on
the mov_rsi family's fetched values.

Consumers: analysis_signals rule_base.prepare_frame (every family's
frame), the analysis_signals pipeline's trend-days store write (run.py
→ analysis.trend_days — the chart's TREND band shading via the
trend-spans API and the live tier's day-context rule-cell matching
both read that store). The classification is FAMILY-AGNOSTIC by
design — one trend state per (code, date), shared by every rule cell,
never re-derived per family.

Label vocabulary note: trend code 4 ('all') is the POOLED stamp of
the sparse non-trend-split families' signal_rules rows (options_wall /
options_vix) — never a day state; no analysis.trend_days row carries
it (the retired 'flat' day state, 2026-10-14..2026-10-15, was the
price-vs-MA60 era's unclaimed bucket).
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from _common.df_utils import (
    grouped_diff,
    grouped_rolling_agg,
    grouped_rolling_quantile,
    grouped_shift,
    host_array,
)
from analysis.trends.episodes import REENTRY_EXIT_DAYS

logger = logging.getLogger(__name__)

# The trailing window + quantile edges of the trend-split extremes
# (moved with the classifier — the single source); the SAME window
# sizes the RSI band edges (one trailing-quantile convention for the
# whole classifier).
TREND_GAP_WINDOW = 255
TREND_TOP_Q = 0.90
TREND_BOTTOM_Q = 0.10

# The RSI RE-ENTRY axis (2026-10-15, window 14 -> 10 same day): the
# Wilder window of the momentum classifier (10 — the repo's measured
# best momentum window, the rsi_window_study winner; responds in days,
# the anti-lag point of the redesign) and the midline half-spread of
# its momentum zones. The band edges are FIXED at 50 +-
# TREND_RSI_ZONE_SPREAD — deliberately NOT per-code trailing
# quantiles: RSI is a BOUNDED, regime-autocorrelated oscillator, so
# its trailing quartiles collapse onto hair-trigger edges in a quiet
# regime (band width ~1 RSI point; a sideways wiggle crosses
# constantly) and onto unreachable edges once one regime dominates the
# window (q25 ~ 2 after a steady decline — the next crash can never
# touch it; measured in temp_scripts/trend_reentry_smoke.py before
# this design was chosen). The SPREAD is window-matched: RSI(10)'s
# distribution is wider than RSI(14)'s, so the zones widen with it
# (14/7.5 -> 10/12.5; measured in temp_scripts/trend_turn_rule_study.
# py — at the unchanged 7.5 the quiet-market occupancy regressed to
# 177/200, WORSE than the retired MA60 rule's 167; at 12.5 it is
# 163/200 with the same Apr-2020 flip date and a day-faster crash
# detection). The mov_rsi family's per-code percentile BARS stay
# adaptive — those gate tail EVENTS; this labels the momentum STATE.
TREND_RSI_WINDOW = 10
TREND_RSI_ZONE_SPREAD = 12.5
# The DEEP-ANCHOR RECOVERY WINDOW: a deep RSI touch at/beyond the
# Wilder classic extreme (<= 30 down / >= 70 up) opens this many traded
# rows of turn window, inside which the midline (50) re-cross with MA20
# slope agreement flips the trend — the post-crash-rebound fix (the
# plain zones sit weeks of continued gains away after a capitulation;
# 000001's Apr-2020 rebound flipped fall->rise at 2020-04-14 under
# this rule vs NEVER in April under the plain zones). Depth-anchored
# deliberately: quiet-market wiggles (RSI grazing 44-48) never open
# the window — measured in temp_scripts/trend_turn_rule_study.py.
TREND_RSI_DEEP = 30.0
TREND_RSI_TURN_WINDOW = 40
# The EFFICIENCY GUARD window: the sign of the Kaufman net change
# (rolling sum of daily deltas) over this many rows is the rise/fall
# agreement condition — the online-candidates study's measured
# replacement of the MA20-slope guard (at n=20 the two coincide; n=10
# flips the Apr-2020 rebound a week earlier, detects the V-turn +3d /
# crash +0d, and keeps the quiet-market occupancy under the retired
# MA60 rule's — temp_scripts/trend_online_candidates_study.py).
TREND_GUARD_WINDOW = 10


# ---------------------------------------------------------------------------
#  Wilder RSI (inline, the mov_ave_spread compute_rsi contract)
# ---------------------------------------------------------------------------

def _contiguous_group_bounds(df: pd.DataFrame,
                             keys: list[str]) -> tuple[np.ndarray, np.ndarray]:
    """(starts, ends) int64 host arrays of contiguous group row ranges
    over ``keys`` — the rsi.py _host_group_bounds generalized to
    arbitrary key columns. Caller guarantees df sorted by [*keys,
    date]. Unwraps the proxy ndarrays ONCE at the pandas→numpy
    boundary."""
    n = len(df)
    change = np.empty(n, dtype=np.bool_)
    change[0] = True
    if n > 1:
        any_change = np.zeros(n - 1, dtype=np.bool_)
        for k in keys:
            col = host_array(df[k].to_numpy())
            any_change |= col[1:] != col[:-1]
        change[1:] = any_change
    pos = np.flatnonzero(change)
    return pos.astype("int64"), np.append(pos[1:], n).astype("int64")


_ewm_gpu_warned = False


def _grouped_ewm_mean(s: pd.Series, df: pd.DataFrame, keys: list[str],
                      alpha: float, min_periods: int) -> pd.Series:
    """Grouped EWM mean (Wilder smoothing: adjust=False, ignore_na=True)
    aligned to df.index — the rsi.py _grouped_ewm_pandas contract.

    GPU route FIRST at every size (unlike rsi.py's 200K-row
    performance threshold): the signals pipeline's zero-[cudf
    fallback] bar — the pandas ``groupby.ewm`` path logs a fallback
    line per call under cudf.pandas (cuDF lacks grouped-ewm), and the
    kernel launch beats those lines at any size. Any GPU failure
    (no cupy / no device / compile error) falls back to pandas — on a
    CPU-only host that fallback is the sanctioned no-GPU case."""
    global _ewm_gpu_warned
    try:
        import cupy as cp
        from _common.df_utils.kernels.ewm import grouped_ewm_mean

        starts, ends = _contiguous_group_bounds(df, keys)
        x = host_array(
            pd.to_numeric(s, errors="coerce").to_numpy()
        ).astype("float64")
        dev = grouped_ewm_mean(
            cp.asarray(x), cp.asarray(starts), cp.asarray(ends),
            alpha=alpha, min_periods=min_periods,
        )
        # materialize via the REAL pandas Series ctor (the sanctioned
        # boundary — the proxy ctor dispatches on the proxy Index)
        real_series = getattr(pd.Series, "_fsproxy_slow", pd.Series)
        return real_series(dev.get(), index=host_array(df.index))
    except Exception as e:  # noqa: BLE001 — any GPU failure → pandas
        if not _ewm_gpu_warned:
            _ewm_gpu_warned = True
            logger.info(
                "[trend rsi] grouped-ewm GPU route unavailable "
                f"({type(e).__name__}: {e}) — pandas groupby.ewm fallback"
            )
    key_cols = [df[k] for k in keys]
    res = (
        s.groupby(key_cols, sort=False)
        .ewm(alpha=alpha, adjust=False, min_periods=min_periods,
             ignore_na=True)
        .mean()
    )
    res = res.reset_index(level=list(range(len(keys))), drop=True)
    return res.reindex(df.index)


def _wilder_rsi(df: pd.DataFrame, window: int) -> pd.Series:
    """Wilder RSI(window) of the ``price`` column per code, on the
    code's OWN traded rows — the analysis.mov_ave_rsi contract (ewm
    alpha=1/W, adjust=False, min_periods=W, ignore_na=True; NaN-price
    rows keep NaN gain/loss and are skipped by the smoothing). The
    ``_trend_delta`` helper column rides df until attach_trend's final
    drop."""
    keys = ["code"]
    grouped_diff(df, keys, "price", out_names="_trend_delta",
                 periods=1, sort=False)
    delta = df["_trend_delta"]
    # NaN delta must keep NaN gain/loss (NOT 0) so ignore_na skips it
    gain = delta.where(delta > 0, 0.0).where(delta.notna())
    loss = (-delta).where(delta < 0, 0.0).where(delta.notna())
    avg_gain = _grouped_ewm_mean(gain, df, keys, 1.0 / window, window)
    avg_loss = _grouped_ewm_mean(loss, df, keys, 1.0 / window, window)
    # RSI = 100 - 100/(1+RS); 100 pure-uptrend / 0 pure-downtrend /
    # NaN flat (0/0) — the rsi.py _rsi_from_avgs edge handling
    rs = avg_gain / avg_loss
    rsi = 100.0 - 100.0 / (1.0 + rs)
    rsi = rsi.where(~((avg_loss == 0) & (avg_gain > 0)), 100.0)
    rsi = rsi.where(~((avg_gain == 0) & (avg_loss > 0)), 0.0)
    return rsi


# ---------------------------------------------------------------------------
#  The classifier
# ---------------------------------------------------------------------------

def attach_trend(df: pd.DataFrame) -> pd.DataFrame:
    """Stamp the frame (one row per (code, date), sorted ASC, carrying
    ``price``) with its int8 ``trend`` column — the classification
    above. The rolling helper columns are dropped before returning;
    the input frame is mutated in place (columns added then removed)
    and returned for chaining."""
    keys = ["code"]
    for w in (20, 255):
        df[f"_ma{w}"] = grouped_rolling_agg(
            df, keys, "price", w, min_periods=w,
        )
    prev20 = grouped_shift(
        df, keys, ["_ma20"], out_names=["_prev20"], periods=1,
    )["_prev20"]
    df["_slope"] = df["_ma20"] - prev20.fillna(df["_ma20"])
    df["_gap255"] = df["price"] / df["_ma255"] - 1.0
    for tag, q in (("_q90", TREND_TOP_Q),
                   ("_q10", TREND_BOTTOM_Q)):
        df[f"{tag}_raw"] = grouped_rolling_quantile(
            df, keys, "_gap255", TREND_GAP_WINDOW,
            TREND_GAP_WINDOW, q,
        )
        df[tag] = grouped_shift(
            df, keys, [f"{tag}_raw"], out_names=[f"{tag}_sh"],
            periods=1,
        )[f"{tag}_sh"]

    # -- the RSI re-entry episodes (rise/fall) -------------------------
    df["_rsi"] = _wilder_rsi(df, TREND_RSI_WINDOW)
    rsi = df["_rsi"]
    # the EFFICIENCY GUARD (2026-10-15, the online-candidates study's
    # measured betterment): the sign of the Kaufman net change over
    # TREND_GUARD_WINDOW rows — the direction the recent path actually
    # traveled — REPLACES the MA20-slope agreement (at n=20 the two are
    # the same signal; n=10 is the fast end). Measured on 000001 +
    # synthetic (temp_scripts/trend_online_candidates_study.py): the
    # Apr-2020 rebound flips a week earlier (2020-04-07, 17/21 rise
    # days), V-turn +3d / crash +0d, quiet-market occupancy 162/200
    # (parity with the slope guard, still under the retired MA60
    # rule's 167), coverage UP (82% labeled) — at ~+10 label flips/yr.
    grouped_diff(df, keys, "price", out_names="_guard_d", periods=1,
                 sort=False)
    guard_net = grouped_rolling_agg(
        df, keys, "_guard_d", TREND_GUARD_WINDOW,
        min_periods=TREND_GUARD_WINDOW, agg="sum",
    )
    guard_up = guard_net > 0
    guard_dn = guard_net < 0
    in_top = rsi >= 50.0 + TREND_RSI_ZONE_SPREAD
    in_bot = rsi <= 50.0 - TREND_RSI_ZONE_SPREAD

    work = pd.DataFrame({"code": df["code"]}, index=df.index)
    work["_pos"] = work.groupby("code", sort=False).cumcount() + 1

    # the DEEP-ANCHOR RECOVERY WINDOW (2026-10-15, the measured fix for
    # the post-crash rebound): a DEEP opposite touch (RSI <= 30 / >= 70,
    # the Wilder classic extremes) opens a TREND_RSI_TURN_WINDOW-row
    # window in which the MIDLINE re-cross with the guard's agreement
    # flips the trend — after a capitulation, RSI recovers
    # proportionally to CONTINUED gains, so the opposite zone sits
    # weeks away (000001's Apr-2020 rebound: +4.6% off the Mar low,
    # RSI pinned 44-56 all month, the zone edge unreachable until Apr
    # 30 — the plain zones labeled the whole recovery fall/unknown).
    # Depth anchors, never a plain touch: a quiet market's wiggles
    # graze 44-48 and must NOT open the window (measured: quiet
    # occupancy under the retired MA60 rule's 167 — the looser 35/65
    # anchor measured over it and was rejected).
    work["_dt"] = (rsi <= TREND_RSI_DEEP).astype("int64") * work["_pos"]
    work["_db"] = (rsi >= 100.0 - TREND_RSI_DEEP).astype("int64") \
        * work["_pos"]
    last_deep_dn = work.groupby("code", sort=False)["_dt"].cummax()
    last_deep_up = work.groupby("code", sort=False)["_db"].cummax()
    dn_recent = (last_deep_dn > 0) \
        & ((work["_pos"] - last_deep_dn) <= TREND_RSI_TURN_WINDOW)
    up_recent = (last_deep_up > 0) \
        & ((work["_pos"] - last_deep_up) <= TREND_RSI_TURN_WINDOW)
    turn_up = dn_recent & (rsi >= 50.0)
    turn_dn = up_recent & (rsi <= 50.0)
    in_top = in_top | (turn_up & guard_up)
    in_bot = in_bot | (turn_dn & guard_dn)

    # episode liveness: last band touch = per-code cummax of
    # (traded-day position on in-band days, else 0), INCLUSIVE of
    # today — the EpisodeDetector packing without its shift; the
    # episode is alive within REENTRY_EXIT_DAYS rows of the touch and
    # the LATEST touch of the two bands owns the day
    work["_t"] = in_top.astype("int64") * work["_pos"]
    work["_b"] = in_bot.astype("int64") * work["_pos"]
    last_top = work.groupby("code", sort=False)["_t"].cummax()
    last_bot = work.groupby("code", sort=False)["_b"].cummax()
    rise_alive = (last_top > 0) \
        & ((work["_pos"] - last_top) <= REENTRY_EXIT_DAYS)
    fall_alive = (last_bot > 0) \
        & ((work["_pos"] - last_bot) <= REENTRY_EXIT_DAYS)
    rise_ep = rise_alive & (last_top >= last_bot)
    fall_ep = fall_alive & (last_bot > last_top)

    slope = df["_slope"].fillna(0.0)
    gap = df["_gap255"].fillna(0.0)
    slope_pos = slope > 0
    slope_neg = slope < 0
    # the rise/fall states need the episode alive AND the efficiency
    # guard agreeing (the label agreement — same guard as the turn
    # touches above); the MA20 slope survives only as the top/bottom
    # extremes' own condition below
    rise = rise_ep & guard_up
    fall = fall_ep & guard_dn
    trend = pd.Series(np.int8(5), index=df.index, dtype="int8")  # unknown
    trend = trend.where(~fall, np.int8(3))                        # fall
    trend = trend.where(~rise, np.int8(2))                        # rise
    deep_low = df["_q10"].notna() & (gap < df["_q10"].fillna(0.0)) \
        & slope_neg
    deep_high = df["_q90"].notna() & (gap > df["_q90"].fillna(0.0)) \
        & slope_pos
    trend = trend.where(~deep_low, np.int8(1))                   # bottom
    trend = trend.where(~deep_high, np.int8(0))                  # top
    df["trend"] = trend.astype("int8")
    return df.drop(columns=[
        c for c in df.columns
        if c.startswith("_ma") or c.startswith("_q")
        or c in ("_slope", "_gap255", "_rsi", "_trend_delta", "_prev20",
                 "_guard_d")
    ])


__all__ = ["attach_trend", "TREND_GAP_WINDOW", "TREND_TOP_Q",
           "TREND_BOTTOM_Q", "TREND_RSI_WINDOW", "TREND_RSI_ZONE_SPREAD",
           "TREND_RSI_DEEP", "TREND_RSI_TURN_WINDOW", "TREND_GUARD_WINDOW"]
