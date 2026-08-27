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
    args = ap.parse_args()
    print(
        download_index(ensure_prev_trading_day=args.ensure_prev_trading_day)
    )
