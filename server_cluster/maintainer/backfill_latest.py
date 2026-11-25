"""server_cluster.maintainer.backfill_latest — the 07:15 missing-session
backfill.

The morning-after self-heal for the 2026-11-20 incident's CORE trap: a
nightly fire dies mid-write (or a live/pre-close snapshot poisons the
day) and the day lands PARTIAL — after which every later rebuild skips
it forever (the builds' identity-presence date-gate treats any date
with rows as loaded; the 11-20 stub sat at 2 of 515 codes for three
days). Detection was the daily check's job since 2026-11-22; this job
is the REPAIR half:

1. Detect — the latest CLOSED trading session
   (:func:`_common._holidays_and_weekdays.expected_publish_session`,
   15:00 close) vs its previous session, per daily-bar family
   (index / stock / etf / futures): covered codes below 90% of the
   previous session's coverage = partial (the family_coverage view,
   the daily-check threshold convention).
2. Purge — the partial session's rows go away, children-first over the
   stats FK graph (a SessionPurge), so the builds' date-gate sees the
   session as missing again and a re-run can actually load it.
3. Backfill — the SAME pipeline instance the nightly chain uses runs
   ``downloads_daily → builds_daily → analysis_daily`` with the
   trading-day gate bypassed (``PipelineRunRequest(force=True)``): the
   backfill-capable downloaders (szse/cnindex/bse/cffex/csindex — the
   date-ranged, historical-capable sources) re-fetch the purged
   session and the builds reload it whole. The live-snapshot leaves
   are safe to re-run here too: their fetchers re-fetch non-final
   sessions (the 2026-11-23 finality gate) and the builds cap at the
   latest CLOSED session (build_commons cap_to_closed_sessions), so
   intraday values can never reach the daily tables. The pipeline call
   stays OUTSIDE the job's event loop — it is a blocking hours-long
   pass whose own audit writes spawn their own loops.
4. Caution — one deduped row per partial family (the Cautions tab is
   the audit trail; the daily check's own cautions stay independent).

Scheduled (own fire group, ``MAINTAINER_BACKFILL_AT`` env override) at
07:15 China — after the 07:00 bring-up + daily check, before the open
— and runnable on demand (``POST /jobs/latest_session_backfill/run``).
A clean session writes nothing; the pass audits itself to
``monitor.service_audit`` like every maintainer job.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from _common._holidays_and_weekdays import (
    expected_publish_session,
    last_business_day,
)
from _common.caution_log import record_service_error
from _common.db_commons import get_db_connection_async
from _common.sql_views import render_sql_view
from server_cluster.maintainer.jobs import (MaintenanceJob, MissedFireGuard,
                                            PassAudit)
from server_cluster.maintainer.pipeline import (NightlyPipelineJob,
                                                PipelineRunRequest)

logger = logging.getLogger(__name__)

CHINA_TZ = ZoneInfo("Asia/Shanghai")

# The fire time (China local) and the audit trail constants.
DEFAULT_FIRE_AT = "07:15"
FIRE_AT_ENV = "MAINTAINER_BACKFILL_AT"
AUDIT_SERVICE = "server_cluster.maintainer.backfill_latest"
AUDIT_EXIT_REASON = "daily 07:15 missing-session backfill"

_CAUTION_SERVICE = "server_cluster.maintainer"

# Coverage threshold — a session below 90% of the previous session's
# coverage is partial (the daily_data_checks views' convention).
COVERAGE_RATIO = 0.9

# The tiers the backfill chain runs, in chain order (strategy tiers stay
# on their normal 16:00 schedule — the analysis outputs they read are
# fresh after this pass, and the nightly fire re-derives them anyway).
BACKFILL_TIERS = ["downloads_daily", "builds_daily", "analysis_daily"]


class FamilyCoverage(BaseModel):
    """One family's session-coverage verdict."""

    family: str
    covered: int
    expected: int

    @property
    def is_partial(self) -> bool:
        return self.covered < self.expected * COVERAGE_RATIO

    @property
    def ratio_line(self) -> str:
        return f"{self.covered} of {self.expected} codes"


