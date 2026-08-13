"""Documented financial formulas for the worst-case-fill backtest model.

All money metrics are in NORMALIZED units (base = 100 at the first BUY).
See ``constants.py`` for the model parameters and conventions.

Conventions
-----------
  - ``anchor_price`` = first BUY fill_price (sets normalized_fill_price = 100)
  - ``normalized_fill_price`` = fill_price / anchor × NORMALIZATION_BASE
  - ``shares`` = total_qty / SHARES_PER_QTY  (normalized share count)
  - ``position`` = shares × normalized_price  (market value)
  - ``cash``     = cumulative sum of (±shares × normalized_price) − fees

Each function below carries its formula in the docstring so the math is
auditable in one place rather than scattered across the engine loop.
"""
from __future__ import annotations

from strategy._trading.constants import (
    FEE_RATE,
    SLIPPAGE_BAND,
    NORMALIZATION_BASE,
    SHARES_PER_QTY,
    TRADING_DAYS_PER_YEAR,
)


# ---------------------------------------------------------------------------
#  Fill prices (worst-case OHLC stress model)
# ---------------------------------------------------------------------------
def worst_case_buy_fill(high: float, low: float, close: float) -> float:
    """Worst-case BUY fill price (highest plausible).

        fill = max(high, close + SLIPPAGE_BAND × (high − low))

    The buyer is stressed to the day's worst (highest) plausible price:
    either the session high or the close + band, whichever is higher.
    """
    rng = high - low
    return max(high, close + SLIPPAGE_BAND * rng)


def worst_case_sell_fill(high: float, low: float, close: float) -> float:
    """Worst-case SELL fill price (lowest plausible).

        fill = min(low, close − SLIPPAGE_BAND × (high − low))

    The seller is stressed to the day's worst (lowest) plausible price:
    either the session low or the close − band, whichever is lower.
    """
    rng = high - low
    return min(low, close - SLIPPAGE_BAND * rng)


# ---------------------------------------------------------------------------
#  Normalization
# ---------------------------------------------------------------------------
def normalized_price(fill_price: float, anchor_price: float) -> float:
    """Rebase a fill_price to the first-BUY anchor (base = 100).

        norm_price = fill_price / anchor_price × NORMALIZATION_BASE
    """
    return fill_price / anchor_price * NORMALIZATION_BASE


# ---------------------------------------------------------------------------
#  Slippage (deviation from close, per-100-shares scale)
# ---------------------------------------------------------------------------
def buy_slippage(fill_price: float, close: float) -> float:
    """Slippage paid on a BUY (≥ 0: you paid more than the close).

        slippage = (fill_price − close) / SHARES_PER_QTY

    Divided by 100 to express it on the same normalized scale as the fee
    (per-100 shares), so slippage and fee are directly comparable in the
    decision table. Always ≥ 0 because the worst-case BUY fill is ≥ close.
    """
    return (fill_price - close) / SHARES_PER_QTY


def sell_slippage(fill_price: float, close: float) -> float:
    """Slippage paid on a SELL (≥ 0: you received less than the close).

        slippage = (close − fill_price) / SHARES_PER_QTY

    Divided by 100 to match the BUY slippage scale (per-100 shares).
    Always ≥ 0 because the worst-case SELL fill is ≤ close.
    """
    return (close - fill_price) / SHARES_PER_QTY


# ---------------------------------------------------------------------------
#  Fee (BUY only)
# ---------------------------------------------------------------------------
def buy_fee(qty: float, norm_price: float) -> float:
    """Fee on a BUY = FEE_RATE × BUY notional (normalized money).

        fee = FEE_RATE × (qty / SHARES_PER_QTY) × norm_price

    Applied to BUY only (0 for SELL, per A-share convention). Deducted
    from cash_after on BUY.
    """
    return FEE_RATE * (qty / SHARES_PER_QTY) * norm_price


# ---------------------------------------------------------------------------
#  Position value (mark-to-market)
# ---------------------------------------------------------------------------
def position_value(total_qty: float, norm_price: float) -> float:
    """Mark-to-market position value (normalized money).

        position = (total_qty / SHARES_PER_QTY) × norm_price
    """
    return total_qty * norm_price / SHARES_PER_QTY


# ---------------------------------------------------------------------------
#  Cash deltas
# ---------------------------------------------------------------------------
def cash_delta_buy(qty: float, norm_price: float, fee: float) -> float:
    """Cash change on a BUY (negative: you pay).

        Δcash = −(qty / SHARES_PER_QTY) × norm_price − fee
    """
    return -(qty / SHARES_PER_QTY) * norm_price - fee


