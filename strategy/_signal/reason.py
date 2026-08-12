"""Human-readable signal-reason builder for the MA-spread signal layer.

Produces the ``signal_reason`` text stored on each trade_decision row.
MA-specific: references MA5/MA{long} columns, RSI, turnover. The engine
calls this callback with the row + the side/confidence it derived from
``signal_confidence``.
"""
from __future__ import annotations

import pandas as pd


def _fmt(x) -> str:
    return f"{x:.4f}" if pd.notna(x) else "NA"


def build_signal_reason(row, side: str, params: dict, confidence: float) -> str:
    """Human-readable reason the MA-spread signal fired (signal_reason column).

    BUY  — MA5/MA{long} golden cross with the four confirmation conditions
           (price vs MA, RSI room, turnover slope) and the resulting confidence.
    SELL — whichever exit triggers fired (death cross, RSI overbought,
           stop-loss below MA{long}) and the resulting confidence.
    """
    ma_long = params["ma_long"]
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