class SessionPurge:
    """Delete ONE session's rows from a family's stats tables,
    children-first over the FK graph (the repair-purge as a reusable
    class — the date-gate trap makes the purge the backfill's
    load-bearing step: without it a re-run skips the partial day
    forever)."""

    # Per-family root tables (the identity/stat tables whose rows ARE
    # the session). The FK walk adds the detail children (exts,
    # valuation, adjustments, margins, options details, the _pNN
    # partitions) automatically.
    FAMILY_ROOTS: dict[str, list[str]] = {
        "index": ["index_identity", "index_basic_stats", "index_tech_stats"],
        "stock": ["stock_identity", "stock_basic_stats", "stock_tech_stats"],
        "etf": ["etf_identity", "etf_basic_stats", "etf_tech_stats"],
        "futures": ["futures_identity", "futures_basic_stats"],
    }

    def __init__(self, family: str, session: date) -> None:
        self._family = family
        self._session = session

    async def run(self) -> dict[str, int]:
        """Children-first DELETE by session date; returns per-table
        deleted-row counts. ONE transaction (asyncpg transaction()
        block) — a failure rolls the whole purge back (the family's
        session stays as-is, the caution says so)."""
        roots = self.FAMILY_ROOTS[self._family]
        ordered = await self._fk_ordered(roots)
        deleted: dict[str, int] = {}
        conn = await get_db_connection_async()
        try:
            async with conn.transaction():
                for table in ordered:
                    status = await conn.execute(
                        f'DELETE FROM stats."{table}" WHERE date = $1',
                        self._session)
                    try:
                        deleted[table] = int(status.rsplit(" ", 1)[1])
                    except (IndexError, ValueError):
                        deleted[table] = 0
        finally:
            await conn.close()
        return deleted

    @staticmethod
    async def _fk_ordered(roots: list[str]) -> list[str]:
        """The family's tables + their FK dependents (recursive, within
        stats), children before parents (asyncpg, one info query; no
        child→child FK edges exist in this schema, so the two-band
        order — all dependents, then the roots — is a valid topo
        order)."""
        conn = await get_db_connection_async()
        try:
            edges = await conn.fetch(
                """
                SELECT src.relname AS child, tgt.relname AS parent
                FROM pg_constraint rc
                JOIN pg_class src ON src.oid = rc.conrelid
                JOIN pg_class tgt ON tgt.oid = rc.confrelid
                JOIN pg_namespace ns ON ns.oid = src.relnamespace
                WHERE rc.contype = 'f' AND ns.nspname = 'stats'
                """)
        finally:
            await conn.close()
        known = set(roots)
        changed = True
        while changed:
            changed = False
            for e in edges:
                if e["parent"] in known and e["child"] not in known:
                    known.add(e["child"])
                    changed = True
        # Children first, then the non-identity stat roots (tech_stats /
        # basic_stats reference identity), then the identity parents
        # LAST (everything above references them).
        dependents = sorted(t for t in known if t not in roots)
        stat_roots = [t for t in roots if not t.endswith("_identity")]
        identity_roots = [t for t in roots if t.endswith("_identity")]
        return dependents + stat_roots + identity_roots


class BackfillOutcome(BaseModel):
    """The pass's structured detail (the JobResult line's atoms)."""

    session: str
    partial_families: list[str] = Field(default_factory=list)
    clean_families: list[str] = Field(default_factory=list)
    ratios: dict[str, str] = Field(default_factory=dict,
                                   description="per-family "
                                   "'covered of expected' line")
    purged: dict[str, dict[str, int]] = Field(default_factory=dict)
    chain_triggered: bool = False
    chain_detail: str = ""


