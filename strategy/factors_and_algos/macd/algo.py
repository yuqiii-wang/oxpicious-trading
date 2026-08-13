"""MACD signal algorithm (pure signal math + DB fetch + reason).

MACD (12/26/9) crossover mean-reversion algo. Migrated from the former
multi-file layout (config.py / algo.py / reason.py / fetch.py / adapter.py)
into a single module with a class inheriting from :class:`AlgoBase`.

Signal summary
--------------
MACD line    = EMA_short(close) - EMA_long(close)        (default 12 / 26)
Signal line  = EMA_signal(MACD line)                     (default 9)
Histogram    = MACD line - Signal line

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
securities (a 5000-point index vs a 5-yuan ETF). EMAs are computed inside
the algo from ``close_price`` — no precomputed analysis columns are needed,
so REQUIRED_COLUMNS is empty (the fetch layer only has to supply OHLC).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from strategy.factors_and_algos._algo import AlgoBase, basic_stats_table, rows_to_df


class MacdAlgo(AlgoBase):
    """MACD (12/26/9) crossover mean-reversion algo."""

    ALGO_NAME = "macd"
    POSITION_AWARE = False

    DEFAULT_PARAMS: dict = {
        "ema_short": 12,
        "ema_long": 26,
        "ema_signal": 9,
        "hist_threshold": 0.005,   # |histogram|/close → full hist_strength (50bps)
        "zero_threshold": 0.02,    # |MACD|/close      → full zero_strength (2%)
        "weight_hist": 0.6,
        "weight_zero": 0.4,
    }

    # MACD computes EMAs from close_price internally, so it needs NO precomputed
    # analysis columns — only OHLC (supplied by the fetch layer for fills).
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
        """Fetch OHLC only (MACD needs no precomputed analysis columns).

        Reads straight from ``stats.<sec_type>_basic_stats``. The basic_stats
        tables are per-sec_type (no sec_type column), so $1 is injected as a
        literal sec_type column to keep the df schema consistent with the
        detail-join algos.
        """
        if not codes:
            return pd.DataFrame()
        basic_stats = basic_stats_table(sec_type)
        sql = f"""
            SELECT
                $1::text AS sec_type, b.code, b.date,
                b.open AS open_price, b.high AS high_price,
                b.low AS low_price, b.close AS close_price
            FROM {basic_stats} b
            WHERE b.code = ANY($2::text[])
            ORDER BY b.code, b.date ASC
        """
        rows = await conn.fetch(sql, sec_type, sorted(codes))
        numeric_cols = ["open_price", "high_price", "low_price", "close_price"]
        return rows_to_df(rows, numeric_cols)

    # ------------------------------------------------------------------
    # Signal math
    # ------------------------------------------------------------------
    def apply_signals(self, df: pd.DataFrame, params: dict) -> pd.DataFrame:
        """Add signal_confidence + signal_value columns for the engine."""
        if df.empty:
            df = df.copy()
            df["signal_confidence"] = pd.Series(dtype=float)
            df["signal_value"] = pd.Series(dtype=float)
            return df

        ema_short = int(params["ema_short"])
        ema_long = int(params["ema_long"])
        ema_signal = int(params["ema_signal"])
        hist_thr = float(params["hist_threshold"])
        zero_thr = float(params["zero_threshold"])
        w_hist = float(params["weight_hist"])
        w_zero = float(params["weight_zero"])

        close = df["close_price"]
        # EMA via standard MACD convention (adjust=False → recursive, seeds with first value).
        ema_s = close.ewm(span=ema_short, adjust=False).mean()
        ema_l = close.ewm(span=ema_long, adjust=False).mean()
        macd_line = ema_s - ema_l
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
        ema_short = params.get("ema_short", 12)
        ema_long = params.get("ema_long", 26)
        ema_signal = params.get("ema_signal", 9)

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
