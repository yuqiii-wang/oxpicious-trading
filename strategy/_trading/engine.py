"""Generic portfolio backtest engine.

Strategy-agnostic: iterates a code's date series chronologically and emits
trade_decision rows. The signal layer (e.g. ``strategy._signal``) is
responsible for adding a single consolidated ``signal_confidence`` column
to the fetched DataFrame BEFORE the engine runs:

  - ``signal_confidence`` ∈ [-100, 100] — the singular b/s confidence:
      > 0  → BUY signal,  value = buy confidence
      < 0  → SELL signal, value = -sell confidence (rising-edge filtered)
      = 0  → no signal
  - ``signal_value`` — auxiliary signal magnitude (stored on each decision).

The engine reads ONLY ``signal_confidence`` (for the b/s decision) and
``signal_value`` (for storage) — it never reaches into strategy-specific
columns. A strategy package supplies the human-readable reason text via
one callback:

  - ``signal_reason_fn(row, side, params, confidence) -> str`` — builds a
    reason string for the ``signal_reason`` column.

The engine supplies the execution layer: worst-case OHLC fills, slippage,
fees, position / cash / realized-P&L accounting, final liquidation, daily
portfolio state, and run-level summary stats. All financial math lives in
``strategy._trading.formula``; this module only orchestrates state.

Execution model (worst-case fill on the signal day)
---------------------------------------------------
  - Signal computed at CLOSE of date T (row attributes + 1-day lag).
  - Order FILLS on the SAME day T using a WORST-CASE price from OHLC.
  - The last bar is reserved for final liquidation (if position remains).
  - A SELL fires only where ``signal_confidence < 0`` (the signal layer
    already rising-edge-filters exits → one SELL per exit episode) and
    only after ``min_holding_period`` trading days since the last BUY.

Position model (long-only, no shorting)
--------------------------------------
  - BUY ``qty`` = confidence (0-100); SELL ``qty_sold`` =
    (confidence/100) × total_qty_before (a FRACTION of the current position).
  - ``total_qty`` is the cumulative quantity in confidence/qty units (NOT
    /100); BUY adds, SELL subtracts. Always ≥ 0 (DB CHECK enforced).
  - No fixed capital budget: cash starts at 0, goes negative on BUY
    (borrowing), positive on SELL. ``total_buy_cost`` (peak capital
    deployed) is computed AFTER the backtest.

All formulas are documented in ``strategy._trading.formula``.
"""
from __future__ import annotations

import math
from typing import Any, Callable, Dict, List

import pandas as pd

from strategy._trading import formula as F
from strategy._trading.constants import TRADING_DAYS_PER_YEAR


# ---------------------------------------------------------------------------
#  Rounding helpers (NUMERIC(18,6) / NUMERIC(12,4) column safety)
# ---------------------------------------------------------------------------
def _round6(x: float) -> float:
    """Round to 6 dp so NUMERIC(18,6) / NUMERIC(18,4) columns don't overflow."""
    if x is None or (isinstance(x, float) and (x != x)):
        return None
    return round(float(x), 6)


def _round2(x: float) -> float:
    """Round confidence to 2 dp (0-100 scale; NUMERIC(12,4) stores it)."""
    if x is None or (isinstance(x, float) and (x != x)):
        return None
    return round(float(x), 2)


def _fmt(x) -> str:
    return f"{x:.4f}" if pd.notna(x) else "NA"


