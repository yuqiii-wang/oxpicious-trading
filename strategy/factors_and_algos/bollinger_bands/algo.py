"""Bollinger Bands signal algorithm (pure signal math + DB fetch + reason).

Bollinger Band (MA20+MA60) mean-reversion algo. Migrated from the former
multi-file layout (config.py / algo.py / reason.py / fetch.py / adapter.py)
into a single module with a class inheriting from :class:`AlgoBase`.

Signal summary
--------------
  z_N = (close - MA_N) / std_N   (std-devs above MA_N)
    z_N >  k  → overbought → SELL  (confidence ∝ (z_N - k) / k)
    z_N < -k  → oversold   → BUY   (confidence ∝ (-k - z_N) / k)

  MA20 and MA60 bands blended by weight, re-normalized over available bands
  so a band still warming up (std NULL) doesn't dilute the other. Output is
  a single signed ``signal_confidence`` ∈ [-100, 100] consumed by the engine
  in ``strategy._trading``.

The algo reads precomputed MA / σ columns from
``analysis.mov_ave_spreads_detail`` (joined with basic_stats for OHLC fills).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from strategy.factors_and_algos._algo import AlgoBase, basic_stats_table, rows_to_df


def _z_score(close, price_vs_ma, std):
    """z = (close - MA) / std. MA derived from price_vs_ma = (close-MA)/MA."""
    pv = price_vs_ma
    denom = (1.0 + pv).where((1.0 + pv) != 0, np.nan)
    excess = close * pv / denom
    return excess / std.where(std > 0, np.nan)


class BollingerBandsAlgo(AlgoBase):
    """Bollinger Band mean-reversion algo (MA20+MA60, blended z-score)."""

    ALGO_NAME = "bollinger_bands"
    POSITION_AWARE = False

    DEFAULT_PARAMS: dict = {
        "band_width": 2.0,       # k: MA ± k·σ (classic Bollinger width)
        "ma_short": 20,          # maps to price_vs_ma20 / std_20days columns
        "ma_long": 60,           # maps to price_vs_ma60 / std_60days columns
        "weight_short": 0.5,     # blend weight for the MA20 band
        "weight_long": 0.5,      # blend weight for the MA60 band
    }

    REQUIRED_COLUMNS: tuple = (
        "price_vs_ma20", "price_vs_ma60",
        "std_20days", "std_60days",
    )

    ALGO_PARAM_KEYS: tuple = (
        "band_width",
        "weight_short",
        "weight_long",
        # ma_short / ma_long are metadata (column-name mapping), not read at
        # runtime by the current vectorized implementation, but declared so a
        # future generic impl can use them.
        "ma_short",
        "ma_long",
    )

    # ------------------------------------------------------------------
    # Fetch
    # ------------------------------------------------------------------
    async def fetch_signal_data(self, conn, sec_type: str, codes: list) -> pd.DataFrame:
        """Fetch OHLC + the BB REQUIRED_COLUMNS for the given codes.

        Joins ``analysis.mov_ave_spreads_detail`` (MA / σ columns) with the
        per-sec_type ``basic_stats`` table (OHLC for fills).
        """
        if not codes:
            return pd.DataFrame()
        basic_stats = basic_stats_table(sec_type)
        cols_sql = ",\n        ".join(f"d.{c}" for c in self.REQUIRED_COLUMNS)
        sql = f"""
            SELECT
                d.sec_type, d.code, d.date,
                {cols_sql},
                b.open AS open_price, b.high AS high_price,
                b.low AS low_price, b.close AS close_price
            FROM analysis.mov_ave_spreads_detail d
            LEFT JOIN {basic_stats} b
                ON b.code = d.code AND b.date = d.date
            WHERE d.sec_type = $1 AND d.code = ANY($2::text[])
            ORDER BY d.code, d.date ASC
        """
        rows = await conn.fetch(sql, sec_type, sorted(codes))
        numeric_cols = list(self.REQUIRED_COLUMNS) + [
            "open_price", "high_price", "low_price", "close_price",
        ]
        return rows_to_df(rows, numeric_cols)

    # ------------------------------------------------------------------
    # Signal math
    # ------------------------------------------------------------------
    def apply_signals(self, df: pd.DataFrame, params: dict) -> pd.DataFrame:
        """Add signal_confidence + signal_value columns for the engine.

        ``params`` reads the BB-specific keys declared in ``ALGO_PARAM_KEYS``:
        ``band_width``, ``weight_short``, ``weight_long``. Trading-layer keys
        in ``params`` (min_holding_period, buy_notional, ...) are ignored
        here — they flow through to the engine untouched.
        """
        if df.empty:
            df = df.copy()
            df["signal_confidence"] = pd.Series(dtype=float)
            df["signal_value"] = pd.Series(dtype=float)
            return df

        k = float(params["band_width"])
        w20 = float(params["weight_short"])
        w60 = float(params["weight_long"])

        close = df["close_price"]
        z20 = _z_score(close, df["price_vs_ma20"], df["std_20days"])
        z60 = _z_score(close, df["price_vs_ma60"], df["std_60days"])

        sell20 = ((z20 - k) / k).clip(0.0, 1.0) * 100.0
        sell60 = ((z60 - k) / k).clip(0.0, 1.0) * 100.0
        buy20 = ((-k - z20) / k).clip(0.0, 1.0) * 100.0
        buy60 = ((-k - z60) / k).clip(0.0, 1.0) * 100.0

        valid20 = z20.notna()
        valid60 = z60.notna()

        def _blend(c20, c60):
            wsum = w20 * valid20 + w60 * valid60
            denom = wsum.where(wsum > 0, 1.0)
            return ((w20 * c20.fillna(0.0) + w60 * c60.fillna(0.0)) / denom
                    ).where(valid20 | valid60, 0.0)

        buy_conf = _blend(buy20, buy60)
        sell_conf = _blend(sell20, sell60)
        signal_value = _blend(z20, z60)

        # Floor fired signals at 1.0 so qty > 0 after rounding.
        buy_conf = buy_conf.where(buy_conf <= 0, buy_conf.clip(lower=1.0))
        sell_conf = sell_conf.where(sell_conf <= 0, sell_conf.clip(lower=1.0))

        signal_confidence = (buy_conf - sell_conf).fillna(0.0)

        df = df.copy()
        df["signal_confidence"] = signal_confidence
        df["signal_value"] = signal_value
        df["z_score_20"] = z20
        df["z_score_60"] = z60
        return df

    # ------------------------------------------------------------------
    # Reason
    # ------------------------------------------------------------------
    def build_signal_reason(self, row, side: str, params: dict, confidence: float) -> str:
        """Report which band(s) actually breached k (not the blended z)."""
        k = params["band_width"]
        z20 = row.get("z_score_20")
        z60 = row.get("z_score_60")
        z20s = f"{z20:.2f}" if pd.notna(z20) else "NA"
        z60s = f"{z60:.2f}" if pd.notna(z60) else "NA"

        if side == "BUY":
            fired = []
            if pd.notna(z20) and z20 < -k:
                fired.append(f"BB20 z={z20s}<-k")
            if pd.notna(z60) and z60 < -k:
                fired.append(f"BB60 z={z60s}<-k")
            return (f"BB BUY: {', '.join(fired) or 'blended'} (oversold, k={k}) "
                    f"| confidence={confidence:.1f}")
        fired = []
        if pd.notna(z20) and z20 > k:
            fired.append(f"BB20 z={z20s}>+k")
        if pd.notna(z60) and z60 > k:
            fired.append(f"BB60 z={z60s}>+k")
        return (f"BB SELL: {', '.join(fired) or 'blended'} (overbought, k={k}) "
                f"| confidence={confidence:.1f}")


# Singleton instance — the registry (strategy.factors_and_algos.get_algo)
# returns this via the algo package's __init__.py ``ALGO`` attribute.
ALGO = BollingerBandsAlgo()


__all__ = ["BollingerBandsAlgo", "ALGO", "_z_score"]
