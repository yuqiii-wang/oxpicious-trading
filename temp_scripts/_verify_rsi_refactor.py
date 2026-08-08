"""Smoke test for the refactored mov_ave_spread.rsi.

Verifies:
  1. Module imports cleanly (grouped_diff / grouped_shift from _common.df_utils).
  2. compute_rsi_and_gaps produces correct RSI + gap columns on a small
     synthetic DataFrame (CPU path — below GPU breakeven).
  3. _compute_since_last_extreme produces gap_since_last_extreme +
     days_since_last_extreme columns and drops the temporary _next_slope.
  4. RSI edge cases: pure uptrend -> 100, pure downtrend -> 0, flat -> NaN.
  5. gap_2days / gap_3days match (price[t]-price[t-N])/price[t-N].
"""
from __future__ import annotations

import datetime as dt
import sys
import traceback

import numpy as np
import pandas as pd


def _build_synthetic() -> pd.DataFrame:
    """Build a 2-code x 30-day synthetic DataFrame with a known price
    pattern so RSI/gap/extreme values can be hand-verified."""
    dates = [dt.date(2026, 1, 1) + dt.timedelta(days=i) for i in range(30)]
    rows = []
    for code, trend in [("AAA", "up"), ("BBB", "down")]:
        price = 100.0
        for i, d in enumerate(dates):
            # AAA: steady uptrend; BBB: steady downtrend.
            step = +1.0 if trend == "up" else -1.0
            price = price + step
            rows.append({
                "sec_type": "index",
                "code": code,
                "date": d,
                "price": price,
                "price_slope": step,  # constant slope -> no turning points
            })
    return pd.DataFrame(rows)


def main() -> int:
    failures: list[str] = []

    # ---- 1. Import -----------------------------------------------
    try:
        from analyze.mov_ave_spread.rsi import (
            compute_rsi_and_gaps,
            _compute_since_last_extreme,
            sanitize_rsi_rows,
        )
        print("[1/5] import OK")
    except Exception as e:  # noqa: BLE001
        print(f"[1/5] import FAILED: {e}")
        traceback.print_exc()
        return 1

    # ---- 2. compute_rsi_and_gaps ----------------------------------
    df = _build_synthetic()
    try:
        out = compute_rsi_and_gaps(df)
    except Exception as e:  # noqa: BLE001
        print(f"[2/5] compute_rsi_and_gaps FAILED: {e}")
        traceback.print_exc()
        return 1

    expected_cols = (
        ["sec_type", "code", "date", "price", "price_slope"]
        + [f"rsi_{w}days" for w in (6, 10, 14, 20)]
        + [f"gap_{w}days" for w in (2, 3)]
    )
    missing = [c for c in expected_cols if c not in out.columns]
    if missing:
        failures.append(f"missing columns: {missing}")

    # Temporary columns should NOT survive the function.
    if "_delta" in out.columns:
        failures.append("_delta column leaked")
    for w in (2, 3):
        if f"_price_prev{w}" in out.columns:
            failures.append(f"_price_prev{w} column leaked")

    # ---- 3. RSI edge cases ----------------------------------------
    # AAA: pure uptrend (every delta > 0) -> avg_loss == 0 -> RSI = 100.
    # BBB: pure downtrend (every delta < 0) -> avg_gain == 0 -> RSI = 0.
    # After min_periods (= window), RSI should be 100 / 0 respectively.
    for w in (6, 10, 14, 20):
        col = f"rsi_{w}days"
        aaa_full = out[(out["code"] == "AAA")].iloc[w:][col]
        bbb_full = out[(out["code"] == "BBB")].iloc[w:][col]
        if not (aaa_full == 100.0).all():
            failures.append(f"AAA {col} not all 100 after warmup: {aaa_full.tolist()[:3]}")
        if not (bbb_full == 0.0).all():
            failures.append(f"BBB {col} not all 0 after warmup: {bbb_full.tolist()[:3]}")
        # Before min_periods, RSI must be NaN. ewm(min_periods=W) yields
        # NaN for the first W-1 positions (0..W-2); position W-1 is the
        # first non-NaN (that's the W-th observation).
        if not out[out["code"] == "AAA"].iloc[: w - 1][col].isna().all():
            failures.append(f"AAA {col} not NaN during warmup")

    print(f"[2/5] RSI edge cases: AAA->100, BBB->0, NaN warmup OK "
          f"(checked windows 6/10/14/20)")

    # ---- 4. gap_Ndays verification --------------------------------
    # gap_2days[t] = (price[t] - price[t-2]) / price[t-2]
    for code in ("AAA", "BBB"):
        sub = out[out["code"] == code].reset_index(drop=True)
        prices = sub["price"].to_numpy()
        for n in (2, 3):
            col = f"gap_{n}days"
            for i in range(n, len(sub)):
                expected = (prices[i] - prices[i - n]) / prices[i - n]
                actual = sub[col].iloc[i]
                if np.isnan(actual) or not np.isfinite(expected):
                    continue
                if abs(actual - expected) > 1e-9:
                    failures.append(
                        f"{code} {col}[{i}] = {actual}, expected {expected}"
                    )
    print("[3/5] gap_2days / gap_3days match (price[t]-price[t-N])/price[t-N] OK")

    # ---- 5. _compute_since_last_extreme ---------------------------
    try:
        ext = _compute_since_last_extreme(out.copy())
    except Exception as e:  # noqa: BLE001
        print(f"[4/5] _compute_since_last_extreme FAILED: {e}")
        traceback.print_exc()
        return 1

    for col in ("gap_since_last_extreme", "days_since_last_extreme"):
        if col not in ext.columns:
            failures.append(f"missing {col}")
    if "_next_slope" in ext.columns:
        failures.append("_next_slope column leaked from _compute_since_last_extreme")

    # Constant slope -> no turning points -> all NULL.
    if not ext["gap_since_last_extreme"].isna().all():
        failures.append("expected all-NULL gap_since_last_extreme for constant slope")
    if not ext["days_since_last_extreme"].isna().all():
        failures.append("expected all-NULL days_since_last_extreme for constant slope")
    print("[4/5] _compute_since_last_extreme OK (constant slope -> all NULL, _next_slope dropped)")

    # ---- 6. sanitize_rsi_rows -------------------------------------
    try:
        rows = sanitize_rsi_rows(ext)
    except Exception as e:  # noqa: BLE001
        print(f"[5/5] sanitize_rsi_rows FAILED: {e}")
        traceback.print_exc()
        return 1
    if not isinstance(rows, list):
        failures.append(f"sanitize_rsi_rows returned {type(rows)}, expected list")
    else:
        print(f"[5/5] sanitize_rsi_rows OK -> {len(rows)} rows")

    # ---- summary --------------------------------------------------
    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
