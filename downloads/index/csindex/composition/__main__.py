"""CLI entry point: python -m downloads.index.csindex.composition.

See :class:`CsindexCompositionRunner.download_index_composition` in
``runner.py`` for the actual logic.

The entry point is the :class:`CsindexCompositionDownload` DownloadJob —
the former ``__main__`` body is ``run()`` and the argparse setup moved
into ``add_arguments`` (byte-identical help).
"""
from __future__ import annotations

import argparse

from downloads._common import DownloadJob

from ._config import SERVICE
from .runner import CsindexCompositionRunner

from _common.log_setup import setup_logging  # noqa: E402
logger = setup_logging("composition")


class CsindexCompositionDownload(DownloadJob):
    """DownloadJob for the CSI composition fetch cycle — the former
    ``__main__`` body is ``run()`` (verbatim argument mapping); the fetch
    logic is the :class:`CsindexCompositionRunner` method."""

    description = ("Download CSI index composition (closeweight) xls and "
                   "convert to CSV.")

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--index-codes", type=str, default=None,
            help="Comma-separated list of index codes (default: all from the sec_classification cache). "
                 "Example: --index-codes 930606,000300,399997",
        )
        parser.add_argument(
            "--out-root", type=str, default=None,
            help="Alternative output root directory",
        )
        parser.add_argument(
            "--no-skip-cached", action="store_true", default=False,
            help="Re-download even if a cached CSV exists",
        )
        parser.add_argument(
            "--force-month-start", action="store_true", default=False,
            help="Force monthly-refresh behavior: bypass cache and stamp CSVs with "
                 "today's date (overrides xls snapshot_date). For testing the "
                 "monthly refresh flow on any day.",
        )
        parser.add_argument(
            "--sleep-sec", type=float, default=None,
            help="Sleep seconds between downloads "
                 f"(default: external_request_config JSON for {SERVICE})",
        )

    def run(self) -> None:
        codes = None
        if self.args.index_codes:
            codes = [c.strip() for c in self.args.index_codes.split(",") if c.strip()]

        result = CsindexCompositionRunner().download_index_composition(
            index_codes=codes,
            out_root=self.args.out_root,
            skip_cached=not self.args.no_skip_cached,
            sleep_sec=self.args.sleep_sec,
            force_month_start=self.args.force_month_start,
        )
        logger.info(result)


if __name__ == "__main__":
    CsindexCompositionDownload().execute()
