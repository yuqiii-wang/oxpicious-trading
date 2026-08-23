"""One-off migration: per-greek skew redesign on
analysis.options_skewness_stats (idempotent).

1. Widen the skewness value columns NUMERIC(10,2) -> NUMERIC(10,4) —
   the new greek_* metrics are small ratios (dpcr ~0.5, balances in
   [-1, 1]) whose signal dies at 2 decimals near the neutral anchor
   (e.g. vega balance -0.0042 rounds to -0.00, killing the
   crossed-neutral detection).
2. Purge ALL legacy greek_* rows (the old per-side ATM-normalized
   centroid semantics, incl. the removed greek_theta/greek_rho) so the
   incremental missing-group detection in analyze.options recomputes
   them with the new PAIR-level semantics.
3. Rebuild the skew_type CHECK constraint with the three remaining
   greek types (delta/gamma/vega).
4. Refresh the skew_type / skewness column comments.

Mirrors the canonical DDL in database/sql/analysis/16_options.sql.
After running this, execute ``python -m analyze.options`` (incremental)
to rebuild the greek_* rows.

Run via ``python -m analyze.options._migrate_greek_skew``.
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

from analyze.options.config import GREEK_SKEW_TYPES  # noqa: E402

TABLE: str = "analysis.options_skewness_stats"

ALL_TYPES: list[str] = ["oi_moneyness", "iv_smile"] + GREEK_SKEW_TYPES

# Value columns widened to 4 decimals (see module docstring).
_NUM_COLS: list[str] = [
    "skewness",
    "skewness_ma5", "skewness_ma20", "skewness_ma60",
    "skewness_std5", "skewness_std20", "skewness_std60",
    "gap_skewness_vs_spot_ma5", "gap_skewness_vs_spot_ma20",
    "gap_skewness_vs_spot_ma60",
    "gap_skewness_vs_spot_slope",
    "gap_skewness_vs_spot_ma5_slope", "gap_skewness_vs_spot_ma20_slope",
    "gap_skewness_vs_spot_ma60_slope",
    "corr_skewness_ma5_vs_spot_ma5",
    "corr_skewness_ma20_vs_spot_ma20",
    "corr_skewness_ma60_vs_spot_ma60",
]

DDL: list[str] = (
    [
        f"ALTER TABLE {TABLE} ALTER COLUMN {c} TYPE NUMERIC(10,4)"
        for c in _NUM_COLS
    ]
    + [
        # Purge legacy greek_* rows (old semantics + removed theta/rho) so
        # incremental detection rebuilds them with the new pair-level
        # metrics.
        f"DELETE FROM {TABLE} WHERE skew_type LIKE 'greek_%'",
        f"ALTER TABLE {TABLE} DROP CONSTRAINT IF EXISTS "
        f"ck_options_skewness_stats_skew_type",
        f"ALTER TABLE {TABLE} ADD CONSTRAINT "
        f"ck_options_skewness_stats_skew_type "
        f"CHECK (skew_type IN ({', '.join(repr(t) for t in ALL_TYPES)}))",
    ]
)

SKEW_TYPE_COMMENT: str = (
    "Data source of the skew metric: oi_moneyness = OI-weighted mean "
    "moneyness (strike/spot, positioning); iv_smile = OI-weighted 3rd "
    "moment of implied vol across strikes (pricing, from "
    "stats.options_greeks); greek_delta = delta-weighted put/call OI "
    "ratio dpcr (whole chain, neutral 0.5 — the delta-weighted "
    "refinement of the plain put/call ratio); greek_gamma = normalized "
    "GEX-style call-minus-put gamma balance (whole chain, neutral 0; "
    "call gamma positive / put gamma negative per the "
    "dealer-positioning sign convention); greek_vega = OTM-wing vega "
    "balance (calls 0<delta<0.5 vs puts -0.5<delta<0, neutral 0 — the "
    "open-interest mirror of the 25d risk reversal). greek_* rows are "
    "PAIR-level: the CALL and PUT rows of the same (date, underlying, "
    "expiry) hold the SAME value. greek_theta/greek_rho removed (no "
    "industry-standard positioning skew: theta is collinear with gamma "
    "by construction, rho is negligible for short-dated options)."
)

SKEWNESS_COMMENT: str = (
    "Daily raw value of the skew_type's metric (all skewness_ma*/std* "
    "columns are rolling stats of it): oi_moneyness = OI-wtd mean "
    "moneyness (K/S); iv_smile = OI-wtd 3rd moment of IV; greek_delta = "
    "delta-wtd put/call OI ratio in [0,1] (neutral 0.5); greek_gamma / "
    "greek_vega = call-vs-put balances in [-1,1] (neutral 0). gap "
    "columns are measured from the type's neutral anchor (1 / 0.5 / 0)."
)

COMMENTS: dict[str, str] = {
    "skew_type": SKEW_TYPE_COMMENT,
    "skewness": SKEWNESS_COMMENT,
}


async def main() -> None:
    conn = await get_db_connection_async()
    try:
        for ddl in DDL:
            await conn.execute(ddl)
            print(f"    -> {ddl.split(TABLE, 1)[1].strip()[:80]} (ok)",
                  flush=True)
        # COMMENT ON COLUMN is DDL — asyncpg cannot bind parameters to DDL,
        # so the (static, self-authored) comment text is inlined with
        # single quotes escaped defensively.
        for col, comment in COMMENTS.items():
            await conn.execute(
                f"COMMENT ON COLUMN {TABLE}.{col} IS "
                f"'{comment.replace(chr(39), chr(39) * 2)}'"
            )
        print(f"Migration complete: {len(DDL)} DDL statements, "
              f"{len(COMMENTS)} comments set.", flush=True)
        print("Next: run `python -m analyze.options` to rebuild the "
              "greek_* rows with the new pair-level metrics.", flush=True)
    finally:
        try:
            await asyncio.wait_for(conn.close(), timeout=10)
        except (asyncio.TimeoutError, Exception):
            pass


if __name__ == "__main__":
    asyncio.run(main())
