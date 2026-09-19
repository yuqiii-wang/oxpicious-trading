"""builds._commons.board_map — stats.sec_board_map population helpers.

Shared by builds.stock / builds.etf / builds.index — each build owns ONE
slice of the board table and refreshes it at the end of its own pipeline:

  builds.stock  → upsert_stock_board_rows()      sec_type='stock'
                  one row per code, pct=100: the stock's own listing board
                  (MAIN / STAR / GEM / BSE) from the LATEST
                  stats.stock_identity row, falling back to the
                  deterministic code-prefix rule when the identity column
                  is NULL/empty (stream-loader identity rows).
  builds.etf    → refresh_board_mix('etf')       sec_type='etf'
  builds.index  → refresh_board_mix('index')     sec_type='index'
                  one row per board present in the LATEST composition
                  snapshot's constituents, pct = composition-weight share
                  (denominator = constituents with a known board, so a
                  code's rows sum to ~100). ETFs WITHOUT any own snapshot
                  fall back to their tracking index's latest snapshot
                  (sec_classification.parent_index_code) — SSE ETFs have
                  no direct composition source. The same fallback is
                  established practice in the sec-composition API.

Incremental semantics (mirroring the table DDL comments in
database/sql/stats/17_sec_board_map.sql):
  stock rows  — boards are prefix-determined, so an existing row never
                goes stale; non-force runs only fill MISSING codes.
  mix rows    — a code's board SET can change between composition
                snapshots, so codes that are missing or whose stored
                max(snapshot_date) is older than the source's are
                DELETEd then re-INSERTed (upsert would leave dropped
                boards behind). force=True rebuilds the whole slice.

Pure asyncpg + set-based SQL — no pandas, safe to call from any build's
connection. Callers MUST run stage_stock_boards(conn) on the SAME
connection before a mix refresh (the mix joins the temp table).
"""
from __future__ import annotations

import logging
from typing import Any, List, Optional

logger = logging.getLogger("board_map")

# Latest-board-per-stock with the prefix fallback. DISTINCT ON over the
# (code, date) PK keeps this an index scan; the fallback reads the code
# suffix — stats.stock_identity.exchange always equals the suffix
# (verified 0 mismatches), so NULL-exchange rows are covered too.
_STOCK_BOARD_DROP_SQL = "DROP TABLE IF EXISTS tmp_stock_board;"

_STOCK_BOARD_BUILD_SQL = """
CREATE TEMP TABLE tmp_stock_board AS
WITH latest AS (
    SELECT DISTINCT ON (code)
           code, board
      FROM stats.stock_identity
     ORDER BY code, date DESC
)
SELECT code,
       COALESCE(
           NULLIF(board, ''),
           CASE
               WHEN code LIKE '%.SS' AND LEFT(code, 3) IN ('688', '689')
                    THEN 'STAR'
               WHEN code LIKE '%.SZ' AND LEFT(code, 2) = '30'
                    THEN 'GEM'
               WHEN code LIKE '%.BJ'
                    THEN 'BSE'
               WHEN code LIKE '%.SS' OR code LIKE '%.SZ'
                    THEN 'MAIN'
           END
       ) AS board
  FROM latest
"""


async def stage_stock_boards(conn: Any) -> None:
    """(Re)create the tmp_stock_board temp table on this connection.

    Must run on the SAME connection before upsert_stock_board_rows() or
    refresh_board_mix() — both join against it.
    """
    await conn.execute(_STOCK_BOARD_DROP_SQL)
    await conn.execute(_STOCK_BOARD_BUILD_SQL)


async def upsert_stock_board_rows(conn: Any, force: bool = False) -> int:
    """Write the stock slice of stats.sec_board_map; returns rows written.

    force=True  — delete the whole stock slice, reinsert every code.
    force=False — insert stock codes MISSING from the table only.
    """
    if force:
        await conn.execute(
            "DELETE FROM stats.sec_board_map WHERE sec_type = 'stock'")
        n = await conn.execute("""
            INSERT INTO stats.sec_board_map
                   (code, sec_type, board, pct, n_stocks, snapshot_date)
            SELECT code, 'stock', board, 100, 1, NULL
              FROM tmp_stock_board
             WHERE board IS NOT NULL
        """)
        logger.info("    [BOARD-MAP] stock slice rebuilt (force)")
    else:
        n = await conn.execute("""
            INSERT INTO stats.sec_board_map
                   (code, sec_type, board, pct, n_stocks, snapshot_date)
            SELECT s.code, 'stock', s.board, 100, 1, NULL
              FROM tmp_stock_board s
             WHERE s.board IS NOT NULL
               AND NOT EXISTS (
                    SELECT 1 FROM stats.sec_board_map m WHERE m.code = s.code)
        """)
    return _inserted_rows(n)


