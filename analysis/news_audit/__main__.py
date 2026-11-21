"""analysis.news_audit — news provider/content audit entry point.

Runs the three steps over text.news (see config.py / steps.py):
  1. ProviderRegistryStep  — sync text.news_providers origins (is_blocked
     preserved), returns the source date span;
  2. ProviderAuditStep     — text.news_provider_audit rows for the target
     5-day windows;
  3. ContentAuditStep      — text.news_content_audit rows for the same
     windows (needs the keyword frame too).

Incremental (default): a window computes once complete, except the
trailing partial window (every run) and windows within --grace-days of the
latest news date (late digestions / keyword rewrites). ``--force``
truncates both audit tables and rebuilds every window from the first news
date. The registry step always runs (cheap full-history aggregate).

Usage (WSL conda): python -m analysis.news_audit [--force] [--grace-days N]
"""
from _common.data_pipeline import bootstrap_runtime

bootstrap_runtime()  # cudf.pandas hook BEFORE pandas first import

from datetime import date  # noqa: E402

from _common.data_analysis import DataAnalysis  # noqa: E402
from _common.db_commons import (  # noqa: E402
    analyze_tables_async,
    get_existing_keys_async,
)
from analysis.news_audit.config import (  # noqa: E402
    CONTENT_AUDIT_TABLE,
    DESCRIPTION,
    GRACE_DAYS,
    PROVIDER_AUDIT_TABLE,
)
from analysis.news_audit.steps import (  # noqa: E402
    ContentAuditStep,
    ProviderAuditStep,
    ProviderRegistryStep,
    fetch_frame,
    news_bearing_window_starts,
)
from analysis.news_audit.windows import AuditWindowGrid  # noqa: E402

_EPOCH = date(1970, 1, 1)


class NewsAuditAnalysis(DataAnalysis):
    """5-day news provider/content audit over the text.news corpus."""

    title = "NEWS AUDIT (providers + content)"
    description = DESCRIPTION
    component = "news_audit"

    def add_arguments(self, ap) -> None:
        self.add_force_arg(ap)
        ap.add_argument(
            "--grace-days", type=int, default=GRACE_DAYS,
            help=f"Recently completed windows within this many days of the "
                 f"latest news date recompute every run (late LLM "
                 f"digestions). Default: {GRACE_DAYS}.",
        )

    def header_fields(self) -> dict:
        return {
            "mode": (
                "FORCE (both audit tables truncated, full rebuild)"
                if self.args.force
                else "incremental (missing + trailing + grace windows)"
            ),
            "grace_days": self.args.grace_days,
        }

    async def run(self) -> None:
        conn = self.conn

        # Step 1 — registry sync; also the source date span for the grid.
        first_date, latest_date = await ProviderRegistryStep().run(conn)
        if latest_date is None:
            return
        grid = AuditWindowGrid(
            first_days=(first_date - _EPOCH).days,
            latest_days=(latest_date - _EPOCH).days,
            grace_days=self.args.grace_days,
            force=self.args.force,
        )

        if self.args.force:
            await self.truncate_tables(
                [PROVIDER_AUDIT_TABLE, CONTENT_AUDIT_TABLE]
            )

        # Target windows per table: existing-key diff against each table's
        # own window_start set (empty after the force truncate above),
        # EXCLUDING windows that contain no news — an empty window never
        # materializes rows, so it must not read as missing forever.
        news_days = await news_bearing_window_starts(conn)
        empty_days = set(grid.all_starts()) - news_days
        steps = (ProviderAuditStep(), ContentAuditStep())
        targets_by_step: dict[str, list[int]] = {}
        for step in steps:
            existing = await get_existing_keys_async(
                conn, step.table, ["window_start"]
            )
            existing_days = {
                (entry - _EPOCH).days if isinstance(entry, date) else (entry[0] - _EPOCH).days
                for entry in existing
            }
            targets_by_step[step.table] = grid.targets(
                existing_days | empty_days
            )
            print(
                f"[{step.title}] {len(targets_by_step[step.table])} "
                f"target window(s) "
                f"({len(news_days - existing_days)} news-bearing missing, "
                f"rest grace/trailing)"
            )

        load_from = min(
            (
                grid.load_from(targets)
                for targets in targets_by_step.values()
                if targets
            ),
            default=None,
        )
        if load_from is None:
            print("[news_audit] all windows up to date — nothing to compute")
            return

        # One shared fetch per source frame (both grains compute from the
        # same article frame; only the content step needs keywords).
        articles = await fetch_frame(
            conn, "analysis/news_audit/steps/audit_articles",
            from_date=load_from,
        )
        keywords = await fetch_frame(
            conn, "analysis/news_audit/steps/audit_keywords",
            from_date=load_from,
        )
        print(
            f"[news_audit] fetched {len(articles):,} article rows / "
            f"{len(keywords):,} keyword rows from {load_from}"
        )

        for step in steps:
            await step.run(
                conn, articles, keywords, grid, targets_by_step[step.table],
            )

        await analyze_tables_async(
            conn, [PROVIDER_AUDIT_TABLE, CONTENT_AUDIT_TABLE]
        )


if __name__ == "__main__":
    NewsAuditAnalysis().execute()
