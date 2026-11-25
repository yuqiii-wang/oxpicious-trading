"""Download the macro precious-metal daily bars — SGE gold/silver spot
+ COMEX gold/silver main continuous — the Macro tab's data source.

Sources (FULL history per instrument, one request each; 4 requests/run):

  SGE 上海黄金交易所 daily spot bars (POST www.sge.com.cn/graph/Dailyhq,
  body ``instid=<species>``): Au99.99 (人民币金价, CNY/克) and Ag99.99
  (人民币银价, CNY/kg) — OHLC only, no volume.

  sina 外盘期货 daily klines (GlobalFuturesService
  .getGlobalFuturesDailyKLine, akshare futures_foreign_hist_sina's
  endpoint): GC (COMEX gold main continuous) and SI (COMEX silver main
  continuous), USD/ozt — OHLC + volume.

Each run overwrites one full-history CSV per instrument in
``temps/gold_silver/`` (re-runs simply refresh the files; the
incremental/--force semantics live in the ``builds.macro`` upsert):

  sge_au99.99.csv  sge_ag99.99.csv  comex_gc.csv  comex_si.csv

CSV schema (``date, open, high, low, close, volume`` — one row per
trading day, ascending, dates ISO ``YYYY-MM-DD``):
  * date    — the trading day (ISO; unparseable rows dropped)
  * open / high / low / close — floats as published (SGE rows carry the
    wire order (date, open, close, LOW, HIGH) — remapped here)
  * volume  — COMEX klines only (lots); NULL for the SGE spot bars

The entry point is the :class:`GoldSilverDownload` DownloadJob.

Usage:
  python -m downloads.macro.gold_silver
  python -m downloads.macro.gold_silver --sleep-sec 10
  python -m downloads.macro.gold_silver --out-root /tmp/alt
"""
from __future__ import annotations

import argparse
from typing import Dict, List, Optional

import pandas as pd

from downloads._common import (
    DownloadJob,
    RunStats,
    build_default_session,
    resolve_out_dir,
    setup_logger,
)
from downloads.macro.gold_silver._endpoints import (
    CSV_COLUMNS,
    SGE_INSTRUMENTS,
    SINA_SYMBOLS,
    GoldSilverEndpoints,
    sge_rows_to_csv_rows,
    sina_kline_text_to_csv_rows,
)

logger = setup_logger("macro_gold_silver")

SERVICE = "downloads.macro.gold_silver"

CSV_ENCODING = "utf-8-sig"


class GoldSilverDownload(DownloadJob):
    """DownloadJob for the macro gold/silver fetch cycle — the efunds
    trend shape over 4 full-history requests; pacing / out_root stay
    per-call arguments."""

    description = ("Download SGE Au99.99/Ag99.99 spot bars + sina COMEX "
                   "GC/SI main-continuous daily klines (full history "
                   "per instrument).")
    component = "macro_gold_silver"
    service = SERVICE

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--out-root", type=str, default=None,
                            help="Alternative output root directory")
        parser.add_argument("--sleep-sec", type=float, default=None,
                            help="Sleep seconds between requests "
                                 f"(default: external_request_config JSON for {SERVICE})")

    def download_gold_silver(
        self,
        *,
        out_root: Optional[str] = None,
        sleep_sec: Optional[float] = None,
    ) -> Dict[str, object]:
        out_dir = resolve_out_dir("", "gold_silver", out_root)
        session = build_default_session()
        endpoints = GoldSilverEndpoints()
        proxy = endpoints.build_proxy(SERVICE, sleep_sec)
        stats = RunStats()

        jobs: List[Dict[str, str]] = [dict(job) for job in SGE_INSTRUMENTS] \
            + [dict(job) for job in SINA_SYMBOLS]
        for job in jobs:
            code, stem = job["code"], job["csv_stem"]
            if job["source"] == "sge":
                payload = endpoints.fetch_sge_daily(
                    session, proxy, code, SERVICE, logger=logger)
                rows = sge_rows_to_csv_rows(payload) if payload else []
            else:
                text = endpoints.fetch_sina_kline(
                    session, proxy, code, SERVICE, logger=logger)
                rows = sina_kline_text_to_csv_rows(text) if text else []

            if not rows:
                logger.warning("[%s] no rows parsed — %s not written",
                               code, stem)
                stats.empty += 1
                continue

            out_path = out_dir / f"{stem}.csv"
            frame = pd.DataFrame(rows, columns=CSV_COLUMNS)
            frame.to_csv(out_path, index=False, encoding=CSV_ENCODING)
            stats.downloaded += 1
            stats.files.append(str(out_path))
            logger.info("[%s] saved %s (%d rows, %s → %s)", code,
                        out_path.name, len(rows), rows[0]["date"],
                        rows[-1]["date"])

        if stats.empty:
            logger.warning("[gold_silver] %d instrument(s) produced no rows",
                           stats.empty)
        return stats.to_dict(out_dir=str(out_dir),
                             instruments=len(jobs) - stats.empty)

    def run(self) -> None:
        self.download_gold_silver(
            out_root=self.args.out_root,
            sleep_sec=self.args.sleep_sec,
        )


if __name__ == "__main__":
    GoldSilverDownload().execute()
