"""Pure-pandas risk computations — no DB inside.

Each function takes trade_decision rows (as dicts or a DataFrame) for one
(seq_id, code) and returns either a single risk_seq row or a list of
risk_period rows. Side-effect free: callers (the orchestrator in
``__init__.py``) handle DB writes.

Risk philosophy:
  - ``_compute_concentration`` finds the worst 30-day cluster of |P&L|.
  - ``_compute_max_drawdown`` finds the worst peak-to-trough decline in
    cumulative realized P&L.
  - ``compute_risk_seq`` combines them into an exponential risk_score:
        concentration_ratio² × |max_drawdown|
    so a clustered P&L stream is exponentially riskier than a spread one.
  - ``compute_risk_periods`` rolls the same metrics up per year/season/month
    and flags periods that dominate (hotspot) or move against the run total
    (counter-trend).
"""
from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from strategy._risks.constants import (
    WINDOW_DAYS, HOTSPOT_SHARE_THRESHOLD,
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
#  Max drawdown — worst peak-to-trough decline in cumulative realized P&L
# ---------------------------------------------------------------------------
def _compute_max_drawdown(sells: pd.DataFrame) -> float:
    """Compute the worst peak-to-trough decline in cumulative realized P&L.

    Returns a non-positive float (0 if no drawdown).
    """
    if sells.empty:
        return 0.0
    cum = sells["realized_pnl"].cumsum().values
    running_max = cum[0]
    max_dd = 0.0
    for v in cum:
        if v > running_max:
            running_max = v
        dd = v - running_max
        if dd < max_dd:
            max_dd = dd
    return float(max_dd)


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
def _risk_grade(risk_score: float, total_abs_pnl: float) -> str:
    """Map risk_score / total_abs_pnl ratio to a grade."""
    if total_abs_pnl <= 0:
        return "LOW"
    ratio = risk_score / total_abs_pnl
    if ratio < 0.10:
        return "LOW"
    if ratio < 0.25:
        return "MODERATE"
    if ratio < 0.50:
        return "ELEVATED"
    return "HIGH"


# ---------------------------------------------------------------------------
#  Per-(seq, code) risk_seq row
# ---------------------------------------------------------------------------
def compute_risk_seq(
    seq_id: int,
    code: str,
    decisions: List[Dict[str, Any]],
    close_prices: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """Compute the strategy_risk_seq row for one (seq_id, code).

    ``decisions`` is the list of trade_decision dicts (rows) for this seq+code.
    ``close_prices`` (optional) is the daily close-price series
    ``[{"date": date, "close_price": float}, ...]`` for the code over the
    run's date range, used to compute the two price-based drawdown stats
    (deepest drop since unzero position / since last buy). When omitted the
    drop columns default to 0 / NULL.
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

    # Top gain / loss
    top_gain_idx = sells["realized_pnl"].idxmax()
    top_loss_idx = sells["realized_pnl"].idxmin()
    top_gain = sells.loc[top_gain_idx]
    top_loss = sells.loc[top_loss_idx]

    # Chronological concentration
    max_30d, ratio, w_start, w_end = _compute_concentration(sells, total_abs)

    # Max drawdown
    max_dd = _compute_max_drawdown(sells)

    # Price-based drawdowns (deepest drop since unzero position / last buy)
    prices_df = pd.DataFrame()
    if close_prices:
        prices_df = pd.DataFrame(close_prices)
        prices_df["date"] = pd.to_datetime(prices_df["date"]).dt.date
        prices_df = prices_df.sort_values("date").reset_index(drop=True)
    (drop_unzero, unzero_peak, unzero_trough,
     drop_since_buy, buy_peak, buy_trough) = _compute_price_drawdowns(df, prices_df)

    # Exponential risk score: concentration_ratio^2 * |max_drawdown|
    risk_score = (ratio ** 2) * abs(max_dd)
    grade = _risk_grade(risk_score, total_abs)

    return {
        "seq_id": seq_id,
        "code": code,
        "total_realized_pnl": round(total_realized, 4),
        "total_abs_pnl": round(total_abs, 4),
        "n_sells": n_sells,
        "n_buys": n_buys,
        "top_gain_pnl": round(float(top_gain["realized_pnl"]), 4),
        "top_gain_exec_date": top_gain["exec_date"],
        "top_gain_signal_reason": top_gain.get("signal_reason"),
        "top_loss_pnl": round(float(top_loss["realized_pnl"]), 4),
        "top_loss_exec_date": top_loss["exec_date"],
        "top_loss_signal_reason": top_loss.get("signal_reason"),
        "max_30d_abs_pnl": round(max_30d, 4),
        "concentration_ratio": round(ratio, 6),
        "concentration_window_start": w_start,
        "concentration_window_end": w_end,
        "max_drawdown": round(max_dd, 4),
        "risk_score": round(risk_score, 4),
        "risk_grade": grade,
        "deepest_drop_since_unzero_pos": round(drop_unzero, 6) if drop_unzero < 0 else 0.0,
        "deepest_drop_since_unzero_pos_peak_date": unzero_peak,
        "deepest_drop_since_unzero_pos_trough_date": unzero_trough,
        "deepest_drop_since_last_buy": round(drop_since_buy, 6) if drop_since_buy < 0 else 0.0,
        "deepest_drop_since_last_buy_peak_date": buy_peak,
        "deepest_drop_since_last_buy_trough_date": buy_trough,
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
) -> List[Dict[str, Any]]:
    """Compute strategy_risk_period rows (year/season/month) for one (seq, code)."""
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

    rows: List[Dict[str, Any]] = []
    for period_type in ("year", "season", "month"):
        sells = sells.copy()
        sells["period_value"] = sells["exec_date"].apply(
            lambda d: period_value(d, period_type)
        )
        for pv, grp in sells.groupby("period_value"):
            grp_pnl = float(grp["realized_pnl"].sum())
            grp_abs = float(grp["abs_pnl"].sum())
            share = grp_abs / total_abs_pnl if total_abs_pnl > 0 else 0.0
            top_gain_idx = grp["realized_pnl"].idxmax()
            top_loss_idx = grp["realized_pnl"].idxmin()
            top_gain = grp.loc[top_gain_idx]
            top_loss = grp.loc[top_loss_idx]
            n_buys = int(((df["side"] == "BUY") &
                          (df["exec_date"].apply(
                              lambda d: period_value(d, period_type)) == pv)).sum())
            is_hotspot = share >= HOTSPOT_SHARE_THRESHOLD
            # counter-trend: period P&L sign differs from run total
            is_counter = (grp_pnl > 0 and total_realized_pnl < 0) or \
                         (grp_pnl < 0 and total_realized_pnl > 0)
            rows.append({
                "seq_id": seq_id,
                "code": code,
                "period_type": period_type,
                "period_value": pv,
                "n_sells": int(len(grp)),
                "n_buys": n_buys,
                "realized_pnl": round(grp_pnl, 4),
                "abs_pnl": round(grp_abs, 4),
                "period_share": round(share, 6),
                "top_gain_pnl": round(float(top_gain["realized_pnl"]), 4),
                "top_gain_exec_date": top_gain["exec_date"],
                "top_loss_pnl": round(float(top_loss["realized_pnl"]), 4),
                "top_loss_exec_date": top_loss["exec_date"],
                "is_concentration_hotspot": is_hotspot,
                "is_counter_trend": is_counter,
            })
    return rows