# Composition-weighted board mix for one sec_type ('etf' or 'index').
# Snapshot resolution: the code's OWN latest sec_composition snapshot,
# plus — ETFs only — the tracking index's latest snapshot for codes with
# no own snapshot. $1 = sec_type, $2 = optional code whitelist.
_MIX_BUILD_SQL = """
CREATE TEMP TABLE tmp_board_mix AS
WITH own_snap AS (
    SELECT DISTINCT ON (code)
           code, code AS comp_code, snapshot_date, source_type
      FROM stats.sec_composition
     WHERE source_type = $1
       AND ($2::text[] IS NULL OR code = ANY($2))
     ORDER BY code, snapshot_date DESC
),
index_snap AS (
    SELECT DISTINCT ON (code)
           code, snapshot_date
      FROM stats.sec_composition
     WHERE source_type = 'index'
     ORDER BY code, snapshot_date DESC
),
etf_fallback AS (
    SELECT sc.code, ls.code AS comp_code, ls.snapshot_date,
           'etf'::text AS source_type
      FROM stats.sec_classification sc
      JOIN index_snap ls ON ls.code = sc.parent_index_code
     WHERE sc.type = 'etf'
       AND sc.parent_index_code <> ''
       AND ($2::text[] IS NULL OR sc.code = ANY($2))
       AND NOT EXISTS (
            SELECT 1 FROM stats.sec_composition c WHERE c.code = sc.code)
),
latest_snap AS (
    SELECT code, comp_code, snapshot_date, source_type FROM own_snap
    UNION
    SELECT code, comp_code, snapshot_date, source_type FROM etf_fallback
    WHERE $1 = 'etf'
),
per_board AS (
    SELECT ls.code,
           ls.source_type,
           ls.snapshot_date,
           b.board,
           SUM(c.weight_pct) AS w,
           COUNT(*)          AS n
      FROM latest_snap ls
      JOIN stats.sec_composition c
        ON c.code = ls.comp_code
       AND c.snapshot_date = ls.snapshot_date
      JOIN tmp_stock_board b
        ON b.code = c.stock_code
     WHERE b.board IS NOT NULL
     GROUP BY ls.code, ls.source_type, ls.snapshot_date, b.board
),
totals AS (
    SELECT code, SUM(w) AS total
      FROM per_board
     GROUP BY code
)
SELECT pb.code,
       pb.source_type       AS sec_type,
       pb.board,
       ROUND(pb.w / t.total * 100, 2) AS pct,
       pb.n::integer        AS n_stocks,
       pb.snapshot_date
  FROM per_board pb
  JOIN totals t ON t.code = pb.code
 WHERE t.total > 0
"""

# Mix codes to refresh in incremental mode: missing from the target or
# whose stored max snapshot_date is older than the source's. $1 = sec_type.
_STALE_CODES_SQL = """
SELECT DISTINCT src.code
  FROM tmp_board_mix src
  LEFT JOIN (
      SELECT code, MAX(snapshot_date) AS max_d
        FROM stats.sec_board_map
       WHERE sec_type = $1
       GROUP BY code
  ) cur ON cur.code = src.code
 WHERE cur.code IS NULL
    OR cur.max_d < src.snapshot_date
"""


async def refresh_board_mix(
    conn: Any,
    sec_type: str,
    *,
    force: bool = False,
    codes: Optional[List[str]] = None,
) -> int:
    """Recompute + write the etf/index mix slice; returns rows written.

    sec_type  — 'etf' or 'index' (the slice this build owns).
    codes     — optional whitelist (e.g. a --code build): only those
                codes are (re)computed; incremental staleness is then
                bypassed for them (a code-scoped run always refreshes).
    Callers MUST stage_stock_boards(conn) on the same connection first.
    """
    if sec_type not in ("etf", "index"):
        raise ValueError(f"unsupported board-mix sec_type: {sec_type!r}")

    await conn.execute("DROP TABLE IF EXISTS tmp_board_mix;")
    await conn.execute(_MIX_BUILD_SQL, sec_type, codes)
    n_codes = await conn.fetchval("SELECT COUNT(DISTINCT code) FROM tmp_board_mix")

    if force or codes:
        await conn.execute(
            "DELETE FROM stats.sec_board_map WHERE sec_type = $1"
            " AND ($2::text[] IS NULL OR code = ANY($2))",
            sec_type, codes,
        )
        n = await conn.execute("""
            INSERT INTO stats.sec_board_map
                   (code, sec_type, board, pct, n_stocks, snapshot_date)
            SELECT code, sec_type, board, pct, n_stocks, snapshot_date
              FROM tmp_board_mix
        """)
        written = _inserted_rows(n)
        logger.info(f"    [BOARD-MAP] {sec_type} mix rebuilt: {n_codes:,} code(s)"
                    f"{' (forced)' if force else ''}")
    else:
        stale = [r["code"] for r in await conn.fetch(_STALE_CODES_SQL, sec_type)]
        if stale:
            await conn.execute(
                "DELETE FROM stats.sec_board_map WHERE sec_type = $1"
                " AND code = ANY($2::text[])",
                sec_type, stale,
            )
            n = await conn.execute("""
                INSERT INTO stats.sec_board_map
                       (code, sec_type, board, pct, n_stocks, snapshot_date)
                SELECT code, sec_type, board, pct, n_stocks, snapshot_date
                  FROM tmp_board_mix
                 WHERE code = ANY($1::text[])
            """, stale)
            written = _inserted_rows(n)
        else:
            written = 0
        logger.info(f"    [BOARD-MAP] {sec_type} mix refresh: "
                    f"{len(stale):,} stale/missing code(s) rewritten")
    return written


def _inserted_rows(status: Any) -> int:
    """asyncpg Execute status ('INSERT 0 123') → row count."""
    try:
        return int(str(status).split()[-1])
    except (ValueError, IndexError):
        return 0
