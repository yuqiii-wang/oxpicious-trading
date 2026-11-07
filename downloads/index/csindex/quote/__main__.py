"""Entry point: ``python -m downloads.index.csindex.quote``.

Default (no args): nightly increment — from2020 (cached-skip) + 1m
window + intraday per code. The PE fetch + history merge are SKIPPED by
default (the recent rows reach the DB via SSE trend / SZSE / CNINDEX /
BSE snapshots in builds CSINDEX DAILY's source ladder; the history CSVs
are only the deep-history fallback).

--refresh-history: also run the PE fetch + history merge (the slow
full-table exports) — the one-time convergence for caches left behind
by skipped runs, or to re-capture restated CSIndex data.

The entry point is the :class:`CsindexQuoteDownload` DownloadJob — the
former ``__main__`` body is ``run()`` and the argparse setup moved into
``add_arguments`` (byte-identical help); the fetch cycle lives on the
:class:`CsindexQuoteRunner` in ``runner.py``.
"""
from __future__ import annotations


import argparse

from downloads._common import DownloadJob

from .runner import CsindexQuoteRunner
from ._config import SERVICE

from _common.log_setup import setup_logging  # noqa: E402
logger = setup_logging("quote")


class CsindexQuoteDownload(DownloadJob):
    """DownloadJob for the CSIndex quote fetch cycle — the former
    ``__main__`` body is ``run()`` (verbatim argument mapping); the fetch
    logic is the :class:`CsindexQuoteRunner` method."""

    description = __doc__

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--ensure-prev-trading-day",
            action="store_true",
            help=(
                "Targeted mode: only fetch codes whose local CSVs lack the "
                "previous trading day row (fast local check per code)."
            ),
        )
        parser.add_argument(
            "--refresh-history",
            action="store_true",
            help=(
                "Also run the PE fetch + history merge (the slow full-table "
                "exports). Default: skipped — recent rows reach the DB via "
                "SSE trend / SZSE / CNINDEX / BSE snapshots."
            ),
        )
        parser.add_argument(
            "--sleep-sec", type=float, default=None,
            help="Sleep seconds between requests "
                 f"(default: external_request_config JSON for {SERVICE})",
        )

    def run(self) -> None:
        logger.info(
            CsindexQuoteRunner().download_index(
                ensure_prev_trading_day=self.args.ensure_prev_trading_day,
                sleep_sec=self.args.sleep_sec,
                refresh_history=self.args.refresh_history,
            )
        )


if __name__ == "__main__":
    CsindexQuoteDownload().execute()
