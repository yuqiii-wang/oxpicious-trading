"""strategy.singleton_trading — generic algo-driven backtest ENTRY POINT.

This package is a pure CLI entry point: it parses args and wires the
algo_signal_collector to the shared discover->fetch->backtest->upsert
runner. All signal logic lives in ``strategy.factors_and_algos`` (the
collector + per-algo packages); there is no fetch/signal code here
anymore — the collector + algos own fetch and signal math.

The strategy identity stored in the DB (``strategy_identity.strategy_name``)
is the **algo name** itself (e.g. ``bollinger_bands`` / ``macd`` /
``ma_spread``), not the package name. Per-(security, date-range) algo params
are loaded from ``strategy.algo_configs`` by
``factors_and_algos.loader.load_params``.

What lives here (algo-agnostic):
  - DEFAULT_ALGO             — the algo used when --algo is omitted
  - STRATEGY_PARAMS          — TRADING-LAYER defaults only (engine keys:
                               min_holding_period, buy_notional,
                               skip_final_liquidation). Algo-specific params
                               (band_width, ema_short, ...) are NOT here —
                               they come from the algo's DEFAULT_PARAMS and
                               the DB algo_configs row.
  - __main__                 — CLI: --algo picks the algo; strategy_name =
                               algo_name. Constructs an AlgoSignalCollector
                               (binary selection: {algo: 1.0}) and feeds it
                               to discover_and_run.
"""
from __future__ import annotations

from strategy._common.constants import (  # noqa: F401
    ALL_SEC_TYPES, DEFAULT_CODES, BATCH_SIZE,
    SEC_TYPE_BASIC_STATS_TABLE, DEFAULT_BUY_NOTIONAL,
)

# Default algo when --algo is omitted. Registered in factors_and_algos.
DEFAULT_ALGO = "bollinger_bands"

# TRADING-LAYER defaults (engine-consumed; NOT algo-specific). These are
# merged into the params dict alongside the algo's DEFAULT_PARAMS + the DB
# algo_configs row. Algo-specific keys (band_width, ema_short, weights, ...)
# are intentionally NOT here — they belong to the algo + the DB config.
#
#   - min_holding_period, buy_notional — consumed by strategy._trading.engine
#   - skip_final_liquidation — True: the 1-month forecast module (_1m_forcast)
#     takes over the sell schedule over the 20 forecast days; the position
#     stays open at the end of the actual OHLC data.
STRATEGY_PARAMS = {
    "min_holding_period": 5,
    "buy_notional": DEFAULT_BUY_NOTIONAL,
    "skip_final_liquidation": True,
}
