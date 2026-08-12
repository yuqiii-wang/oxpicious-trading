"""Pure-pandas risk computations — no DB inside.

Each function takes trade_decision rows (as dicts or a DataFrame) for one
(seq_id, code) and returns either a single risk_seq row or a list of
risk_period rows. Side-effect free: callers (the orchestrator in
``__init__.py``) handle DB writes.

Risk philosophy:
  - ``_compute_concentration`` finds the worst 30-day cluster of |P&L|
    (still computed for the concentration_ratio column / UI hotspot flag).
  - ``_compute_top_drawdowns`` finds the top-3 peak-to-trough declines in
    cumulative realized P&L (persisted as drawdown_1st/2nd/3rd columns).
  - ``_compute_risk_score`` is the EXPONENTIAL rolling-window risk score:
    for each window W ∈ {1d, 30d, 90d, 365d}, the worst W-day rolling LOSS
    (realized, and separately unrealized MTM dip + window-end residual)
    contributes ``exp(k · loss_fraction / threshold_W) - 1``, where the
    threshold_W comes from a log-curve fit through (month=25%, season=50%,
    year=75% of total_abs_pnl). Unrealized is weighted at 30% of realized.
    LOSSES ONLY — gain windows contribute 0. A consecutive losing-month
    STREAK component is added on top (``_streak_contribution``) so a
    sustained multi-month bleed grows the score exponentially. A PER-PERIOD
    OVERRIDE component (``_period_override_risk``) adds three rule-based
    signals — monthly additional unrealized loss (>10% of capital), monthly
    gain (>50%), seasonal gain (>80%) — each scaled so hitting its threshold
    contributes 6.0 (pushes grade to HIGH on its own).
  - ``compute_risk_periods`` rolls P&L up per year/season/month for the UI
    chart and flags periods that dominate (hotspot) or move against the run
    total (counter-trend).
"""
from __future__ import annotations

import datetime
import math
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from strategy._risks.constants import (
    WINDOW_DAYS, HOTSPOT_SHARE_THRESHOLD,
    RISK_WINDOW_DAYS, _LOG_FIT_A, _LOG_FIT_B, MIN_LOSS_THRESHOLD,
    RISK_EXP_K, MAX_LOSS_RATIO, UNREALIZED_WEIGHT,
    LOSING_STREAK_MIN, LOSING_STREAK_THRESHOLD_MONTHS,
    RISK_GRADE_LOW_BOUND, RISK_GRADE_MODERATE_BOUND, RISK_GRADE_ELEVATED_BOUND,
    MONTHLY_UNREALIZED_LOSS_HIGH_THRESHOLD,
    MONTHLY_GAIN_HIGH_THRESHOLD, SEASONAL_GAIN_HIGH_THRESHOLD,
)
from strategy._risks.periods import period_value


# ---------------------------------------------------------------------------
#  Concentration — largest 30-day rolling sum of |realized_pnl|
# ---------------------------------------------------------------------------
def _compute_concentration(
    sells: pd.DataFrame,
    total_abs_pnl: float,
) -> Tuple[float, float, Optional[datetime.date], Optional[datetime.date]]:
    """Compute the 30-day rolling |pnl| concentration.

    Returns (max_30d_abs_pnl, concentration_ratio, window_start, window_end).
    The window is anchored at each SELL's exec_date and spans [d-29, d].
    """
    if sells.empty or total_abs_pnl <= 0:
        return 0.0, 0.0, None, None

    # Convert exec_date (python date) to pandas Timestamp for vectorized math.
    ts = pd.to_datetime(sells["exec_date"])
    abs_pnl = sells["abs_pnl"].to_numpy()  # already |realized_pnl|
    max_window = 0.0
    best_start: Optional[datetime.date] = None
    best_end: Optional[datetime.date] = None
    for i in range(len(ts)):
        d = ts.iloc[i]
        lo = d - pd.Timedelta(days=WINDOW_DAYS - 1)
        mask = (ts >= lo) & (ts <= d)
        w = float(abs_pnl[mask.to_numpy()].sum())
        if w > max_window:
            max_window = w
            best_start = lo.date()
            best_end = d.date()
    ratio = max_window / total_abs_pnl if total_abs_pnl > 0 else 0.0
    return max_window, ratio, best_start, best_end


