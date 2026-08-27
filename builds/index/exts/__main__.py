"""
build_index_exts — Build stats.index_exts + stats.etf_trading_amt +
stats.exchange_trading_amt + stats.sec_similars (sec_type='index').

This package is split into three independent build steps, each with its own
missing-data detection and own date granularity:

  builds.index.exts._index_exts.build_index_exts(conn, force)
    stats.index_exts (per-(trading-date, index_code)): etf_num,
    total_etf_trading_amount, ma5, stock_num.
    stats.etf_trading_amt (per-(trading-date, industry_id)): same ETF
    turnover grouped by industry_id.
    Driven by stats.etf_liquidity_margin dates (daily).

  builds.index.exts._exchange_trading_amt.build_exchange_trading_amt(conn, force)
    stats.exchange_trading_amt (per-(date, exchange)): total_trading_amount
    proxied by ONE representative broad-market index per exchange
    (SZ->399001, SS->000001). Driven by stats.index_basic_stats dates
    (daily); own skip check so it runs even when index_exts had nothing
    to do.

  builds.index.exts._sec_similars.build_sec_similars(conn, force)
    stats.sec_similars (per-(composition-snapshot-date, code, sec_type)):
    top-5 similar codes + top-5 similar/dissimilar industries by mutual
    shared composition weight.
    Driven by stats.sec_composition snapshot dates (quarterly).

The steps are independent (different sources, different date grains,
different skip checks) so they can be re-run safely at any cadence.

Incremental mode (default): each step only (re)computes the dates it is
missing. --force: truncate the step's target table(s) first, then full
recompute.

Usage:
  python -m builds.index.exts             # incremental (missing dates only)
  python -m builds.index.exts --force     # full recompute
"""

# resource pre-check -- exit early when sys/GPU memory is insufficient
from _common.pre_check import pre_check

pre_check()

# cudf.pandas activation — must run before pandas first import
from _common.df_utils._activate import activate
activate()

import argparse
import asyncio
import time

from _common.build_commons import (  # noqa: E402
    setup_utf8_stdout,
    get_db_connection_async,
    add_force_arg,
    print_build_header,
    print_wall_time,
)

setup_utf8_stdout()

from builds.index.exts._index_exts import build_index_exts  # noqa: E402
from builds.index.exts._exchange_trading_amt import build_exchange_trading_amt  # noqa: E402
from builds.index.exts._sec_similars import build_sec_similars  # noqa: E402


async def main():
    ap = argparse.ArgumentParser(
        description="Build stats.index_exts + stats.etf_trading_amt "
                    "+ stats.exchange_trading_amt + stats.sec_similars "
                    "(missing dates only, or --force for full recompute)."
    )
    add_force_arg(ap)
    args = ap.parse_args()

    t0 = time.time()
    print_build_header(
        "BUILD INDEX EXTS + ETF/EXCHANGE TRADING AMT + SEC SIMILARS "
        "(index / industry / exchange aggregation + composition similars)",
        mode="FORCE (full recompute)" if args.force
             else "incremental (missing dates only)",
    )

    conn = await get_db_connection_async()
    try:
        # Step A: per-(date, index) + per-(date, industry) ETF metrics.
        # Independent of sec_similars — driven by etf_liquidity_margin.
        await build_index_exts(conn, force=args.force)

        # Step B: per-(date, exchange) trading amount proxied by a
        # representative broad-market index per exchange (SZ->399001,
        # SS->000001). Driven by index_basic_stats; own skip check, so it
        # runs even when index_exts had nothing to do.
        await build_exchange_trading_amt(conn, force=args.force)

        # Step C: per-(composition-date, code) top-5 similar codes +
        # similar/dissimilar industries.
        # Driven by sec_composition snapshot dates; own skip check, so it
        # runs even when index_exts had nothing to do.
        await build_sec_similars(conn, force=args.force)
    finally:
        try:
            await conn.close()
        except Exception:
            pass

    print_wall_time(t0)


if __name__ == "__main__":
    asyncio.run(main())
