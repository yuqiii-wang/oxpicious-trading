"""SnapshotSelection — the incremental snapshot-resolution mixin of
SignalEngine (analyze.analysis_signals.engines._snapshots).

SnapshotSelection — now
snapshot-keyed end to end: the annual grid's ROLLING LATEST snapshot
(keyed at the sec_type's latest available data date) plus the mutable
scope rule shared with analysis_forecasts (config.mutable_dates).

The resolution is COVERAGE-COMPLETE in two dimensions (the 2026-09-28
partial-backfill leg):

  - snapshot level — a snapshot key missing from signal_strategies, or
    inside the mutable scope, emits EVERY code and population cohort;
  - (code, cohort) level — a snapshot key already written still
    re-emits the (code, population) identity triples it never covered.
    ``have`` is snapshot-granular (the first registered code marks the
    key written), so identity rows that join the registry AFTER the
    key's first emission — a newly listed index, or the 2026-09-27
    ohlcv-boost population backfill adding the boost/selection cohorts
    to already-frozen year-ends — never saw their snapshot emitted.
    Each such triple re-emits its cohort for exactly the missing codes
    (a code-scoped bucket read — a per-code pkey probe, not a whole
    -snapshot pass). A triple that legitimately registered nothing
    (every bucket failed the gates) is recorded in the
    snapshot_emissions ATTEMPTS registry at emission time, so it never
    re-scopes: the diff runs identity MINUS covered MINUS attempted —
    without the attempts leg the legit-empty triples (routine for the
    thin ohlcv-selection subset) would re-emit as no-ops every run.
"""
from __future__ import annotations

from datetime import date
from typing import NamedTuple

from _common.db_commons import chunked_dml_by_key_async

from analyze.analysis_forecasts.config import (
    TABLE_IDENTITIES,
    mutable_dates,
)
from analyze.analysis_signals.config import (
    TABLE_EMISSIONS,
    TABLE_HISTORY,
    TABLE_STRATEGIES,
)


class SnapshotScope(NamedTuple):
    """One target snapshot's emission scope: the stat_date plus, per
    declared population cohort, the codes to emit — None = EVERY code
    the snapshot's identities carry (the full emission; the cohort
    reads run unscoped), a list = the PARTIAL BACKFILL of exactly
    those codes, an empty list = the cohort is already covered (its
    emission pass is skipped)."""

    stat_date: date
    codes_by_population: dict[str | None, list[str] | None]


