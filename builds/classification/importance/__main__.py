"""builds.classification.importance — rebuild stats.sec_importance_ranks.

Per-code processing-importance rank 0-9 WITHIN each sec_type (0 = most
important; every type has its own full 0-9 scale). One row per code,
covering every stats.sec_classification code; the whole table is
TRUNCATE + rebuilt. Downloads / builds / analysis then enumerate codes
rank 0 -> 9 through ``_common.importance`` so throttled or interrupted
runs have covered the important codes first.

``sec_type`` is the label_dim SMALLINT code (``_common.labels``
SEC_TYPE_CODE: etf=0 index=1 stock=2 — same domain as the analysis
schema's converted tables). ``exchange`` is the download-service routing
key: sec_classification.exchange per code, OVERRIDDEN for curated codes
by the per-code exchange in the importance_rules JSONs (which may set
the finer ``SZSE`` = SZSE-published-index marker that routes the code
to the SZSE downloader/streamer — csindex services skip it).

TOP 2 RANKS ARE HAND-CURATED, not computed: ranks 0-1 of every sec_type
come from hardcoded code lists in
database/sec_statics/importance_rules/<sec_type>_<rank>_rank.json
(loaded via database.sec_statics.importance_rules_store). Curated codes
keep their listed rank verbatim — they override the quantitative ladder.
The computed ladder NEVER assigns ranks 0-1 (SQL emits a -1 sentinel for
curated codes; the overlay fills 0/1 in Python).

Computed bands 2-9 (authoritative copy in the DDL header of
database/sql/stats/07_sec_classification.sql):

  INDEX 2-7 NTILE(6) turnover sextiles of active real non-curated
        indices · 8 no-amount · 9 inactive + DUMMY
  ETF   2-7 NTILE(6) turnover sextiles of active non-curated ETFs ·
        8 no-amount · 9 inactive
  STOCK 2-5 max index weight >= 7/5/3/2% · 6 >=1% or ETF-held ·
        7 any composition member · 8 no membership · 9 inactive

Quantitative inputs (all monthly snapshot, trailing AMT_WINDOW_DAYS):
  stats.sec_classification (type/is_active/is_dummy/aum_yi/exchange,
  ETF parent),
  stats.sec_index_tags (broad flag, audit column only),
  stats.index_basic_stats.trading_amount / stats.etf_liquidity_margin.trading_amount,
  stats.sec_composition (per-index and per-ETF LATEST snapshots; stock
  weights INCLUDE broad indices — unlike classification parent rows).

Usage:
  python -m builds.classification.importance            # monthly guard
  python -m builds.classification.importance --force    # always rebuild

Incremental contract: without --force the run is a NO-OP when the table
already has an as_of within the current month (the nightly main_run tier
invokes it daily; it rebuilds once per month). --force always rebuild.

The :class:`ImportanceBuild` entry class (a :class:`DataBuild` subclass)
owns the runtime lifecycle. SQL-only compute (no pandas) — the whole
ranking is ONE asyncpg query + the curated-rank overlay; the result
(~15K rows, single chunk) is written with the plain upsert rung per the
db-sql-write size ladder.
"""
from __future__ import annotations

import datetime as _dt

from _common.data_pipeline import bootstrap_runtime

bootstrap_runtime()

from typing import Dict, List  # noqa: E402

from _common.data_build import DataBuild  # noqa: E402
from _common.db_commons import (  # noqa: E402
    bulk_upsert_async,
    truncate_table_async,
)
from _common.importance import save_importance_ranks_cache  # noqa: E402
from _common.labels import SEC_TYPE_CODE, SEC_TYPE_LABEL  # noqa: E402
from _common.log_setup import setup_logging  # noqa: E402
from database.sec_statics.importance_rules_store import (  # noqa: E402
    load_curated_exchanges,
    load_curated_ranks,
)

logger = setup_logging("classification_importance")

TABLE = "stats.sec_importance_ranks"
AMT_WINDOW_DAYS = 200  # calendar days ≈ 130 trading days of turnover
CURATED_SENTINEL = -1  # SQL marker for "rank comes from the JSON overlay"

