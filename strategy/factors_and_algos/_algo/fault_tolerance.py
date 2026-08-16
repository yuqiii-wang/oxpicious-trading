"""Fault tolerance — bidirectional OHLC stress test on decision days.

When ``fault_tolerance > 0`` (0-20%), the baseline backtest runs first
to identify BUY/SELL decision dates. Then the OHLC on those dates is
perturbed in **both** directions (UP and DOWN) by
``ft% × |Δclose|``, and the algo's ``apply_signals`` is re-run on each
stressed OHLC **using the same precomputed tech stats** (MA, std, RSI
from the DB — NOT recomputed).

Why both directions?
--------------------
The "adverse" direction depends on the algo's signal logic:

  - **Trend-following** (MACD): BUY = bullish momentum → DOWN weakens
    it (breaks the crossover). SELL = bearish momentum → UP weakens it.
  - **Mean-reversion** (Bollinger Bands): BUY = oversold (z < −k) → UP
    weakens it (towards the band). SELL = overbought (z > k) → DOWN
    weakens it.

Since no single direction is adverse for all algos, BOTH directions
are run and BOTH stressed confidences are stored. The UI shows both
metrics so the user can see which direction was adverse for that algo.

Result annotation
-----------------
For each baseline decision, two fields are attached:

  - ``ft_stressed_conf_up``: |stressed signal| when OHLC moved UP.
    0 if the trade would be REMOVED under UP stress (sign flipped).
  - ``ft_stressed_conf_down``: |stressed signal| when OHLC moved DOWN.
    0 if the trade would be REMOVED under DOWN stress.

NULL when no FT was applied (baseline strategy). The baseline decisions
themselves are unchanged (same dates, sides, P&L) — the FT data is a
comparison annotation on trade_decision rows.
"""
from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd


# ---------------------------------------------------------------------------
#  Strategy-name suffix
# ---------------------------------------------------------------------------
def append_ft_suffix(name: str, ft: float) -> str:
    """Append ``_ft{N}`` suffix when ``ft > 0`` (e.g. ``macd`` → ``macd_ft10``)."""
    if not ft or float(ft) <= 0:
        return name
    return f"{name}_ft{int(round(float(ft)))}"


# ---------------------------------------------------------------------------
#  OHLC stress (single direction)
# ---------------------------------------------------------------------------
def _stress_ohlc_directional(
    df: pd.DataFrame,
    decisions: List[Dict[str, Any]],
    ft_pct: float,
    direction: int,
) -> pd.DataFrame:
    """Perturb OHLC on decision dates in ONE direction.

    ``direction`` = +1 shifts all decision-day OHLC UP by
    ``(ft_pct/100) × |Δclose|``; −1 shifts DOWN. Non-decision rows are
    untouched. Tech-stat columns (MA, std, RSI, …) are left AS-IS from
    the DB — only ``open/high/low/close`` are perturbed.
    """
    ft = float(ft_pct) / 100.0
    if ft <= 0 or df.empty or not decisions:
        return df.copy()

    stressed = df.copy()

    # |Δclose| per code (first row per code is NaN → fill 0).
    stressed["_delta_close"] = (
        stressed.groupby("code")["close_price"].diff().abs().fillna(0.0)
    )

    # Build a (code, date) → side lookup from baseline decisions.
    dec_rows = [
        {"code": d["code"], "date": d["exec_date"], "_side": d["side"]}
        for d in decisions
    ]
    dec_df = pd.DataFrame(dec_rows)

    # Merge to flag decision rows.
    stressed = stressed.merge(dec_df, on=["code", "date"], how="left")

    # Uniform shift on all decision dates (direction determines sign).
    stressed["_stress_shift"] = 0.0
    dec_mask = stressed["_side"].notna()
    stressed.loc[dec_mask, "_stress_shift"] = (
        direction * ft * stressed.loc[dec_mask, "_delta_close"]
    )

    for col in ("open_price", "high_price", "low_price", "close_price"):
        if col in stressed.columns:
            stressed[col] = stressed[col] + stressed["_stress_shift"]

    return stressed.drop(columns=["_delta_close", "_side", "_stress_shift"])


# ---------------------------------------------------------------------------
#  Stressed-confidence attachment (bidirectional)
# ---------------------------------------------------------------------------
def _build_conf_lookup(stressed_df: pd.DataFrame) -> Dict[tuple, float]:
    """Build (code, date) → signal_confidence lookup from a stressed df."""
    lookup: Dict[tuple, float] = {}
    if stressed_df.empty or "signal_confidence" not in stressed_df.columns:
        return lookup
    for _, row in stressed_df[["code", "date", "signal_confidence"]].iterrows():
        key = (row["code"], row["date"])
        val = row["signal_confidence"]
        if pd.notna(val):
            lookup[key] = float(val)
    return lookup


