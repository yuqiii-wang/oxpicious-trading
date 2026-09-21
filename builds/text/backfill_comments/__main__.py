"""builds.text.backfill_comments — CLI entry point.

Re-fetches zhihu comments for news rows already stored in text.news and
writes them through builds.text.upsert's comment pipeline. See the package
docstring for the "why" (comments were only scraped on first download, so
most of the stored corpus had none) and the batching/retry semantics.

Usage::

    python -m builds.text.backfill_comments
                                              # all zhihu items missing comments
    python -m builds.text.backfill_comments --start-date 2026-09-01
                                              # window on the news date
    python -m builds.text.backfill_comments --min-votes 20 --limit 500
                                              # best-first probe budget
    python -m builds.text.backfill_comments --force
                                              # re-probe items that already
                                              # have comments too (refresh)
    python -m builds.text.backfill_comments --sleep-sec 2.0
                                              # gentler on the public API
"""
from __future__ import annotations

import datetime

from _common.data_build import DataBuild
from _common.log_setup import setup_logging

logger = setup_logging("text")

TODAY_STR = datetime.date.today().isoformat()


class BackfillCommentsBuild(DataBuild):
    title = "BACKFILL COMMENTS  ·  zhihu comments -> text.news_comments"
    component = "text"

    def add_arguments(self, parser) -> None:
        parser.add_argument("--start-date", type=str, default=None,
                            help="Only probe articles dated on/after YYYY-MM-DD.")
        parser.add_argument("--end-date", type=str, default=None,
                            help="Only probe articles dated on/before YYYY-MM-DD.")
        parser.add_argument("--min-votes", type=int, default=None,
                            help="Only probe articles with votes >= N (default: all).")
        parser.add_argument("--limit", type=int, default=None,
                            help="Cap the number of items probed (default: no cap).")
        parser.add_argument("--sleep-sec", type=float, default=None,
                            help="Base sleep between probes (default: the "
                                 "downloader's COMMENT_SLEEP_SEC).")
        parser.add_argument("--force", action="store_true",
                            help="Also re-probe items that already have comments "
                                 "(default: only items with none).")

    def apply_args(self) -> None:
        def _date(flag: str) -> datetime.date | None:
            value = getattr(self.args, flag)
            if not value:
                return None
            try:
                return datetime.date.fromisoformat(value)
            except ValueError:
                logger.error("    [FATAL] invalid --%s (expected YYYY-MM-DD): %s",
                             flag.replace("_", "-"), value)
                raise SystemExit(2)

        self.start = _date("start_date")
        self.end = _date("end_date")

    def header_fields(self) -> dict:
        return {
            "Scope": "all zhihu items (refresh)" if self.args.force
                     else "items missing comments",
            "Window": f"{self.start or '…'} → {self.end or '…'}",
            "Min votes": self.args.min_votes if self.args.min_votes is not None else "any",
            "Limit": self.args.limit if self.args.limit is not None else "none",
            "Today": TODAY_STR,
        }

    async def run(self) -> None:
        from builds.text.backfill_comments import run_backfill

        logger.info("\n[1/2] Connecting to database …")
        conn = await self.connect_db()
        try:
            logger.info("\n[2/2] Probing zhihu comments for stored items …")
            counters = await run_backfill(
                conn,
                start=self.start,
                end=self.end,
                min_votes=self.args.min_votes,
                limit=self.args.limit,
                force=self.args.force,
                sleep_sec=self.args.sleep_sec,
            )
        finally:
            from _common.db_commons import close_pools_async
            await close_pools_async()
        logger.info(
            "BACKFILL COMMENTS DONE — probed %d item(s): %d with comments, "
            "%d empty/failed, %d new comment rows written",
            counters["items"], counters["with_comments"],
            counters["empty"], counters["rows"])


if __name__ == "__main__":
    BackfillCommentsBuild().execute()
