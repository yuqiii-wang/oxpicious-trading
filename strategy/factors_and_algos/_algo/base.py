"""Abstract base class for pluggable signal algos.

Every algo in ``strategy.factors_and_algos`` (macd) inherits from
:class:`AlgoBase`. The ABC enforces a consistent
contract — each algo MUST declare its class attributes + implement the three
abstract methods (``fetch_signal_data``, ``apply_signals``,
``build_signal_reason``). The concrete methods (``build_params``,
``run_backtest``, ``compute_daily_rows``) are SHARED across all algos,
eliminating the per-algo ``config.py`` / ``adapter.py`` duplication.

Contract
--------
Class attributes (each algo overrides):
  ``ALGO_NAME``         — unique short name (e.g. "macd")
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
from strategy.factors_and_algos._algo.tuning import (
    SIGNAL_CONFIDENCE_THRESHOLD,
    tune_signals,
    apply_exec_delays,
)


class AlgoBase(ABC):
    """Abstract base for all pluggable signal algos.

    Subclasses MUST set the class attributes + implement the three abstract
    methods. The concrete methods (``build_params``, ``build_params_from_json``,
    ``run_backtest``, ``compute_daily_rows``) are inherited as-is.
    """

    # ------------------------------------------------------------------
    # Class attributes — each algo overrides these
    # ------------------------------------------------------------------
    ALGO_NAME: str = ""
    POSITION_AWARE: bool = False
    DEFAULT_PARAMS: Dict[str, Any] = {}
    REQUIRED_COLUMNS: tuple = ()
    ALGO_PARAM_KEYS: tuple = ()

    # JSON-serializable declaration of the algo's TUNABLE model params —
    # the search space the optimization engine (``_optm_engine``) samples.
    # Empty dict = no tunable model params (only the COMMON trading space
    # is optimized). Schema per entry (Optuna-compatible):
    #   {"type": "int"|"float", "low": x, "high": y, "log": bool?, "step": n?}
    #   {"type": "categorical", "choices": [...]}
    # The engine reads this via the base-class contract, so ANY inherited
    # algo that declares a space is optimizable without engine changes.
    TUNABLE_SPACE: Dict[str, dict] = {}

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

    def build_params_from_json(self, params_json: str | dict | None) -> dict:
        """Build a full params dict from a JSON arg (str or decoded dict).

        Accepts common algo params as a JSON argument — a JSON object
        string (CLI ``--params-json``) or an already-decoded dict — merges
        it over ``DEFAULT_PARAMS`` via :meth:`build_params`, and returns
        the fully-populated param dict. ``None`` / empty / ``"null"`` →
        plain defaults. Unknown keys pass through untouched (same rule as
        ``build_params``) so trading-layer keys may travel in the JSON.
        """
        import json

        if params_json is None:
            return self.build_params()
        if isinstance(params_json, str):
            s = params_json.strip()
            if not s or s.lower() == "null":
                return self.build_params()
            params_json = json.loads(s)
        if not isinstance(params_json, dict):
            raise TypeError(
                "params_json must be a JSON object string or dict, got "
                f"{type(params_json).__name__}"
            )
        return self.build_params(dict(params_json))

    # ------------------------------------------------------------------
    # Abstract: each algo implements its own fetch / signal / reason
    # ------------------------------------------------------------------
    @abstractmethod
    async def fetch_signal_data(self, conn, sec_type: str, codes: list) -> pd.DataFrame:
        """Fetch the per-(code, date) data the algo needs from the DB.

        Each algo owns its table joins (MACD reads OHLC straight from basic_stats). Returns a DataFrame
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

        When ``params["fault_tolerance"] > 0``, a post-hoc stress pass
        runs after the baseline backtest: OHLC on each baseline decision
        date is adversarially perturbed (BUY up, SELL down by ft% of
        |Δclose|), ``apply_signals`` is re-run on the stressed OHLC
        (same precomputed tech stats — NOT recomputed), and the stressed
        ``signal_confidence`` is attached to each decision as
        ``ft_stressed_conf``. The baseline decisions themselves are
        unchanged; the FT data is a comparison annotation for the UI.
        """
        if not df.empty:
            df = self.apply_signals(df, params)
            # tune_signals / apply_exec_delays mutate in-place — copy so
            # the caller's df (e.g. the optimizer's cached base df) is
            # never modified across trials.
            df = df.copy()
            # Base signal tuning (inherited by all sub-algos): zero out
            # sub-threshold |signal_confidence| so dust trades are ignored
            # by the engine. The threshold is param-driven (default
            # SIGNAL_CONFIDENCE_THRESHOLD) so the optimizer can tune it.
            df = tune_signals(
                df,
                threshold=float(
                    params.get("conf_threshold", SIGNAL_CONFIDENCE_THRESHOLD)
                    or SIGNAL_CONFIDENCE_THRESHOLD
                ),
            )
            # Execution-date tuning (inherited by all sub-algos): shift
            # BUY/SELL execution N trading days after the signal bar.
            # Default delays are 0 (execute on the signal bar), so normal
            # runs are unchanged; optimizer-tuned params flow through the
            # DB algo_configs row.
            df = apply_exec_delays(
                df,
                buy_delay=int(params.get("buy_exec_delay", 0) or 0),
                sell_delay=int(params.get("sell_exec_delay", 0) or 0),
            )
        decisions = _run_backtest_engine(
            df, params, sec_type, codes,
            signal_reason_fn=self.build_signal_reason,
        )

        ft = float(params.get("fault_tolerance", 0) or 0)
        if ft > 0 and decisions:
            from strategy.factors_and_algos._algo.fault_tolerance import run_ft_stress
            decisions = run_ft_stress(
                self, df, params, sec_type, codes,
                baseline_decisions=decisions,
                signal_reason_fn=self.build_signal_reason,
            )
        return decisions

    def compute_daily_rows(self, code_df, decisions, anchor_price):
        """Delegate to the signal-agnostic engine helper (daily P&L)."""
        return _compute_daily_rows(code_df, decisions, anchor_price)


__all__ = ["AlgoBase"]
