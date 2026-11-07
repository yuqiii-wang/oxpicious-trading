"""Entry point: ``python -m downloads.index.cnindex.archive``.

Refreshes the CNINDEX daily archive CSVs (temps/cnindex_archive) from
hq.cnindex.com.cn — the only source covering 国证2000 (399303), 国证A50
(399310) and 国证1000 (399311). Content-gated incremental: codes whose
history CSV already covers the newest publishable session are skipped;
lags the publish by design at most one run.

The entry point is the :class:`CnindexArchiveDownload` DownloadJob — the
former ``__main__`` body is ``run()`` and the argparse setup moved into
``add_arguments`` (byte-identical help); the fetch cycle lives on the
:class:`CnindexArchiveRunner` in ``runner.py``.
"""
from __future__ import annotations

import argparse

from downloads._common import DownloadJob

from ._config import SERVICE
from .runner import CnindexArchiveRunner

from _common.log_setup import setup_logging  # noqa: E402
logger = setup_logging("cnindex_archive")


class CnindexArchiveDownload(DownloadJob):
    """DownloadJob for the CNINDEX archive fetch cycle — the former
    ``__main__`` body is ``run()`` (verbatim argument mapping); the fetch
    logic is the :class:`CnindexArchiveRunner` method."""

    description = __doc__

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--code", type=str, default=None,
            help="Download a single index code then exit (e.g. --code 399303).",
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
        logger.info(CnindexArchiveRunner().download_cnindex_archive(
            index_codes=[self.args.code] if self.args.code else None,
            force=self.args.force,
            sleep_sec=self.args.sleep_sec,
        ))


if __name__ == "__main__":
    CnindexArchiveDownload().execute()
