"""Portfolio backtest engine for the MA-spread strategy.

Iterates each code's date series chronologically and emits trade_decision
rows. Execution model (no look-ahead):

  - Signal computed at CLOSE of date T (row attributes + 1-day lag).
  - Order FILLS at the OPEN of T+1 (next trading day for that code).
  - If T+1 has no data (end of series) the signal is dropped (can't fill).

Position sizing (confidence-based, long-only, NO fixed capital):
  - There is NO capital budget. Cash starts at 0 and goes NEGATIVE with each
    BUY (borrowing to buy). total_buy_cost (sum of all BUY costs) is computed
    after the backtest and replaces the capital concept.
  - Every BUY/SELL ``qty`` field is a confidence score in (0, 100]:
      BUY:  deploy (confidence/100) * buy_notional yuan → buy shares.
            Position accumulates freely across multiple BUYs (unlimited).
      SELL: close (confidence/100) of CURRENT POSITION → sell shares.
            confidence = fraction of position to close (NOT fraction of
            capital — fixes the asymmetry where SELL confidence was measured
            against capital but BUY against the same).
            SELL is always capped at current position (no shorting); if
            position is 0 the SELL is skipped.
    So BUY confidence scales a fixed notional, while SELL confidence scales
    the current position — they're on different but meaningful scales, and
    a SELL can never be larger than the accumulated BUY position.

  - SELL fires on the rising-edge of exit_signal (so one SELL per exit
    episode, not one per bar the exit condition persists).
  - min_holding_period: a SELL is only allowed once `min_holding_period`
    trading days have elapsed since the LAST BUY (any BUY, not just the one
    that opened the current position). Measured on the code's own trading-
    day index.

  - Final liquidation: after the last bar, if any long position remains open
    (BUYs not yet fully closed by SELLs), a forced SELL is emitted on the
    last trading day at the CLOSE price to liquidate everything.

  - Realized P&L: (fill_price - cost_basis) * shares_sold - (commission +
    fees). cost_basis is the weighted-average BUY price; reset to 0 when
    position drops to 0. Gains/losses are the percentage change from buy to
    sell: total_return = final_cash / total_buy_cost (= realized_pnl /
    total_buy_cost when all positions are closed).

  - position_after >= 0 is enforced at the DB level (CHECK constraint in
    trade_decision). The engine guarantees this by sizing SELL as a fraction
    of current position; the DB CHECK is the safety net.

Costs (A-share): commission_rate both sides; stamp_duty_rate SELL side only.
"""
from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd

from strategy.ma_spread_trading.signals import apply_signals


def _round6(x: float) -> float:
    """Round to 6 dp so NUMERIC(18,6) / NUMERIC(24,4) columns don't overflow."""
    if x is None or (isinstance(x, float) and (x != x)):
        return None
    return round(float(x), 6)


def _round2(x: float) -> float:
    """Round confidence to 2 dp (0-100 scale; NUMERIC(24,4) stores it)."""
    if x is None or (isinstance(x, float) and (x != x)):
        return None
    return round(float(x), 2)


def _fmt(x) -> str:
    return f"{x:.4f}" if pd.notna(x) else "NA"


