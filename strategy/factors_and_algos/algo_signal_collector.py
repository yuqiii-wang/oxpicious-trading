"""Algo signal collector — consolidate weighted signals from one or more
algos into a single ``signal_confidence``.

This is the weighted-consolidation seam above the pluggable algos in
``strategy.factors_and_algos``. Each algo produces its own signed
``signal_confidence`` ∈ [-100, 100] (BUY > 0, SELL < 0); the collector
FETCHES each algo's data, APPLIES its signals, and WEIGHTS them into one
consolidated signal that the execution engine (``strategy._trading``)
consumes.

Two modes
---------
BINARY: exactly one algo at weight 1.0. The collector delegates fetch /
apply_signals / build_signal_reason to that algo, so behavior is identical
to running the algo directly. ``selection = {"bollinger_bands": 1.0}``
selects Bollinger Bands.

MIXED: multiple algos with non-zero weights. The collector fetches each
algo's data INDEPENDENTLY (each owns its table joins), applies each algo's
signals on its own DataFrame, then weight-blends the per-algo
``signal_confidence`` into one signed value clipped to [-100, 100]. NETTING:
when algos disagree (some BUY, some SELL) on the same bar, the NET signal
(w_sum × conf) decides the side — net positive → BUY, net negative → SELL.
This is the composite-strategy netting rule.

Per-algo contribution visibility
-------------------------------
In MIXED mode, the per-algo ``signal_confidence`` columns (``sig_<algo>``)
are KEPT on the DataFrame (not dropped) so :meth:`build_signal_reason` can
read them from the row and encode a per-algo breakdown into
``signal_reason`` as a JSON prefix:

    ``__MIX__<json>__<human-readable>``

The JSON carries ``{algos: [{a, algo, w, c, n}], blended, netted}`` so the
UI can render a structured tooltip showing each algo's weight (w), raw
signal_confidence (c), and net contribution (n = w × c). ``netted=True``
when some algos fired BUY (c>0) and others SELL (c<0) on the same bar —
the composite signal is the net.

The blend is only valid for NON-POSITION-AWARE algos (POSITION_AWARE =
False); see :func:`strategy.factors_and_algos.portfolio.check_position_aware`.

The collector exposes the same surface a single algo does
(``fetch_signal_data`` / ``apply_signals`` / ``build_signal_reason`` /
``run_backtest`` / ``compute_daily_rows``), so the runner in
``strategy._common.runner`` can treat a collector and a bare algo
interchangeably.

Mixed-mode orchestration
------------------------
The portfolio module (:mod:`strategy.factors_and_algos.portfolio`) drives
the two-phase mixed flow:
  1. ``run_sub_algos`` — each sub-algo runs INDEPENDENTLY (its own
     strategy_identity, skip-if-already-found).
  2. ``build_algo_portfolio`` — the collector (mixed mode) runs the blended
     portfolio backtest under a new ``portfolio:<...>`` strategy_name.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import pandas as pd

from strategy._trading.engine import (
    run_backtest as _run_backtest_engine,
    compute_daily_rows as _compute_daily_rows,
)
from strategy.factors_and_algos import get_algo
from strategy.factors_and_algos.portfolio import blend_signal_confidence, _short_name


class AlgoSignalCollector:
    """Collect + weight signals from one or more algos.

    Parameters
    ----------
    selection:
        ``{algo_name: weight}``. Weights should be non-negative. Algos with
        weight 0 are skipped. In BINARY mode (exactly one algo), weight is
        ignored (treated as 1.0). In MIXED mode, weights are normalized so
        they sum to 1.0 (a non-normalized map like ``{a: 2, b: 3}`` is
        treated as ``{a: 0.4, b: 0.6}``).

    The resolved algo modules are cached on construction (also validates the
    algo names early via :func:`get_algo`). MIXED mode also validates
    position-awareness (all algos must have ``POSITION_AWARE = False``).
    """

    def __init__(self, selection: Dict[str, float]):
        if not selection:
            raise ValueError("selection must contain at least one algo")
        self.selection: Dict[str, float] = {
            name: float(w) for name, w in selection.items()
        }
        # Resolve + cache algo modules (validates names early).
        self._algos = {name: get_algo(name) for name in self.selection}

        # Active algos = those with non-zero weight.
        self._active = {
            name: w for name, w in self.selection.items() if w != 0
        }
        if not self._active:
            raise ValueError("selection must have at least one algo with non-zero weight")

        # Binary mode: exactly one active algo.
        self._binary_algo_name: Optional[str] = None
        if len(self._active) == 1:
            self._binary_algo_name = next(iter(self._active))

        # Normalized weights for MIXED mode (sum to 1.0).
        total_w = sum(self._active.values())
        self._norm_weights: Dict[str, float] = {
            name: w / total_w for name, w in self._active.items()
        } if total_w > 0 else dict(self._active)

        # Per-algo fetched dfs (MIXED mode only, populated by _fetch_mixed).
        self._mixed_dfs: Dict[str, pd.DataFrame] = {}

    # ------------------------------------------------------------------
    # Mode helpers
    # ------------------------------------------------------------------
    @property
    def is_binary(self) -> bool:
        """True when exactly one algo is selected (binary mode)."""
        return self._binary_algo_name is not None

    @property
    def primary_algo(self):
        """The single selected algo module (binary mode only)."""
        if self._binary_algo_name is None:
            raise NotImplementedError(
                "primary_algo is only available in binary mode "
                "(single algo, weight 1.0)"
            )
        return self._algos[self._binary_algo_name]

    # ------------------------------------------------------------------
    # Fetch — each algo fetches what data it needs
    # ------------------------------------------------------------------
    async def fetch_signal_data(self, conn, sec_type: str, codes: list) -> pd.DataFrame:
        """Fetch the data the selected algo(s) need.

        Binary: delegate to the selected algo's ``fetch_signal_data`` — each
        algo owns its table joins (e.g. ma_spread joins mov_ave_rsi, MACD
        reads OHLC straight from basic_stats).

        Mixed: fetch each algo's data INDEPENDENTLY + merge OHLC on
        (code, date). Per-algo REQUIRED_COLUMNS stay on each algo's own df
        (stored in ``self._mixed_dfs``) — the returned unified df carries
        only OHLC + keys; ``apply_signals`` re-joins the blended signal.
        """
        if self.is_binary:
            return await self.primary_algo.fetch_signal_data(conn, sec_type, codes)
        return await self._fetch_mixed(conn, sec_type, codes)

    async def _fetch_mixed(self, conn, sec_type: str, codes: list) -> pd.DataFrame:
        """Mixed-mode fetch: fetch each algo's df, return a unified OHLC df.

        Each active algo's full df (OHLC + its REQUIRED_COLUMNS) is stored
        in ``self._mixed_dfs`` so ``_blend_signals`` can apply the algo on
        its own column slice. The returned df is a UNION of all algos' dates
        with OHLC coalesced (algos that lack a date contribute NaN columns
        -> their signal on that date is 0 in the blend).
        """
        self._mixed_dfs = {}
        ohlc_frames: List[pd.DataFrame] = []
        ohlc_cols = ["open_price", "high_price", "low_price", "close_price"]
        key_cols = ["sec_type", "code", "date"]

        for algo_name, weight in self._active.items():
            algo = self._algos[algo_name]
            df = await algo.fetch_signal_data(conn, sec_type, codes)
            if df.empty:
                continue
            self._mixed_dfs[algo_name] = df
            # Extract OHLC + keys for the merge frame.
            keep = [c for c in key_cols if c in df.columns] + ohlc_cols
            ohlc_frames.append(df[keep].copy())

        if not ohlc_frames:
            return pd.DataFrame()

        # Outer-merge all OHLC frames on (sec_type, code, date). All algos
        # read the same basic_stats table for OHLC, so values match on
        # overlapping dates; the outer join unions the date sets.
        merged = ohlc_frames[0]
        for f in ohlc_frames[1:]:
            merged = merged.merge(
                f, on=[c for c in key_cols if c in merged.columns],
                how="outer", suffixes=("", "_dup"),
            )
            # Coalesce OHLC columns (prefer non-NaN from either side).
            for col in ohlc_cols:
                dup = f"{col}_dup"
                if dup in merged.columns:
                    merged[col] = merged[col].fillna(merged[dup])
                    merged = merged.drop(columns=[dup])
        return merged.sort_values(["code", "date"]).reset_index(drop=True)

    # ------------------------------------------------------------------
    # Apply + consolidate — weight the per-algo signal_confidence
    # ------------------------------------------------------------------
    def apply_signals(self, df: pd.DataFrame, params: dict) -> pd.DataFrame:
        """Apply the selected algo(s) + consolidate into ``signal_confidence``.

        Binary: delegate to the selected algo's ``apply_signals`` (the algo
        adds ``signal_confidence`` + ``signal_value`` itself).

        Mixed: apply each algo on its OWN stored df (from ``_fetch_mixed``),
        extract per-algo ``signal_confidence``, merge into the unified df,
        and weight-blend into one signed value clipped to [-100, 100]. BUY
        takes priority when both sides fire on the same bar (net positive ->
        BUY, mirroring the binary consolidate rule). Each algo uses its
        own DEFAULT_PARAMS (merged with any matching keys from ``params``).
        """
        if self.is_binary:
            return self.primary_algo.apply_signals(df, params)
        return self._blend_signals(df, params)

    def _blend_signals(self, df: pd.DataFrame, params: dict) -> pd.DataFrame:
        """Weight-blend per-algo signal_confidence (mixed mode).

        Each algo's ``signal_confidence`` is computed on its own stored df
        (from ``_fetch_mixed``) using its DEFAULT_PARAMS merged with any
        matching keys from ``params`` (so trading-layer overrides propagate
        if the algo reads them). The per-algo signals are merged on
        (code, date) into the unified OHLC df, then blended by weight into
        ``signal_confidence`` (clipped to [-100, 100]).
        """
        if df.empty or not self._mixed_dfs:
            # No per-algo data (all algos returned empty) — emit zero signal.
            df["signal_confidence"] = 0.0
            df["signal_value"] = 0.0
            return df

        # Build per-algo signal frames: (code, date, sig_<algo>).
        sig_frames: List[pd.DataFrame] = []
        algo_names: List[str] = []
        for algo_name, df_algo in self._mixed_dfs.items():
            algo = self._algos[algo_name]
            # Merge the algo's DEFAULT_PARAMS with any matching keys from the
            # passed params (so trading-layer overrides propagate if the
            # algo reads them — most algos don't, but this is safe).
            param_keys = getattr(algo, "ALGO_PARAM_KEYS", ())
            overrides = {k: v for k, v in params.items() if k in param_keys}
            algo_params = algo.build_params(overrides)
            df_algo = algo.apply_signals(df_algo.copy(), algo_params)
            col = f"sig_{algo_name}"
            sig = df_algo[["code", "date", "signal_confidence"]].rename(
                columns={"signal_confidence": col}
            )
            sig_frames.append(sig)
            algo_names.append(algo_name)

        if not sig_frames:
            df["signal_confidence"] = 0.0
            df["signal_value"] = 0.0
            return df

        # Merge per-algo signals into the unified OHLC df on (code, date).
        for sig in sig_frames:
            df = df.merge(sig, on=["code", "date"], how="left")

        # Vectorized weight-blend. Missing (NaN) per-algo signals -> 0.
        blended = pd.Series(0.0, index=df.index)
        for name in algo_names:
            w = self._norm_weights.get(name, 0.0)
            if w == 0:
                continue
            blended = blended + w * df[f"sig_{name}"].fillna(0.0)
        blended = blended.clip(-100.0, 100.0)

        df["signal_confidence"] = blended
        # signal_value: auxiliary magnitude (abs of the blended signal).
        df["signal_value"] = blended.abs()

        # KEEP the per-algo signal columns (sig_<algo>) on the df so
        # build_signal_reason can read them from the row and encode a
        # per-algo contribution breakdown into signal_reason. The engine
        # ignores these extra columns (it only reads signal_confidence +
        # signal_value). Renamed to algo_conf_<algo> for clarity in the df.
        for name in algo_names:
            df = df.rename(columns={f"sig_{name}": f"algo_conf_{name}"})
        return df

    # ------------------------------------------------------------------
    # Reason — human-readable text for trade_decision.signal_reason
    # ------------------------------------------------------------------
    def build_signal_reason(
        self, row, side: str, params: dict, confidence: float,
    ) -> str:
        """Build the ``signal_reason`` text.

        Binary: delegate to the selected algo's reason builder (algo-specific
        phrasing — e.g. MA5/MA60 cross vs BB z-score vs MACD crossover).

        Mixed: build a per-algo contribution breakdown encoded as a JSON
        prefix (``__MIX__<json>__``) followed by a human-readable summary.
        The JSON carries each algo's weight (w), raw signal_confidence (c),
        and net contribution (n = w × c), plus the blended total and whether
        netting occurred (some algos BUY, some SELL on this bar). The UI
        parses the prefix to render a structured tooltip; the human-readable
        part shows in the cell.
        """
        if self.is_binary:
            return self.primary_algo.build_signal_reason(row, side, params, confidence)

        # Read per-algo signal_confidence from the row (columns algo_conf_<algo>
        # kept by _blend_signals). Compute per-algo contribution = w × c.
        contributions = []
        for algo_name in sorted(self._active):
            weight = self._norm_weights.get(algo_name, 0.0)
            col = f"algo_conf_{algo_name}"
            raw = row.get(col) if hasattr(row, "get") else None
            if raw is None and isinstance(row, dict):
                raw = row.get(col)
            try:
                raw_c = float(raw) if raw is not None else 0.0
            except (TypeError, ValueError):
                raw_c = 0.0
            if raw_c != raw_c:  # NaN guard
                raw_c = 0.0
            contrib = weight * raw_c
            contributions.append({
                "a": _short_name(algo_name),   # abbreviation (bb/macd/ma)
                "algo": algo_name,              # full name
                "w": round(weight, 4),
                "c": round(raw_c, 2),
                "n": round(contrib, 2),
            })

        blended = round(sum(c["n"] for c in contributions), 2)
        # Netting: some algos BUY (c>0) and some SELL (c<0) on this bar.
        has_buy = any(c["c"] > 0 for c in contributions)
        has_sell = any(c["c"] < 0 for c in contributions)
        netted = has_buy and has_sell

        mix_data = {
            "algos": contributions,
            "blended": blended,
            "netted": netted,
            "side": side,
        }
        json_prefix = f"__MIX__{json.dumps(mix_data, ensure_ascii=False)}__"

        # Human-readable summary (shown in the cell, truncated with ellipsis).
        parts = []
        for c in contributions:
            parts.append(
                f"{c['a']} w={c['w']:.0%} c={c['c']:+.1f}→{c['n']:+.1f}"
            )
        label = f"MIXED {side}" + (" (netted)" if netted else "")
        human = f"{label}: {', '.join(parts)} | Σ={blended:+.1f}"
        return f"{json_prefix}{human}"

    # ------------------------------------------------------------------
    # Backtest — apply consolidated signals + run the execution engine
    # ------------------------------------------------------------------
    def run_backtest(
        self, df: pd.DataFrame, params: dict, sec_type: str, codes: list,
    ) -> List[Dict[str, Any]]:
        """Apply the consolidated signal, then run the signal-agnostic engine.

        The engine reads only ``signal_confidence`` (+ ``signal_value``) and
        the trading-layer keys in ``params``; it never reaches into
        algo-specific columns. ``signal_reason_fn`` is the collector's
        :meth:`build_signal_reason` so the reason text matches whatever
        algo(s) produced the signal.
        """
        if not df.empty:
            df = self.apply_signals(df, params)
        return _run_backtest_engine(
            df, params, sec_type, codes,
            signal_reason_fn=self.build_signal_reason,
        )

    def compute_daily_rows(self, code_df, decisions, anchor_price):
        """Delegate to the signal-agnostic engine helper."""
        return _compute_daily_rows(code_df, decisions, anchor_price)


__all__ = ["AlgoSignalCollector"]
