"""builds.etf — Build combined SZSE + SSE ETF OHLCV + margin + composition +
PE data and insert directly to the database (missing-data-only).

NOTE: This script loads ONLY ETF data. Index composition (CSI + SZSE
closeweight CSVs) is now loaded by `python -m builds.index.composition`
which writes to the same stats.sec_composition table with
source_type='index'. Run builds.index.composition BEFORE builds.index.baseline
so that index shared weights are available for close-price estimation.

Reads the per-day SZSE/SSE CSV archives produced by download scripts:
  - SZSE: szse_archive/szse_etf_YYYYMMDD.csv        (2022-01 → 2025-06-30 legacy)
  - SZSE: szse_trend/szse_trend_etf_YYYYMMDD.csv    (2025-07 → today snapshot)
  - SSE: sse_trend/sse_trend_etf_YYYYMMDD.csv       (today snapshot, ETF/fund tab)
  - SZSE/SSE margin detail CSVs                     (per-security margin)
  - SZSE: szse_etf_composition/szse_etf_comp_YYYYMMDD_<code>.csv (per-file)

ETF PE: computed via HARMONIC weighting of constituent stock PE from
stats.stock_basic_stats by the LATEST stats.sec_composition snapshot:
    PE_etf = SUM(w_i) / SUM(w_i / PE_i)
Loss-making constituents (NULL PE) are excluded from both numerator and
denominator. PE scope is INCREMENTAL — recomputed only for rows eligible
for re-upsert this run (missing dates ∪ corp-action resync codes ∪ PE-null
keys). Run builds.stock BEFORE builds.etf so stock PE is available.

The full staged pipeline lives in ``builds.etf.pipeline``; this entry keeps
only resource pre-check + cudf activation + CLI parsing.

Missing-data detection flow (DB-first):
  OHLCV + margin (cross-date dependency — splits + MAs need FULL history):
    discover files → DB scope (force purge / missing dates + recent re-scan)
    → read only needed CSVs + DB history → merge/adjust/MAs → incremental PE
    → filter to write candidates → upsert etf_identity + 4 split tables.
  ETF composition (sec_composition source_type='etf'): missing snapshots only.
  sec_classification type='etf': per-code quality metrics, idempotent upsert
    (classification + index_code columns preserved on conflict).

With --force: truncate stats.etf_identity and DELETE FROM stats.sec_composition
WHERE source_type='etf' (index composition rows preserved), then read ALL
source CSVs (DB empty → full-history scope).

Usage:
  python -m builds.etf
  python -m builds.etf --start-date 2024-01-01 --end-date 2025-06-30
  python -m builds.etf --force
  python -m builds.etf --code 159919.SZ           (single-ETF test filter)
"""

# resource pre-check -- exit early when sys/GPU memory is insufficient
from _common.pre_check import pre_check

pre_check()
import argparse

import warnings
warnings.filterwarnings("ignore")

# cudf.pandas activation — must run before pandas first import
from _common.df_utils._activate import activate
activate()

from _common.build_commons import (
    setup_utf8_stdout, add_common_build_args,
)
setup_utf8_stdout()

import asyncio

from builds._commons.code_filter import add_code_arg, normalize_code


async def main() -> None:
    ap = argparse.ArgumentParser(
        description="Build SZSE + SSE ETF + margin + composition + PE and insert to database (missing-data-only)."
    )
    add_common_build_args(ap)
    add_code_arg(ap)
    args = ap.parse_args()
    # Resolve once so pipeline stages share the canonical suffixed code.
    args.resolved_code = normalize_code(args.code)

    from builds.etf.pipeline.main import run
    await run(args)


if __name__ == "__main__":
    asyncio.run(main())
