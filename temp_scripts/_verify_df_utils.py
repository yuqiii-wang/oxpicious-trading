"""Verify the _common.df_utils consolidation + backward-compat shims.

Run from WSL:
    python3 -m temp_scripts._verify_df_utils
"""
from __future__ import annotations

import sys


def main() -> int:
    print(f"Python: {sys.version}")

    # Test 1: new consolidated package imports
    from _common.df_utils import (
        should_use_gpu, decide_gpu, GPUDecision, list_thresholds,
        detect_gpu, is_gpu_available, get_gpu_info, GPUInfo, reset_cache,
        OP_PROFILES, breakeven_rows, estimate_df_memory_bytes, fits_in_vram,
        compute_moving_averages, grouped_rolling_agg,
        grouped_diff, grouped_shift,
    )
    print("OK: _common.df_utils imports all public symbols")

    # Test 2: legacy _common._cuDF shim still works (identity-equal)
    from _common._cuDF import should_use_gpu as sug_legacy, GPUDecision as GD_legacy
    from _common._cuDF.detector import detect_gpu as dg_legacy
    from _common._cuDF.thresholds import breakeven_rows as br_legacy, OP_PROFILES as op_legacy
    from _common._cuDF.router import decide_gpu as dgc_legacy
    assert sug_legacy is should_use_gpu, "should_use_gpu identity mismatch"
    assert GD_legacy is GPUDecision, "GPUDecision identity mismatch"
    assert dg_legacy is detect_gpu, "detect_gpu identity mismatch"
    assert br_legacy is breakeven_rows, "breakeven_rows identity mismatch"
    assert op_legacy is OP_PROFILES, "OP_PROFILES identity mismatch"
    assert dgc_legacy is decide_gpu, "decide_gpu identity mismatch"
    print("OK: _common._cuDF shim re-exports are identity-equal to _common.df_utils")

    # Test 4: legacy analyze._common.rolling shim still works
    from analyze._common.rolling import grouped_rolling_agg as gra_legacy
    assert gra_legacy is grouped_rolling_agg, "grouped_rolling_agg identity mismatch"
    print("OK: analyze._common.rolling shim re-exports grouped_rolling_agg")

    # Test 5: analyze._common package still re-exports grouped_rolling_agg
    from analyze._common import grouped_rolling_agg as gra_pkg
    assert gra_pkg is grouped_rolling_agg, "analyze._common.grouped_rolling_agg identity mismatch"
    print("OK: analyze._common package re-exports grouped_rolling_agg")

    # Test 6: functional smoke - should_use_gpu on a tiny df (CPU expected)
    import pandas as pd
    df = pd.DataFrame({"code": ["A", "A", "B", "B"], "close": [1.0, 2.0, 3.0, 4.0]})
    res = should_use_gpu(df, op_type="rolling_mean", verbose=False)
    print(f"OK: should_use_gpu(tiny df) -> {res} (expected False - below breakeven)")

    # Test 7: list_thresholds returns sensible values
    ths = list_thresholds(n_numeric_cols=10)
    print(f"OK: list_thresholds() returns {len(ths)} op profiles")
    print(f"    rolling_mean   breakeven = {ths['rolling_mean']:,}")
    print(f"    rolling_std    breakeven = {ths['rolling_std']:,}")
    print(f"    rolling_corr   breakeven = {ths['rolling_corr']:,}")
    print(f"    groupby_diff   breakeven = {ths['groupby_diff']:,}")
    print(f"    groupby_shift  breakeven = {ths['groupby_shift']:,}")
    print(f"    merge          breakeven = {ths['merge']:,}")

    # Test 8: functional smoke - compute_moving_averages (CPU path)
    df2 = pd.DataFrame({
        "code": ["A"] * 6 + ["B"] * 6,
        "date": list(range(6)) + list(range(6)),
        "close": [10.0, 11.0, 12.0, 13.0, 14.0, 15.0,
                  100.0, 110.0, 120.0, 130.0, 140.0, 150.0],
    }).sort_values(["code", "date"]).reset_index(drop=True)
    df2 = compute_moving_averages(
        df2, group_key="code", value_col="close",
        windows=[5], min_periods=1, add_ratio=True, ratio_window=5,
    )
    expected_ma5_a_last = round(sum([11.0, 12.0, 13.0, 14.0, 15.0]) / 5, 6)
    actual_ma5_a_last = df2[df2["code"] == "A"]["ma5"].iloc[-1]
    assert abs(actual_ma5_a_last - expected_ma5_a_last) < 1e-9, (
        f"ma5 mismatch: {actual_ma5_a_last} != {expected_ma5_a_last}"
    )
    print(f"OK: compute_moving_averages(CPU) ma5[A,last] = {actual_ma5_a_last} (expected {expected_ma5_a_last})")

    # Test 9: functional smoke - grouped_rolling_agg (CPU path)
    std5 = grouped_rolling_agg(
        df2, ["code"], "close", window=5, min_periods=5, agg="std", ddof=0,
    )
    df2["std_5"] = std5
    # std of [11,12,13,14,15] (ddof=0) = sqrt(variance) = sqrt(2.0) ≈ 1.414214
    actual_std_a_last = df2[df2["code"] == "A"]["std_5"].iloc[-1]
    expected_std = round((2.0 ** 0.5), 6)
    assert abs(actual_std_a_last - expected_std) < 1e-6, (
        f"std5 mismatch: {actual_std_a_last} != {expected_std}"
    )
    print(f"OK: grouped_rolling_agg(CPU) std5[A,last] = {actual_std_a_last} (expected {expected_std})")

    # Test 10: functional smoke - grouped_diff (CPU path, multi-column)
    df3 = pd.DataFrame({
        "sec_type": ["etf"] * 5,
        "code": ["X"] * 5,
        "date": list(range(5)),
        "price": [10.0, 11.0, 12.0, 13.0, 14.0],
    }).sort_values(["sec_type", "code", "date"]).reset_index(drop=True)
    grouped_diff(
        df3, ["sec_type", "code"],
        cols=["price"],
        out_names=["price_slope"],
    )
    # price_slope[t] = price[t] - price[t-1]; first row NaN
    assert pd.isna(df3["price_slope"].iloc[0]), "first slope should be NaN"
    assert df3["price_slope"].iloc[1] == 1.0, f"slope[1] != 1.0, got {df3['price_slope'].iloc[1]}"
    print("OK: grouped_diff(CPU) price_slope[0]=NaN, price_slope[1]=1.0")

    # Test 11: functional smoke - grouped_shift (CPU path)
    df4 = pd.DataFrame({
        "sec_type": ["etf"] * 4,
        "code": ["Y"] * 4,
        "date": list(range(4)),
        "price": [10.0, 20.0, 30.0, 40.0],
    }).sort_values(["sec_type", "code", "date"]).reset_index(drop=True)
    grouped_shift(
        df4, ["sec_type", "code"], ["price"],
        out_names=["price_prev2"], periods=2,
    )
    assert pd.isna(df4["price_prev2"].iloc[0]), "shift2[0] should be NaN"
    assert pd.isna(df4["price_prev2"].iloc[1]), "shift2[1] should be NaN"
    assert df4["price_prev2"].iloc[2] == 10.0, f"shift2[2] != 10.0, got {df4['price_prev2'].iloc[2]}"
    assert df4["price_prev2"].iloc[3] == 20.0, f"shift2[3] != 20.0, got {df4['price_prev2'].iloc[3]}"
    print("OK: grouped_shift(CPU) price_prev2 = [NaN, NaN, 10, 20]")

    # Test 12: integration - compute_slopes_curvatures via grouped_diff
    # (this is the call site that was refactored to use the new helper).
    from analyze.mov_ave_spread.helpers import compute_slopes_curvatures
    from analyze.mov_ave_spread.config import MA_WINDOWS
    n = 8  # need at least 3 rows per code to get a non-NaN curvature
    codes = ["A"] * n + ["B"] * n
    dates = list(range(n)) * 2
    # Linear price: slope = 1.0, curvature = 0.0 (after row 2)
    prices_a = [float(i + 10) for i in range(n)]
    prices_b = [float(i * 2 + 100) for i in range(n)]  # slope = 2.0
    df5 = pd.DataFrame({
        "sec_type": ["etf"] * (2 * n),
        "code": codes,
        "date": dates,
        "price": prices_a + prices_b,
    })
    # Add all ma{W} columns (= price, to keep things simple).
    for w in MA_WINDOWS:
        df5[f"ma{w}"] = df5["price"]
    df5 = df5.sort_values(["sec_type", "code", "date"]).reset_index(drop=True)
    df5 = compute_slopes_curvatures(df5)
    # price_slope[A] = 1.0 (from row 1 onward), price_slope[B] = 2.0
    assert pd.isna(df5[df5["code"] == "A"]["price_slope"].iloc[0]), "slope[A,0] should be NaN"
    assert df5[df5["code"] == "A"]["price_slope"].iloc[1] == 1.0, (
        f"slope[A,1] != 1.0, got {df5[df5['code']=='A']['price_slope'].iloc[1]}"
    )
    assert df5[df5["code"] == "B"]["price_slope"].iloc[1] == 2.0, (
        f"slope[B,1] != 2.0, got {df5[df5['code']=='B']['price_slope'].iloc[1]}"
    )
    # price_curvature[A] = 0.0 (linear -> constant slope -> zero curvature)
    a_curv = df5[df5["code"] == "A"]["price_curvature"].iloc[2]
    assert a_curv == 0.0, f"curvature[A,2] != 0.0, got {a_curv}"
    # ma5_slope mirrors price_slope (since ma5 == price)
    assert df5[df5["code"] == "A"]["ma5_slope"].iloc[1] == 1.0, "ma5_slope mismatch"
    # All expected slope + curvature columns are present
    for w in MA_WINDOWS:
        assert f"ma{w}_slope" in df5.columns, f"missing ma{w}_slope"
        assert f"ma{w}_curvature" in df5.columns, f"missing ma{w}_curvature"
    assert "price_slope" in df5.columns and "price_curvature" in df5.columns
    print(f"OK: compute_slopes_curvatures(via grouped_diff) slopes+curvatures correct for {len(MA_WINDOWS)+1} columns")

    # Test 13: integration - compute_rolling_stds via grouped_rolling_agg
    # (unchanged call site, but now imports from _common.df_utils).
    from analyze.mov_ave_spread.helpers import compute_rolling_stds
    df6 = pd.DataFrame({
        "sec_type": ["etf"] * 6,
        "code": ["Z"] * 6,
        "date": list(range(6)),
        "price": [10.0, 12.0, 14.0, 16.0, 18.0, 20.0],
    }).sort_values(["sec_type", "code", "date"]).reset_index(drop=True)
    df6 = compute_rolling_stds(df6)
    # std_5days of [10,12,14,16,18] (ddof=0) = sqrt(8.0) ≈ 2.828427
    expected_std5 = round((8.0 ** 0.5), 6)
    actual_std5 = df6["std_5days"].iloc[4]  # 5th row = first full 5-window
    assert abs(actual_std5 - expected_std5) < 1e-6, (
        f"std_5days mismatch: {actual_std5} != {expected_std5}"
    )
    # First 4 rows should be NaN (min_periods=5)
    assert pd.isna(df6["std_5days"].iloc[0]), "std_5days[0] should be NaN (min_periods=5)"
    print(f"OK: compute_rolling_stds(via grouped_rolling_agg) std_5days = {actual_std5} (expected {expected_std5})")

    print()
    print("ALL IMPORT + SMOKE TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