class SnapshotSelection:
    """resolve_snapshots / sweep_stale_snapshots — which stat_dates
    this family (re-)emits for a sec_type, and which retired keys to
    purge. Requires the engine's ``bucket`` and ``signal_type``
    identity attrs."""

    bucket: str
    signal_type: str

    async def resolve_snapshots(
        self,
        conn,
        sec_type: str,
        force: bool,
        snapshots: int | None,
    ) -> list[SnapshotScope]:
        """The scoped snapshot targets for the family, ascending.

        The target snapshots are the stat_dates PRESENT in
        analysis_forecasts' identities registry (bucket-filtered) —
        signals are produced ONLY for snapshots the forecasts already
        own, never re-derived. Optionally capped to the newest N
        (--snapshots).

        FULL emission = the snapshots MISSING from signal_strategies
        plus the MUTABLE SCOPE among the present ones
        (config.mutable_dates — the ROLLING LATEST snapshot, always,
        plus the newest completed year-end while its 20-trading-day
        forward windows are still unrealized). A snapshot written while
        its long-horizon mixed legs were incomplete is deleted +
        re-emitted on every run until it freezes; completed year-ends
        outside the mutable scope are all-history and never re-emitted.
        --force makes every present snapshot a full target (the caller
        purges first).

        PARTIAL BACKFILL = the already-written snapshot keys whose
        (code, population) identity coverage has holes — see the module
        docstring. Each becomes a scope emitting only the missing
        codes, per cohort."""
        rows = await conn.fetch(
            f"SELECT DISTINCT stat_date FROM {TABLE_IDENTITIES} "
            f"WHERE sec_type = $1 AND bucket = $2 ORDER BY stat_date",
            sec_type, self.bucket,
        )
        present: list[date] = [r["stat_date"] for r in rows]
        if snapshots is not None:
            present = present[-snapshots:]

        if force:
            return [
                SnapshotScope(
                    m, {p: None for p in self.populations},
                )
                for m in present
            ]

        written_rows = await conn.fetch(
            f"SELECT DISTINCT end_date FROM {TABLE_STRATEGIES} "
            f"WHERE sec_type = $1 AND signal_type = $2",
            sec_type, self.signal_type,
        )
        have: set[date] = {r["end_date"] for r in written_rows}
        refresh = mutable_dates(set(present), date.today())
        full = [
            m for m in present if m not in have or m in refresh
        ]
        full_set = set(full)
        candidates = [m for m in present if m in have and m not in full_set]
        scopes = [
            SnapshotScope(m, {p: None for p in self.populations})
            for m in full
        ]
        scopes += await self._resolve_partial_backfill(
            conn, sec_type, candidates,
        )
        return scopes

    async def _resolve_partial_backfill(
        self,
        conn,
        sec_type: str,
        candidates: list[date],
    ) -> list[SnapshotScope]:
        """The partial-backfill scopes for the already-written snapshot
        keys: ONE grouped scan per side (identities and strategies both
        aggregate their (code, cohort) date sets — code-grouped, so the
        hash-partitioned reads prune per partition instead of the
        pathological un-prunable NOT-EXISTS join; the attempts registry
        aggregates the same shape), then the coverage diff per (code,
        population cohort) in Python — the strategies table's
        ohlcv_type verdict mirrors the identity registry's, and the
        attempts registry stores the cohort under its '' sentinel for
        the NULL parent. A triple already ATTEMPTED (this registry —
        see the module docstring) never re-scopes."""
        if not candidates:
            return []
        candidate_set = set(candidates)
        id_rows = await conn.fetch(
            f"SELECT i.code, i.ohlcv_type, array_agg("
            f"DISTINCT i.stat_date ORDER BY i.stat_date) AS dates "
            f"FROM {TABLE_IDENTITIES} i "
            f"WHERE i.sec_type = $1 AND i.bucket = $2 "
            f"GROUP BY 1, 2",
            sec_type, self.bucket,
        )
        covered_rows = await conn.fetch(
            f"SELECT s.code, s.ohlcv_type, array_agg("
            f"DISTINCT s.end_date ORDER BY s.end_date) AS dates "
            f"FROM {TABLE_STRATEGIES} s "
            f"WHERE s.sec_type = $1 AND s.signal_type = $2 "
            f"GROUP BY 1, 2",
            sec_type, self.signal_type,
        )
        attempted_rows = await conn.fetch(
            f"SELECT e.code, e.ohlcv_type, array_agg("
            f"DISTINCT e.stat_date ORDER BY e.stat_date) AS dates "
            f"FROM {TABLE_EMISSIONS} e "
            f"WHERE e.sec_type = $1 AND e.signal_type = $2 "
            f"GROUP BY 1, 2",
            sec_type, self.signal_type,
        )

        def _by_code_pop(rows) -> dict[tuple[str, str | None], set[date]]:
            """(code, cohort) → dates; '' / NULL cohort rows → the None
            parent key."""
            keyed: dict[tuple[str, str | None], set[date]] = {}
            for r in rows:
                keyed.setdefault((r["code"], r["ohlcv_type"] or None), set()) \
                    .update(r["dates"])
            return keyed

        covered = _by_code_pop(covered_rows)
        attempted = _by_code_pop(attempted_rows)
        missing: dict[date, dict[str | None, list[str]]] = {}
        for r in id_rows:
            key = (r["code"], r["ohlcv_type"])
            gaps = (
                (candidate_set & set(r["dates"]))
                - covered.get(key, set())
                - attempted.get(key, set())
            )
            for d in gaps:
                missing.setdefault(d, {}).setdefault(
                    r["ohlcv_type"], [],
                ).append(r["code"])
        return [
            SnapshotScope(
                d,
                {p: missing[d].get(p, []) for p in self.populations},
            )
            for d in sorted(missing)
        ]

    async def sweep_stale_snapshots(self, conn, sec_type: str) -> int:
        """Delete the family's rows whose end_date is NOT a stat_date
        present in analysis_forecasts' identities registry — the
        ROLLING LATEST key's predecessor (yesterday's key once today's
        has taken over) and any legacy keys. Runs BEFORE
        resolve_snapshots so a retired key can neither suppress a
        re-emission (it counts as 'have') nor linger. The retired
        snapshot's HISTORY rows live in its calendar year (the
        ownership interval) — deleted with it, as are its ATTEMPTS
        stamps (a re-registered key must re-emit from a clean slate).
        The deletes are CHUNKED
        BY PARTITION KEY (the bulk-write rule's delete flavor): the
        retired scope's rows are counted per code and deleted in
        ~100K-row whole-code transactions (both tables of a chunk in
        one transaction), replica-lag throttle between chunks. Returns
        the number of retired snapshot keys."""
        id_rows = await conn.fetch(
            f"SELECT DISTINCT stat_date FROM {TABLE_IDENTITIES} "
            f"WHERE sec_type = $1 AND bucket = $2",
            sec_type, self.bucket,
        )
        present: set[date] = {r["stat_date"] for r in id_rows}
        have_rows = await conn.fetch(
            f"SELECT DISTINCT end_date FROM {TABLE_STRATEGIES} "
            f"WHERE sec_type = $1 AND signal_type = $2",
            sec_type, self.signal_type,
        )
        stale = sorted({r["end_date"] for r in have_rows} - present)
        # The attempts registry's retired keys: deleted whole (one row
        # per (code, cohort) — no chunking needed).
        if stale:
            await conn.execute(
                f"DELETE FROM {TABLE_EMISSIONS} "
                f"WHERE sec_type = $1 AND signal_type = $2 "
                f"AND stat_date = ANY($3::date[])",
                sec_type, self.signal_type, stale,
            )
        for d in stale:
            first = date(d.year, 1, 1)
            next_year = date(d.year + 1, 1, 1)
            # per-code doomed-row counts across BOTH tables (the
            # chunker's weights; each query carries its own args)
            weights: dict[str, int] = {}
            for sql, args in (
                (f"SELECT code, count(*) AS n FROM {TABLE_STRATEGIES} "
                 f"WHERE sec_type = $1 AND signal_type = $2 "
                 f"AND end_date = $3 GROUP BY code",
                 (sec_type, self.signal_type, d)),
                (f"SELECT code, count(*) AS n FROM {TABLE_HISTORY} "
                 f"WHERE sec_type = $1 AND signal_type = $2 "
                 f"AND date >= $3 AND date < $4 GROUP BY code",
                 (sec_type, self.signal_type, first, next_year)),
            ):
                for r in await conn.fetch(sql, *args):
                    weights[r["code"]] = weights.get(r["code"], 0) + r["n"]
            await chunked_dml_by_key_async(
                conn,
                statements=[
                    (f"DELETE FROM {TABLE_STRATEGIES} "
                     f"WHERE sec_type = $1 AND signal_type = $2 "
                     f"AND end_date = $3",
                     " AND code = ANY(${ph}::text[])",
                     (sec_type, self.signal_type, d)),
                    (f"DELETE FROM {TABLE_HISTORY} "
                     f"WHERE sec_type = $1 AND signal_type = $2 "
                     f"AND date >= $3 AND date < $4",
                     " AND code = ANY(${ph}::text[])",
                     (sec_type, self.signal_type, first, next_year)),
                ],
                params=(),
                weighted_keys=list(weights.items()),
            )
        return len(stale)
