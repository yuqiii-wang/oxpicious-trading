"""One-off migration: exit-quality columns on analysis.options_bands
(idempotent).

Re-study 2026-10-05 (temp_scripts/study_corridor_events_v2.py, ~4,700
underlying-days): the stored `event` is a one-day binary cross of the
PRIOR session's corridor edges — ~50% of exits breach by less than
0.5x the day's own fluctuation (edge re-centred onto spot) and CONTINUE
in the breach direction, while deep breaches (>= 1.5x sigma20) are the
capitulation flush that snaps back (pooled 80% up, mean 5-session
+2.0%); a low-side exit that also closes below the chain-dominant PUT
wall's low while that wall was ACTIVE is the strongest cell (80% up,
mean +3.12%).

1. ADD COLUMN sigma20 / dev_edge_pct / dev_z / exit_class /
   wall_side_broken / wall_state_prior.
2. Rebuild the exit_class / wall_state_prior CHECK constraints
   (idempotent drop + add).
3. Set the column comments.
4. Purge analysis.options_bands so the incremental missing-pair
   detection in analyze.options recomputes every row WITH the new
   columns.

Mirrors the canonical DDL in database/sql/analysis/24_options_bands.sql.
After running this, execute ``python -m analyze.options`` (incremental)
to rebuild the table.

Run via ``python -m analyze.options._migrate_exit_quality``.
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
logger = setup_logging("_migrate_exit_quality")

TABLE: str = "analysis.options_bands"

EXIT_CLASS_VALUES: list[str] = ["FLUSH", "BREAK", "NOISE"]
WALL_STATE_VALUES: list[str] = ["ACTIVE", "ERODED", "BREACHED"]

SIGMA20_COMMENT: str = (
    "Trailing 20-session std of daily % returns (the spot fluctuation "
    "unit): the normalizer that separates sub-fluctuation edge noise "
    "from genuine breaches. NULL on the first 20 sessions of a history."
)

DEV_EDGE_COMMENT: str = (
    "Exit depth beyond the PRIOR session's corridor edge, % of spot "
    "(positive = outside): EXIT_LOW = (prior_low - close)/close*100, "
    "EXIT_HIGH = (close - prior_high)/close*100; NULL on non-exit days."
)

DEV_Z_COMMENT: str = (
    "dev_edge_pct / sigma20: breach depth in fluctuation units. "
    "Re-study (2026-10-05, temp_scripts/study_corridor_events_v2.py, "
    "~4,700 underlying-days): dev_z < 0.5 exits CONTINUE in the breach "
    "direction (low side 29% up, mean 5-session -0.71% — half of all "
    "exits are this noise), dev_z >= 1.5 flushes snap back (80% up, "
    "mean +2.0%; per underlying 000300 87.5% / 000852 / 000016 66.7%)."
)

EXIT_CLASS_COMMENT: str = (
    "Exit-quality class by dev_z: FLUSH (>= 1.5, capitulation-grade "
    "breach — the validated snap-back cell), BREAK (0.5..1.5, genuine "
    "but unresolved), NOISE (< 0.5, sub-fluctuation cross — "
    "continuation-leaning, not a level signal). NULL on non-exit days "
    "or when sigma20 is unknown."
)

WALL_SIDE_COMMENT: str = (
    "1 = the exit also closed beyond the PRIOR session's chain-dominant "
    "wall edge on the exit side (the defended insurance floor/cap gave "
    "way — strongest observed cell: EXIT_LOW & floor broken & wall "
    "ACTIVE = 80% up, mean 5-session +3.12%), 0 = the wall held, NULL "
    "on non-exit days or when no eligible wall existed."
)

WALL_STATE_COMMENT: str = (
    "Prior-session lifecycle state (ACTIVE/ERODED/BREACHED) of the "
    "chain-dominant wall on the exit side: an ACTIVE wall breaking "
    "carries the insurance-capitulation reading; NULL when no eligible "
    "wall existed that prior session."
)

DDL: list[str] = [
    f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS sigma20 NUMERIC(10,4)",
    f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS dev_edge_pct NUMERIC(10,4)",
    f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS dev_z NUMERIC(8,3)",
    f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS exit_class TEXT",
    f"ALTER TABLE {TABLE} DROP CONSTRAINT IF EXISTS ck_options_bands_exit_class",
    f"ALTER TABLE {TABLE} ADD CONSTRAINT ck_options_bands_exit_class "
    f"CHECK (exit_class IN ({', '.join(repr(v) for v in EXIT_CLASS_VALUES)}))",
    f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS wall_side_broken INTEGER",
    f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS wall_state_prior TEXT",
    f"ALTER TABLE {TABLE} DROP CONSTRAINT IF EXISTS ck_options_bands_wall_state_prior",
    f"ALTER TABLE {TABLE} ADD CONSTRAINT ck_options_bands_wall_state_prior "
    f"CHECK (wall_state_prior IN ({', '.join(repr(v) for v in WALL_STATE_VALUES)}))",
    # Purge so the incremental detection rebuilds every row with the new
    # columns (PK pairs all disappear -> all recomputed).
    f"DELETE FROM {TABLE}",
]

COMMENTS: dict[str, str] = {
    "sigma20": SIGMA20_COMMENT,
    "dev_edge_pct": DEV_EDGE_COMMENT,
    "dev_z": DEV_Z_COMMENT,
    "exit_class": EXIT_CLASS_COMMENT,
    "wall_side_broken": WALL_SIDE_COMMENT,
    "wall_state_prior": WALL_STATE_COMMENT,
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
        logger.info("Next: run `python -m analyze.options` to rebuild the "
              "bands rows with the exit-quality columns.")
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
