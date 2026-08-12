"""Constants shared across all strategy backtest + risk pipelines.

These values are strategy-agnostic — they describe the security universe,
DB table names, and execution defaults that any strategy (ma_spread,
mean-reversion, momentum, etc.) would reuse.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Security universe — the three sec_types backed by analysis.mov_ave_spreads_detail
# ---------------------------------------------------------------------------
ALL_SEC_TYPES: tuple = ("index", "etf", "stock")

# Default sec_type for a generic test run (per project convention: test with
# index first; re-run for etf/stock once verified).
DEFAULT_SEC_TYPE = "index"

# Default code for a generic test run — a broad index with full history.
DEFAULT_CODES: tuple = ("000970",)

# ---------------------------------------------------------------------------
# Batch processing — fetch + backtest in batches of this many codes to keep
# peak memory bounded for large universes (e.g. 11K stocks).
# ---------------------------------------------------------------------------
BATCH_SIZE = 500

# ---------------------------------------------------------------------------
# DB tables — the strategy schema is shared across all strategies.
# Each strategy run writes one strategy_seq row + N trade_decision rows.
# ---------------------------------------------------------------------------
SEQ_TABLE = "strategy.strategy_identity"
INFO_TABLE = "strategy.strategy_results"
DECISION_TABLE = "strategy.trade_decision"
DAILY_TABLE = "strategy.strategy_daily"

# stats basic_stats table per sec_type — source of open/close for fill prices.
# Indices use bare codes (e.g. "000970"); ETF/stock use suffixed codes
# (e.g. "159007.SZ"). The analysis detail table uses the SAME code format per
# sec_type, so the JOIN is on the raw code string.
SEC_TYPE_BASIC_STATS_TABLE: dict = {
    "index": "stats.index_basic_stats",
    "etf":   "stats.etf_basic_stats",
    "stock": "stats.stock_basic_stats",
}

# Default notional (yuan) deployed per BUY trade at confidence=100. Each BUY
# deploys (confidence/100) * buy_notional, so confidence 25 = 25% of this.
# There is no fixed capital budget — BUYs accumulate freely (unlimited),
# and total_buy_cost (the sum of all BUY costs) is computed after the
# backtest. 100,000 yuan per trade = a realistic A-share entry size.
DEFAULT_BUY_NOTIONAL = 100_000.0