def cash_delta_sell(qty_sold: float, norm_price: float) -> float:
    """Cash change on a SELL (positive: you receive). No fee on SELL.

        Δcash = +(qty_sold / SHARES_PER_QTY) × norm_price
    """
    return (qty_sold / SHARES_PER_QTY) * norm_price


# ---------------------------------------------------------------------------
#  Realized P&L (SELL)
# ---------------------------------------------------------------------------
def realized_pnl(
    qty_sold: float, norm_price: float, cost_basis_norm: float,
) -> float:
    """Realized P&L on a SELL (normalized money).

        realized = (qty_sold / SHARES_PER_QTY) × (sell_norm − cost_basis_norm)

    where ``cost_basis_norm`` is the weighted-average BUY normalized price
    (see :func:`weighted_avg_cost_basis`). Positive for a gain, negative
    for a loss.
    """
    return qty_sold * (norm_price - cost_basis_norm) / SHARES_PER_QTY


# ---------------------------------------------------------------------------
#  Cost basis (weighted-average BUY normalized price)
# ---------------------------------------------------------------------------
def weighted_avg_cost_basis(
    prev_total_qty: float,
    prev_cost_basis: float,
    qty: float,
    norm_price: float,
) -> float:
    """Post-BUY weighted-average BUY normalized price (cost basis).

        new_total = prev_total_qty + qty
        cost_basis = (prev_total × prev_cost + qty × norm_price) / new_total

    Reset to 0 when ``new_total`` ≤ 0 (full liquidation). The cost basis is
    a PRICE (not money), so it is NOT divided by SHARES_PER_QTY.
    """
    new_total = prev_total_qty + qty
    if new_total <= 0:
        return 0.0
    return (prev_total_qty * prev_cost_basis + qty * norm_price) / new_total


def sell_qty(confidence: float, total_qty_before: float) -> float:
    """Quantity sold on a SELL.

        qty_sold = (confidence / 100) × total_qty_before

    Confidence is a FRACTION of the current position (NOT capital), so
    confidence ≤ 100 ⇒ qty_sold ≤ total_qty_before (no shorting).
    """
    return (confidence / 100.0) * total_qty_before


# ---------------------------------------------------------------------------
#  Total buy cost (peak capital deployed)
# ---------------------------------------------------------------------------
def total_buy_cost(
    max_total_qty_after: float, mean_buy_price: float,
) -> float:
    """Peak capital deployed (normalized money).

        total_buy_cost = (max_total_qty_after / SHARES_PER_QTY) × mean_buy_price

    where ``max_total_qty_after`` is the peak position size reached during
    the run and ``mean_buy_price`` is the cost basis at that decision.
    Total Return = final_cash / total_buy_cost.
    """
    return (max_total_qty_after / SHARES_PER_QTY) * mean_buy_price


# ---------------------------------------------------------------------------
#  Annualized return on capital
# ---------------------------------------------------------------------------
def annualized_return(
    total_pnl: float,
    capital_deployed: float,
    mean_holding_days: float,
) -> float:
    """Annualized return on capital.

        return_rate = (total_pnl / capital_deployed / max(mean_holding_days, 1))
                      × TRADING_DAYS_PER_YEAR

    Returns 0 when ``capital_deployed`` ≤ 0 (no capital at risk) or
    ``mean_holding_days`` ≤ 0 (no elapsed holding time). The ``max(..., 1)``
    clamp prevents division by a tiny positive fraction (e.g. 0.001 when a
    late large-qty BUY pulls mean_buy_period close to the current day count),
    which would otherwise inflate return_rate to NUMERIC(18,6)-overflowing
    magnitudes.
    """
    if capital_deployed > 0 and mean_holding_days > 0:
        return (
            total_pnl / capital_deployed / max(mean_holding_days, 1.0)
            * TRADING_DAYS_PER_YEAR
        )
    return 0.0


# ---------------------------------------------------------------------------
#  Sharpe ratio (annualized, rf=0)
# ---------------------------------------------------------------------------
def sharpe_ratio(mean: float, std: float) -> float:
    """Annualized Sharpe ratio (risk-free rate = 0).

        Sharpe = (mean / std) × √TRADING_DAYS_PER_YEAR

    Guarded against 0 / NaN / inf (returns 0 in those cases).
    """
    import math

    if std == 0 or std != std:  # std == 0 or NaN
        return 0.0
    ratio = mean / std * math.sqrt(TRADING_DAYS_PER_YEAR)
    if ratio != ratio or ratio in (float("inf"), float("-inf")):  # NaN / inf
        return 0.0
    return ratio