# ---------------------------------------------------------------------------
#  Top-3 drawdowns — worst peak-to-trough declines in cumulative realized P&L
# ---------------------------------------------------------------------------
def _compute_top_drawdowns(
    sells: pd.DataFrame,
) -> Tuple[float, List[Optional[datetime.date]], List[Optional[float]]]:
    """Find the top-3 deepest peak-to-trough drawdowns in cumulative realized
    P&L (over the SELL timeline).

    A drawdown episode is a maximal span where cumulative P&L is below its
    running peak; the episode ends when cumulative P&L reaches a new high.
    Returns (max_dd_magnitude, [trough_date_1st, trough_date_2nd,
    trough_date_3rd], [magnitude_1st, magnitude_2nd, magnitude_3rd]) where
    max_dd_magnitude is the most-negative episode magnitude (<= 0) used to
    drive risk_score, the dates are the trough dates of the top-3 episodes,
    and the magnitudes are the per-episode signed P&L deltas (trough -
    peak, <= 0). 1st = worst. Fewer than 3 episodes → trailing slots None.
    """
    if sells.empty:
        return 0.0, [None, None, None], [None, None, None]
    cum = sells["realized_pnl"].cumsum().to_numpy()
    dates = sells["exec_date"].to_numpy()
    running_max = float(cum[0])
    peak_val = float(cum[0])
    trough_val: Optional[float] = None
    trough_date: Optional[datetime.date] = None
    in_dd = False
    episodes: List[Tuple[float, Optional[datetime.date]]] = []
    for i in range(len(cum)):
        v = float(cum[i])
        if v > running_max:
            # new high — close any open drawdown episode
            if in_dd:
                episodes.append((trough_val - peak_val, trough_date))
                in_dd = False
            running_max = v
            peak_val = v
        elif v < running_max:
            if not in_dd:
                in_dd = True
                trough_val = v
                trough_date = _as_date(dates[i])
            elif v < trough_val:
                trough_val = v
                trough_date = _as_date(dates[i])
    # close any episode still open at the end of the timeline
    if in_dd:
        episodes.append((trough_val - peak_val, trough_date))
    if not episodes:
        return 0.0, [None, None, None], [None, None, None]
    max_dd = float(min(e[0] for e in episodes))
    episodes.sort(key=lambda e: e[0])  # most negative (worst) first
    top = episodes[:3]
    trough_dates: List[Optional[datetime.date]] = [e[1] for e in top]
    magnitudes: List[Optional[float]] = [float(e[0]) for e in top]
    while len(trough_dates) < 3:
        trough_dates.append(None)
    while len(magnitudes) < 3:
        magnitudes.append(None)
    return max_dd, trough_dates, magnitudes


# ---------------------------------------------------------------------------
#  Price-based drawdowns — worst unrealized peak-to-trough decline of the
#  security's CLOSE price. Two flavours:
#    1) since unzero position: over any maximal span where position > 0.
#    2) since last buy:        from a BUY entry (seed peak = fill_price)
#                              until the next decision.
#  Both are returned as signed fractional ratios (<= 0; 0 = no drop).
# ---------------------------------------------------------------------------
def _max_dd_in_range(
    prices: pd.DataFrame,
    start_date,
    end_date,
    seed_peak: Optional[float] = None,
) -> Tuple[float, Optional[datetime.date], Optional[datetime.date]]:
    """Worst peak-to-trough drawdown of close_price in [start, end].

    ``seed_peak`` (when given) initializes the running peak — used for the
    "since last buy" flavour so the entry fill_price is the reference. When
    ``None`` the peak starts at the first close in the range ("since unzero
    position" flavour).

    Returns (drawdown_ratio, peak_date, trough_date). drawdown_ratio <= 0.
    """
    mask = (prices["date"] >= start_date) & (prices["date"] <= end_date)
    seg = prices.loc[mask]
    if seg.empty:
        return 0.0, None, None
    closes = seg["close_price"].to_numpy()
    dates = seg["date"].to_numpy()
    peak = float(seed_peak) if seed_peak is not None else float(closes[0])
    peak_date: Optional[datetime.date] = (
        start_date if seed_peak is not None else _as_date(dates[0])
    )
    max_dd = 0.0
    max_dd_peak: Optional[datetime.date] = None
    max_dd_trough: Optional[datetime.date] = None
    for i, c in enumerate(closes):
        c = float(c)
        if c > peak:
            peak = c
            peak_date = _as_date(dates[i])
        dd = (c - peak) / peak if peak > 0 else 0.0
        if dd < max_dd:
            max_dd = dd
            max_dd_peak = peak_date
            max_dd_trough = _as_date(dates[i])
    return float(max_dd), max_dd_peak, max_dd_trough


def _as_date(v) -> Optional[datetime.date]:
    """Coerce a numpy/pandas/python date value to python ``datetime.date``."""
    if v is None:
        return None
    if isinstance(v, datetime.date) and not isinstance(v, datetime.datetime):
        return v
    if isinstance(v, datetime.datetime):
        return v.date()
    # numpy datetime64 -> pandas Timestamp -> date
    return pd.Timestamp(v).date()