def _build_signal_reason(row, side: str, params: dict, confidence: float) -> str:
    """Human-readable reason the signal fired (for signal_reason column).

    Includes the confidence breakdown so the user can see WHY a particular
    score was assigned.
    """
    ma_long = params["ma_long"]
    if side == "BUY":
        return (f"MA5/MA{ma_long} golden cross (spread "
                f"{_fmt(row[f'ma5_vs_ma{ma_long}_prev'])} -> "
                f"{_fmt(row[f'ma5_vs_ma{ma_long}'])}); price_vs_ma{ma_long}="
                f"{_fmt(row[f'price_vs_ma{ma_long}'])}, RSI14="
                f"{_fmt(row['rsi_14days'])}, amt_ma5_slope="
                f"{_fmt(row['trading_amt_ma5_slope'])}) | confidence="
                f"{confidence:.1f}")
    # SELL — identify which exit triggered
    reasons = []
    if row["death_cross"]:
        reasons.append(f"MA5/MA{ma_long} death cross")
    if pd.notna(row["rsi_14days"]) and row["rsi_14days"] > params["exit_rsi_max"]:
        reasons.append(f"RSI14={_fmt(row['rsi_14days'])}>{params['exit_rsi_max']}")
    if pd.notna(row[f"price_vs_ma{ma_long}"]) and \
            row[f"price_vs_ma{ma_long}"] < params["stop_loss_vs_ma_long"]:
        reasons.append(f"price_vs_ma{ma_long}={_fmt(row[f'price_vs_ma{ma_long}'])}"
                       f"<{params['stop_loss_vs_ma_long']}")
    base = "SELL: " + "; ".join(reasons) if reasons else "SELL signal"
    return f"{base} | confidence={confidence:.1f}"


