"""Cross-family orchestration for analyze.analysis_signals.

The per-family lifecycle (snapshot resolution, emission, transactional
writes, identity upsert) lives ON the engines — see engines._base
SignalEngine.run; this module only iterates the registry's engines and
runs the post-run is_active refresh once per sec_type."""
from __future__ import annotations

import logging

from analyze.analysis_signals.engines import RunStats, engines_for

logger = logging.getLogger(__name__)

# The post-run maintenance statement (executed with $1 = sec_type after
# every run): is_active = each (code, signal_type, signal_sub_type)'s
# LATEST end_date — the live tier's and the UI config menu's current
# threshold set. (The GPU signal_order rank that ran beside it went
# with the signal_order column's removal 2026-10-05 — the ranking was
# its only consumer. The retired cohort ohlcv_type term went with the
# cohorts: the column is unwritten/NULL everywhere now.)
_REFRESH_IS_ACTIVE_SQL = """
UPDATE analysis_signals.signal_thresholds s
SET is_active = (s.end_date = latest.max_end)
FROM (
    SELECT code, signal_type, signal_sub_type, MAX(end_date) AS max_end
    FROM analysis_signals.signal_thresholds
    WHERE sec_type = $1
    GROUP BY code, signal_type, signal_sub_type
) latest
WHERE s.sec_type = $1
  AND s.code = latest.code
  AND s.signal_type = latest.signal_type
  AND s.signal_sub_type = latest.signal_sub_type
"""


async def refresh_is_active(conn, sec_type: str) -> str:
    """Stamp is_active over the sec_type's threshold rows (the current
    threshold set = each config's LATEST end_date row). Must run on the
    PRIMARY connection the engines just wrote through."""
    return await conn.execute(_REFRESH_IS_ACTIVE_SQL, sec_type)


async def process_sec_type(
    conn,
    sec_type: str,
    *,
    force: bool = False,
    metrics: frozenset[str] | None = None,
    snapshots: int | None = None,
    codes: frozenset[str] | None = None,
) -> dict[str, RunStats]:
    """Run every selected family's engine for the sec_type, then
    refresh is_active over the whole sec_type once. ``codes``
    optionally scopes the universe (the study-scope rule).
    Returns {stage_key: RunStats} for the families that ran."""
    stats: dict[str, RunStats] = {}
    ran_any = False
    for engine in engines_for(metrics):
        run = await engine.run(
            conn, sec_type, force=force, snapshots=snapshots, codes=codes,
        )
        stats[engine.stage_key] = run
        ran_any = ran_any or run.snapshots > 0

    if ran_any or force:
        await refresh_is_active(conn, sec_type)
    return stats