# One row per code, final shape. $1 curated codes (JSON), $2 window start,
# $3 as_of. Curated codes get CURATED_SENTINEL — never a computed 0/1.
# sec_type is emitted as the label_dim SMALLINT code (SEC_TYPE_CODE).
RANK_SQL = f"""
WITH cls AS (
    SELECT DISTINCT ON (code) code, type, is_active, is_dummy,
           aum_yi::float8 AS aum_yi, exchange
      FROM stats.sec_classification
     ORDER BY code, parent_index_code
),
idx_broad AS (
    -- audit column only (is_broad_market); ranking uses the curated JSONs
    SELECT c.code, BOOL_OR(t.is_broad_market) AS is_broad
      FROM stats.sec_classification c
      JOIN stats.sec_index_tags t ON t.code = c.code
     WHERE c.type = 'index' AND NOT c.is_dummy AND c.parent_index_code = ''
     GROUP BY c.code
),
etf_broad AS (
    -- audit column only; ANY classification row of the ETF
    -- (parent_index_is_primary is name-match dependent, misses 588000 -> 000688)
    SELECT e.code
      FROM stats.sec_classification e
      JOIN stats.sec_classification pi
        ON pi.code = e.parent_index_code AND pi.type = 'index'
       AND NOT pi.is_dummy AND pi.parent_index_code = ''
      JOIN stats.sec_index_tags t
        ON t.code = e.parent_index_code AND t.is_broad_market = TRUE
     WHERE e.type = 'etf'
     GROUP BY e.code
),
idx_amt AS (
    SELECT b.code, AVG(b.trading_amount)::float8 AS amt
      FROM stats.index_basic_stats b
     WHERE b.date >= $2
     GROUP BY b.code
),
etf_amt AS (
    SELECT l.code, AVG(l.trading_amount)::float8 AS amt
      FROM stats.etf_liquidity_margin l
     WHERE l.date >= $2
     GROUP BY l.code
),
idx_pop AS (
    SELECT c.code, c.is_active, c.is_dummy, a.amt, c.exchange,
           COALESCE(g.is_broad, FALSE) AS is_broad,
           (c.code = ANY($1)) AS curated
      FROM cls c
      LEFT JOIN idx_amt a ON a.code = c.code
      LEFT JOIN idx_broad g ON g.code = c.code
     WHERE c.type = 'index'
),
idx_sextile AS (
    SELECT code, NTILE(6) OVER (ORDER BY amt DESC, code) AS band
      FROM idx_pop
     WHERE is_active AND NOT is_dummy AND NOT curated
       AND amt IS NOT NULL
),
idx_pct AS (
    SELECT code, PERCENT_RANK() OVER (ORDER BY amt) AS pct
      FROM idx_pop
     WHERE is_active AND NOT is_dummy AND amt IS NOT NULL
),
etf_pop AS (
    SELECT c.code, c.is_active, a.amt, c.aum_yi, c.exchange,
           (b.code IS NOT NULL) AS broad_parent
      FROM cls c
      LEFT JOIN etf_amt a ON a.code = c.code
      LEFT JOIN etf_broad b ON b.code = c.code
     WHERE c.type = 'etf'
),
etf_sextile AS (
    SELECT code, NTILE(6) OVER (ORDER BY amt DESC, code) AS band
      FROM etf_pop
     WHERE is_active AND amt IS NOT NULL
       AND code <> ALL($1)
),
etf_pct AS (
    SELECT code, PERCENT_RANK() OVER (ORDER BY amt) AS pct
      FROM etf_pop
     WHERE is_active AND amt IS NOT NULL
),
idx_latest AS (
    SELECT DISTINCT ON (code) code, snapshot_date
      FROM stats.sec_composition
     WHERE source_type = 'index'
     ORDER BY code, snapshot_date DESC
),
stk_w AS (
    SELECT sc.stock_code, MAX(sc.weight_pct)::float8 AS max_w
      FROM stats.sec_composition sc
      JOIN idx_latest l
        ON l.code = sc.code AND l.snapshot_date = sc.snapshot_date
     WHERE sc.source_type = 'index'
     GROUP BY sc.stock_code
),
etf_latest AS (
    SELECT DISTINCT ON (code) code, snapshot_date
      FROM stats.sec_composition
     WHERE source_type = 'etf'
     ORDER BY code, snapshot_date DESC
),
etf_held AS (
    SELECT DISTINCT sc.stock_code
      FROM stats.sec_composition sc
      JOIN etf_latest l
        ON l.code = sc.code AND l.snapshot_date = sc.snapshot_date
     WHERE sc.source_type = 'etf'
),
stk_pop AS (
    SELECT c.code, c.is_active, c.exchange,
           w.max_w AS max_w,
           (h.stock_code IS NOT NULL) AS etf_held
      FROM cls c
      LEFT JOIN stk_w w ON w.stock_code = c.code
      LEFT JOIN etf_held h ON h.stock_code = c.code
     WHERE c.type = 'stock'
),
idx_out AS (
    SELECT p.code, {SEC_TYPE_CODE["index"]}::smallint AS sec_type,
           CASE WHEN NOT p.is_active OR p.is_dummy THEN 9
                WHEN p.curated THEN -1
                WHEN p.amt IS NULL THEN 8
                ELSE s.band + 1
           END::smallint AS importance_rank,
           p.amt, pc.pct AS liquidity_pct,
           NULL::float8 AS max_w, NULL::boolean AS etf_held,
           p.is_broad, NULL::float8 AS aum_yi, p.exchange
      FROM idx_pop p
      LEFT JOIN idx_sextile s ON s.code = p.code
      LEFT JOIN idx_pct pc ON pc.code = p.code
),
etf_out AS (
    SELECT p.code, {SEC_TYPE_CODE["etf"]}::smallint AS sec_type,
           CASE WHEN NOT p.is_active THEN 9
                WHEN p.code = ANY($1) THEN -1
                WHEN p.amt IS NULL THEN 8
                ELSE s.band + 1
           END::smallint AS importance_rank,
           p.amt, pc.pct AS liquidity_pct,
           NULL::float8 AS max_w, NULL::boolean AS etf_held,
           p.broad_parent AS is_broad, p.aum_yi, p.exchange
      FROM etf_pop p
      LEFT JOIN etf_sextile s ON s.code = p.code
      LEFT JOIN etf_pct pc ON pc.code = p.code
),
stk_out AS (
    SELECT p.code, {SEC_TYPE_CODE["stock"]}::smallint AS sec_type,
           CASE WHEN NOT p.is_active THEN 9
                WHEN p.code = ANY($1) THEN -1
                WHEN p.max_w >= 7 THEN 2
                WHEN p.max_w >= 5 THEN 3
                WHEN p.max_w >= 3 THEN 4
                WHEN p.max_w >= 2 THEN 5
                WHEN p.max_w >= 1 OR p.etf_held THEN 6
                WHEN p.max_w > 0 THEN 7
                ELSE 8
           END::smallint AS importance_rank,
           NULL::float8 AS amt, NULL::float8 AS liquidity_pct,
           p.max_w, p.etf_held,
           NULL::boolean AS is_broad, NULL::float8 AS aum_yi, p.exchange
      FROM stk_pop p
)
SELECT code, sec_type, exchange, importance_rank,
       amt::numeric AS avg_trading_amount,
       liquidity_pct::numeric AS liquidity_pct,
       max_w::numeric AS max_parent_weight_pct,
       etf_held,
       is_broad AS is_broad_market,
       aum_yi::numeric AS aum_yi,
       $3::date AS as_of
  FROM (SELECT * FROM idx_out
        UNION ALL SELECT * FROM etf_out
        UNION ALL SELECT * FROM stk_out) u
"""