def _compute_price_drawdowns(
    df: pd.DataFrame,
    prices: pd.DataFrame,
) -> Tuple[float, Optional[datetime.date], Optional[datetime.date],
           float, Optional[datetime.date], Optional[datetime.date]]:
    """Compute the two price-based drawdown stats.

    ``df`` is the decisions DataFrame (sorted by exec_date) with columns
    side / exec_date / fill_price / position_after. ``prices`` is a
    DataFrame with date / close_price (sorted by date).

    Returns (drop_unzero, unzero_peak, unzero_trough,
             drop_since_buy, buy_peak, buy_trough).
    """
    drop_unzero = 0.0
    unzero_peak: Optional[datetime.date] = None
    unzero_trough: Optional[datetime.date] = None
    drop_since_buy = 0.0
    buy_peak: Optional[datetime.date] = None
    buy_trough: Optional[datetime.date] = None

    if df.empty or prices.empty:
        return (drop_unzero, unzero_peak, unzero_trough,
                drop_since_buy, buy_peak, buy_trough)

    last_price_date = _as_date(prices["date"].iloc[-1])

    # ---- 1) deepest drop since unzero position -----------------------------
    # A maximal unzero period runs from the exec_date of the decision that
    # first makes position_after > 0 to the exec_date of the decision that
    # brings it back to 0.
    holding = False
    period_start = None
    for _, d in df.iterrows():
        pa = d.get("position_after")
        pa = float(pa) if pa is not None and pd.notna(pa) else 0.0
        exec_date = d["exec_date"]
        if not holding and pa > 0:
            holding = True
            period_start = exec_date
        elif holding and pa == 0:
            dd, pdate, tdate = _max_dd_in_range(
                prices, period_start, exec_date)
            if dd < drop_unzero:
                drop_unzero = dd
                unzero_peak = pdate
                unzero_trough = tdate
            holding = False
            period_start = None
    # Still holding at the end → extend to the last available price date.
    if holding and period_start is not None and last_price_date is not None:
        dd, pdate, tdate = _max_dd_in_range(
            prices, period_start, last_price_date)
        if dd < drop_unzero:
            drop_unzero = dd
            unzero_peak = pdate
            unzero_trough = tdate

    # ---- 2) deepest drop since last buy ------------------------------------
    # For each BUY, the window is [buy.exec_date, next_decision.exec_date]
    # (or the last price date if it is the final decision). The peak is
    # seeded with the BUY fill_price so the drop is measured from entry.
    buy_mask = df["side"] == "BUY"
    buy_positions = df.index[buy_mask].tolist()
    for k, bi in enumerate(buy_positions):
        buy = df.loc[bi]
        start_date = buy["exec_date"]
        seed = buy.get("fill_price")
        seed = float(seed) if seed is not None and pd.notna(seed) else None
        if k + 1 < len(buy_positions):
            next_idx = buy_positions[k + 1]
            # Window ends at the next decision after this buy (any side).
            # Search the df for the first row whose index > bi.
            after = df.index[df.index > bi]
            end_date = df.loc[after[0], "exec_date"] if len(after) else last_price_date
        else:
            # Last buy: window extends to the next decision after it, or the
            # last price date if it is the final decision overall.
            after = df.index[df.index > bi]
            end_date = df.loc[after[0], "exec_date"] if len(after) else last_price_date
        if end_date is None:
            end_date = last_price_date
        dd, pdate, tdate = _max_dd_in_range(
            prices, start_date, end_date, seed_peak=seed)
        if dd < drop_since_buy:
            drop_since_buy = dd
            buy_peak = pdate
            buy_trough = tdate

    return (drop_unzero, unzero_peak, unzero_trough,
            drop_since_buy, buy_peak, buy_trough)


# ---------------------------------------------------------------------------
#  Risk grade — LOW / MODERATE / ELEVATED / HIGH
# ---------------------------------------------------------------------------
def _risk_grade(risk_score: float) -> str:
    """Map the (absolute) exponential rolling-window risk_score to a grade.

    Boundaries on the new scale (k = ln 2 ⇒ one window at threshold = 1.0):
        < 1.0  → LOW       (no window reaches its loss threshold)
        < 3.0  → MODERATE  (one window past threshold, or several near it)
        < 6.0  → ELEVATED  (multiple windows past threshold)
        else   → HIGH
    """
    if risk_score < RISK_GRADE_LOW_BOUND:
        return "LOW"
    if risk_score < RISK_GRADE_MODERATE_BOUND:
        return "MODERATE"
    if risk_score < RISK_GRADE_ELEVATED_BOUND:
        return "ELEVATED"
    return "HIGH"


# ---------------------------------------------------------------------------
#  Exponential rolling-window risk score
# ---------------------------------------------------------------------------
def _loss_threshold(window_days: int) -> float:
    """Log-fit loss threshold (fraction of total_abs_pnl) for a rolling
    window of ``window_days`` days.

    Fits L(T) = a·ln(T) + b through the (month=25%, season=50%, year=75%)
    anchors (T in months). Clamped to MIN_LOSS_THRESHOLD for very short
    windows where the log curve would go negative (e.g. 1 day).
    """
    months = window_days / 30.0
    raw = _LOG_FIT_A * math.log(months) + _LOG_FIT_B
    return max(raw, MIN_LOSS_THRESHOLD)


def _exp_contribution(loss_fraction: float, threshold: float) -> float:
    """Exponential risk contribution for one window.

    ``exp(k · min(ratio, MAX_LOSS_RATIO)) - 1`` where ratio =
    loss_fraction / threshold and k = ln 2. Returns 0 when loss_fraction
    <= 0 (gains contribute nothing). At loss_fraction = threshold the
    contribution is exactly 1.0; the ratio is capped at MAX_LOSS_RATIO to
    keep the score bounded for anomalous tiny-capital runs.
    """
    if loss_fraction <= 0 or threshold <= 0:
        return 0.0
    ratio = min(loss_fraction / threshold, MAX_LOSS_RATIO)
    return math.exp(RISK_EXP_K * ratio) - 1.0