# ---------------------------------------------------------------------------
#  Per-code backtest
# ---------------------------------------------------------------------------
def backtest_single_code(
    code_df: pd.DataFrame,
    params: dict,
    sec_type: str,
    signal_reason_fn: Callable[[Any, str, dict, float], str],
) -> List[Dict[str, Any]]:
    """Run the backtest for ONE code's date series.

    ALL financial metrics are in normalized units (base=100 at first BUY).
    Returns a list of trade_decision row dicts (without seq_id / decision_no
    — assigned by the caller).

    ``code_df`` must already carry the consolidated signal column produced
    by the signal layer: ``signal_confidence`` (signed [-100, 100]; >0 BUY,
    <0 SELL rising-edge, 0 none) plus the auxiliary ``signal_value``.
    """
    if code_df.empty:
        return []

    min_hp = params["min_holding_period"]

    cd = code_df.reset_index(drop=True)
    n = len(cd)

    # Portfolio state (all in normalized units).
    cash = 0.0               # cumulative qty × norm_price (BUY −, SELL +)
    total_qty = 0.0          # cumulative quantity (qty/confidence units, NOT /100); always >= 0
    cost_basis_norm = 0.0    # weighted-avg BUY normalized_fill_price (remaining)
    last_buy_exec_idx = None
    anchor_price: float | None = None  # first BUY fill_price (norm = 100)

    decisions: List[Dict[str, Any]] = []

    for i in range(n):
        row = cd.iloc[i]
        if i >= n - 1:
            break  # last bar reserved for final liquidation
        # Worst-case fill prices derived from the signal day's OHLC.
        high = row.get("high_price")
        low = row.get("low_price")
        close = row.get("close_price")
        if pd.isna(high) or pd.isna(low) or pd.isna(close):
            continue
        high = float(high)
        low = float(low)
        close = float(close)
        if high <= 0 or low <= 0 or close <= 0:
            continue
        buy_fill = F.worst_case_buy_fill(high, low, close)
        sell_fill = F.worst_case_sell_fill(high, low, close)
        exec_date = row["date"]

        # Singular b/s confidence from the signal layer: >0 BUY, <0 SELL.
        sig = float(row.get("signal_confidence") or 0.0)

        # ---- BUY: positive consolidated signal ------------------------
        if sig > 0.0:
            confidence = sig
            fill_price = buy_fill
            # Set the normalization anchor on the first BUY.
            if anchor_price is None:
                anchor_price = float(fill_price)
            norm_price = F.normalized_price(float(fill_price), anchor_price)
            qty = confidence  # BUY: qty = confidence (0-100)

            slippage = F.buy_slippage(fill_price, close)
            fee = F.buy_fee(qty, norm_price)

            # Mark-to-market position at current execution price.
            position_before = F.position_value(total_qty, norm_price)

            # Update weighted-avg cost basis (normalized).
            new_total_qty = total_qty + qty
            cost_basis_norm = F.weighted_avg_cost_basis(
                total_qty, cost_basis_norm, qty, norm_price,
            )

            cash_after = cash + F.cash_delta_buy(qty, norm_price, fee)
            total_qty = new_total_qty
            position_after = F.position_value(total_qty, norm_price)
            decisions.append({
                "sec_type": sec_type,
                "code": row["code"],
                "side": "BUY",
                "qty": _round2(qty),
                "exec_date": exec_date,
                "fill_price": _round6(fill_price),
                "normalized_fill_price": _round6(norm_price),
                # Post-BUY cost basis (weighted-avg BUY norm price including
                # this BUY). Exposed as a column so the UI can show the new
                # average entry after each BUY.
                "normalized_mean_buy_price": _round6(cost_basis_norm),
                "position_before": _round6(position_before),
                "position_after": _round6(position_after),
                "cash_before": _round6(cash),
                "cash_after": _round6(cash_after),
                "total_qty_before": _round6(total_qty - qty),
                "total_qty_after": _round6(total_qty),
                "realized_pnl": _round6(0.0),
                "slippage": _round6(slippage),
                "fee": _round6(fee),
                "signal_value": _round6(row.get("signal_value")),
                "signal_reason": signal_reason_fn(row, "BUY", params, confidence),
            })
            cash = cash_after
            last_buy_exec_idx = i
            continue

        # ---- SELL: negative consolidated signal -----------------------
        if sig < 0.0 and last_buy_exec_idx is not None:
            bars_held = i - last_buy_exec_idx
            if bars_held < min_hp:
                continue
            if total_qty <= 0:
                continue
            confidence = -sig  # sell confidence magnitude (signal was negated)
            if anchor_price is None:
                continue  # no anchor → can't normalize (shouldn't happen)
            fill_price = sell_fill
            norm_price = F.normalized_price(float(fill_price), anchor_price)
            slippage = F.sell_slippage(fill_price, close)
            position_before = F.position_value(total_qty, norm_price)
            qty_sold = F.sell_qty(confidence, total_qty)

            realized = F.realized_pnl(qty_sold, norm_price, cost_basis_norm)
            # Capture the cost basis used to compute realized_pnl BEFORE any
            # reset to 0 (total_qty_after <= 0 case). Exposed as a column so
            # the UI can show what average buy price the SELL is exiting against.
            mean_buy_price = cost_basis_norm

            cash_after = cash + F.cash_delta_sell(qty_sold, norm_price)
            total_qty_after = total_qty - qty_sold
            position_after = F.position_value(total_qty_after, norm_price)
            if total_qty_after <= 0:
                cost_basis_norm = 0.0

            decisions.append({
                "sec_type": sec_type,
                "code": row["code"],
                "side": "SELL",
                "qty": _round2(qty_sold),
                "exec_date": exec_date,
                "fill_price": _round6(fill_price),
                "normalized_fill_price": _round6(norm_price),
                # Pre-SELL cost basis (the value realized_pnl was computed
                # against). Stays constant across partial SELLs; the last
                # SELL before total_qty→0 carries the final cost basis.
                "normalized_mean_buy_price": _round6(mean_buy_price),
                "position_before": _round6(position_before),
                "position_after": _round6(position_after),
                "cash_before": _round6(cash),
                "cash_after": _round6(cash_after),
                "total_qty_before": _round6(total_qty),
                "total_qty_after": _round6(total_qty_after),
                "realized_pnl": _round6(realized),
                "slippage": _round6(slippage),
                "fee": _round6(0.0),
                "signal_value": _round6(row.get("signal_value")),
                "signal_reason": signal_reason_fn(row, "SELL", params, confidence),
            })
            cash = cash_after
            total_qty = total_qty_after

    # ---- Final liquidation: sell all remaining total_qty on the last day
    if total_qty > 0 and n > 0 and anchor_price is not None:
        last_row = cd.iloc[n - 1]
        last_date = last_row["date"]
        last_high = last_row.get("high_price")
        last_low = last_row.get("low_price")
        last_close = last_row.get("close_price")
        if pd.notna(last_high) and pd.notna(last_low) and pd.notna(last_close) \
                and last_high > 0 and last_low > 0 and last_close > 0:
            fill_price = F.worst_case_sell_fill(
                float(last_high), float(last_low), float(last_close),
            )
            norm_price = F.normalized_price(fill_price, anchor_price)
            slippage = F.sell_slippage(fill_price, float(last_close))
            position_before = F.position_value(total_qty, norm_price)
            qty_sold = total_qty  # sell everything remaining
            realized = F.realized_pnl(qty_sold, norm_price, cost_basis_norm)
            # Capture the cost basis used to compute realized_pnl BEFORE the
            # reset to 0 (this SELL always drives total_qty to 0).
            mean_buy_price = cost_basis_norm
            cash_after = cash + F.cash_delta_sell(qty_sold, norm_price)
            decisions.append({
                "sec_type": sec_type,
                "code": last_row["code"],
                "side": "SELL",
                "qty": _round2(qty_sold),
                "exec_date": last_date,
                "fill_price": _round6(fill_price),
                "normalized_fill_price": _round6(norm_price),
                "normalized_mean_buy_price": _round6(mean_buy_price),
                "position_before": _round6(position_before),
                "position_after": _round6(0.0),
                "cash_before": _round6(cash),
                "cash_after": _round6(cash_after),
                "total_qty_before": _round6(total_qty),
                "total_qty_after": _round6(0.0),
                "realized_pnl": _round6(realized),
                "slippage": _round6(slippage),
                "fee": _round6(0.0),
                "signal_value": _round6(last_row.get("signal_value")),
                "signal_reason": (f"FINAL LIQUIDATION: close all remaining "
                                  f"qty ({qty_sold:.4f}) at worst-case sell"),
            })
            cash = cash_after
            total_qty = 0.0
            cost_basis_norm = 0.0

    return decisions


