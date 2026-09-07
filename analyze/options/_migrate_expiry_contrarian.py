"""One-off migration: pre-expiry contrarian metrics on
analysis.options_skewness_stats (idempotent).

1. Add the recency-aware contrarian metric columns on the gap
   (skewness − neutral) if missing:
     cross_count_20d       — neutral crossings in the trailing 20 sessions
     days_since_last_cross — sessions since the gap last crossed neutral
     gap_side_share_20d    — share of the trailing 20 sessions at/above
                             neutral, in [0,1]
2. Drop the legacy cumulative count_skewness_curve_crossed_spot
   (history-length-biased, never decays) and keep it dropped.
3. Refresh the column comments.

Mirrors the canonical DDL in database/sql/analysis/16_options.sql.
After running this, execute ``python -m analyze.options --force`` — the
new columns are 0/NULL until rebuilt and incremental mode only fills
MISSING groups, so it would not refresh existing rows.

Run via ``python -m analyze.options._migrate_expiry_contrarian``.
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(
    0,
    os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ),
)

from _common.build_commons import (  # noqa: E402
    setup_utf8_stdout,
    get_db_connection_async,
)

setup_utf8_stdout()

from _common.log_setup import setup_logging  # noqa: E402
logger = setup_logging("_migrate_expiry_contrarian")

TABLE: str = "analysis.options_skewness_stats"

DDL: list[str] = [
    f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS "
    "cross_count_20d INTEGER NOT NULL DEFAULT 0",
    f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS "
    "days_since_last_cross INTEGER NOT NULL DEFAULT 0",
    f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS "
    "gap_side_share_20d NUMERIC(6,4)",
    # The legacy cumulative counter stays retired.
    f"ALTER TABLE {TABLE} DROP COLUMN IF EXISTS "
    "count_skewness_curve_crossed_spot",
]

COMMENTS: dict[str, str] = {
    "cross_count_20d": (
        "Pre-expiry contrarian metric: number of neutral crossings in "
        "the TRAILING 20 SESSIONS of the gap (skewness − the type's "
        "neutral anchor). A crossing is a day where the gap's sign "
        "bucket (>= 0 vs < 0) differs from the previous day's; NaN gaps "
        "neither cross nor reset. Comparable across expiry groups and "
        "recency-weighted (unlike a cumulative counter). High counts = "
        "positioning contested around neutral into expiry (choppy, "
        "mean-reverting); low counts with a large gap = established "
        "one-sided positioning."
    ),
    "days_since_last_cross": (
        "Pre-expiry contrarian metric: trading days since the gap "
        "(skewness − neutral) last crossed the neutral anchor, 0 = "
        "crossed today. Freshness of the last flip: a small value marks "
        "a just-flipped regime; a large value (or the group's age in "
        "days while never crossed) marks an established, unchallenged "
        "positioning into expiry."
    ),
    "gap_side_share_20d": (
        "Pre-expiry contrarian metric: fraction of the trailing 20 "
        "sessions with the gap (skewness − neutral) at/above neutral, "
        "in [0,1]; NaN-gap days are excluded from both numerator and "
        "denominator. Measures one-sided crowding: values near 0 or 1 = "
        "persistent positioning on one side of neutral into expiry "
        "(crowded trade, contrarian fade); values near 0.5 = contested, "
        "choppy positioning."
    ),
}


async def main() -> None:
    conn = await get_db_connection_async()
    try:
        for ddl in DDL:
            await conn.execute(ddl)
            logger.info(f"    -> {ddl.split(TABLE, 1)[1].strip()[:80]} (ok)")
        # COMMENT ON COLUMN is DDL — asyncpg cannot bind parameters to DDL,
        # so the (static, self-authored) comment text is inlined with
        # single quotes escaped defensively.
        for col, comment in COMMENTS.items():
            await conn.execute(
                f"COMMENT ON COLUMN {TABLE}.{col} IS "
                f"'{comment.replace(chr(39), chr(39) * 2)}'"
            )
        logger.info(f"Migration complete: {len(DDL)} DDL statements, "
              f"{len(COMMENTS)} comments set.")
        logger.info("Next: run `python -m analyze.options --force` to "
              "rebuild all rows with the contrarian metrics.")
    finally:
        try:
            await asyncio.wait_for(conn.close(), timeout=10)
        except (asyncio.TimeoutError, Exception):
            pass


if __name__ == "__main__":
    from _common.post_check import post_check
    try:
        asyncio.run(main())
    finally:
        post_check()