def _longest_losing_streak(sells: pd.DataFrame) -> int:
    """Length of the longest run of consecutive losing trading months.

    Months with SELL activity are grouped by ``YYYY-MM`` (summing
    realized_pnl) and ordered chronologically; no-trade months are SKIPPED
    (they neither extend nor break the run). A "losing month" is one whose
    summed realized_pnl < 0. Returns 0 when no losing month exists.
    """
    if sells.empty:
        return 0
    s = sells[["exec_date", "realized_pnl"]].copy()
    s["period"] = s["exec_date"].apply(lambda d: period_value(d, "month"))
    monthly = s.groupby("period")["realized_pnl"].sum().sort_index()
    longest = 0
    cur = 0
    for pnl in monthly:
        if pnl < 0:
            cur += 1
            if cur > longest:
                longest = cur
        else:
            cur = 0
    return longest


def _streak_contribution(streak: int) -> float:
    """Exponential risk contribution for a consecutive losing-month streak.

    Kicks in only when ``streak >= LOSING_STREAK_MIN`` (a single isolated
    losing month contributes 0 — "continuous losses for MULTIPLE months" is
    the signal). contribution = ``exp(k · streak / THRESHOLD) - 1`` with
    k = ln 2 and the ratio capped at MAX_LOSS_RATIO. With THRESHOLD = 2:
    2-mo → 1.0, 4-mo → 3.0, 6-mo → 7.0, 8-mo → 15.0 (cap).
    """
    if streak < LOSING_STREAK_MIN:
        return 0.0
    ratio = min(streak / LOSING_STREAK_THRESHOLD_MONTHS, MAX_LOSS_RATIO)
    return math.exp(RISK_EXP_K * ratio) - 1.0


def _rolling_realized_loss(sells: pd.DataFrame, window_days: int) -> float:
    """Worst (most negative) rolling ``window_days``-day sum of realized_pnl.

    Returns a value <= 0 (0 when no losing window exists). Uses pandas
    time-based rolling so sparse SELL dates are handled correctly.
    """
    if sells.empty:
        return 0.0
    s = sells[["exec_date", "realized_pnl"]].copy()
    s["_ts"] = pd.to_datetime(s["exec_date"])
    s = s.set_index("_ts").sort_index()
    roll_sum = s["realized_pnl"].rolling(f"{window_days}D").sum()
    worst = float(roll_sum.min())
    return worst if worst < 0 else 0.0


def _rolling_unrealized_loss(
    daily_df: pd.DataFrame, window_days: int,
) -> Tuple[float, float]:
    """Worst rolling ``window_days``-day unrealized MTM loss.

    Returns ``(max_loss, end_loss)`` where:
      - ``max_loss`` = most negative rolling MIN of unrealized_pnl (deepest
        intra-window MTM dip).
      - ``end_loss`` = unrealized_pnl at the END of the window that produced
        ``max_loss`` (did the dip persist to window-end, or recover?).

    Both are <= 0; (0.0, 0.0) when no losing window exists.
    """
    if daily_df.empty:
        return 0.0, 0.0
    s = daily_df[["trade_date", "unrealized_pnl"]].copy()
    s["_ts"] = pd.to_datetime(s["trade_date"])
    s = s.set_index("_ts").sort_index()
    roll_min = s["unrealized_pnl"].rolling(f"{window_days}D").min()
    if roll_min.empty:
        return 0.0, 0.0
    worst_idx = roll_min.idxmin()
    worst_min = float(roll_min.loc[worst_idx])
    if not math.isfinite(worst_min) or worst_min >= 0:
        return 0.0, 0.0
    end_val = float(s["unrealized_pnl"].loc[worst_idx])
    return worst_min, (end_val if end_val < 0 else 0.0)


