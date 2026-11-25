"""Entry point: ``python -m downloads.index.csindex.pe`` — the WEEKLY
PE service (service split 2026-11-23, out of the nightly
``downloads.index.csindex.quote --pe-only`` step that ran
2026-11-20..22): per code ONLY the incremental PE (peg) series fetch +
the ``{code}_history.csv`` merge that folds it in (no from2020/1m/
intraday/mop-up work). The PE series has no other source —
``stats.index_valuation.pe`` folds it in via builds CSINDEX DAILY.

Weekly gate: the scheduled run no-ops while the last NORMAL exit of
THIS service in monitor.service_audit is younger than WEEKLY_GATE_DAYS
calendar days (the nightly chain fires the downloads_weekly tier every
trading day, so the gate is what makes off-cadence fires no-ops).
``--force`` bypasses the gate; no audit history (first run) proceeds by
design.

The fetch is the shared :class:`CsindexQuoteRunner` (the price
package's machinery) driven with ``pe_only=True`` — see that
docstring for the mode's semantics.
"""
from __future__ import annotations


import argparse

from downloads._common import (
    DownloadJob,
    days_since_last_normal_exit,
)
from _common.db_commons import get_db_connection

from downloads.index.csindex.price.runner import CsindexQuoteRunner

from _common.log_setup import setup_logging  # noqa: E402
logger = setup_logging("csindex_pe")

SERVICE = "downloads.index.csindex.pe"

# The weekly orchestration gate: the scheduled run no-ops while the last
# normal exit (monitor.service_audit) is younger than this many calendar
# days — the nightly chain fires the downloads_weekly tier every trading
# day, so the gate is what makes off-cadence fires no-ops. --force
# bypasses (explicit runs).
WEEKLY_GATE_DAYS = 6


class CsindexPeDownload(DownloadJob):
    """DownloadJob: the weekly CSIndex index-PE fetch (the price
    runner's ``pe_only`` path under its own service name)."""

    description = (
        "Weekly CSIndex index-PE fetch: per index ONLY the incremental "
        "PE (peg) series fetch + the {code}_history.csv merge — the PE "
        "series has no other source. Own weekly gate (>= 6 days since "
        "the last normal exit) no-ops off-cadence fires; --force bypass."
    )

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--force", action="store_true",
            help="Bypass the weekly gate (run the PE fetch even when "
                 "the last normal exit is recent).",
        )
        parser.add_argument(
            "--sleep-sec", type=float, default=None,
            help="Sleep seconds between requests "
                 f"(default: external_request_config JSON for {SERVICE})",
        )

    def run(self) -> None:
        if not self.args.force:
            days_idle = days_since_last_normal_exit(SERVICE)
            if days_idle is not None and days_idle < WEEKLY_GATE_DAYS:
                logger.info(
                    "weekly gate: last normal exit %.1f day(s) ago "
                    "(< %d) — nothing to do.",
                    days_idle, WEEKLY_GATE_DAYS,
                )
                return
        logger.info(
            CsindexQuoteRunner(service=SERVICE).download_index(
                pe_only=True,
                sleep_sec=self.args.sleep_sec,
            )
        )


if __name__ == "__main__":
    CsindexPeDownload().execute()