# ---------------------------------------------------------------------------
#  Run-across-codes driver
# ---------------------------------------------------------------------------
def run_backtest(
    df: pd.DataFrame,
    params: dict,
    sec_type: str,
    codes: list,
    signal_reason_fn: Callable[[Any, str, dict, float], str],
) -> List[Dict[str, Any]]:
    """Run the backtest across all codes.

    ``df`` must ALREADY carry the consolidated ``signal_confidence`` column
    (the signal layer — e.g. ``strategy._signal.apply_signals`` — is applied
    by the caller BEFORE invoking the engine). The engine reads only
    ``signal_confidence`` (+ ``signal_value``); it never reaches into
    strategy-specific signal columns.

    All decisions are concatenated and returned UNSORTED (the caller assigns
    decision_no after sorting by exec_date).
    """
    if df.empty:
        return []

    all_decisions: List[Dict[str, Any]] = []
    for code, code_df in df.groupby("code", sort=False):
        decisions = backtest_single_code(code_df, params, sec_type, signal_reason_fn)
        if decisions:
            all_decisions.extend(decisions)
    return all_decisions


# ---------------------------------------------------------------------------
#  Total buy cost (peak capital deployed)
# ---------------------------------------------------------------------------
def compute_total_buy_cost(decisions: List[Dict[str, Any]]) -> float:
    """Peak capital deployed = (max(total_qty_after) / 100) ×
    normalized_mean_buy_price at that decision.
    """
    if not decisions:
        return 0.0
    max_d = max(decisions, key=lambda d: d.get("total_qty_after") or 0.0)
    return F.total_buy_cost(
        max_d.get("total_qty_after") or 0.0,
        max_d.get("normalized_mean_buy_price") or 0.0,
    )