# ---------------------------------------------------------------------------
#  Per-period HIGH-risk override components
# ---------------------------------------------------------------------------
# Three components based on per-period P&L as a fraction of capital_base
# (total_buy_cost). Each is scaled by RISK_GRADE_ELEVATED_BOUND (6.0) so
# that hitting its threshold contributes 6.0 to the score — enough to push
# the grade to HIGH on its own. Below threshold the contribution is
# exponential (proportional), so a near-threshold period still raises the
# score meaningfully without dominating it.
#
# The three rules (per spec):
#   1. Monthly additional unrealized loss: month-over-month MTM change in
#      unrealized_pnl < -10% of capital → HIGH.
#   2. Monthly gain: realized_pnl + max intra-month unrealized_pnl >
#      50% of capital → HIGH.
#   3. Seasonal gain: realized_pnl + max intra-season unrealized_pnl >
#      80% of capital → HIGH.
def _period_override_risk(
    sells: pd.DataFrame,
    daily_df: pd.DataFrame,
    capital_base: float,
) -> float:
    """Per-period HIGH-risk override component for the risk_score.

    Returns the summed contribution of the three override rules. Each rule
    uses ``scale * (exp(k · min(ratio, MAX_LOSS_RATIO)) - 1)`` where
    ``scale = RISK_GRADE_ELEVATED_BOUND`` (6.0) so that at-threshold
    (ratio = 1) the contribution is exactly 6.0 — pushing the total score
    to HIGH on its own. Gains/losses below their thresholds contribute
    proportionally (exponential), so a near-threshold period still raises
    the score without dominating it.

    ``capital_base`` = total_buy_cost (peak capital deployed), the same
    "% of capital" denominator used by the rolling-window loss_fraction.
    """
    if capital_base <= 0:
        return 0.0
    scale = RISK_GRADE_ELEVATED_BOUND  # 6.0 — at-threshold contribution
    score = 0.0

    # ---- Component 1: Monthly additional unrealized loss (MTM delta) ----
    # The "additional" unrealized loss in a month = the MTM change for that
    # month = end-of-month unrealized_pnl minus end-of-previous-month
    # unrealized_pnl. The first month bases off 0 (no open position before
    # the first BUY). Only the WORST (most negative) monthly delta drives
    # the component — a single bad month is the signal.
    if not daily_df.empty:
        d = daily_df[["trade_date", "unrealized_pnl"]].copy()
        d["month"] = d["trade_date"].apply(lambda x: period_value(x, "month"))
        d = d.sort_values("trade_date")
        # End-of-month unrealized_pnl (last trading day in each month).
        ends = d.groupby("month", as_index=False).last()
        ends["mtm_change"] = ends["unrealized_pnl"].diff().fillna(
            ends["unrealized_pnl"].iloc[0]
        )
        worst_delta = float(ends["mtm_change"].min())
        if worst_delta < 0:
            threshold_val = MONTHLY_UNREALIZED_LOSS_HIGH_THRESHOLD * capital_base
            ratio = min(abs(worst_delta) / threshold_val, MAX_LOSS_RATIO)
            score += scale * (math.exp(RISK_EXP_K * ratio) - 1.0)

    # ---- Components 2 & 3: Monthly / seasonal gain -----------------------
    # Gain basis = realized_pnl + max intra-period unrealized_pnl (raw sum,
    # matches the UI "Total P&L" bar). Only the BEST (largest) per-period
    # gain drives each component — a single outsized-gain period is the
    # signal. Realized comes from SELLs; max unrealized from the daily
    # series. Periods with no SELLs contribute 0 realized (but may still
    # have a max unrealized gain from an open position).
    if not sells.empty or not daily_df.empty:
        # Per-period realized_pnl sums (SELLs only; 0 for periods with no SELLs).
        monthly_realized: Dict[str, float] = {}
        seasonal_realized: Dict[str, float] = {}
        if not sells.empty:
            s = sells[["exec_date", "realized_pnl"]].copy()
            s["month"] = s["exec_date"].apply(lambda d: period_value(d, "month"))
            s["season"] = s["exec_date"].apply(lambda d: period_value(d, "season"))
            monthly_realized = s.groupby("month")["realized_pnl"].sum().to_dict()
            seasonal_realized = s.groupby("season")["realized_pnl"].sum().to_dict()

        # Per-period max intra-period unrealized_pnl (peak MTM).
        max_monthly_unreal: Dict[str, float] = {}
        max_seasonal_unreal: Dict[str, float] = {}
        if not daily_df.empty:
            d2 = daily_df[["trade_date", "unrealized_pnl"]].copy()
            d2["month"] = d2["trade_date"].apply(lambda x: period_value(x, "month"))
            d2["season"] = d2["trade_date"].apply(lambda x: period_value(x, "season"))
            max_monthly_unreal = d2.groupby("month")["unrealized_pnl"].max().to_dict()
            max_seasonal_unreal = d2.groupby("season")["unrealized_pnl"].max().to_dict()

        # Monthly gain = realized + max intra-month unrealized (raw sum).
        all_months = set(monthly_realized) | set(max_monthly_unreal)
        best_month_gain = 0.0
        for m in all_months:
            gain = float(monthly_realized.get(m, 0.0)) + \
                float(max_monthly_unreal.get(m, 0.0))
            if gain > best_month_gain:
                best_month_gain = gain
        if best_month_gain > 0:
            threshold_val = MONTHLY_GAIN_HIGH_THRESHOLD * capital_base
            ratio = min(best_month_gain / threshold_val, MAX_LOSS_RATIO)
            score += scale * (math.exp(RISK_EXP_K * ratio) - 1.0)

        # Seasonal gain = realized + max intra-season unrealized (raw sum).
        all_seasons = set(seasonal_realized) | set(max_seasonal_unreal)
        best_season_gain = 0.0
        for sz in all_seasons:
            gain = float(seasonal_realized.get(sz, 0.0)) + \
                float(max_seasonal_unreal.get(sz, 0.0))
            if gain > best_season_gain:
                best_season_gain = gain
        if best_season_gain > 0:
            threshold_val = SEASONAL_GAIN_HIGH_THRESHOLD * capital_base
            ratio = min(best_season_gain / threshold_val, MAX_LOSS_RATIO)
            score += scale * (math.exp(RISK_EXP_K * ratio) - 1.0)

    return score


