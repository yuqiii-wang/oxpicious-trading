"""CLI entry point: python -m downloads.index.bse.composition.

Downloads the 北证50 (899050) / 北证专精特新 (899601) composition 成分股列表
(constituent + weight closeweight snapshots, CSI co-published autofile) —
see :class:`BseCompositionRunner.download_bse_index_composition` in
``runner.py`` for the logic.

The entry point is the :class:`BseCompositionDownload` DownloadJob — the
former ``__main__`` body is ``run()`` and the argparse setup moved into
``add_arguments`` (byte-identical help); the fetch cycle lives on the
:class:`BseCompositionRunner` in ``runner.py``.
"""
from __future__ import annotations

import argparse

from downloads._common import DownloadJob

from ._config import SERVICE
from .runner import BseCompositionRunner

from _common.log_setup import setup_logging  # noqa: E402
logger = setup_logging("bse_index_composition")


class BseCompositionDownload(DownloadJob):
    """DownloadJob for the BSE composition fetch cycle — the former
    ``__main__`` body is ``run()`` (verbatim argument mapping); the fetch
    logic is the :class:`BseCompositionRunner` method."""

    description = ("Download BSE index composition (CSI co-published closeweight) "
                   "xls and convert to CSV.")

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--code", type=str, default=None,
            help="Download a single index code then exit (e.g. --code 899050 or "
                 "--code 899601). Default: both BSE_INDEX_CODES.",
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
        result = BseCompositionRunner().download_bse_index_composition(
            index_codes=[self.args.code] if self.args.code else None,
            out_root=self.args.out_root,
            skip_cached=not self.args.no_skip_cached,
            sleep_sec=self.args.sleep_sec,
            force_month_start=self.args.force_month_start,
        )
        logger.info(result)


if __name__ == "__main__":
    BseCompositionDownload().execute()
