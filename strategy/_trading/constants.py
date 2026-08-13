"""Constants for the trading / execution / portfolio model.

Strategy-agnostic: these describe the worst-case fill model, transaction
costs, and the normalized-space conventions used by every strategy's
backtest engine. A strategy package (e.g. singleton_trading) supplies the
signal layer; this package supplies the execution layer.

Normalized-space model (ALL money metrics in base-100 units):
  - The FIRST BUY fill_price is the anchor (→ normalized_fill_price = 100).
  - Every decision carries normalized_fill_price = fill_price / anchor * 100.
  - shares = total_qty / SHARES_PER_QTY  (the normalized share count), so
    position = shares * norm_price is on the same comparable scale across
    strategies and codes regardless of the raw price level.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Transaction costs
# ---------------------------------------------------------------------------
# Fee = FEE_RATE × BUY notional (normalized money), applied to BUY only
# (0 for SELL). A-share convention: the buyer pays the spread + a small fee;
# the seller pays neither (commission is folded into this single rate).
# 0.002 = 0.2% of the BUY notional (qty / 100 × norm_price).
FEE_RATE = 0.002

# ---------------------------------------------------------------------------
# Worst-case fill model
# ---------------------------------------------------------------------------
# The backtest is a conservative stress-test: every BUY fills at the day's
# worst (highest) plausible price and every SELL fills at the day's worst
# (lowest) plausible price. The "plausible" band is SLIPPAGE_BAND × (high-low)
# beyond the close, clamped to the day's OHLC range:
#   BUY  = max(high, close + SLIPPAGE_BAND × (high - low))
#   SELL = min(low,  close - SLIPPAGE_BAND × (high - low))
# 0.05 = 5% of the day's range beyond the close as the stress margin.
SLIPPAGE_BAND = 0.05

# ---------------------------------------------------------------------------
# Normalization conventions
# ---------------------------------------------------------------------------
# The first BUY fill_price is the anchor; its normalized_fill_price = 100.
# normalized_fill_price = fill_price / anchor × NORMALIZATION_BASE.
NORMALIZATION_BASE = 100.0

# Money = (total_qty / SHARES_PER_QTY) × norm_price.
# Dividing by 100 keeps position ≈ total_qty at the entry anchor (norm=100),
# so position and total_qty are directly comparable in the decision table.
SHARES_PER_QTY = 100.0

# ---------------------------------------------------------------------------
# Annualization (for Sharpe + return-rate formulas)
# ---------------------------------------------------------------------------
# ~255 trading days per year on A-share / US-equity calendars.
TRADING_DAYS_PER_YEAR = 255