def _compute_risk_score(
    sells: pd.DataFrame,
    daily_df: pd.DataFrame,
    capital_base: float,
) -> float:
    """Exponential rolling-window risk score.

    For each window W ∈ RISK_WINDOW_DAYS (1d, 30d, 90d, 365d):
      - REALIZED: worst W-day rolling sum of realized_pnl (losses only).
      - UNREALIZED: worst W-day rolling MIN of unrealized_pnl (max_loss)
        plus that window's end unrealized_pnl (end_loss).

    Each loss contributes ``exp(k · loss_fraction / threshold_W) - 1`` where
    ``loss_fraction = |loss| / capital_base`` (capital_base = total_buy_cost,
    the peak capital deployed — the stable "% of capital" basis) and
    ``threshold_W`` is the log-fit threshold for window W. Unrealized
    contributions are weighted at UNREALIZED_WEIGHT (30%) vs realized.

    A consecutive losing-month STREAK component is added at full weight on
    top: the longest back-to-back run of losing trading months contributes
    ``exp(k · streak / THRESHOLD) - 1`` (0 for streak < LOSING_STREAK_MIN),
    so a sustained multi-month bleed pushes the grade up exponentially
    even when each individual month's loss is below the window thresholds.

    A PER-PERIOD OVERRIDE component (``_period_override_risk``) is added at
    full weight on top: three rules — monthly additional unrealized loss
    (>10% of capital), monthly gain (>50%), seasonal gain (>80%) — each
    scaled so that hitting its threshold contributes 6.0 (RISK_GRADE_
    ELEVATED_BOUND) to the score, pushing the grade to HIGH on its own.
    Below threshold the contribution is exponential (proportional).
    """
    if capital_base <= 0:
        return 0.0
    realized_risk = 0.0
    unrealized_risk = 0.0
    for w in RISK_WINDOW_DAYS:
        threshold = _loss_threshold(w)
        # Realized loss
        r_loss = _rolling_realized_loss(sells, w)
        if r_loss < 0:
            realized_risk += _exp_contribution(
                abs(r_loss) / capital_base, threshold)
        # Unrealized max dip + end residual
        u_max, u_end = _rolling_unrealized_loss(daily_df, w)
        if u_max < 0:
            unrealized_risk += _exp_contribution(
                abs(u_max) / capital_base, threshold)
        if u_end < 0:
            unrealized_risk += _exp_contribution(
                abs(u_end) / capital_base, threshold)
    streak_risk = _streak_contribution(_longest_losing_streak(sells))
    period_risk = _period_override_risk(sells, daily_df, capital_base)
    return (realized_risk
            + UNREALIZED_WEIGHT * unrealized_risk
            + streak_risk
            + period_risk)


