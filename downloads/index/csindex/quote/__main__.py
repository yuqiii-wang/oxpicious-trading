"""Entry point: ``python -m downloads.index.csindex.quote``.

Default (no args): nightly increment — from2020 (cached-skip) + 1m
window + intraday per code. The PE fetch + history merge are SKIPPED by
default (the recent rows reach the DB via SSE trend / SZSE / CNINDEX /
BSE snapshots in builds CSINDEX DAILY's source ladder; the history CSVs
are only the deep-history fallback).

--refresh-history: also run the PE fetch + history merge (the slow
full-table exports) — the one-time convergence for caches left behind
by skipped runs, or to re-capture restated CSIndex data.
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
        "--refresh-history",
        action="store_true",
        help=(
            "Also run the PE fetch + history merge (the slow full-table "
            "exports). Default: skipped — recent rows reach the DB via "
            "SSE trend / SZSE / CNINDEX / BSE snapshots."
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
            refresh_history=args.refresh_history,
        )
    )
