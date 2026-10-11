"""One-off migration: edge-significance columns on
analysis.options_bands (idempotent).

1. ADD COLUMN new_hi_40d / new_lo_40d / edge_sig — fresh strike-grid
   expansion counts (distinct strikes present beyond the chain's
   max/min strike of 40 sessions earlier) and the edge-significance
   amplifier (a breached/approached corridor edge whose own side just
   expanded by >= 2 new strikes; UP / DOWN / BOTH / NONE).
2. Rebuild the edge_sig CHECK constraint (idempotent drop + add).
3. Set the column comments.
4. Purge analysis.options_bands so the incremental missing-pair
   detection in analysis.options recomputes every row WITH the new
   columns.

Mirrors the canonical DDL in database/sql/analysis/24_options_bands.sql.
After running this, execute ``python -m analysis.options`` (incremental)
to rebuild the table.

Run via ``python -m analysis.options._migrate_edge_sig``.
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
logger = setup_logging("_migrate_edge_sig")

TABLE: str = "analysis.options_bands"

EDGE_SIG_VALUES: list[str] = ["NONE", "UP", "DOWN", "BOTH"]

NEW_HI_COMMENT: str = (
    "Distinct strikes present on the date ABOVE the chain's max strike of "
    "40 sessions earlier: fresh UPSIDE strike-grid expansion (exchanges "
    "list new higher strikes as the underlying rallies toward the old "
    "ceiling). NULL when <40 prior sessions in history. Grids step by 2 "
    "strikes at a time, so >= 2 = one genuine expansion step."
)

NEW_LO_COMMENT: str = (
    "Mirror of new_hi_40d BELOW the chain's min strike of 40 sessions "
    "earlier (fresh DOWNSIDE grid expansion)."
)

EDGE_SIG_COMMENT: str = (
    "Edge-significance amplifier: a breached/approached corridor edge "
    "(upper: lean DOWN or EXIT_HIGH; lower: lean UP or EXIT_LOW) whose "
    "SAME side just saw fresh strike-grid expansion (>= 2 new strikes in "
    "the trailing 40 sessions) — the breach is into freshly-listed price "
    "territory, not routine corridor noise. UP = upper edge amplified "
    "(new higher strikes), DOWN = lower edge amplified (new lower "
    "strikes), BOTH = both sides, NONE = not amplified (NULL when the "
    "40-session window is incomplete). e.g. 000300 2025-08-28: EXIT_HIGH "
    "with new_hi_40d=6 -> UP."
)

DDL: list[str] = [
    f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS new_hi_40d INTEGER",
    f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS new_lo_40d INTEGER",
    f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS edge_sig TEXT",
    f"ALTER TABLE {TABLE} DROP CONSTRAINT IF EXISTS ck_options_bands_edge_sig",
    f"ALTER TABLE {TABLE} ADD CONSTRAINT ck_options_bands_edge_sig "
    f"CHECK (edge_sig IN ({', '.join(repr(v) for v in EDGE_SIG_VALUES)}))",
    # Purge so the incremental detection rebuilds every row with the new
    # columns (PK pairs all disappear -> all recomputed).
    f"DELETE FROM {TABLE}",
]

COMMENTS: dict[str, str] = {
    "new_hi_40d": NEW_HI_COMMENT,
    "new_lo_40d": NEW_LO_COMMENT,
    "edge_sig": EDGE_SIG_COMMENT,
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
        logger.info("Next: run `python -m analysis.options` to rebuild the "
              "bands rows with the edge-significance columns.")
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