# ---------------------------------------------------------------------------
#  Sharpe ratios (daily Δtotal_pnl, annualized ×√255)
# ---------------------------------------------------------------------------
def _compute_sharpe_ratios(daily_rows: List[Dict[str, Any]]) -> None:
    """Compute sharpe_ratio / sharpe_ratio_255d / sharpe_ratio_500d from
    daily Δtotal_pnl and write them onto each row IN-PLACE.

    Sharpe = mean(Δtotal_pnl) / std(Δtotal_pnl) × √255  (annualized, rf=0).

      - sharpe_ratio      — cumulative over ALL history up to this trade_date
      - sharpe_ratio_255d — rolling 255-trading-day window (~1 year)
      - sharpe_ratio_500d — rolling 500-trading-day window (~2 years)

    Δtotal_pnl = total_pnl[t] − total_pnl[t−1] captures both realized
    gains/losses (from SELLs) and mark-to-market changes. The first day has
    no delta → 0. Windows with < 2 deltas or σ = 0 → 0.
    """
    for row in daily_rows:
        row.setdefault("sharpe_ratio", 0.0)
        row.setdefault("sharpe_ratio_255d", 0.0)
        row.setdefault("sharpe_ratio_500d", 0.0)

    if len(daily_rows) < 2:
        return

    total_pnl = pd.Series(
        [float(r.get("total_pnl") or 0.0) for r in daily_rows]
    )
    deltas = total_pnl.diff()  # deltas[0] = NaN (no previous day)
    sqrt_255 = math.sqrt(TRADING_DAYS_PER_YEAR)

    def _sharpe(mean: pd.Series, std: pd.Series) -> pd.Series:
        """mean / std × √255, guarded against 0/NaN/inf."""
        ratio = (mean / std * sqrt_255)
        return ratio.replace([float("inf"), float("-inf")], 0.0).fillna(0.0)

    sharpe_full = _sharpe(
        deltas.expanding(min_periods=2).mean(),
        deltas.expanding(min_periods=2).std(ddof=1),
    )
    sharpe_255 = _sharpe(
        deltas.rolling(window=255, min_periods=2).mean(),
        deltas.rolling(window=255, min_periods=2).std(ddof=1),
    )
    sharpe_500 = _sharpe(
        deltas.rolling(window=500, min_periods=2).mean(),
        deltas.rolling(window=500, min_periods=2).std(ddof=1),
    )

    for i, row in enumerate(daily_rows):
        row["sharpe_ratio"] = _round6(float(sharpe_full.iloc[i]) if pd.notna(sharpe_full.iloc[i]) else 0.0)
        row["sharpe_ratio_255d"] = _round6(float(sharpe_255.iloc[i]) if pd.notna(sharpe_255.iloc[i]) else 0.0)
        row["sharpe_ratio_500d"] = _round6(float(sharpe_500.iloc[i]) if pd.notna(sharpe_500.iloc[i]) else 0.0)


