"""MA-spread signal algorithm (pure signal math + DB fetch + reason).

MA5/MA{long} cross trend-following algo. Migrated from the former multi-file
layout (config.py / algo.py / reason.py / fetch.py / adapter.py) into a
single module with a class inheriting from :class:`AlgoBase`.

Signal summary
--------------
  Entry (BUY):  MA5/MA{long} golden cross confirmed by price>MA{long},
                RSI not overbought, and rising turnover.
  Exit  (SELL): rising-edge of ANY of: death cross, RSI overbought,
                stop-loss below MA{long}.

  Consolidated output (produced by apply_signals):
      signal_confidence ∈ [-100, 100]
        > 0  → BUY signal,  value = buy confidence
        < 0  → SELL signal, value = -sell confidence (rising-edge filtered)
        = 0  → no signal

Cross detection (no look-ahead — uses row T + a 1-day lag):
  ma5_vs_ma60_prev = ma5_vs_ma60 shifted by 1 trading day per code.
  golden_cross = (prev <= 0) & (curr > 0)
  death_cross  = (prev >= 0) & (curr < 0)

Confidence (0-100):
  buy_confidence  — weighted blend of MA-spread magnitude, price_vs_ma_long
                    distance, RSI room, turnover slope.
  sell_confidence — weighted blend of fired exit triggers' strengths; only
                    fired triggers contribute (confluence raises confidence).

exit_signal_rising — rising-edge of exit_signal per code. The consolidated
                    signal_confidence is < 0 only on this rising edge, so
                    partial closes don't cascade on every bar an exit
                    condition persists.

DB fetch joins analysis.mov_ave_spreads_detail + analysis.mov_ave_rsi +
stats.<sec_type>_basic_stats. NOTE: peaks_and_floors_date is deliberately
NOT selected — it carries look-ahead bias (belt detection extends into
the future).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from strategy.factors_and_algos._algo import AlgoBase, basic_stats_table, rows_to_df


def _clip01_series(s: pd.Series) -> pd.Series:
    """Clip a pandas Series to [0, 1]; NaN → 0 (vectorized)."""
    return s.fillna(0.0).clip(lower=0.0, upper=1.0)


def _add_cross_columns(df: pd.DataFrame, ma_long: int = 60) -> pd.DataFrame:
    """Add per-code lagged spread + cross flags.

    Adds columns:
      ma{long}_vs_ma5_prev  — previous day's ma5_vs_ma{long} (NaN on first row)
      golden_cross          — True when spread crosses from <=0 to >0
      death_cross           — True when spread crosses from >=0 to <0
    """
    spread_col = f"ma5_vs_ma{ma_long}"
    prev_col = f"{spread_col}_prev"
    df = df.sort_values(["code", "date"]).reset_index(drop=True)
    df[prev_col] = df.groupby("code")[spread_col].shift(1)
    curr = df[spread_col]
    prev = df[prev_col]
    df["golden_cross"] = (prev <= 0) & (curr > 0)
    df["death_cross"] = (prev >= 0) & (curr < 0)
    # A cross is only valid when both curr and prev are non-null.
    df.loc[curr.isna() | prev.isna(), ["golden_cross", "death_cross"]] = False
    return df


def _mark_entry_signals(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Add ``entry_signal`` (bool) column: True on BUY signal dates.

    Entry requires: golden cross + price above MA{long} + RSI not overbought +
    rising turnover. All conditions evaluated on the same row (date T).
    """
    ma_long = params["ma_long"]
    spread_col = f"price_vs_ma{ma_long}"
    df["entry_signal"] = (
        df["golden_cross"]
        & (df[spread_col] > params["entry_price_vs_ma_long_min"])
        & (df["rsi_14days"] < params["entry_rsi_max"])
        & (df["trading_amt_ma5_slope"] > 0)
    )
    # Null inputs cannot produce a signal.
    df.loc[df[spread_col].isna() | df["rsi_14days"].isna()
           | df["trading_amt_ma5_slope"].isna(), "entry_signal"] = False
    return df