def _stressed_magnitude(stressed_sig: float, side: str) -> float:
    """Convert a stressed signal_confidence to a magnitude or 0 (removed).

    If the stressed signal has the SAME sign as the baseline side (BUY>0,
    SELL<0), return |stressed signal| (trade survives, confidence was cut).
    Otherwise (sign flip or zero) return 0 — the trade would be REMOVED
    under that direction's stress.
    """
    if side == "BUY" and stressed_sig > 0:
        return round(abs(stressed_sig), 4)
    if side == "SELL" and stressed_sig < 0:
        return round(abs(stressed_sig), 4)
    return 0.0


def attach_ft_stressed_conf(
    decisions: List[Dict[str, Any]],
    stressed_up_df: pd.DataFrame,
    stressed_down_df: pd.DataFrame,
) -> List[Dict[str, Any]]:
    """Attach ``ft_stressed_conf_up`` and ``ft_stressed_conf_down`` to each
    baseline decision.

    For each decision, the stressed ``signal_confidence`` is looked up in
    BOTH the up-shifted and down-shifted signal dfs. Each direction's
    magnitude is stored independently (no "adverse" pick — the UI shows
    both so the user can see which direction was adverse for that algo).
    """
    if not decisions:
        return decisions

    up_lookup = _build_conf_lookup(stressed_up_df)
    down_lookup = _build_conf_lookup(stressed_down_df)

    for d in decisions:
        key = (d["code"], d["exec_date"])
        side = d["side"]
        up_sig = up_lookup.get(key, 0.0)
        down_sig = down_lookup.get(key, 0.0)
        d["ft_stressed_conf_up"] = _stressed_magnitude(up_sig, side)
        d["ft_stressed_conf_down"] = _stressed_magnitude(down_sig, side)

    return decisions


# ---------------------------------------------------------------------------
#  Two-pass runner (used by AlgoBase + AlgoSignalCollector)
# ---------------------------------------------------------------------------
def run_ft_stress(
    algo,
    df: pd.DataFrame,
    params: dict,
    sec_type: str,
    codes: list,
    baseline_decisions: List[Dict[str, Any]],
    signal_reason_fn,
) -> List[Dict[str, Any]]:
    """Run the bidirectional FT stress pass after the baseline backtest.

    Steps:
      1. Produce two stressed OHLC dfs: all decision dates shifted UP,
         and all shifted DOWN, by ``ft% × |Δclose|``.
      2. Re-run ``algo.apply_signals`` on EACH stressed df (same tech
         stats from DB — NOT recomputed).
      3. For each baseline decision, look up the stressed confidence in
         BOTH directions and store BOTH magnitudes
         (``ft_stressed_conf_up`` + ``ft_stressed_conf_down``).

    Returns the **baseline decisions** (unchanged dates/sides/P&L) with
    both FT metrics annotated on each row.
    """
    ft = float(params.get("fault_tolerance", 0) or 0)
    if ft <= 0 or not baseline_decisions:
        return baseline_decisions

    # 1. Stress OHLC in both directions.
    stressed_up = _stress_ohlc_directional(df, baseline_decisions, ft, +1)
    stressed_down = _stress_ohlc_directional(df, baseline_decisions, ft, -1)

    if stressed_up.empty and stressed_down.empty:
        for d in baseline_decisions:
            d["ft_stressed_conf_up"] = None
            d["ft_stressed_conf_down"] = None
        return baseline_decisions

    # 2. Re-run apply_signals on each stressed OHLC (same tech stats), then
    #    apply the SAME base signal tuning (sub-threshold conf < 5 → 0) so
    #    the FT magnitude correctly reflects "trade removed under stress"
    #    when the stressed signal drops below the threshold.
    from strategy.factors_and_algos._algo.tuning import tune_signals
    if not stressed_up.empty:
        stressed_up = tune_signals(algo.apply_signals(stressed_up, params))
    if not stressed_down.empty:
        stressed_down = tune_signals(algo.apply_signals(stressed_down, params))

    # 3. Attach both directional stressed confidences to each decision.
    baseline_decisions = attach_ft_stressed_conf(
        baseline_decisions, stressed_up, stressed_down,
    )

    return baseline_decisions
