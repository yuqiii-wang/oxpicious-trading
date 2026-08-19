"""Shared metric primitives for the regime-aware losses (pure functions).

Both loss modules (omega.py for Set A, calmar.py for Set B) build on
these — no DB, no algo knowledge:

  - ``trade_returns``             — per-exit return rates R_t
  - ``monthly_pnl``               — per-month equity Δ (mark-to-market)
  - ``monthly_pnl_from_decisions``— realized-only fallback
  - ``aggregate_monthly_pnl``     — sum monthly Δ across codes
  - ``positive_month_fraction``   — share of months with Δ > 0
  - ``equity_max_drawdown``       — max peak-to-trough of the aggregated
                                    cross-code equity curve
"""
from __future__ import annotations

from typing import Dict, Iterable, List


def trade_returns(decisions: List[dict]) -> List[float]:
    """Per-exit return rates R_t = realized_pnl / cost basis of sold qty.

    Every SELL decision (including the forced final liquidation) exits
    ``qty`` units bought at the weighted-avg ``normalized_mean_buy_price``
    (both in normalized units, anchor = 100 at the first BUY), so

        R_t = realized_pnl / ((qty / 100) × normalized_mean_buy_price)

    is a pure return rate comparable across securities. BUY decisions
    carry no return; exits with a non-positive denominator (dust qty /
    missing cost basis) are skipped.
    """
    out: List[float] = []
    for d in decisions:
        if d.get("side") != "SELL":
            continue
        qty = float(d.get("qty") or 0.0)
        basis = float(d.get("normalized_mean_buy_price") or 0.0)
        denom = (qty / 100.0) * basis
        if denom <= 0.0:
            continue
        pnl = float(d.get("realized_pnl") or 0.0)
        out.append(pnl / denom)
    return out


def monthly_pnl(daily_rows: List[dict]) -> Dict[str, float]:
    """Per-calendar-month equity Δ from ONE code's daily rows.

    Δ(month) = total_pnl at the month's last trading day − total_pnl at
    the previous month's last trading day (mark-to-market: realized +
    unrealized). Returns ``{'YYYY-MM': Δ}`` in chronological order;
    months with no rows are simply absent (a data gap is not a zero
    month — the gap's drift is attributed to the next observed month).
    """
    month_end: Dict[str, float] = {}
    order: List[str] = []
    current_key: str | None = None
    current_pnl = 0.0
    for row in daily_rows:
        d = row.get("trade_date")
        if d is None:
            continue
        key = f"{d.year:04d}-{d.month:02d}"
        if key != current_key:
            if current_key is not None:
                month_end[current_key] = current_pnl
                order.append(current_key)
            current_key = key
        current_pnl = float(row.get("total_pnl") or 0.0)
    if current_key is not None:
        month_end[current_key] = current_pnl
        order.append(current_key)

    out: Dict[str, float] = {}
    prev = 0.0
    for key in order:
        out[key] = month_end[key] - prev
        prev = month_end[key]
    return out


def monthly_pnl_from_decisions(decisions: List[dict]) -> Dict[str, float]:
    """Realized-PnL-by-SELL-month fallback (daily rows unavailable)."""
    out: Dict[str, float] = {}
    for d in decisions:
        if d.get("side") != "SELL":
            continue
        dt = d.get("exec_date")
        if dt is None:
            continue
        key = f"{dt.year:04d}-{dt.month:02d}"
        out[key] = out.get(key, 0.0) + float(d.get("realized_pnl") or 0.0)
    return dict(sorted(out.items()))


def aggregate_monthly_pnl(
    per_code: Iterable[Dict[str, float]],
) -> Dict[str, float]:
    """Sum monthly Δ across codes, aligned by 'YYYY-MM' key."""
    out: Dict[str, float] = {}
    for monthly in per_code:
        for k, v in monthly.items():
            out[k] = out.get(k, 0.0) + v
    return dict(sorted(out.items()))


def positive_month_fraction(monthly: Dict[str, float]) -> float:
    """Fraction of months with positive PnL (0.0 when there are none)."""
    if not monthly:
        return 0.0
    pos = sum(1 for v in monthly.values() if v > 0.0)
    return pos / len(monthly)


def equity_max_drawdown(
    per_code_daily: Iterable[List[dict]],
) -> Dict[str, object]:
    """Max peak-to-trough drawdown of the cross-code aggregated equity.

    Equity curve = Σ total_pnl across codes per trade_date (sorted).
    The running peak starts at 0 (breakeven), so a curve that dips
    negative immediately still records the loss as drawdown. Returns
    ``{"max_dd", "peak_equity", "trough_date", "n_days"}`` where
    ``max_dd`` is in the same normalized PnL units as total_pnl (divide
    by peak capital deployed to get the drawdown as a fraction of
    equity).
    """
    by_date: Dict[object, float] = {}
    for daily_rows in per_code_daily:
        for row in daily_rows:
            d = row.get("trade_date")
            if d is None:
                continue
            by_date[d] = by_date.get(d, 0.0) + float(row.get("total_pnl") or 0.0)
    if not by_date:
        return {"max_dd": 0.0, "peak_equity": 0.0, "trough_date": None,
                "n_days": 0}

    dates = sorted(by_date)
    peak = 0.0
    max_dd = 0.0
    trough_date = None
    for d in dates:
        v = by_date[d]
        if v > peak:
            peak = v
        dd = peak - v
        if dd > max_dd:
            max_dd = dd
            trough_date = d
    return {"max_dd": max_dd, "peak_equity": peak, "trough_date": trough_date,
            "n_days": len(dates)}


__all__ = [
    "trade_returns",
    "monthly_pnl",
    "monthly_pnl_from_decisions",
    "aggregate_monthly_pnl",
    "positive_month_fraction",
    "equity_max_drawdown",
]
