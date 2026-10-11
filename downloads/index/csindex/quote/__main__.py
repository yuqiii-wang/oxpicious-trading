"""Entry point: ``python -m downloads.index.csindex.quote``.

Default (no args): full nightly sweep — from2020 (cached-skip) + 1m
window + PE + history merge + intraday per code.

--ensure-prev-trading-day: targeted mode for the "Build Yday Ref" UI
button chain — skip ALL network work for codes whose local 1m/history
CSV already contains the previous trading day; only laggards are
fetched (1m window only; PE/intraday stay nightly-owned).
"""
from __future__ import annotations


import argparse

from .runner import download_index
from ._config import SERVICE

from _common.log_setup import setup_logging  # noqa: E402
logger = setup_logging("quote")

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--ensure-prev-trading-day",
        action="store_true",
        help=(
            "Targeted mode: only fetch codes whose local CSVs lack the "
            "previous trading day row (fast local check per code)."
        ),
    )
    ap.add_argument(
        "--sleep-sec", type=float, default=None,
        help="Sleep seconds between requests "
             f"(default: external_request_config JSON for {SERVICE})",
    )
    args = ap.parse_args()
    from _common.service_heartbeat import autoregister  # lazy: keeps pandas behind the cudf hook
    autoregister()  # heartbeat + run audit (monitor.*)
    logger.info(
        download_index(
            ensure_prev_trading_day=args.ensure_prev_trading_day,
            sleep_sec=args.sleep_sec,
        )
    )
