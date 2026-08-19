"""strategy.singleton_trading — generic algo-driven backtest ENTRY POINT.

This package is a pure CLI entry point: it parses args and wires the
algo_signal_collector to the shared discover->fetch->backtest->upsert
runner. All signal logic lives in ``strategy.factors_and_algos`` (the
collector + per-algo packages); there is no fetch/signal code here
anymore — the collector + algos own fetch and signal math.

The strategy identity stored in the DB (``strategy_identity.strategy_name``)
is the **algo name** itself (e.g. ``macd``), not the package name.
Per-(security, date-range) algo params are loaded from
``strategy.algo_configs`` by ``factors_and_algos.loader.load_params``.

What lives here (algo-agnostic):
  - DEFAULT_ALGO             — resolved lazily from _algo.registry
                               (single source; lazy so importing this
                               package stays pandas-free — see below)
  - STRATEGY_PARAMS          — TRADING-LAYER defaults only (engine keys:
                               min_holding_period, buy_notional,
                               skip_final_liquidation). Algo-specific params
                               (ema_short, ...) are NOT here —
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


# TRADING-LAYER defaults (engine-consumed; NOT algo-specific). These are
# merged into the params dict alongside the algo's DEFAULT_PARAMS + the DB
# algo_configs row. Algo-specific keys (ema_short, weights, ...)
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


# LAZY (PEP 562): DEFAULT_ALGO resolves to the registry's single source on
# first attribute access. Eagerly importing the registry here would pull
# ``strategy.factors_and_algos._algo.base`` → pandas BEFORE ``__main__.py``
# installs the cudf.pandas import hook (``python -m`` executes THIS
# ``__init__`` first), which silently keeps Run Strategy on CPU pandas.
def __getattr__(name: str):
    if name == "DEFAULT_ALGO":
        from strategy.factors_and_algos._algo.registry import DEFAULT_ALGO
        return DEFAULT_ALGO
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | {"DEFAULT_ALGO"})
