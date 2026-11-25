"""analysis.news_audit — news audit entry point.

Runs the audit steps over text.news (see config.py / steps.py):
  1. ProviderRegistryStep  — sync text.news_providers origins (is_blocked
     preserved), returns the source date span;
  1b. ProviderAutoBlockStep — flag is_blocked on origins with >1 audited
     fake item (the news API's default filters read it);
  2. ProviderAuditStep     — text.news_provider_audit rows for the target
     5-day windows;
  3. ContentAuditStep      — text.news_content_audit rows for the same
     windows (needs the keyword frame too);
  4. CoverageAuditStep     — text.news_coverage_audit rows at the DAILY
     grain (per trading day per industry, '000001' = the whole-market
     rollup row) — the coverage basis analysis.news_text_daily reads
     (the 2026-11-22 coverage migration).

The --coverage toggle selects which audits a run executes
(all | providers | content | coverage; default all). The registry sync
always runs (cheap, and the window grids' date span comes from it).

Window audits — incremental (default): a window computes once complete,
except the trailing partial window (every run) and windows within
--grace-days of the latest news date (late digestions / keyword
rewrites). ``--force`` truncates the audit tables and rebuilds every
window from the first news date.

Coverage audit — incremental (default): article-bearing trading days
missing from the table + the same grace tail (late digestions correct
the just-closed days); the probe loads from the OLDEST existing day so
a pipeline-down gap still heals. ``--force`` truncates + rebuilds from
the calendar start. Weekend articles roll forward to the next trading
day (the shared calendar-roll convention).

Usage (WSL conda):
  python -m analysis.news_audit [--force] [--grace-days N]
      [--coverage {all,providers,content,coverage}]
"""
from _common.data_pipeline import bootstrap_runtime

bootstrap_runtime()  # cudf.pandas hook BEFORE pandas first import

from datetime import date, timedelta  # noqa: E402

from _common.data_analysis import DataAnalysis  # noqa: E402
from _common.db_commons import (  # noqa: E402
    analyze_tables_async,
    get_existing_keys_async,
)
from _common.df_utils import host_array  # noqa: E402
from analysis._common.calendar_roll import (  # noqa: E402
    day_numbers,
    roll_to_calendar,
)
from analysis.news_audit.config import (  # noqa: E402
    CONTENT_AUDIT_TABLE,
    COVERAGE_CHOICES,
    COVERAGE_TABLE,
    DESCRIPTION,
    GRACE_DAYS,
    PROVIDER_AUDIT_TABLE,
)
from analysis.news_audit.steps import (  # noqa: E402
    ContentAuditStep,
    CoverageAuditStep,
    ProviderAuditStep,
    ProviderAutoBlockStep,
    ProviderRegistryStep,
    fetch_calendar_days,
    fetch_frame,
    news_bearing_window_starts,
)
from analysis.news_audit.windows import AuditWindowGrid  # noqa: E402

_EPOCH = date(1970, 1, 1)


class NewsAuditAnalysis(DataAnalysis):
    """The news audits (providers + content windows, daily coverage)."""

    title = "NEWS AUDIT (providers + content + coverage)"
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
        ap.add_argument(
            "--coverage", choices=COVERAGE_CHOICES, default="all",
            help="Which audits to run: all (default) | providers | "
                 "content | coverage (the daily per-industry grain the "
                 "text panel reads).",
        )

    def header_fields(self) -> dict:
        return {
            "mode": (
                "FORCE (audit tables truncated, full rebuild)"
                if self.args.force
                else "incremental (missing + trailing + grace)"
            ),
            "grace_days": self.args.grace_days,
            "coverage": self.args.coverage,
        }

    async def _run_window_audits(self, conn) -> None:
        """The 5-day provider/content window audits (the pre-existing
        flow, untouched by the coverage migration)."""
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

        tables = []
        if self.args.coverage in ("all", "providers"):
            tables.append(PROVIDER_AUDIT_TABLE)
        if self.args.coverage in ("all", "content"):
            tables.append(CONTENT_AUDIT_TABLE)
        if self.args.force and tables:
            await self.truncate_tables(tables)

        # Target windows per table: existing-key diff against each table's
        # own window_start set (empty after the force truncate above),
        # EXCLUDING windows that contain no news — an empty window never
        # materializes rows, so it must not read as missing forever.
        news_days = await news_bearing_window_starts(conn)
        empty_days = set(grid.all_starts()) - news_days
        steps = []
        if self.args.coverage in ("all", "providers"):
            steps.append(ProviderAuditStep())
        if self.args.coverage in ("all", "content"):
            steps.append(ContentAuditStep())
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
            # Audits are unchanged, but a pending auto-block (first
            # backfill run, manual unblock of a >1-fake origin) still
            # applies — cheap, idempotent, one statement.
            await ProviderAutoBlockStep().run(conn)
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
        # After the audits: freshly recomputed windows count toward the
        # >1-fake auto-block.
        await ProviderAutoBlockStep().run(conn)
        await analyze_tables_async(conn, [s.table for s in steps])

    async def _run_coverage_audit(self, conn) -> None:
        """The daily per-industry coverage audit (the panel's flow basis)."""
        cal_days = await fetch_calendar_days(conn)
        if len(cal_days) == 0:
            print("[news_audit] empty trading calendar — coverage skipped")
            return

        if self.args.force:
            await self.truncate_tables([COVERAGE_TABLE])

        # Target set: article-bearing trading days missing from the table
        # + the trailing grace window (late digestions correct the
        # just-closed days). The probe loads from the OLDEST existing day
        # so a pipeline-down gap longer than the grace window still heals.
        existing = await get_existing_keys_async(conn, COVERAGE_TABLE, ["date"])
        existing_days = {
            (entry - _EPOCH).days if isinstance(entry, date) else (entry[0] - _EPOCH).days
            for entry in existing
        }
        probe_from_day = (
            int(cal_days[0])
            if self.args.force or not existing_days
            else min(existing_days)
        )
        probe = await fetch_frame(
            conn, "analysis/news_audit/steps/audit_articles",
            from_date=_EPOCH + timedelta(days=probe_from_day),
        )
        if probe.empty:
            print("[news_audit] text.news has no rows in scope — "
                  "coverage skipped")
            return
        probe_days = probe.drop(columns=["date_epoch"]).assign(
            day=day_numbers(probe["date_epoch"]),
        )
        article_days = set(
            host_array(roll_to_calendar(probe_days, cal_days)["day"])
            .astype("int64").tolist()
        )
        missing = article_days - existing_days
        max_day = max(article_days)
        grace = {d for d in article_days if d > max_day - self.args.grace_days}
        targets = sorted(missing | grace)
        if not targets:
            print("[news_audit] coverage up to date — nothing to compute")
            return

        from_day = min(targets)
        articles = await fetch_frame(
            conn, "analysis/news_audit/steps/audit_articles",
            from_date=_EPOCH + timedelta(days=from_day),
        )
        print(
            f"[news_audit coverage] {len(targets)} target trading day(s) "
            f"({len(missing)} missing, {len(grace)} grace tail); "
            f"{len(articles):,} article rows from {_EPOCH + timedelta(days=from_day)}"
        )
        written = await CoverageAuditStep().run(conn, articles, cal_days, targets)
        if written:
            await analyze_tables_async(conn, [COVERAGE_TABLE])

    async def run(self) -> None:
        conn = self.conn
        if self.args.coverage in ("all", "providers", "content"):
            await self._run_window_audits(conn)
        if self.args.coverage in ("all", "coverage"):
            await self._run_coverage_audit(conn)


if __name__ == "__main__":
    NewsAuditAnalysis().execute()