# ---------------------------------------------------------------------------
#  Per-(seq, code) risk_seq row
# ---------------------------------------------------------------------------
def compute_risk_seq(
    seq_id: int,
    code: str,
    decisions: List[Dict[str, Any]],
    close_prices: Optional[List[Dict[str, Any]]] = None,
    daily_rows: Optional[List[Dict[str, Any]]] = None,
    total_buy_cost: float = 0.0,
) -> Optional[Dict[str, Any]]:
    """Compute the strategy_risks row for one (seq_id, code).

    ``decisions`` is the list of trade_decision dicts (rows) for this seq+code.
    ``close_prices`` (optional) is the daily close-price series
    ``[{"date": date, "close_price": float}, ...]`` for the code over the
    run's date range, used to compute the two price-based drawdown stats
    (deepest drop since unzero position / since last buy). When omitted the
    drop columns default to 0 / NULL.
    ``daily_rows`` (optional) is the strategy_daily unrealized_pnl series
    ``[{"trade_date": date, "unrealized_pnl": float}, ...]``, used by the
    exponential rolling-window risk score for the unrealized-loss component.
    When omitted, only the realized-loss component contributes.
    ``total_buy_cost`` is the peak normalized capital deployed (from
    strategy_results), used as the "% of capital" denominator for
    loss_fraction. Falls back to total_abs_pnl when 0/unavailable.
    """
    if not decisions:
        return None
    df = pd.DataFrame(decisions)
    df["exec_date"] = pd.to_datetime(df["exec_date"]).dt.date
    df = df.sort_values("exec_date").reset_index(drop=True)

    sells = df[df["side"] == "SELL"].copy()
    buys = df[df["side"] == "BUY"]
    n_sells = len(sells)
    n_buys = len(buys)
    if n_sells == 0:
        return None

    sells["realized_pnl"] = pd.to_numeric(sells["realized_pnl"], errors="coerce").fillna(0.0)
    sells["abs_pnl"] = sells["realized_pnl"].abs()
    total_realized = float(sells["realized_pnl"].sum())
    total_abs = float(sells["abs_pnl"].sum())

    # Top-3 gains / losses — store decision_no as FK ref to trade_decision.
    # nlargest/nsmallest handle ties by keeping the first occurrence; if fewer
    # than 3 SELLs exist, the missing slots stay None (NULL in DB).
    top_gains = sells.nlargest(3, "realized_pnl")
    top_losses = sells.nsmallest(3, "realized_pnl")
    pnl_gain_nos = list(top_gains["decision_no"]) + [None] * 3
    pnl_loss_nos = list(top_losses["decision_no"]) + [None] * 3

    # Top-3 highest-confidence BUYs (by qty descending; qty = confidence 0-100).
    buys_df = df[df["side"] == "BUY"].copy()
    buys_df["qty"] = pd.to_numeric(buys_df["qty"], errors="coerce").fillna(0.0)
    top_conf_buys = buys_df.nlargest(3, "qty")
    conf_buy_nos = list(top_conf_buys["decision_no"]) + [None] * 3

    # Chronological concentration
    max_30d, ratio, w_start, w_end = _compute_concentration(sells, total_abs)

    # Top-3 cumulative-P&L drawdowns. max_dd is the worst magnitude (transient
    # — drives risk_score); dd_dates are the trough dates and dd_vals are the
    # per-episode signed magnitudes (trough - peak, <= 0) of the top-3
    # episodes, both persisted in strategy_risks.
    max_dd, dd_dates, dd_vals = _compute_top_drawdowns(sells)

    # Price-based drawdowns (deepest drop since unzero position / last buy)
    prices_df = pd.DataFrame()
    if close_prices:
        prices_df = pd.DataFrame(close_prices)
        prices_df["date"] = pd.to_datetime(prices_df["date"]).dt.date
        prices_df = prices_df.sort_values("date").reset_index(drop=True)
    (drop_unzero, unzero_peak, unzero_trough,
     drop_since_buy, buy_peak, buy_trough) = _compute_price_drawdowns(df, prices_df)

    # Daily unrealized_pnl series for the risk score's unrealized component.
    daily_df = pd.DataFrame()
    if daily_rows:
        daily_df = pd.DataFrame(daily_rows)
        daily_df["trade_date"] = pd.to_datetime(daily_df["trade_date"]).dt.date
        daily_df = daily_df.sort_values("trade_date").reset_index(drop=True)

    # Exponential rolling-window risk score. Multi-horizon rolling losses
    # (1d / 30d / 90d / 365d) scaled by log-fit thresholds, with unrealized
    # MTM losses (max dip + window-end residual) at 30% weight. LOSSES ONLY.
    # Denominator = total_buy_cost (peak capital deployed, the stable "% of
    # capital" basis); falls back to total_abs_pnl when total_buy_cost is 0.
    capital_base = total_buy_cost if total_buy_cost > 0 else total_abs
    risk_score = _compute_risk_score(sells, daily_df, capital_base)
    grade = _risk_grade(risk_score)

    return {
        "seq_id": seq_id,
        "code": code,
        # NOTE: total_realized_pnl / total_abs_pnl / n_sells / n_buys were
        # MOVED to strategy_results (written by the backtest runner). They are
        # still computed locally here (total_realized / total_abs / n_sells /
        # n_buys) to drive concentration_ratio, risk_grade, and the per-period
        # rollup, but are NOT part of the strategy_risks row anymore.
        # Top-3 gain/loss trades stored as FK refs (decision_no) to
        # trade_decision — the UI JOINs to fetch pnl/date/reason on demand.
        "pnl_gain_1st_decision_no": int(pnl_gain_nos[0]) if pnl_gain_nos[0] is not None else None,
        "pnl_gain_2nd_decision_no": int(pnl_gain_nos[1]) if pnl_gain_nos[1] is not None else None,
        "pnl_gain_3rd_decision_no": int(pnl_gain_nos[2]) if pnl_gain_nos[2] is not None else None,
        "pnl_loss_1st_decision_no": int(pnl_loss_nos[0]) if pnl_loss_nos[0] is not None else None,
        "pnl_loss_2nd_decision_no": int(pnl_loss_nos[1]) if pnl_loss_nos[1] is not None else None,
        "pnl_loss_3rd_decision_no": int(pnl_loss_nos[2]) if pnl_loss_nos[2] is not None else None,
        "confidence_buy_1st_decision_no": int(conf_buy_nos[0]) if conf_buy_nos[0] is not None else None,
        "confidence_buy_2nd_decision_no": int(conf_buy_nos[1]) if conf_buy_nos[1] is not None else None,
        "confidence_buy_3rd_decision_no": int(conf_buy_nos[2]) if conf_buy_nos[2] is not None else None,
        "max_30d_abs_pnl": round(max_30d, 4),
        "concentration_ratio": round(ratio, 6),
        "concentration_window_start": w_start,
        "concentration_window_end": w_end,
        "drawdown_1st_date": dd_dates[0],
        "drawdown_2nd_date": dd_dates[1],
        "drawdown_3rd_date": dd_dates[2],
        "drawdown_1st_val": round(dd_vals[0], 4) if dd_vals[0] is not None else None,
        "drawdown_2nd_val": round(dd_vals[1], 4) if dd_vals[1] is not None else None,
        "drawdown_3rd_val": round(dd_vals[2], 4) if dd_vals[2] is not None else None,
        "risk_score": round(risk_score, 4),
        "risk_grade": grade,
        "deepest_drop_since_unzero_pos": round(drop_unzero, 6) if drop_unzero < 0 else 0.0,
        "deepest_drop_since_unzero_pos_peak_date": unzero_peak,
        "deepest_drop_since_unzero_pos_trough_date": unzero_trough,
        "deepest_drop_since_last_buy": round(drop_since_buy, 6) if drop_since_buy < 0 else 0.0,
        "deepest_drop_since_last_buy_peak_date": buy_peak,
        "deepest_drop_since_last_buy_trough_date": buy_trough,
        # Carried alongside (NOT upserted to strategy_risk_seq) so the
        # orchestrator can pass them to compute_risk_periods without
        # recomputing. Stripped before upsert by _strip_non_column_keys.
        "_total_realized_pnl": round(total_realized, 4),
        "_total_abs_pnl": round(total_abs, 4),
    }


