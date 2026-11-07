"""Entry point: ``python -m downloads.index.bse.archive``.

Refreshes the BSE index daily archive CSVs (temps/bse_index_archive) from
www.bse.cn — each index page's own kline endpoint (北证50 bse_bz50.html,
北证专精特新 bse_zjtx.html; see the per-index urls roles). Serves BOTH the
historical archive and the daily trend: each endpoint always returns the
full daily series (899050 base date 2022-04-29, 899601 base date 2024-06-28
→ now), the run appends missing dates, and the 19:00 daily schedule lands
each session's final row — the same one-CSV-per-code convention as
``downloads.index.cnindex.archive``.

The entry point is the :class:`BseArchiveDownload` DownloadJob — the
former ``__main__`` body is ``run()`` and the argparse setup moved into
``add_arguments`` (byte-identical help); the fetch cycle lives on the
:class:`BseArchiveRunner` in ``runner.py``.
"""
from __future__ import annotations

import argparse

from downloads._common import DownloadJob

from .runner import SERVICE, BseArchiveRunner

from _common.log_setup import setup_logging  # noqa: E402
logger = setup_logging("bse_index_archive")


class BseArchiveDownload(DownloadJob):
    """DownloadJob for the BSE archive fetch cycle — the former ``__main__``
    body is ``run()`` (verbatim argument mapping); the fetch logic is the
    :class:`BseArchiveRunner` method."""

    description = __doc__

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--code", type=str, default=None,
            help="Download a single index code then exit (e.g. --code 899050 or "
                 "--code 899601).",
        )
        parser.add_argument(
            "--force", action="store_true",
            help="Rewrite the history CSV entirely from the API (default: "
                 "append-only merge).",
        )
        parser.add_argument(
            "--sleep-sec", type=float, default=None,
            help="Sleep seconds between requests "
                 f"(default: external_request_config JSON for {SERVICE})",
        )

    def run(self) -> None:
        logger.info(BseArchiveRunner().download_bse_index_archive(
            index_codes=[self.args.code] if self.args.code else None,
            force=self.args.force,
            sleep_sec=self.args.sleep_sec,
        ))


if __name__ == "__main__":
    BseArchiveDownload().execute()