class ImportanceBuild(DataBuild):
    """``python -m builds.classification.importance`` — monthly rank rebuild."""

    title = "BUILD SEC IMPORTANCE RANKS  ·  curated 0-1 + computed 2-9  ·  monthly"
    component = "importance"

    def add_arguments(self, parser) -> None:
        self.add_force_arg(parser)

    def header_fields(self) -> dict:
        from _common.build_commons import TODAY_STR

        return {
            "Table": TABLE,
            "Window": f"trailing {AMT_WINDOW_DAYS}d turnover",
            "Today": TODAY_STR,
            "Mode": ("FORCE (truncate + rebuild)" if self.args.force
                     else "monthly guard (no-op when this month is built)"),
        }

    async def _already_built_this_month(self, conn) -> bool:
        first_of_month = _dt.date.today().replace(day=1)
        last = await conn.fetchval(f"SELECT MAX(as_of) FROM {TABLE}")
        return last is not None and last >= first_of_month

    @staticmethod
    def _apply_curated_overlay(
        rows: List[Dict], curated: Dict[tuple, List[str]],
        curated_exchanges: Dict[str, str],
    ) -> None:
        """Fill ranks 0/1 + curated exchanges in place from the JSON maps.

        Rows with importance_rank == CURATED_SENTINEL get their listed
        rank. Curated codes missing from the classification (stale JSON
        entries) or listed under the wrong sec_type are warned and
        skipped — the table only ever contains real classified codes.

        A curated code carrying a JSON exchange OVERRIDES the
        classification exchange (the download-service routing key — e.g.
        the 'SZSE' published-index marker); a disagreement is logged.
        """
        by_code = {r["code"]: r for r in rows}
        for (sec_type, rank), codes in sorted(curated.items()):
            want_type = SEC_TYPE_CODE[sec_type]
            applied = 0
            for code in codes:
                row = by_code.get(code)
                if row is None:
                    logger.warning("curated %s rank %d: %s not in "
                                   "sec_classification — skipped", sec_type,
                                   rank, code)
                    continue
                if row["sec_type"] != want_type:
                    logger.warning("curated %s rank %d: %s is sec_type %s "
                                   "— skipped", sec_type, rank, code,
                                   SEC_TYPE_LABEL[row["sec_type"]])
                    continue
                if row["importance_rank"] == 9:
                    logger.warning("curated %s rank %d: %s is inactive/"
                                   "dummy in classification — overriding "
                                   "to rank %d", sec_type, rank, code, rank)
                row["importance_rank"] = rank
                applied += 1
                exch = curated_exchanges.get(code)
                if exch:
                    if row["exchange"] and row["exchange"] != exch:
                        logger.info("curated %s: %s exchange %s overrides "
                                    "classification %s", sec_type, code,
                                    exch, row["exchange"])
                    row["exchange"] = exch
            logger.info("curated overlay %s rank %d: %d/%d codes applied",
                        sec_type, rank, applied, len(codes))
        leftover = [r["code"] for r in rows
                    if r["importance_rank"] == CURATED_SENTINEL]
        if leftover:
            # cannot happen (sentinel rows exist only for $1 = curated
            # codes) — a hard fail keeps the invariant loud if it ever does
            raise RuntimeError(
                f"curated sentinel rows left after overlay: {leftover[:10]}")

    async def run(self) -> None:
        conn = await self.connect_db()
        try:
            if not self.args.force and await self._already_built_this_month(conn):
                logger.info(
                    "[SKIP] %s already built this month (--force to rebuild)",
                    TABLE,
                )
                return

            curated = load_curated_ranks()
            curated_exchanges = load_curated_exchanges()
            curated_codes = sorted(
                {code for codes in curated.values() for code in codes})
            logger.info(f"[0/3] Curated top ranks from "
                  f"database/sec_statics/importance_rules: "
                  f"{len(curated_codes)} codes "
                  f"({', '.join(f'{k[0]}r{k[1]}={len(v)}' for k, v in sorted(curated.items()))}); "
                  f"{len(curated_exchanges)} curated exchange overrides")

            today = _dt.date.today()
            win_start = today - _dt.timedelta(days=AMT_WINDOW_DAYS)
            logger.info(f"[1/3] Ranking all codes (window {win_start} -> "
                  f"{today}) …")
            records = await conn.fetch(
                RANK_SQL, curated_codes, win_start, today)
            rows: List[Dict] = [dict(r) for r in records]
            logger.info(f"[1/3] {len(rows):,} ranked codes")

            logger.info("[2/3] Applying curated rank 0/1 + exchange "
                        "overlay …")
            self._apply_curated_overlay(rows, curated, curated_exchanges)

            logger.info(f"[3/3] Truncating {TABLE} + upserting "
                  f"{len(rows):,} rows …")
            await truncate_table_async(conn, TABLE)
            n = await bulk_upsert_async(
                conn, TABLE, rows, key_columns=["code"])
            logger.info(f"[3/3] wrote {n:,} rows")

            # File cache for the download tier (DB-decoupled ordering —
            # see _common.importance.RANKS_CACHE_FILE): final post-overlay
            # rows, same maps the DB now holds (ranks AND the exchange
            # download-service routing key).
            save_importance_ranks_cache(
                {r["code"]: int(r["importance_rank"]) for r in rows},
                {r["code"]: r["exchange"] for r in rows
                 if r["exchange"] is not None},
            )

            counts: Dict[tuple, int] = {}
            examples: Dict[tuple, str] = {}
            for r in rows:
                key = (r["sec_type"], r["importance_rank"])
                counts[key] = counts.get(key, 0) + 1
                examples.setdefault(key, r["code"])
            logger.info("Summary (sec_type, rank -> count, first code):")
            for key in sorted(counts):
                logger.info(f"  {SEC_TYPE_LABEL[key[0]]:5s} r{key[1]}  "
                      f"{counts[key]:6,}  e.g. {examples[key]}")
        finally:
            await conn.close()


if __name__ == "__main__":
    ImportanceBuild().execute()