def backtest_single_code(
    code_df: pd.DataFrame,
    params: dict,
    sec_type: str,
) -> List[Dict[str, Any]]:
    """Run the backtest for ONE code's date series.

    ``code_df`` must be sorted by date ascending and carry the signal columns
    + open_price / close_price. Returns a list of trade_decision row dicts
    (without seq_id / decision_no — assigned by the caller).

    No fixed capital: cash starts at 0 and goes negative on BUY (borrowing).
    total_buy_cost (sum of all BUY costs) is computed by the caller after the
    backtest and stored in strategy_seq.
    """
    if code_df.empty:
        return []

    min_hp = params["min_holding_period"]
    comm_rate = params["commission_rate"]
    stamp_rate = params["stamp_duty_rate"]
    buy_notional = params["buy_notional"]

    # Reset index so positional access (i, i+1) maps to chronological order.
    cd = code_df.reset_index(drop=True)
    n = len(cd)
    dates = cd["date"].tolist()
    # date -> positional index, for min_holding_period trading-day math.
    date_idx = {d: i for i, d in enumerate(dates)}

    cash = 0.0              # starts at 0; goes negative on BUY (borrowing)
    position = 0.0          # net shares held (always >= 0; long-only)
    cost_basis = 0.0        # weighted-avg BUY price while position > 0
    last_buy_exec_idx = None  # positional index of the last BUY fill date

    decisions: List[Dict[str, Any]] = []

    for i in range(n):
        row = cd.iloc[i]
        # Need T+1 to fill; skip on the last bar.
        if i + 1 >= n:
            break
        nxt = cd.iloc[i + 1]
        fill_price = nxt["open_price"]
        # Can't fill if next bar's open is missing.
        if pd.isna(fill_price) or fill_price <= 0:
            continue
        signal_date = row["date"]
        exec_date = nxt["date"]

        # ---- BUY: entry signal (regardless of current position) -----
        # Confidence ∈ (0, 100] drives a FIXED notional deployment (NOT a
        # fraction of capital — there is no capital). deploy =
        # (confidence/100) * buy_notional. Position accumulates freely.
        if bool(row.get("entry_signal", False)):
            confidence = float(row.get("buy_confidence", 0.0) or 0.0)
            if confidence <= 0.0:
                continue  # no confidence → no trade
            deploy = (confidence / 100.0) * buy_notional
            if deploy <= 0:
                continue
            shares = deploy / fill_price
            gross = shares * fill_price
            commission = gross * comm_rate
            fees = 0.0

            # Update long cost basis (weighted average). Position is always
            # >= 0 in this long-only model (SELL is capped to position), so
            # every BUY extends the long. No short-covering branch needed.
            new_position = position + shares
            if new_position > 0:
                cost_basis = (position * cost_basis
                              + shares * fill_price) / new_position
            else:
                cost_basis = 0.0

            cash_after = cash - (gross + commission + fees)
            position_after = position + shares
            decisions.append({
                "sec_type": sec_type,
                "code": row["code"],
                "side": "BUY",
                "qty": _round2(confidence),
                "signal_date": signal_date,
                "exec_date": exec_date,
                "fill_price": _round6(fill_price),
                "gross_value": _round6(gross),
                "commission": _round6(commission),
                "fees": _round6(fees),
                "position_before": _round6(position),
                "position_after": _round6(position_after),
                "cash_before": _round6(cash),
                "cash_after": _round6(cash_after),
                "realized_pnl": _round6(0.0),
                "signal_value": _round6(row.get(f"ma5_vs_ma{params['ma_long']}")),
                "signal_reason": _build_signal_reason(row, "BUY", params, confidence),
            })
            cash = cash_after
            position = position_after
            last_buy_exec_idx = date_idx[exec_date]
            continue

        # ---- SELL: rising-edge exit signal (fraction of position) --
        # KEY FIX: SELL confidence is the FRACTION OF CURRENT POSITION to
        # close (NOT fraction of capital). confidence 78 = close 78% of
        # the current position. This fixes the asymmetry where a SELL with
        # confidence 78 could be larger than a BUY with confidence 25.
        #
        # SELL fires on the rising-edge of exit_signal (one SELL per exit
        # episode). Gate: only after min_holding_period since last BUY.
        if bool(row.get("exit_signal_rising", False)) \
                and last_buy_exec_idx is not None:
            # min_holding_period gate relative to the last BUY (any BUY).
            bars_held = i - last_buy_exec_idx
            if bars_held < min_hp:
                continue  # too soon after last BUY; hold through
            # No position to close — skip this SELL entirely.
            if position <= 0:
                continue
            confidence = float(row.get("sell_confidence", 0.0) or 0.0)
            if confidence <= 0.0:
                continue  # no confidence → no trade
            # SELL: close (confidence/100) of CURRENT POSITION.
            # This is the KEY FIX — confidence is fraction of position,
            # not fraction of capital. shares ≤ position always.
            shares = (confidence / 100.0) * position
            if shares <= 0:
                continue
            gross = shares * fill_price
            commission = gross * comm_rate
            fees = gross * stamp_rate

            # Realized P&L: percentage change from buy to sell, in yuan.
            # realized = (fill_price - cost_basis) * shares - costs.
            realized = (fill_price - cost_basis) * shares - (commission + fees)

            cash_after = cash + (gross - commission - fees)
            position_after = position - shares
            # Position drops to 0 → no long basis remains.
            if position_after <= 0:
                cost_basis = 0.0

            decisions.append({
                "sec_type": sec_type,
                "code": row["code"],
                "side": "SELL",
                "qty": _round2(confidence),
                "signal_date": signal_date,
                "exec_date": exec_date,
                "fill_price": _round6(fill_price),
                "gross_value": _round6(gross),
                "commission": _round6(commission),
                "fees": _round6(fees),
                "position_before": _round6(position),
                "position_after": _round6(position_after),
                "cash_before": _round6(cash),
                "cash_after": _round6(cash_after),
                "realized_pnl": _round6(realized),
                "signal_value": _round6(row.get(f"ma5_vs_ma{params['ma_long']}")),
                "signal_reason": _build_signal_reason(row, "SELL", params, confidence),
            })
            cash = cash_after
            position = position_after
            # Don't reset last_buy_exec_idx — min_hp gate is relative to
            # the last BUY (any BUY), so future SELLs are still gated.

    # ---- Final liquidation: sell all remaining position on the last day -
    # After the main loop, if any long position remains open (BUYs not yet
    # fully closed by SELLs), force a SELL at the last bar's CLOSE price to
    # liquidate everything. This makes the final return reflect the full
    # position being sold out. qty=100 (max confidence = forced exit).
    if position > 0 and n > 0:
        last_row = cd.iloc[n - 1]
        last_date = last_row["date"]
        last_close = last_row.get("close_price")
        if pd.notna(last_close) and last_close > 0:
            shares = position  # sell everything remaining
            gross = shares * last_close
            commission = gross * comm_rate
            fees = gross * stamp_rate
            realized = (last_close - cost_basis) * shares - (commission + fees)
            cash_after = cash + (gross - commission - fees)
            decisions.append({
                "sec_type": sec_type,
                "code": last_row["code"],
                "side": "SELL",
                "qty": _round2(100.0),
                "signal_date": last_date,
                "exec_date": last_date,
                "fill_price": _round6(last_close),
                "gross_value": _round6(gross),
                "commission": _round6(commission),
                "fees": _round6(fees),
                "position_before": _round6(position),
                "position_after": _round6(0.0),
                "cash_before": _round6(cash),
                "cash_after": _round6(cash_after),
                "realized_pnl": _round6(realized),
                "signal_value": _round6(last_row.get(f"ma5_vs_ma{params['ma_long']}")),
                "signal_reason": (f"FINAL LIQUIDATION: close all remaining "
                                  f"position ({shares:.4f} shares) at last close"),
            })
            cash = cash_after
            position = 0.0
            cost_basis = 0.0

    return decisions