# ---------------------------------------------------------------------------
#  Per-period risk rows (year / season / month)
# ---------------------------------------------------------------------------
def compute_risk_periods(
    seq_id: int,
    code: str,
    decisions: List[Dict[str, Any]],
    total_abs_pnl: float,
    total_realized_pnl: float,
    daily_rows: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Compute strategy_risk_period rows (year/season/month) for one (seq, code).

    ``daily_rows`` (optional) is the strategy_daily series
    ``[{"trade_date": date, "unrealized_pnl": float}, ...]`` used to compute
    the per-period mark-to-market change in unrealized_pnl
    (unrealized_pnl(end of period) - unrealized_pnl(end of previous period)).
    When omitted, every period's unrealized_pnl is 0.
    """
    if not decisions:
        return []
    df = pd.DataFrame(decisions)
    df["exec_date"] = pd.to_datetime(df["exec_date"]).dt.date
    df = df.sort_values("exec_date").reset_index(drop=True)
    sells = df[df["side"] == "SELL"].copy()
    if sells.empty:
        return []
    sells["realized_pnl"] = pd.to_numeric(sells["realized_pnl"], errors="coerce").fillna(0.0)
    sells["abs_pnl"] = sells["realized_pnl"].abs()

    # Per-period end-of-period unrealized_pnl from the daily series. For each
    # period_type, map every daily row to its period_value and keep the
    # unrealized_pnl of the LAST trading day in each period.
    daily_df = pd.DataFrame()
    if daily_rows:
        daily_df = pd.DataFrame(daily_rows)
        daily_df["trade_date"] = pd.to_datetime(daily_df["trade_date"]).dt.date
        daily_df = daily_df.sort_values("trade_date").reset_index(drop=True)
        daily_df["unrealized_pnl"] = pd.to_numeric(
            daily_df["unrealized_pnl"], errors="coerce").fillna(0.0)

    def _end_unrealized_by_period(period_type: str) -> Dict[str, float]:
        """{period_value: unrealized_pnl at the last trading day in that period}."""
        if daily_df.empty:
            return {}
        pv_series = daily_df["trade_date"].apply(
            lambda d: period_value(d, period_type)
        )
        tmp = daily_df.assign(period_value=pv_series)
        # last row per period (daily_df is sorted by trade_date)
        ends = tmp.groupby("period_value", as_index=False).last()
        return dict(zip(ends["period_value"], ends["unrealized_pnl"]))

    def _max_unrealized_by_period(period_type: str) -> Dict[str, float]:
        """{period_value: max daily unrealized_pnl within that period}."""
        if daily_df.empty:
            return {}
        pv_series = daily_df["trade_date"].apply(
            lambda d: period_value(d, period_type)
        )
        tmp = daily_df.assign(period_value=pv_series)
        maxs = tmp.groupby("period_value", as_index=False)["unrealized_pnl"].max()
        return dict(zip(maxs["period_value"], maxs["unrealized_pnl"]))

    rows: List[Dict[str, Any]] = []
    for period_type in ("year", "season", "month"):
        sells = sells.copy()
        sells["period_value"] = sells["exec_date"].apply(
            lambda d: period_value(d, period_type)
        )
        end_unreal = _end_unrealized_by_period(period_type)
        max_unreal = _max_unrealized_by_period(period_type)
        # SELL periods in chronological order — the MTM change for each is its
        # end-of-period unrealized minus the previous SELL period's end. The
        # first SELL period bases off 0 (no open position before the first BUY).
        ordered_pvs = sorted(sells["period_value"].unique())
        prev_end = 0.0
        for pv in ordered_pvs:
            grp = sells[sells["period_value"] == pv]
            grp_pnl = float(grp["realized_pnl"].sum())
            grp_abs = float(grp["abs_pnl"].sum())
            share = grp_abs / total_abs_pnl if total_abs_pnl > 0 else 0.0
            n_buys = int(((df["side"] == "BUY") &
                          (df["exec_date"].apply(
                              lambda d: period_value(d, period_type)) == pv)).sum())
            is_hotspot = share >= HOTSPOT_SHARE_THRESHOLD
            # counter-trend: period P&L sign differs from run total
            is_counter = (grp_pnl > 0 and total_realized_pnl < 0) or \
                         (grp_pnl < 0 and total_realized_pnl > 0)
            cur_end = float(end_unreal.get(pv, 0.0))
            cur_max = float(max_unreal.get(pv, 0.0))
            unreal = cur_end - prev_end
            prev_end = cur_end
            rows.append({
                "seq_id": seq_id,
                "code": code,
                "period_type": period_type,
                "period_value": pv,
                "n_sells": int(len(grp)),
                "n_buys": n_buys,
                "realized_pnl": round(grp_pnl, 4),
                "unrealized_pnl": round(unreal, 4),
                "max_unrealized_pnl": round(cur_max, 4),
                "end_unrealized_pnl": round(cur_end, 4),
                "abs_pnl": round(grp_abs, 4),
                "period_share": round(share, 6),
                "is_concentration_hotspot": is_hotspot,
                "is_counter_trend": is_counter,
            })
    return rows
