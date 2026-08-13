"""Abstract base class for pluggable signal algos.

Every algo in ``strategy.factors_and_algos`` (bollinger_bands, macd,
ma_spread) inherits from :class:`AlgoBase`. The ABC enforces a consistent
contract — each algo MUST declare its class attributes + implement the three
abstract methods (``fetch_signal_data``, ``apply_signals``,
``build_signal_reason``). The concrete methods (``build_params``,
``run_backtest``, ``compute_daily_rows``) are SHARED across all algos,
eliminating the per-algo ``config.py`` / ``adapter.py`` duplication.

Contract
--------
Class attributes (each algo overrides):
  ``ALGO_NAME``         — unique short name (e.g. "bollinger_bands")
  ``POSITION_AWARE``    — False = position-irrelevant (safe to blend)
  ``DEFAULT_PARAMS``    — algo-specific default param dict
  ``REQUIRED_COLUMNS``  — data columns the algo reads from fetched df
  ``ALGO_PARAM_KEYS``   — params the algo actually reads (for validation)

Abstract methods (each algo implements):
  ``fetch_signal_data(conn, sec_type, codes) -> DataFrame``
      DB fetch — each algo owns its table joins / column selection.
  ``apply_signals(df, params) -> DataFrame``
      Pure signal math — adds ``signal_confidence`` + ``signal_value``.
  ``build_signal_reason(row, side, params, confidence) -> str``
      Human-readable text for ``trade_decision.signal_reason``.

Concrete methods (shared, inherited):
  ``build_params(overrides) -> dict``
      Merge DEFAULT_PARAMS with caller-supplied overrides (identical
      logic across all algos — previously duplicated in each config.py).
  ``run_backtest(df, params, sec_type, codes) -> list[dict]``
      Apply signals, then delegate to the signal-agnostic execution engine
      in ``strategy._trading.engine``. The engine reads only
      ``signal_confidence`` (+ ``signal_value``) + trading-layer keys
      (min_holding_period, buy_notional, skip_final_liquidation) — it
      never reaches into algo-specific columns.
  ``compute_daily_rows(code_df, decisions, anchor_price)``
      Re-exported from the engine (daily P&L / position bookkeeping).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List

import pandas as pd

from strategy._trading.engine import (
    run_backtest as _run_backtest_engine,
    compute_daily_rows as _compute_daily_rows,
)


class AlgoBase(ABC):
    """Abstract base for all pluggable signal algos.

    Subclasses MUST set the class attributes + implement the three abstract
    methods. The concrete methods (``build_params``, ``run_backtest``,
    ``compute_daily_rows``) are inherited as-is.
    """

    # ------------------------------------------------------------------
    # Class attributes — each algo overrides these
    # ------------------------------------------------------------------
    ALGO_NAME: str = ""
    POSITION_AWARE: bool = False
    DEFAULT_PARAMS: Dict[str, Any] = {}
    REQUIRED_COLUMNS: tuple = ()
    ALGO_PARAM_KEYS: tuple = ()

    # ------------------------------------------------------------------
    # Concrete: merge DEFAULT_PARAMS with caller overrides
    # ------------------------------------------------------------------
    def build_params(self, overrides: dict | None = None) -> dict:
        """Merge ``DEFAULT_PARAMS`` with caller-supplied ``overrides``.

        This is the dynamic-config seam: a strategy (or the DB loader) calls
        ``build_params(its_overrides)`` to get a fully-populated algo param
        dict. Only algo-specific keys are managed here; trading-layer keys
        (buy_notional, min_holding_period, ...) are added by the strategy
        and travel in the same dict unchanged.

        ``overrides`` may contain extra (non-algo) keys — they are passed
        through untouched so the merged dict stays compatible with the
        engine, which reads trading-layer keys from the same ``params``.
        """
        merged = dict(self.DEFAULT_PARAMS)
        if overrides:
            merged.update(overrides)
        return merged

    # ------------------------------------------------------------------
    # Abstract: each algo implements its own fetch / signal / reason
    # ------------------------------------------------------------------
    @abstractmethod
    async def fetch_signal_data(self, conn, sec_type: str, codes: list) -> pd.DataFrame:
        """Fetch the per-(code, date) data the algo needs from the DB.

        Each algo owns its table joins (e.g. ma_spread joins mov_ave_rsi,
        MACD reads OHLC straight from basic_stats). Returns a DataFrame
        sorted by (code, date) with OHLC + the algo's REQUIRED_COLUMNS.
        """

    @abstractmethod
    def apply_signals(self, df: pd.DataFrame, params: dict) -> pd.DataFrame:
        """Add ``signal_confidence`` + ``signal_value`` columns to ``df``.

        Pure signal math — no DB, no engine. Reads the algo's REQUIRED_COLUMNS
        + the algo-specific keys in ``params``. Trading-layer keys in
        ``params`` are ignored here (they flow through to the engine).
        """

    @abstractmethod
    def build_signal_reason(self, row, side: str, params: dict, confidence: float) -> str:
        """Build the ``signal_reason`` text for a trade_decision row.

        Called by the engine with the row + the side/confidence it derived
        from ``signal_confidence``. Algo-specific phrasing (e.g. MA5/MA60
        cross vs BB z-score vs MACD crossover).
        """

    # ------------------------------------------------------------------
    # Concrete: apply signals + delegate to the execution engine
    # ------------------------------------------------------------------
    def run_backtest(
        self, df: pd.DataFrame, params: dict, sec_type: str, codes: list,
    ) -> List[Dict[str, Any]]:
        """Apply this algo's signals, then run the signal-agnostic engine.

        The engine reads only ``signal_confidence`` (+ ``signal_value``)
        and the trading-layer keys in ``params`` (min_holding_period,
        buy_notional, skip_final_liquidation). ``signal_reason_fn`` is
        this algo's :meth:`build_signal_reason` so the reason text matches
        the algo's phrasing.
        """
        if not df.empty:
            df = self.apply_signals(df, params)
        return _run_backtest_engine(
            df, params, sec_type, codes,
            signal_reason_fn=self.build_signal_reason,
        )

    def compute_daily_rows(self, code_df, decisions, anchor_price):
        """Delegate to the signal-agnostic engine helper (daily P&L)."""
        return _compute_daily_rows(code_df, decisions, anchor_price)


__all__ = ["AlgoBase"]
