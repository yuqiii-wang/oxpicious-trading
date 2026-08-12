"""MA-spread signal algorithm + consolidation into a singular b/s confidence.

Reads the per-(code, date) DataFrame produced by _signal.fetch, evaluates
the MA trading algorithm (MA5/MA{long} cross detection, entry/exit rules,
confidence scoring), and CONSOLIDATES the result into a single signed
``signal_confidence`` value per row that strategy._trading consumes.

Consolidated output (signal_confidence ∈ [-100, 100]):
  > 0  → BUY signal,  value = buy_confidence  (0-100]
  < 0  → SELL signal, value = -sell_confidence  (rising-edge filtered)
  = 0  → no signal

BUY takes priority over SELL when both fire on the same bar (preserving
the engine's original entry-first ordering).

Cross detection (no look-ahead — uses row T + a 1-day lag):
  ma5_vs_ma60_prev = ma5_vs_ma60 shifted by 1 trading day per code.
  golden_cross = (prev <= 0) & (curr > 0)
  death_cross  = (prev >= 0) & (curr < 0)

Entry (BUY) — ALL must hold:
  golden_cross
  price_vs_ma60 > entry_price_vs_ma_long_min
  rsi_14days < entry_rsi_max
  trading_amt_ma5_slope > 0   (turnover rising)

Exit (SELL) — ANY must hold (checked only when holding + min_holding_period
elapsed; the holding gate is applied in _trading.engine, not here):
  death_cross
  rsi_14days > exit_rsi_max
  price_vs_ma60 < stop_loss_vs_ma_long

Confidence (0-100):
  buy_confidence  — weighted blend of MA-spread magnitude, price_vs_ma_long
                    distance, RSI room, turnover slope.
  sell_confidence — weighted blend of fired exit triggers' strengths; only
                    fired triggers contribute (confluence raises confidence).

exit_signal_rising — rising-edge of exit_signal per code. The consolidated
                    signal_confidence is < 0 only on this rising edge, so
                    partial closes don't cascade on every bar an exit
                    condition persists.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _clip01_series(s: pd.Series) -> pd.Series:
    """Clip a pandas Series to [0, 1]; NaN → 0 (vectorized)."""
    return s.fillna(0.0).clip(lower=0.0, upper=1.0)


def add_cross_columns(df: pd.DataFrame, ma_long: int = 60) -> pd.DataFrame:
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


def mark_entry_signals(df: pd.DataFrame, params: dict) -> pd.DataFrame:
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


def mark_exit_signals(df: pd.DataFrame, params: dict) -> pd.DataFrame:
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


def add_confidence_columns(df: pd.DataFrame, params: dict) -> pd.DataFrame:
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


def consolidate_signal(df: pd.DataFrame) -> pd.DataFrame:
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


def apply_signals(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Run the MA trading algorithm + consolidate to a singular b/s signal.

    Adds the intermediate cross / entry / exit / confidence columns and the
    two columns consumed by strategy._trading:

      signal_confidence — singular signed b/s confidence ∈ [-100, 100]
                          (>0 BUY, <0 SELL rising-edge, 0 none). This is the
                          consolidated value handed off to _trading.
      signal_value      — the MA-spread magnitude (auxiliary context stored
                          on each trade_decision row; the engine reads it
                          but does not use it for the b/s decision).
    """
    ma_long = params["ma_long"]
    df = add_cross_columns(df, ma_long=ma_long)
    df = mark_entry_signals(df, params)
    df = mark_exit_signals(df, params)
    df = add_confidence_columns(df, params)
    df = consolidate_signal(df)
    # signal_value = the MA-spread magnitude carried onto each decision row.
    df["signal_value"] = df[f"ma5_vs_ma{ma_long}"]
    return df
