"""SZSE ETF archive entry point.

Dispatches to sub-commands:

  python -m downloads.etf.szse.archive              # market data (default)
  python -m downloads.etf.szse.archive market       # market data (explicit)
  python -m downloads.etf.szse.archive reports      # quarterly reports + CSV extraction

The default (no args) preserves backwards compatibility with main.sh which
calls ``python -m downloads.etf.szse.archive`` for the daily market archive.

The entry point is the :class:`SzseEtfArchiveDownload` DownloadJob — the
former main() body is its ``run()`` (the lazy market/reports imports and
the ``logger.info(result)`` calls stay verbatim) and the argparse
declarations moved to ``add_arguments`` with the explicit ``prog`` kept via
``build_parser``; the fetch layers are the SzseArchiveMarket and
SzseArchiveReports classes.
"""
from __future__ import annotations


import argparse

from downloads._common import DownloadJob

from _common.log_setup import setup_logging  # noqa: E402
logger = setup_logging("archive")


class SzseEtfArchiveDownload(DownloadJob):
    """DownloadJob for the SZSE ETF archive entry — the former main() body
    is ``run()`` verbatim (sub-command dispatch + lazy imports) and the
    argparse declarations moved to ``add_arguments``; the market/reports
    fetch layers are the SzseArchiveMarket / SzseArchiveReports classes."""

    description = "SZSE ETF archive: market data and quarterly reports."

    def build_parser(self) -> argparse.ArgumentParser:
        # The old parser carried an explicit prog (the -m entry's argparse
        # default would be "__main__.py").
        parser = argparse.ArgumentParser(
            prog="downloads.etf.szse.archive",
            description=self.description or self.title or None,
        )
        self.add_arguments(parser)
        return parser

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "subcommand",
            nargs="?",
            default="market",
            choices=["market", "reports"],
            help="Sub-command to run (default: market).",
        )
        # Market sub-command options
        parser.add_argument("--start-date", type=str, default="2020-01-01",
                        help="Market: start date (default: 2020-01-01)")
        parser.add_argument("--end-date", type=str, default=None,
                        help="Market: end date (default: today)")
        parser.add_argument("--out-root", type=str, default=None,
                        help="Override output root directory")
        parser.add_argument("--sleep-sec", type=float, default=None,
                        help="Override sleep seconds between requests")
        # Reports sub-command options
        parser.add_argument("--etf-code", type=str, action="append", default=None,
                        help="Reports: process only this ETF code (bare 6-digit). "
                             "Repeatable. Default: all SZ ETFs from DB.")
        parser.add_argument("--max-etfs", type=int, default=None,
                        help="Reports: limit to N ETFs (dev/testing)")
        parser.add_argument("--no-extract", action="store_true", default=False,
                        help="Reports: skip PDF->CSV extraction")
        parser.add_argument("--no-other-only", action="store_true", default=False,
                        help="Reports: disable the default OTHER-classification "
                             "pre-filter (download ALL SZ ETFs, not just "
                             "sector_id='OTHER'). Ignored when --etf-code is given.")
        parser.add_argument("--no-lof", action="store_true", default=False,
                        help="Reports: do NOT add SZSE LOFs (16xxxx.SZ) to the "
                             "download universe. By default all active LOFs are "
                             "included even when --no-other-only is unset, since "
                             "LOF quarterly reports publish the same holdings "
                             "sections as ETF reports. Ignored when --etf-code "
                             "is given or when --no-other-only is set (the "
                             "all-active branch already contains LOFs).")

    def run(self) -> None:
        if self.args.subcommand == "market":
            from downloads.etf.szse.archive.market import SzseArchiveMarket
            result = SzseArchiveMarket().download_szse_archive_etf(
                out_root=self.args.out_root,
                end_date=self.args.end_date,
                start_date=self.args.start_date,
                sleep_sec=self.args.sleep_sec,
            )
            logger.info(result)
        elif self.args.subcommand == "reports":
            from downloads.etf.szse.archive.reports import SzseArchiveReports
            result = SzseArchiveReports().download_szse_etf_reports(
                out_root=self.args.out_root,
                etf_codes=self.args.etf_code,
                sleep_sec=self.args.sleep_sec,
                extract=not self.args.no_extract,
                max_etfs=self.args.max_etfs,
                other_only=not self.args.no_other_only,
                include_lof=not self.args.no_lof,
                start_date=self.args.start_date,
            )
            logger.info(result)


if __name__ == "__main__":
    SzseEtfArchiveDownload().execute()