def run_backtest(
    df: pd.DataFrame,
    params: dict,
    sec_type: str,
    codes: list,
) -> List[Dict[str, Any]]:
    """Run the backtest across all codes.

    No fixed capital: each BUY deploys (confidence/100) * buy_notional, and
    cash starts at 0 (goes negative on BUY). total_buy_cost (sum of all BUY
    costs) is computed by the runner after the backtest and stored in
    strategy_seq. SELL closes (confidence/100) of current position.
    All decisions are concatenated and returned UNSORTED (the caller assigns
    decision_no after sorting by exec_date).
    """
    if df.empty:
        return []
    df = apply_signals(df, params)

    all_decisions: List[Dict[str, Any]] = []
    for code, code_df in df.groupby("code", sort=False):
        decisions = backtest_single_code(code_df, params, sec_type)
        if decisions:
            all_decisions.extend(decisions)
    return all_decisions


def compute_total_buy_cost(decisions: List[Dict[str, Any]]) -> float:
    """Sum (gross_value + commission + fees) across all BUY decisions.

    This replaces the old capital concept: total_buy_cost is the total amount
    invested across all BUYs, and Total Return = final_cash / total_buy_cost.
    """
    return sum(
        (d.get("gross_value") or 0.0)
        + (d.get("commission") or 0.0)
        + (d.get("fees") or 0.0)
        for d in decisions
        if d["side"] == "BUY"
    )


def summarize(decisions: List[Dict[str, Any]], params: dict) -> Dict[str, Any]:
    """Compute run-level summary stats for logging.

    Returns a dict with n_buys, n_sells, realized_pnl, final_cash,
    total_buy_cost, etc. final_cash is the net cash after all trades
    (starts at 0; negative = still invested/borrowed, positive = profit).
    """
    if not decisions:
        return {"n_decisions": 0, "n_buys": 0, "n_sells": 0,
                "realized_pnl": 0.0, "final_cash": 0.0,
                "total_buy_cost": 0.0}
    n_buys = sum(1 for d in decisions if d["side"] == "BUY")
    n_sells = sum(1 for d in decisions if d["side"] == "SELL")
    realized = sum(d["realized_pnl"] or 0.0 for d in decisions
                   if d["side"] == "SELL")
    # final_cash = cash_after of the last decision by exec_date.
    last = max(decisions, key=lambda d: (d["exec_date"], d["side"] == "BUY"))
    total_buy_cost = compute_total_buy_cost(decisions)
    return {
        "n_decisions": len(decisions),
        "n_buys": n_buys,
        "n_sells": n_sells,
        "realized_pnl": round(realized, 2),
        "final_cash": last["cash_after"],
        "total_buy_cost": round(total_buy_cost, 2),
    }
