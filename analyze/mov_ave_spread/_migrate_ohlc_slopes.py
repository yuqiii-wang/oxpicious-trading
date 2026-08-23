"""One-off migration: add the roof/floor line-slope columns to
analysis.mov_ave_spreads_detail_ohlc (idempotent).

Mirrors the ALTER TABLE ... ADD COLUMN IF NOT EXISTS + COMMENT ON COLUMN
block in database/sql/analysis/03_mov_ave_spreads.sql. After this runs,
the incremental pipeline's repair predicate
(find_ohlc_repair_dates: 2nd anchor populated but slope NULL) flags every
pre-slope row for recompute with the 2nd-anchor-after-top semantics.

Run via ``python -m analyze.mov_ave_spread._migrate_ohlc_slopes``.
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

from analyze.mov_ave_spread.config import OHLC_WINDOWS  # noqa: E402

TABLE: str = "analysis.mov_ave_spreads_detail_ohlc"

DDL: list[str] = []
for w in OHLC_WINDOWS:
    DDL.append(
        f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS "
        f"high_line_slope_{w}d NUMERIC(18,6)"
    )
    DDL.append(
        f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS "
        f"low_line_slope_{w}d NUMERIC(18,6)"
    )

COMMENTS: dict[str, str] = {
    "high_line_slope_20d": (
        "Slope of the roof line through the two high anchors (high_20d "
        "close -> high_2nd_20d intraday high), in price units per trading "
        "day: (high_2nd_20d - high_20d) / (trading days between the two "
        "anchor dates). Negative = descending roof. NULL when either "
        "anchor is absent."
    ),
    "low_line_slope_20d": (
        "Slope of the floor line through the two low anchors (low_20d "
        "close -> low_2nd_20d intraday low), in price units per trading "
        "day: (low_2nd_20d - low_20d) / (trading days between the two "
        "anchor dates). Positive = ascending floor. NULL when either "
        "anchor is absent."
    ),
}
for w in OHLC_WINDOWS:
    if w == 20:
        continue
    COMMENTS[f"high_line_slope_{w}d"] = (
        f"Slope of the roof line through the two high anchors of the "
        f"{w}d window, in price units per trading day. Negative = "
        f"descending roof."
    )
    COMMENTS[f"low_line_slope_{w}d"] = (
        f"Slope of the floor line through the two low anchors of the "
        f"{w}d window, in price units per trading day. Positive = "
        f"ascending floor."
    )


async def main() -> None:
    conn = await get_db_connection_async()
    try:
        for ddl in DDL:
            await conn.execute(ddl)
            col: str = ddl.split("EXISTS ", 1)[1].split(" ", 1)[0]
            print(f"    -> ADD COLUMN {col} (ok)", flush=True)
        # COMMENT ON COLUMN is DDL — asyncpg cannot bind parameters to DDL,
        # so the (static, self-authored) comment text is inlined with
        # single quotes escaped defensively.
        for col, comment in COMMENTS.items():
            await conn.execute(
                f"COMMENT ON COLUMN {TABLE}.{col} IS "
                f"'{comment.replace(chr(39), chr(39) * 2)}'"
            )
        print(f"Migration complete: {len(DDL)} columns ensured, "
              f"{len(COMMENTS)} comments set.", flush=True)
    finally:
        try:
            await asyncio.wait_for(conn.close(), timeout=10)
        except (asyncio.TimeoutError, Exception):
            pass


if __name__ == "__main__":
    asyncio.run(main())
