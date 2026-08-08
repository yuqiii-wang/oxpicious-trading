"""Verify slope/curvature temporal direction (PAST, not future).

Key insight: if slope used FUTURE data (shift(-1) = t+1), the LAST row of
each group would be NaN (no future row). If it uses PAST data (diff(1) =
t - t-1), the FIRST row is NaN (no past row) and the LAST row is valid.

This script builds a 5-day synthetic series per code and checks:
  1. slope[t] == price[t] - price[t-1]  (past-looking)
  2. curvature[t] == slope[t] - slope[t-1]
                   == (price[t] - price[t-1]) - (price[t-1] - price[t-2])
                   == price[t] - 2*price[t-1] + price[t-2]  (past-looking)
  3. First row of each group: slope is NaN (no past), curvature is NaN.
  4. Last row of each group: slope is VALID (not NaN) — proves past-looking.
"""
from __future__ import annotations

import datetime as dt
import sys

import numpy as np
import pandas as pd


def main() -> int:
    from analyze.mov_ave_spread.helpers import compute_slopes_curvatures
    from analyze.mov_ave_spread.config import MA_WINDOWS

    dates = [dt.date(2026, 1, 1) + dt.timedelta(days=i) for i in range(5)]
    rows = []
    for code, base in [("AAA", 100.0), ("BBB", 200.0)]:
        for i, d in enumerate(dates):
            rows.append({
                "sec_type": "index",
                "code": code,
                "date": d,
                "price": base + i * 1.0,  # +1 per day
                "ma5": base + i * 0.5,
                "ma20": base + i * 0.3,
                "ma60": base + i * 0.2,
                "ma120": base + i * 0.1,
                "ma255": base + i * 0.05,
            })
    df = pd.DataFrame(rows)
    out = compute_slopes_curvatures(df)

    failures: list[str] = []

    for code in ("AAA", "BBB"):
        sub = out[out["code"] == code].reset_index(drop=True)
        prices = sub["price"].to_numpy()

        # Check 1: slope[t] = price[t] - price[t-1] (past)
        for i in range(1, len(sub)):
            expected = prices[i] - prices[i - 1]
            actual = sub["price_slope"].iloc[i]
            if np.isnan(actual) or abs(actual - expected) > 1e-9:
                failures.append(f"{code} price_slope[{i}]={actual} != {expected}")

        # Check 2: curvature[t] = price[t] - 2*price[t-1] + price[t-2] (past)
        for i in range(2, len(sub)):
            expected = prices[i] - 2 * prices[i - 1] + prices[i - 2]
            actual = sub["price_curvature"].iloc[i]
            if np.isnan(actual) or abs(actual - expected) > 1e-9:
                failures.append(f"{code} price_curvature[{i}]={actual} != {expected}")

        # Check 3: FIRST row — slope NaN (no past), curvature NaN (no t-2)
        if not np.isnan(sub["price_slope"].iloc[0]):
            failures.append(f"{code} first-row slope should be NaN (no past), got {sub['price_slope'].iloc[0]}")
        if not np.isnan(sub["price_curvature"].iloc[0]):
            failures.append(f"{code} first-row curvature should be NaN, got {sub['price_curvature'].iloc[0]}")
        # Second row: slope valid, curvature NaN (needs t-2)
        if np.isnan(sub["price_slope"].iloc[1]):
            failures.append(f"{code} second-row slope should be valid, got NaN")
        if not np.isnan(sub["price_curvature"].iloc[1]):
            failures.append(f"{code} second-row curvature should be NaN (no t-2), got {sub['price_curvature'].iloc[1]}")

        # Check 4: LAST row — slope VALID (proves PAST-looking, not future)
        last = len(sub) - 1
        if np.isnan(sub["price_slope"].iloc[last]):
            failures.append(f"{code} last-row slope is NaN — would indicate FUTURE-looking (BUG!)")
        if np.isnan(sub["price_curvature"].iloc[last]):
            failures.append(f"{code} last-row curvature is NaN — would indicate FUTURE-looking (BUG!)")

        # Verify all MA windows too
        for w in MA_WINDOWS:
            col = f"ma{w}"
            slope_col = f"ma{w}_slope"
            curv_col = f"ma{w}_curvature"
            vals = sub[col].to_numpy()
            for i in range(1, len(sub)):
                expected = vals[i] - vals[i - 1]
                actual = sub[slope_col].iloc[i]
                if np.isnan(actual) or abs(actual - expected) > 1e-9:
                    failures.append(f"{code} {slope_col}[{i}]={actual} != {expected}")
            # Last row valid
            if np.isnan(sub[slope_col].iloc[last]):
                failures.append(f"{code} last-row {slope_col} is NaN — FUTURE-looking (BUG!)")
            if np.isnan(sub[curv_col].iloc[last]):
                failures.append(f"{code} last-row {curv_col} is NaN — FUTURE-looking (BUG!)")

    if failures:
        print("FAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1

    print("SLOPE/CURVATURE TEMPORAL VERIFICATION PASSED")
    print("  - slope[t]     = price[t] - price[t-1]      (PAST, t-1)")
    print("  - curvature[t] = price[t] - 2*price[t-1] + price[t-2]  (PAST, t-1 + t-2)")
    print("  - First row: NaN (no past)  |  Last row: VALID (confirms past-looking)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
