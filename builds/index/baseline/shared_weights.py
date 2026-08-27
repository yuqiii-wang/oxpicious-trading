"""Fetch index shared weights from sec_composition (for close estimation).

Used by the close-price estimation step in build_daily to find the best
proxy index for a missing trading day: when index A is missing a date, we
pick index B with the highest shared weight (> SHARED_WEIGHT_THRESHOLD) and
use B's daily change to estimate A's close.
"""
from __future__ import annotations

from _common.build_commons import rec_col


async def fetch_index_shared_weights(conn) -> dict:
    """Compute composition shared weight for every (index_a, index_b) pair.

    Uses the LATEST composition snapshot in stats.sec_composition for each
    index code.  For stocks held by BOTH indices, sums the weight_pct of
    index_a (the subject's shared weight).  Returns a dict:
        { (code_a, code_b): shared_weight_a }
    where shared_weight_a = Σ w_a on stocks held by both a and b.
    """
    rows = await conn.fetch("""
        WITH latest AS (
            SELECT code, source_type, MAX(snapshot_date) AS max_date
            FROM stats.sec_composition
            WHERE stock_code IS NOT NULL
              AND source_type = 'index'
            GROUP BY code, source_type
        ),
        holdings AS (
            SELECT sc.code, LEFT(sc.stock_code, 6) AS normalized_code,
                   sc.weight_pct
            FROM stats.sec_composition sc
            JOIN latest ld ON sc.code = ld.code
                          AND sc.source_type = ld.source_type
                          AND sc.snapshot_date = ld.max_date
            WHERE sc.stock_code IS NOT NULL
        )
        SELECT
            h1.code AS code_a,
            h2.code AS code_b,
            SUM(h1.weight_pct) AS shared_weight_a
        FROM holdings h1
        JOIN holdings h2 ON h1.normalized_code = h2.normalized_code
        WHERE h1.code != h2.code
        GROUP BY h1.code, h2.code
    """)
    # Whole-column zip pairing; NaN rows (sum of NULL weights) filtered out
    result: dict = {}
    for a, b, sw in zip(rec_col(rows, "code_a"),
                        rec_col(rows, "code_b"),
                        rec_col(rows, "shared_weight_a")):
        if sw == sw:  # NaN check
            result[(a, b)] = float(sw)
    return result