# ---------------------------------------------------------------------------
#  Daily portfolio state
# ---------------------------------------------------------------------------
def compute_daily_rows(
    code_df: pd.DataFrame,
    decisions: List[Dict[str, Any]],
    anchor_price: float,
) -> List[Dict[str, Any]]:
    """Compute daily portfolio state rows from the OHLC series + decisions.

    For each trading day from the first BUY date to the end of the series:
    - Carries forward total_qty, cash, cost_basis_norm from the last decision
      executed on or before that day. If a decision was executed on that day,
      the state is updated to that decision's after-state.
    - position_value = (total_qty / 100) × normalized_close
    - unrealized_pnl = (total_qty / 100) × (normalized_close − cost_basis_norm)
      = P&L if ALL remaining position were sold at the day's close price.
    - total_pnl = realized_pnl_cum + unrealized_pnl
    - return_rate = ANNUALIZED return on capital = (total_pnl /
      capital_deployed / max(mean_holding_days, 1)) × 255, where
      capital_deployed = (total_qty / 100) × cost_basis_norm (current
      cost basis × shares) and mean_holding_days = (trade_date −
      first_buy_date).days − mean_buy_period. 0 when total_qty = 0 (no
      capital at risk) or mean_holding_days <= 0.
    - normalized_mean_buy_period = weighted-avg BUY period (calendar days
      since the first BUY), weighted on remaining qty. Mirrors cost_basis_norm
      in the TIME dimension: BUY updates the weighted average; SELL keeps it
      constant (proportional reduction); resets to 0 on full liquidation.
      Mean holding time = (trade_date − first_buy_date).days − this value;
      used as the mean buy time to derive per-holding-period return.

    ``decisions`` must already be sorted + numbered (decision_no assigned by
    ``assign_decision_no``). ``anchor_price`` is the first BUY fill_price
    (normalization anchor; normalized_close = close / anchor * 100).
    """
    if code_df.empty or not decisions or anchor_price is None:
        return []

    cd = code_df.reset_index(drop=True)

    # Build a lookup: exec_date → decision (at most one decision per day in
    # this backtest model — the main loop processes one signal per day).
    decision_by_date = {d["exec_date"]: d for d in decisions}

    first_buy = next((d for d in decisions if d["side"] == "BUY"), None)
    if first_buy is None:
        return []
    first_buy_date = first_buy["exec_date"]

    # Find the index of the first BUY's exec_date — daily rows start here.
    start_idx = None
    for i in range(len(cd)):
        if cd.iloc[i]["date"] == first_buy["exec_date"]:
            start_idx = i
            break
    if start_idx is None:
        return []

    # Running state (carried from last decision; updated on decision days).
    total_qty = 0.0
    cash = 0.0
    cost_basis_norm = 0.0
    realized_pnl_cum = 0.0
    # mean_buy_period mirrors cost_basis_norm in the TIME dimension: the
    # weighted-avg BUY period (calendar days since the first BUY), weighted
    # on remaining qty. BUY updates the weighted average; SELL keeps it
    # constant (a partial SELL reduces all historical buys proportionally,
    # so the weighted-avg buy date is unchanged); resets to 0 on full
    # liquidation. Stored as normalized_mean_buy_period so holding time =
    # (trade_date − first_buy_date).days − mean_buy_period.
    mean_buy_period = 0.0

    daily_rows: List[Dict[str, Any]] = []
    for i in range(start_idx, len(cd)):
        row = cd.iloc[i]
        trade_date = row["date"]
        close_price = row.get("close_price")
        if pd.isna(close_price) or close_price <= 0:
            continue

        normalized_close = F.normalized_price(float(close_price), anchor_price)

        # Apply decision if one was executed on this date.
        decision = decision_by_date.get(trade_date)
        is_decision_day = decision is not None
        decision_no = None
        if is_decision_day:
            # coerce to float: decisions may come from the DB (Decimal) when
            # recomputing daily rows in-place, or from the backtest (float).
            total_qty = float(decision.get("total_qty_after") or 0.0)
            cash = float(decision.get("cash_after") or 0.0)
            # normalized_mean_buy_price is the carried-forward cost basis:
            #   BUY  → post-BUY weighted average (new cost basis)
            #   SELL → pre-SELL cost basis (constant across partial SELLs;
            #          after a full SELL total_qty=0 so unrealized_pnl=0)
            cost_basis_norm = float(decision.get("normalized_mean_buy_price") or 0.0)
            # normalized_mean_buy_period mirrors cost_basis_norm in the TIME
            # dimension (weighted-avg BUY period in days since first BUY).
            #   BUY  → post-BUY weighted average including this BUY's period
            #   SELL → unchanged (proportional reduction); 0 on full liquidation
            if decision["side"] == "BUY":
                this_buy_period = (trade_date - first_buy_date).days
                qty = float(decision.get("qty") or 0.0)
                tq_before = float(decision.get("total_qty_before") or 0.0)
                tq_after = float(decision.get("total_qty_after") or 0.0)
                if tq_after > 0:
                    mean_buy_period = (
                        tq_before * mean_buy_period + qty * this_buy_period
                    ) / tq_after
                else:
                    mean_buy_period = 0.0
            else:  # SELL
                realized_pnl_cum += float(decision.get("realized_pnl") or 0.0)
                tq_after = float(decision.get("total_qty_after") or 0.0)
                if tq_after <= 0:
                    mean_buy_period = 0.0
                # else: stays constant (proportional reduction)
            decision_no = decision.get("decision_no")

        position_value = F.position_value(total_qty, normalized_close)
        unrealized_pnl = F.realized_pnl(total_qty, normalized_close, cost_basis_norm)
        total_pnl = realized_pnl_cum + unrealized_pnl

        # return_rate = ANNUALIZED return on capital.
        # Uses normalized_mean_buy_price (cost_basis_norm) for capital,
        # normalized_mean_buy_period for the holding-time derivation, and
        # total_pnl as the numerator. 0 when total_qty = 0 (no capital at
        # risk) or mean_holding_days <= 0.
        capital_deployed = F.position_value(total_qty, cost_basis_norm)
        mean_holding_days = (trade_date - first_buy_date).days - mean_buy_period
        return_rate = F.annualized_return(
            total_pnl, capital_deployed, mean_holding_days,
        )

        daily_rows.append({
            "trade_date": trade_date,
            "close_price": _round6(float(close_price)),
            "normalized_close": _round6(normalized_close),
            "total_qty": _round6(total_qty),
            "cost_basis_norm": _round6(cost_basis_norm),
            "position_value": _round6(position_value),
            "cash": _round6(cash),
            "realized_pnl_cum": _round6(realized_pnl_cum),
            "unrealized_pnl": _round6(unrealized_pnl),
            "total_pnl": _round6(total_pnl),
            "return_rate": _round6(return_rate),
            "normalized_mean_buy_period": _round6(mean_buy_period),
            "is_decision_day": is_decision_day,
            "decision_no": decision_no,
        })

    # Compute annualized Sharpe ratios (×√255, rf=0) of daily Δtotal_pnl over
    # three windows (full history, 255d, 500d) and write them onto each row.
    _compute_sharpe_ratios(daily_rows)

    return daily_rows


# ---------------------------------------------------------------------------
#  Run-level summary
# ---------------------------------------------------------------------------
def summarize(decisions: List[Dict[str, Any]], params: dict) -> Dict[str, Any]:
    """Compute run-level summary stats for logging."""
    if not decisions:
        return {"n_decisions": 0, "n_buys": 0, "n_sells": 0,
                "realized_pnl": 0.0, "final_cash": 0.0,
                "total_buy_cost": 0.0}
    n_buys = sum(1 for d in decisions if d["side"] == "BUY")
    n_sells = sum(1 for d in decisions if d["side"] == "SELL")
    realized = sum(d["realized_pnl"] or 0.0 for d in decisions
                   if d["side"] == "SELL")
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