class LatestSessionBackfillJob(MaintenanceJob):
    """Detect a partial latest-closed session, purge its rows, and
    drive the backfill-capable chain (downloads → builds → analysis,
    trading-day gate bypassed) through the shared pipeline job — the
    morning-after repair for a died-mid-write nightly fire."""

    name = "latest_session_backfill"
    description = ("Daily 07:15 missing-session backfill: check the "
                   "latest CLOSED session's coverage per daily-bar "
                   "family (index/stock/etf/futures), purge a partial "
                   "session's rows children-first (the builds' "
                   "date-gate would skip it forever), and run "
                   "downloads→builds→analysis (gate bypassed) through "
                   "the nightly-pipeline job to fill it.")

    def __init__(self, pipeline: NightlyPipelineJob) -> None:
        super().__init__()
        self._pipeline = pipeline
        self.fire_at = os.environ.get(FIRE_AT_ENV, DEFAULT_FIRE_AT)

    def _run(self) -> tuple[list[str], str]:
        started = datetime.now(timezone.utc)
        # The missed-fire tripwire first (best-effort, never fatal).
        MissedFireGuard(AUDIT_SERVICE, self.fire_at, self.name).check()
        session = expected_publish_session(eod_hour=15)
        prev = last_business_day(session - timedelta(days=1))
        try:
            # Detect + purge on the job's own event loop; the chain
            # trigger stays outside it (the pipeline pass is blocking,
            # hours-long, and its audit writes spawn their own loops).
            outcome = asyncio.run(self._detect_and_purge(session, prev))
            if outcome.partial_families:
                result = self._pipeline.run(PipelineRunRequest(
                    tiers=list(BACKFILL_TIERS), force=True))
                outcome.chain_triggered = True
                outcome.chain_detail = result.detail
        except Exception:
            PassAudit().record(AUDIT_SERVICE, started, False,
                               AUDIT_EXIT_REASON)
            raise
        detail = self._detail_line(outcome)
        ok = not outcome.partial_families or outcome.chain_triggered
        PassAudit().record(AUDIT_SERVICE, started, ok, AUDIT_EXIT_REASON)
        logger.info("latest-session backfill: %s", detail)
        return [], detail

    async def _detect_and_purge(self, session: date,
                                prev: date) -> BackfillOutcome:
        """Coverage verdicts, the cautions, and the children-first
        purge of every partial family's session rows."""
        outcome = BackfillOutcome(session=session.isoformat())
        for cov in await self._coverage(session, prev):
            outcome.ratios[cov.family] = cov.ratio_line
            if cov.is_partial:
                outcome.partial_families.append(cov.family)
            else:
                outcome.clean_families.append(cov.family)
        if not outcome.partial_families:
            return outcome
        for family in outcome.partial_families:
            purge = SessionPurge(family, session)
            outcome.purged[family] = await purge.run()
            record_service_error(
                f"latest session {session} partial in {family} "
                f"({outcome.ratios[family]}) — rows purged, backfill "
                f"chain triggered",
                dedupe_key=f"backfill:{family}",
                detail={
                    "session": session.isoformat(),
                    "family": family,
                    "covered_vs_expected": outcome.ratios[family],
                    "purged_rows": outcome.purged[family],
                },
                service=_CAUTION_SERVICE, severity="warning")
        return outcome

    async def _coverage(self, session: date,
                        prev: date) -> list[FamilyCoverage]:
        view = render_sql_view(
            "server_cluster/maintainer/backfill_latest/family_coverage",
            session=session, prev=prev)
        conn = await get_db_connection_async()
        try:
            rows = await conn.fetch(view.sql, *view.params)
        finally:
            await conn.close()
        return [FamilyCoverage(family=r["family"], covered=r["covered"],
                               expected=r["expected"]) for r in rows]

    @staticmethod
    def _detail_line(outcome: BackfillOutcome) -> str:
        parts = [f"session {outcome.session}"]
        if outcome.clean_families:
            parts.append(f"loaded: {', '.join(outcome.clean_families)}")
        if outcome.partial_families:
            parts.append("PARTIAL: " + ", ".join(
                f"{f} ({outcome.ratios[f]})"
                for f in outcome.partial_families))
            parts.append("backfill chain "
                         + ("ran: " + outcome.chain_detail
                            if outcome.chain_triggered
                            else "NOT triggered"))
        else:
            parts.append("fully loaded — nothing to do")
        return "; ".join(parts)
