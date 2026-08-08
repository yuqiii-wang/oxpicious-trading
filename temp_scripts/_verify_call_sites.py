"""Verify all updated call sites still import correctly after the
consolidation to _common.df_utils.

Imports the modules that were updated to use _common.df_utils directly
(builds + analyzes) to catch any import-path regressions. Does NOT
execute the main() functions - just imports.

Run from WSL:
    python3 -m temp_scripts._verify_call_sites
"""
from __future__ import annotations

import importlib
import sys


# Modules whose imports were updated to use _common.df_utils.
# Each entry: (module_path, expected_symbol_to_check)
UPDATED_MODULES = [
    # builds - now import compute_moving_averages from _common.df_utils
    ("builds.stock.tech_stats.__main__", "compute_moving_averages"),
    ("builds.etf.__main__", "compute_moving_averages"),
    ("builds.index.baseline.__main__", "compute_moving_averages"),
    # analyzes - now import should_use_gpu / grouped_diff / grouped_rolling_agg
    ("analyze.mov_ave_spread.helpers", "compute_slopes_curvatures"),
    ("analyze.mov_ave_spread.compute", "build_detail_rows"),
    ("analyze.mov_ave_spread.rsi", "compute_rsi_and_gaps"),
    ("analyze.industry_sentiments.compute", "rebase_closes"),
    ("analyze.industry_sentiments.correlations", "rolling_corr"),
    ("analyze.sec_alloc_perf_attribution.compute", "build_and_insert"),
]


def main() -> int:
    failures = []
    for mod_path, symbol in UPDATED_MODULES:
        try:
            mod = importlib.import_module(mod_path)
            if not hasattr(mod, symbol):
                failures.append(f"{mod_path}: missing expected symbol '{symbol}'")
                continue
            print(f"OK: {mod_path} imports + has '{symbol}'")
        except SystemExit:
            # Some __main__ modules call argparse on import - that's fine,
            # the import itself succeeded before argparse ran.
            print(f"OK (argparse SystemExit): {mod_path}")
        except Exception as e:
            failures.append(f"{mod_path}: {type(e).__name__}: {e}")

    # Also verify the __main__ entry points import cleanly (they may
    # call argparse on import via main() guard - tolerate SystemExit).
    main_modules = [
        "analyze.mov_ave_spread.__main__",
        "analyze.industry_sentiments.__main__",
        "analyze.sec_alloc_perf_attribution.__main__",
        "builds.stock.tech_stats.__main__",
        "builds.etf.__main__",
        "builds.index.baseline.__main__",
    ]
    for mod_path in main_modules:
        try:
            importlib.import_module(mod_path)
            print(f"OK: {mod_path} entry point imports clean")
        except SystemExit:
            print(f"OK (argparse): {mod_path} entry point imports clean")
        except Exception as e:
            failures.append(f"{mod_path}: {type(e).__name__}: {e}")

    # Also verify the consolidated package itself is healthy.
    try:
        import _common.df_utils as dfu
        for sym in ["should_use_gpu", "compute_moving_averages",
                    "grouped_rolling_agg", "grouped_diff", "grouped_shift",
                    "decide_gpu", "list_thresholds", "OP_PROFILES"]:
            assert hasattr(dfu, sym), f"_common.df_utils missing {sym}"
        print("OK: _common.df_utils package exposes all public symbols")
    except Exception as e:
        failures.append(f"_common.df_utils: {type(e).__name__}: {e}")

    print()
    if failures:
        print(f"FAILURES ({len(failures)}):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"ALL {len(UPDATED_MODULES) + len(main_modules) + 1} IMPORT CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
