"""Evaluation adapters — the uniform backtest seam for the trainer.

The trainer's objective functions need ONE backtest surface regardless
of what produces the signals. Binary mode wraps a single ``AlgoBase``
algo (the historical behavior); mixed mode wraps the
``AlgoSignalCollector`` and swaps its per-algo frames
(``collector._mixed_dfs``) to the segment being evaluated, so every
IS / OOS / full-series backtest runs on segment-standalone data — the
same per-code positional-split rule ``_split_is_oos`` applies to the
unified frames.

Both adapters mirror the ``AlgoBase`` surface (``TUNABLE_SPACE``,
``DEFAULT_PARAMS``, ``run_backtest``, ``compute_daily_rows``) so the
trainer helpers (``snap_categorical_params``, ``suggest_set_a_params``)
accept either unchanged.
"""
from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd

from strategy.factors_and_algos._algo import AlgoBase


class OptmBacktester:
    """Base adapter: one backtest callable + a segment-preparation hook.

    ``prepare(segment)`` runs before a segment ("is" / "oos" / "full")
    is evaluated; the base implementation is a no-op.

    The replay-fidelity flags (``AlgoBase.DUST_FILTER`` /
    ``TUNES_EXEC_DELAYS`` / ``TUNES_MIN_HOLDING``) ride the adapter so
    the trainer's space builders (``space.py``) read them from
    ``ctx.backtester`` unchanged — the base defaults keep every wrapper
    that doesn't override them on the standard dust-filter + delays +
    holding-gate path.
    """

    TUNABLE_SPACE: Dict[str, dict] = {}
    DEFAULT_PARAMS: Dict[str, Any] = {}
    DUST_FILTER: bool = True
    TUNES_EXEC_DELAYS: bool = True
    TUNES_MIN_HOLDING: bool = True

    def prepare(self, segment: str) -> None:
        """Hook before evaluating a segment (default: no-op)."""

    def run_backtest(
        self, df: pd.DataFrame, params: dict, sec_type: str, codes: list,
    ) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def compute_daily_rows(self, code_df, decisions, anchor_price) -> list:
        raise NotImplementedError


class AlgoBacktester(OptmBacktester):
    """Binary adapter — delegates to one :class:`AlgoBase` algo."""

    def __init__(self, algo: AlgoBase):
        self.algo = algo
        self.TUNABLE_SPACE = dict(getattr(algo, "TUNABLE_SPACE", {}) or {})
        self.DEFAULT_PARAMS = dict(getattr(algo, "DEFAULT_PARAMS", {}) or {})
        # The replay-fidelity flags pass through so the trainer's space
        # builders see the WRAPPED algo's behavior (the oracle drops the
        # conf_threshold axis from Set A and the delay + holding-period
        # axes from Set B).
        self.DUST_FILTER = bool(getattr(algo, "DUST_FILTER", True))
        self.TUNES_EXEC_DELAYS = bool(getattr(algo, "TUNES_EXEC_DELAYS", True))
        self.TUNES_MIN_HOLDING = bool(getattr(algo, "TUNES_MIN_HOLDING", True))

    def run_backtest(
        self, df: pd.DataFrame, params: dict, sec_type: str, codes: list,
    ) -> List[Dict[str, Any]]:
        return self.algo.run_backtest(df, params, sec_type, codes)

    def compute_daily_rows(self, code_df, decisions, anchor_price) -> list:
        return self.algo.compute_daily_rows(code_df, decisions, anchor_price)


class CollectorBacktester(OptmBacktester):
    """Mixed adapter — delegates to the :class:`AlgoSignalCollector`.

    ``segments`` maps segment name → ``{algo_name: DataFrame}`` (the
    exact shape of ``collector._mixed_dfs``), built once by
    ``_optm_engine.__main__`` from positional per-code splits of each
    algo's fetched frame. ``prepare`` swaps the collector's per-algo
    frames to the segment's copies; the blend joins them onto the
    unified evaluation df on (code, date), so per-code frames join
    correctly against the multi-code segment frames.

    Analysis-source algos carry no model params, so the tunable space is
    empty: Set A TPE is binary-only — mixed mode searches the curated
    comb grid (Set C) instead.
    """

    def __init__(self, collector, segments: Dict[str, Dict[str, pd.DataFrame]]):
        self.collector = collector
        self.segments = segments

    def prepare(self, segment: str) -> None:
        frames = self.segments.get(segment)
        if frames is not None:
            self.collector._mixed_dfs = frames

    def run_backtest(
        self, df: pd.DataFrame, params: dict, sec_type: str, codes: list,
    ) -> List[Dict[str, Any]]:
        return self.collector.run_backtest(df, params, sec_type, codes)

    def compute_daily_rows(self, code_df, decisions, anchor_price) -> list:
        return self.collector.compute_daily_rows(code_df, decisions, anchor_price)


__all__ = ["OptmBacktester", "AlgoBacktester", "CollectorBacktester"]
