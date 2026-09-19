"""Cross-family orchestration for analyze.analysis_signals.

The per-family lifecycle (month resolution, emission, transactional
writes, identity upsert) lives ON the engines — see engines._base
SignalEngine.run; this module only iterates the registry's engines and
runs the post-run active/rank refresh once per sec_type."""
from __future__ import annotations

import logging
from pathlib import Path

from analyze.analysis_signals.config import (
    SQL_IS_ACTIVE,
    SQL_SIGNAL_ORDER,
)
from analyze.analysis_signals.engines import RunStats, engines_for

logger = logging.getLogger(__name__)

# The post-run maintenance SQL (02_is_active / 03_signal_order_rank —
# $1 = sec_type), loaded from the repo's SQL tree: history-tier rules
# live in SQL, Python only loads and runs them.
_SQL_DIR = (
    Path(__file__).resolve().parents[3]
    / "database" / "sql" / "analysis" / "analysis_signals"
)


def _sql_text(name: str) -> str:
    return (_SQL_DIR / name).read_text(encoding="utf-8")


async def process_sec_type(
    conn,
    sec_type: str,
    *,
    force: bool = False,
    metrics: frozenset[str] | None = None,
    months: int | None = None,
) -> dict[str, RunStats]:
    """Run every selected family's engine for the sec_type, then
    refresh is_active + signal_order over the whole sec_type once.
    Returns {stage_key: RunStats} for the families that ran."""
    stats: dict[str, RunStats] = {}
    ran_any = False
    for engine in engines_for(metrics):
        run = await engine.run(
            conn, sec_type, force=force, months=months,
        )
        stats[engine.stage_key] = run
        ran_any = ran_any or run.months > 0

    if ran_any or force:
        await conn.execute(_sql_text(SQL_IS_ACTIVE), sec_type)
        await conn.execute(_sql_text(SQL_SIGNAL_ORDER), sec_type)
    return stats
