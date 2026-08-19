"""MACD signal algorithm (pure signal math + DB fetch + reason).

MACD (10/60/6) crossover mean-reversion algo. Migrated from the former
multi-file layout (config.py / algo.py / reason.py / fetch.py / adapter.py)
into a single module with a class inheriting from :class:`AlgoBase`.

Signal summary
--------------
MACD line    = EMA_short(close) - EMA_long(close)        (default 10 / 60)
Signal line  = EMA_signal(MACD line)                     (default 6)
Histogram    = MACD line - Signal line

The short/long EMAs are READ FROM ``stats.<sec_type>_tech_stats`` (precomputed
ema10 / ema60 / ema120 / ema255 columns) instead of being recomputed inline.
The signal line EMA is still computed inline because it is an EMA of the
MACD line (not of close), so no precomputed column exists for it. Defaults
(10/60/6) are chosen so all three spans have a precomputed counterpart in
tech_stats — only the signal-line EMA runs inline.

Trigger is the classic **crossover** (edge), so one BUY per bullish cross
and one SELL per bearish cross (naturally spaced; the engine's
min_holding_period still gates SELLs):
  MACD crosses ABOVE signal → BUY   (bullish momentum)
  MACD crosses BELOW signal → SELL  (bearish momentum)

Confidence blends two normalized strength components:
  hist_strength = |histogram / close| / hist_threshold    (cross vigor)
  zero_strength = (MACD below zero for BUY / above zero for SELL) / zero_threshold
  confidence    = (weight_hist * hist_strength + weight_zero * zero_strength) * 100

Everything is normalized by close_price so the algo is comparable across
securities (a 5000-point index vs a 5-yuan ETF).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from strategy.factors_and_algos._algo import (
    AlgoBase,
    basic_stats_table,
    tech_stats_table,
    rows_to_df,
)


class MacdAlgo(AlgoBase):
    """MACD (10/60/6) crossover mean-reversion algo.

    Short/long EMAs are read precomputed from ``stats.<sec_type>_tech_stats``;
    the signal-line EMA is computed inline from the MACD line.
    """

    ALGO_NAME = "macd"
    POSITION_AWARE = False

    DEFAULT_PARAMS: dict = {
        "ema_short": 10,
        "ema_long": 60,
        "ema_signal": 6,
        "hist_threshold": 0.005,   # |histogram|/close → full hist_strength (50bps)
        "zero_threshold": 0.02,    # |MACD|/close      → full zero_strength (2%)
        "weight_hist": 0.6,
        "weight_zero": 0.4,
    }

    # Tunable model-param search space for the optimization engine
    # (strategy.factors_and_algos._optm_engine). ema_short/ema_long are
    # CATEGORICAL (DISCRETE options only) because they map to precomputed
    # ema{N} columns in stats.<sec_type>_tech_stats — every span in
    # ``_PRECOMPUTED_EMA_SPANS`` is a legal choice; anything else (e.g. the
    # classic MACD 12/26) has NO column and would KeyError. The
    # signal-line EMA is computed inline so its span is a free int.
    # The optimizer repairs invalid combos (ema_short >= ema_long).
    TUNABLE_SPACE: dict = {
        "ema_short": {"type": "categorical", "choices": [6, 10, 20, 60]},
        "ema_long": {"type": "categorical", "choices": [20, 60, 120, 255]},
        "ema_signal": {"type": "int", "low": 3, "high": 15},
        "hist_threshold": {"type": "float", "low": 0.001, "high": 0.02, "log": True},
        "zero_threshold": {"type": "float", "low": 0.005, "high": 0.05, "log": True},
        "weight_hist": {"type": "float", "low": 0.1, "high": 0.9},
        "weight_zero": {"type": "float", "low": 0.1, "high": 0.9},
    }

    # Precomputed EMA spans available in stats.<sec_type>_tech_stats
    # (verified for index/etf/stock). The fetch pulls ALL of them once so
    # per-trial (ema_short, ema_long) combos need no re-fetch during
    # optimization. ALSO the snapping domain for non-discrete param values
    # arriving from stale DB configs / CLI JSON (e.g. classic 12/26 →
    # nearest valid 10/20).
    _PRECOMPUTED_EMA_SPANS: tuple = (6, 10, 20, 60, 120, 255)

    @classmethod
    def _snap_span(cls, value, allow: tuple | None = None) -> int:
        """Snap an EMA span to the NEAREST precomputed span (discrete only).

        Params can arrive from sources that don't respect the discrete
        option set (stale ``strategy.algo_configs`` rows, hand-written
        ``--params-json``, older optimizer studies) — e.g. the classic
        MACD 12/26, for which no ``ema12``/``ema26`` column exists.
        Snapping to the nearest valid span keeps those runs working
        instead of KeyError-ing. Ties round DOWN (more responsive EMA).
        """
        spans = tuple(allow) if allow else cls._PRECOMPUTED_EMA_SPANS
        v = int(value)
        return min(spans, key=lambda s: (abs(s - v), s))

    # Precomputed EMA columns read from stats.<sec_type>_tech_stats.
    # The column names are param-driven (ema{ema_short}, ema{ema_long});
    # the tuple is populated in fetch_signal_data / apply_signals via params.
    REQUIRED_COLUMNS: tuple = ()

    ALGO_PARAM_KEYS: tuple = (
        "ema_short",
        "ema_long",
        "ema_signal",
        "hist_threshold",
        "zero_threshold",
        "weight_hist",
        "weight_zero",
    )

    # ------------------------------------------------------------------
    # Fetch
    # ------------------------------------------------------------------
    async def fetch_signal_data(self, conn, sec_type: str, codes: list) -> pd.DataFrame:
        """Fetch OHLC + ALL precomputed EMA spans from tech_stats.

        Joins:
          - stats.<sec_type>_tech_stats (t) — precomputed EMAs
            (ema6, ema10, ema20, ema60, ema120, ema255 — every span in
            ``_PRECOMPUTED_EMA_SPANS``)
          - stats.<sec_type>_basic_stats (b) — OHLC for fill prices

        Fetching ALL spans once (instead of just the two the current
        params select) lets the optimization engine sample any
        (ema_short, ema_long) combo per trial without re-fetching;
        ``apply_signals`` picks the columns by params.

        The LEFT JOIN on basic_stats means a row with a missing open price
        still survives (fill will be skipped in the backtest if OHLC is
        NULL on the fill date). The JOIN on tech_stats is also LEFT so a
        missing EMA row doesn't drop the OHLCV row — apply_signals will
        NaN-drop it.
        """
        if not codes:
            return pd.DataFrame()
        basic_stats = basic_stats_table(sec_type)
        tech_stats = tech_stats_table(sec_type)
        ema_cols = ",\n                ".join(
            f"t.ema{span} AS ema{span}" for span in self._PRECOMPUTED_EMA_SPANS
        )
        sql = f"""
            SELECT
                $1::text AS sec_type,
                b.code, b.date,
                b.open  AS open_price,
                b.high  AS high_price,
                b.low   AS low_price,
                b.close AS close_price,
                {ema_cols}
            FROM {basic_stats} b
            LEFT JOIN {tech_stats} t
                ON t.code = b.code AND t.date = b.date
            WHERE b.code = ANY($2::text[])
            ORDER BY b.code, b.date ASC
        """
        rows = await conn.fetch(sql, sec_type, sorted(codes))
        numeric_cols = [
            "open_price", "high_price", "low_price", "close_price",
        ] + [f"ema{span}" for span in self._PRECOMPUTED_EMA_SPANS]
        return rows_to_df(rows, numeric_cols)

    # ------------------------------------------------------------------
    # Signal math
    # ------------------------------------------------------------------
    def apply_signals(self, df: pd.DataFrame, params: dict) -> pd.DataFrame:
        """Add signal_confidence + signal_value columns for the engine.

        Reads the precomputed ``ema{span}`` columns (fetched from
        tech_stats) selected by ``params["ema_short"]`` / ``params["ema_long"]``
        to form the MACD line; computes the signal-line EMA inline (it's an
        EMA of the MACD line, not of close). EMA spans are DISCRETE —
        values are snapped to the nearest precomputed span
        (``_PRECOMPUTED_EMA_SPANS``) and the pair repaired so
        short < long, so any param source (DB config, CLI JSON, optimizer)
        is safe.
        """
        if df.empty:
            df = df.copy()
            df["signal_confidence"] = pd.Series(dtype=float)
            df["signal_value"] = pd.Series(dtype=float)
            return df

        ema_signal = int(params["ema_signal"])
        hist_thr = float(params["hist_threshold"])
        zero_thr = float(params["zero_threshold"])
        w_hist = float(params["weight_hist"])
        w_zero = float(params["weight_zero"])

        close = df["close_price"]
        # Precomputed EMAs from tech_stats — the span columns are selected
        # BY PARAMS so the optimizer can sample (ema_short, ema_long)
        # combos per trial against the single all-spans fetch. Spans snap
        # to the discrete precomputed set; if the snapped pair is not
        # strictly short < long, bump ema_long to the next larger span.
        short_span = self._snap_span(params["ema_short"])
        long_span = self._snap_span(params["ema_long"])
        if short_span >= long_span:
            larger = [s for s in self._PRECOMPUTED_EMA_SPANS if s > short_span]
            long_span = larger[0] if larger else short_span
        short_col = f"ema{short_span}"
        long_col = f"ema{long_span}"
        ema_s = df[short_col]
        ema_l = df[long_col]
        macd_line = ema_s - ema_l
        # Signal line = EMA of the MACD line (NOT of close) → cannot reuse
        # any precomputed EMA column; computed inline.
        signal_line = macd_line.ewm(span=ema_signal, adjust=False).mean()
        histogram = macd_line - signal_line

        # Normalize by close so thresholds are comparable across securities.
        macd_pct = macd_line / close.where(close != 0, np.nan)
        hist_pct = histogram / close.where(close != 0, np.nan)

        # Crossover (edge) detection: compare this bar's MACD/signal to the
        # previous bar's. First bar has no prior → no cross (NaN → False).
        prev_macd = macd_line.shift(1)
        prev_signal = signal_line.shift(1)
        bullish_cross = (prev_macd <= prev_signal) & (macd_line > signal_line)
        bearish_cross = (prev_macd >= prev_signal) & (macd_line < signal_line)

        # Strength components, both clipped to [0, 1] before blending.
        hist_strength = (hist_pct.abs() / hist_thr).clip(0.0, 1.0)
        # BUY zero-strength: MACD below zero = deeper value (clip negative→0).
        buy_zero = ((-macd_pct) / zero_thr).clip(0.0, 1.0)
        # SELL zero-strength: MACD above zero = more overbought (clip negative→0).
        sell_zero = (macd_pct / zero_thr).clip(0.0, 1.0)

        buy_conf = (w_hist * hist_strength + w_zero * buy_zero) * 100.0
        sell_conf = (w_hist * hist_strength + w_zero * sell_zero) * 100.0

        # Fire ONLY on the crossover bar (edge). Non-cross bars carry 0.
        buy_conf = buy_conf.where(bullish_cross, 0.0)
        sell_conf = sell_conf.where(bearish_cross, 0.0)

        # Floor fired signals at 1.0 so qty > 0 after rounding (matches BB algo).
        buy_conf = buy_conf.where(buy_conf <= 0, buy_conf.clip(lower=1.0))
        sell_conf = sell_conf.where(sell_conf <= 0, sell_conf.clip(lower=1.0))

        signal_confidence = (buy_conf - sell_conf).fillna(0.0)
        signal_value = hist_pct.fillna(0.0)

        df = df.copy()
        df["signal_confidence"] = signal_confidence
        df["signal_value"] = signal_value
        df["macd_line"] = macd_line
        df["signal_line"] = signal_line
        df["histogram"] = histogram
        return df

    # ------------------------------------------------------------------
    # Reason
    # ------------------------------------------------------------------
    def build_signal_reason(self, row, side: str, params: dict, confidence: float) -> str:
        """Report the MACD crossover that fired this decision."""
        ema_short = params.get("ema_short", 10)
        ema_long = params.get("ema_long", 60)
        ema_signal = params.get("ema_signal", 6)

        macd = row.get("macd_line")
        sig = row.get("signal_line")
        hist = row.get("histogram")

        def _fmt(v) -> str:
            return f"{v:.4f}" if pd.notna(v) else "NA"

        if side == "BUY":
            return (f"MACD BUY: cross↑ MACD={_fmt(macd)}>Sig={_fmt(sig)} "
                    f"(hist={_fmt(hist)}, ema={ema_short}/{ema_long}/{ema_signal}) "
                    f"| confidence={confidence:.1f}")
        return (f"MACD SELL: cross↓ MACD={_fmt(macd)}<Sig={_fmt(sig)} "
                f"(hist={_fmt(hist)}, ema={ema_short}/{ema_long}/{ema_signal}) "
                f"| confidence={confidence:.1f}")


# Singleton instance — the registry returns this via the algo package's
# __init__.py ``ALGO`` attribute.
ALGO = MacdAlgo()


__all__ = ["MacdAlgo", "ALGO"]
