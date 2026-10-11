"""Cross-family orchestration for analysis.analysis_signals.

The per-family lifecycle (snapshot resolution, emission, transactional
writes, identity upsert) lives ON the engines — see engines._base
SignalEngine.run; this module only iterates the registry's engines and
runs the post-run is_active refresh once per sec_type."""
from __future__ import annotations

import logging

from _common.labels import SEC_TYPE_CODE

from analysis.analysis_signals.engines import RunStats, engines_for

logger = logging.getLogger(__name__)

# The post-run maintenance statement (executed with $1 = the sec_type's
# label code after every run): is_active = each (code, signal_type,
# signal_sub_type)'s LATEST end_date — the live tier's and the UI config
# menu's current threshold set. (The GPU signal_order rank that ran
# beside it went with the signal_order column's removal 2026-10-05 — the
# ranking was its only consumer. The retired cohort ohlcv_type column
# was REPLACED 2026-10-06 by is_ohlcv_boosted (the engines/ohlcv_boost
# verdict — an analysis marker, never part of the threshold set).)
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
    PRIMARY connection the engines just wrote through. ``sec_type`` is
    the label; the stored column is its SMALLINT code."""
    return await conn.execute(
        _REFRESH_IS_ACTIVE_SQL, SEC_TYPE_CODE[sec_type],
    )


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