def _mark_exit_signals(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Add ``exit_signal`` (bool) + ``exit_signal_rising`` columns.

    Exit triggers on ANY of: death cross, RSI overbought, stop-loss below
    MA{long}. The min_holding_period gate is enforced in _trading.engine
    (it depends on the last BUY fill date, which is portfolio state, not a
    row attribute).

    ``exit_signal_rising`` is the rising-edge of exit_signal per code (True
    only on the first bar of an exit episode), so partial closes in the
    engine don't cascade on every bar the exit condition persists.
    """
    ma_long = params["ma_long"]
    spread_col = f"price_vs_ma{ma_long}"
    df["exit_signal"] = (
        df["death_cross"]
        | (df["rsi_14days"] > params["exit_rsi_max"])
        | (df[spread_col] < params["stop_loss_vs_ma_long"])
    )
    df.loc[df[spread_col].isna() | df["rsi_14days"].isna(), "exit_signal"] = False
    # Rising edge per code: True only on the first bar of an exit episode.
    # Use int8 (0/1) for the shift+fillna chain — shift() on bool returns
    # object dtype (with NaN), and fillna on object triggers a FutureWarning
    # about silent downcasting. int8 round-trips cleanly through shift.
    exit_int = df["exit_signal"].fillna(False).astype("int8")
    prev_exit = (
        exit_int.groupby(df["code"], sort=False).shift(1)
        .fillna(0).astype("int8")
    )
    df["exit_signal_rising"] = (exit_int & ~prev_exit).astype(bool)
    return df


def _add_confidence_columns(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Add ``buy_confidence`` and ``sell_confidence`` (0-100) per row.

    Each sub-score is normalized to [0, 1] via fixed thresholds
    (params["confidence_thresholds"]) and then blended via
    params["confidence_weights"]. The result is scaled to [0, 100] and
    clipped to that range.

    BUY (all four sub-conditions must already be true for entry_signal;
    the blend reflects how STRONG each is):
      - ma_spread_strength   = |ma5_vs_ma60| / spread_threshold
      - price_above_strength = price_vs_ma60 / price_above_threshold  (>0 only)
      - rsi_room_strength    = (entry_rsi_max - rsi_14days) / rsi_room_threshold
      - turnover_strength    = trading_amt_ma5_slope / turnover_slope_threshold

    SELL (only fired triggers contribute; confluence raises confidence):
      - death_cross_strength = |ma5_vs_ma60| / spread_threshold  (if death_cross)
      - rsi_excess_strength  = (rsi_14days - exit_rsi_max) / rsi_excess_threshold
                               (if rsi > exit_rsi_max)
      - stop_loss_strength   = (stop_loss_vs_ma_long - price_vs_ma60) /
                              stop_loss_depth_threshold  (if price < stop_loss)
    """
    ma_long = params["ma_long"]
    ma_spread_col = f"ma5_vs_ma{ma_long}"
    price_vs_col = f"price_vs_ma{ma_long}"
    th = params["confidence_thresholds"]
    w = params["confidence_weights"]

    # ---- BUY sub-scores (vectorized) -------------------------------------
    spread_strength = _clip01_series(df[ma_spread_col].abs() / th["ma_spread"])
    price_above_raw = df[price_vs_col].where(df[price_vs_col] > 0, 0.0)
    price_above_strength = _clip01_series(price_above_raw / th["price_above_ma_long"])
    rsi_room_raw = (params["entry_rsi_max"] - df["rsi_14days"]).clip(lower=0)
    rsi_room_strength = _clip01_series(rsi_room_raw / th["rsi_room"])
    turnover_raw = df["trading_amt_ma5_slope"].where(
        df["trading_amt_ma5_slope"] > 0, 0.0)
    turnover_strength = _clip01_series(turnover_raw / th["turnover_slope"])

    bw = w["buy"]
    buy_blend = (
        bw["ma_spread"]      * spread_strength
        + bw["price_above"]  * price_above_strength
        + bw["rsi_room"]     * rsi_room_strength
        + bw["turnover"]     * turnover_strength
    )
    # Normalize by sum of weights so the result is in [0, 1] regardless of
    # how the weights are configured (defensive: avoid div-by-zero).
    buy_weight_sum = sum(bw.values()) or 1.0
    # Floor at 1.0 when the signal fires so qty > 0 holds after _round2 in
    # the engine (CHECK constraint qty > 0). Rows where entry_signal is
    # False are forced to 0.0 below.
    df["buy_confidence"] = (buy_blend / buy_weight_sum * 100.0).clip(
        lower=1.0, upper=100.0)
    # BUY signal must have fired for confidence to matter; otherwise 0.
    df.loc[~df["entry_signal"], "buy_confidence"] = 0.0

    # ---- SELL sub-scores (vectorized; only fired triggers contribute) ---
    dc_strength = _clip01_series(df[ma_spread_col].abs() / th["ma_spread"])
    rsi_excess_raw = (df["rsi_14days"] - params["exit_rsi_max"]).clip(lower=0)
    rsi_excess_strength = _clip01_series(rsi_excess_raw / th["rsi_excess"])
    sl_depth_raw = (params["stop_loss_vs_ma_long"] - df[price_vs_col]).clip(lower=0)
    sl_depth_strength = _clip01_series(sl_depth_raw / th["stop_loss_depth"])

    sw = w["sell"]
    sell_blend = (
        sw["death_cross"] * dc_strength * df["death_cross"].astype(float)
        + sw["rsi_excess"] * rsi_excess_strength
            * (df["rsi_14days"] > params["exit_rsi_max"]).astype(float)
        + sw["stop_loss"] * sl_depth_strength
            * (df[price_vs_col] < params["stop_loss_vs_ma_long"]).astype(float)
    )
    # Re-normalize by the sum of weights of FIRED triggers (so a single strong
    # trigger can reach 100, not be capped at its own weight).
    fired_weight = (
        sw["death_cross"] * df["death_cross"].astype(float)
        + sw["rsi_excess"] * (df["rsi_14days"] > params["exit_rsi_max"]).astype(float)
        + sw["stop_loss"] * (df[price_vs_col] < params["stop_loss_vs_ma_long"]).astype(float)
    )
    fired_weight = fired_weight.where(fired_weight > 0, 1.0)
    # Floor at 1.0 when the signal fires so qty > 0 holds after _round2 in
    # the engine (CHECK constraint qty > 0). Rows where exit_signal is
    # False are forced to 0.0 below.
    df["sell_confidence"] = (sell_blend / fired_weight * 100.0).clip(
        lower=1.0, upper=100.0)
    df.loc[~df["exit_signal"], "sell_confidence"] = 0.0
    return df


def _consolidate_signal(df: pd.DataFrame) -> pd.DataFrame:
    """Consolidate entry/exit + buy/sell confidence into a single signed
    ``signal_confidence`` column — the singular b/s confidence value
    consumed by strategy._trading.

        > 0  → BUY signal,  value = +buy_confidence   (0-100]
        < 0  → SELL signal, value = -sell_confidence   (rising-edge filtered)
        = 0  → no signal

    BUY priority: when both entry_signal and exit_signal_rising are True
    on the same bar, the BUY wins (matches the engine's entry-first
    processing order). SELL contributes only where exit_signal_rising is
    True AND entry_signal is False — so the rising-edge semantics are
    preserved exactly (no cascading SELLs across an exit episode).
    """
    buy_mask = df["entry_signal"].fillna(False).to_numpy()
    sell_mask = df["exit_signal_rising"].fillna(False).to_numpy()
    buy_conf = df["buy_confidence"].fillna(0.0).to_numpy(dtype=float)
    sell_conf = df["sell_confidence"].fillna(0.0).to_numpy(dtype=float)

    conf = np.zeros(len(df), dtype=float)
    conf[buy_mask] = buy_conf[buy_mask]
    sell_only = sell_mask & ~buy_mask
    conf[sell_only] = -sell_conf[sell_only]
    df["signal_confidence"] = conf
    return df


class MaSpreadAlgo(AlgoBase):
    """MA5/MA{long} cross trend-following algo with RSI / turnover confirmation."""

    ALGO_NAME = "ma_spread"
    POSITION_AWARE = False

    DEFAULT_PARAMS: dict = {
        "ma_short": 5,
        "ma_long": 60,

        # Entry confirmation thresholds.
        "entry_rsi_max": 70.0,              # don't BUY if RSI(14) >= this
        "entry_price_vs_ma_long_min": 0.0,  # price must be above MA{long}

        # Exit thresholds (evaluated only after min_holding_period).
        "exit_rsi_max": 75.0,               # SELL if RSI(14) > this (take-profit)
        "stop_loss_vs_ma_long": -0.05,      # SELL if price_vs_ma60 < this (-5%)

        # Confidence scoring (0-100; stored as trade_decision.qty).
        "confidence_thresholds": {
            # BUY sub-scores
            "ma_spread":           0.05,   # |ma5_vs_ma60| = 5% -> full strength
            "price_above_ma_long": 0.10,   # price_vs_ma60 = 10% above -> full
            "rsi_room":            30.0,   # 30 RSI points of room -> full
            "turnover_slope":      0.10,   # trading_amt_ma5_slope = 0.10 -> full
            # SELL sub-scores
            "rsi_excess":          10.0,   # 10 RSI points over exit_rsi_max -> full
            "stop_loss_depth":     0.05,   # 5% below stop_loss_vs_ma_long -> full
        },
        "confidence_weights": {
            "buy": {
                "ma_spread":     0.35,   # cross strength is the dominant signal
                "price_above":   0.25,   # trend confirmation
                "rsi_room":      0.25,   # momentum headroom
                "turnover":      0.15,   # liquidity buildup (weakest weight)
            },
            "sell": {
                "death_cross":   0.40,   # primary exit signal
                "rsi_excess":    0.30,   # take-profit strength
                "stop_loss":     0.30,   # stop-loss depth
            },
        },
    }

    # Data columns the algo reads, split by source table so fetch can apply
    # the correct alias prefix (d. for mov_ave_spreads_detail vs r. for
    # mov_ave_rsi). These column lists ARE the "what data to read" declaration:
    # they encode the MA trading requirements (MA gaps, slopes, sigma,
    # turnover-MAs, RSI).
    DETAIL_SIGNAL_COLUMNS: tuple = (
        "price_vs_ma5", "price_vs_ma20", "price_vs_ma60", "price_vs_ma120",
        "price_vs_ma255",
        "ma5_vs_ma20", "ma5_vs_ma60", "ma5_vs_ma120", "ma5_vs_ma255",
        "ma5_slope", "ma60_slope",
        "std_20days", "std_60days",
        "trading_amt_ma5", "trading_amt_ma60",
        "trading_amt_ma5_slope", "trading_amt_ma60_slope",
        "trading_amt_market_share_vs_ma20",
    )
    RSI_SIGNAL_COLUMNS: tuple = (
        "rsi_6days", "rsi_10days", "rsi_14days", "rsi_20days",
        "gap_2days", "gap_3days",
        "date_of_last_extreme", "days_since_last_extreme", "gap_since_last_extreme",
    )

    # Combined tuple: all signal columns the algo reads from the fetched df.
    REQUIRED_COLUMNS: tuple = DETAIL_SIGNAL_COLUMNS + RSI_SIGNAL_COLUMNS

    ALGO_PARAM_KEYS: tuple = (
        "ma_short", "ma_long",
        "entry_rsi_max", "entry_price_vs_ma_long_min",
        "exit_rsi_max", "stop_loss_vs_ma_long",
        "confidence_thresholds", "confidence_weights",
    )

    # ------------------------------------------------------------------
    # Fetch
    # ------------------------------------------------------------------
    async def fetch_signal_data(self, conn, sec_type: str, codes: list) -> pd.DataFrame:
        """Fetch the per-(code, date) signal + fill-price series.

        Joins:
          - analysis.mov_ave_spreads_detail (d) — MA-gap / slope / sigma / turnover-MA cols
          - analysis.mov_ave_rsi            (r) — Wilder RSI + short-term gaps
          - stats.<sec_type>_basic_stats    (b) — OHLC for fill prices

        The LEFT JOIN on basic_stats means a row with a missing open price
        still survives (fill will be skipped in the backtest if OHLC is NULL
        on the fill date).

        NOTE: peaks_and_floors_date is deliberately NOT selected — it
        carries look-ahead bias (belt detection extends into the future).
        """
        if not codes:
            return pd.DataFrame()
        basic_stats = basic_stats_table(sec_type)
        detail_cols_sql = ",\n    ".join(f"d.{c}" for c in self.DETAIL_SIGNAL_COLUMNS)
        rsi_cols_sql = ",\n    ".join(f"r.{c}" for c in self.RSI_SIGNAL_COLUMNS)
        sql = f"""
            SELECT
                d.sec_type,
                d.code,
                d.date,
                {detail_cols_sql},
                {rsi_cols_sql},
                b.open  AS open_price,
                b.high  AS high_price,
                b.low   AS low_price,
                b.close AS close_price
            FROM analysis.mov_ave_spreads_detail d
            JOIN analysis.mov_ave_rsi r
                ON r.sec_type = d.sec_type
               AND r.code = d.code
               AND r.date = d.date
            LEFT JOIN {basic_stats} b
                ON b.code = d.code
               AND b.date = d.date
            WHERE d.sec_type = $1
              AND d.code = ANY($2::text[])
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
        """Run the MA trading algorithm + consolidate to a singular b/s signal.

        Adds the intermediate cross / entry / exit / confidence columns and
        the two columns consumed by strategy._trading:

          signal_confidence — singular signed b/s confidence ∈ [-100, 100]
                              (>0 BUY, <0 SELL rising-edge, 0 none).
          signal_value      — the MA-spread magnitude (auxiliary context stored
                              on each trade_decision row; the engine reads it
                              but does not use it for the b/s decision).
        """
        ma_long = params["ma_long"]
        df = _add_cross_columns(df, ma_long=ma_long)
        df = _mark_entry_signals(df, params)
        df = _mark_exit_signals(df, params)
        df = _add_confidence_columns(df, params)
        df = _consolidate_signal(df)
        # signal_value = the MA-spread magnitude carried onto each decision row.
        df["signal_value"] = df[f"ma5_vs_ma{ma_long}"]
        return df

    # ------------------------------------------------------------------
    # Reason
    # ------------------------------------------------------------------
    def build_signal_reason(self, row, side: str, params: dict, confidence: float) -> str:
        """Human-readable reason the MA-spread signal fired (signal_reason column).

        BUY  — MA5/MA{long} golden cross with the four confirmation conditions
               (price vs MA, RSI room, turnover slope) and the resulting confidence.
        SELL — whichever exit triggers fired (death cross, RSI overbought,
               stop-loss below MA{long}) and the resulting confidence.
        """
        ma_long = params["ma_long"]

        def _fmt(x) -> str:
            return f"{x:.4f}" if pd.notna(x) else "NA"

        if side == "BUY":
            return (f"MA5/MA{ma_long} golden cross (spread "
                    f"{_fmt(row[f'ma5_vs_ma{ma_long}_prev'])} -> "
                    f"{_fmt(row[f'ma5_vs_ma{ma_long}'])}); price_vs_ma{ma_long}="
                    f"{_fmt(row[f'price_vs_ma{ma_long}'])}, RSI14="
                    f"{_fmt(row['rsi_14days'])}, amt_ma5_slope="
                    f"{_fmt(row['trading_amt_ma5_slope'])}) | confidence="
                    f"{confidence:.1f}")
        reasons = []
        if row["death_cross"]:
            reasons.append(f"MA5/MA{ma_long} death cross")
        if pd.notna(row["rsi_14days"]) and row["rsi_14days"] > params["exit_rsi_max"]:
            reasons.append(f"RSI14={_fmt(row['rsi_14days'])}>{params['exit_rsi_max']}")
        if pd.notna(row[f"price_vs_ma{ma_long}"]) and \
                row[f"price_vs_ma{ma_long}"] < params["stop_loss_vs_ma_long"]:
            reasons.append(f"price_vs_ma{ma_long}={_fmt(row[f'price_vs_ma{ma_long}'])}"
                           f"<{params['stop_loss_vs_ma_long']}")
        base = "SELL: " + "; ".join(reasons) if reasons else "SELL signal"
        return f"{base} | confidence={confidence:.1f}"


# Singleton instance — the registry returns this via the algo package's
# __init__.py ``ALGO`` attribute.
ALGO = MaSpreadAlgo()


__all__ = [
    "MaSpreadAlgo",
    "ALGO",
    "_clip01_series",
    "_add_cross_columns",
    "_mark_entry_signals",
    "_mark_exit_signals",
    "_add_confidence_columns",
    "_consolidate_signal",
]
