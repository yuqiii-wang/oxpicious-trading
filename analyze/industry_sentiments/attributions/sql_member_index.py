"""Member-index benchmark SQL for the industry-attributions step.

In addition to the broad-market benchmarks, each industry's OWN member
indices are also inserted as benchmark rows so the per-industry attribution
bar chart shows the industry's own indices alongside the broad-market
benchmarks.

Two-phase implementation using a mapping table:
  Phase 1 (MEMBER_INDEX_MAP_POPULATE_SQL): TRUNCATE +
    INSERT...SELECT into analysis.industry_member_index_map — computes
    the composition-derived weights ONCE (~235 rows, cheap).
  Phase 2 (MEMBER_INDEX_INSERT_SQL_*): simple JOIN between the mapping
    table and stats.index_basic_stats dates — no CTE aggregation per
    date, just a cross-join expansion. Fast even for 400K+ rows.

Two variants of phase 2:
  _FULL: no date filter, plain INSERT (TRUNCATE already issued).
  _INCREMENTAL: ``ibs.date = ANY($1::date[])`` + plain INSERT.
"""
from __future__ import annotations

from analyze.industry_sentiments.attributions.config import MAP_TABLE
from analyze.industry_sentiments.attributions.sql_broad_market import (
    _format_composition_ctes,
)


# ---------------------------------------------------------------------------
#  Weight semantics (per (industry_id, member_index M, date)):
#    industry_shared_weight  = SUM over other same-industry members N
#                              (N != M) of N's weight on stocks held by
#                              BOTH N and M. Computed directly from
#                              stats.sec_composition (NOT from
#                              sec_alloc_perf_attribution, which only keeps
#                              the top-3 non-broad indices per industry as
#                              benchmarks — not enough for "all member
#                              indices"). Mirrors code_sec_shared_weight
#                              semantics (subject's OWN weight on shared
#                              stocks). Self-pair (M, M) excluded.
#    benchmark_shared_weight = M's weight on the UNION of industry member
#                              stocks (from the benchmark_shared CTE).
#                              Since M is itself a member of the industry,
#                              M's stocks are a subset of the union, so
#                              this is typically ~100 (M's total weight on
#                              its own stocks).
#
#  Broad-market codes are EXCLUDED from member_indices (they are already
#  materialized by the broad-market INSERT and would cause PK conflicts).
#  This also means BROAD_* industries (whose members are all broad-market)
#  get NO member-index rows — correct, since those indices are already
#  covered as broad-market benchmarks.
#
#  The non_this_industry_* columns stay NULL for member-index rows (the
#  return-based decomposition is only meaningful for broad-market
#  benchmarks — the merged broad-market INSERT filters broad_codes so
#  these rows are never computed).
#
#  Date dimension: stats.index_basic_stats.date for the member index
#  (dates where it has a non-NULL close). This ensures the row exists for
#  every trading day the member index has a close, so the UI can compute
#  benchmark_return on-the-fly for any selected date.

# --- Phase 1: populate the mapping table (composition weights, ONCE) -----
# TRUNCATE + INSERT...SELECT. Uses the same composition CTEs as the
# broad-market INSERT so the composition snapshot is consistent.
# {industry_filter} scopes to one industry (per-industry variant).
_MEMBER_INDEX_MAP_POPULATE_CTE = """
WITH {composition_ctes},
broad_codes AS (
    SELECT DISTINCT code
    FROM stats.sec_index_tags
    WHERE is_broad_market = TRUE
),
-- Distinct (industry_id, member_index_code) pairs, EXCLUDING broad-market
-- codes (already materialized by the broad-market INSERT; excluding them
-- avoids PK conflicts). BROAD_* industries (whose members are all
-- broad-market) thus get NO member-index rows.
-- EXCEPTION: benchmark_broadmarket is a REAL tag-defined industry whose
-- members ARE broad-market indices — its membership comes from
-- stats.sec_index_tags (the curated flagship benchmark set), NOT from the
-- primary sec_classification columns, so broad-market codes are NOT
-- excluded for it and shared weights are 0 (tag membership, not
-- composition-derived).
member_indices AS (
    SELECT DISTINCT cls.industry_id, h.code AS benchmark_code
    FROM holdings h
    JOIN stats.sec_classification cls
        ON cls.code = h.code AND cls.type = 'index'
    WHERE cls.industry_id IS NOT NULL
      AND cls.industry_id <> ''
      AND cls.is_active = TRUE
      AND cls.is_industry_not_strategy = TRUE
      {industry_filter}
      AND h.code NOT IN (SELECT code FROM broad_codes)
    UNION
    SELECT t.industry_id, t.code AS benchmark_code
    FROM stats.sec_index_tags t
    WHERE t.industry_id = 'benchmark_broadmarket'
      {tag_industry_filter}
),
-- Total weight per (industry, stock) across ALL same-industry members.
-- Used to compute industry_shared_weight WITHOUT an expensive holdings
-- self-join: for each member M, the other members' overlap with M =
-- SUM over stocks S held by M of (total_weight(S) - M.weight_pct(S)).
industry_stock_weights AS (
    SELECT
        cls.industry_id,
        h.stock_code,
        SUM(h.weight_pct) AS total_weight
    FROM holdings h
    JOIN stats.sec_classification cls
        ON cls.code = h.code AND cls.type = 'index'
    WHERE cls.industry_id IS NOT NULL
      AND cls.industry_id <> ''
      AND cls.is_active = TRUE
      AND cls.is_industry_not_strategy = TRUE
      {industry_filter}
    GROUP BY cls.industry_id, h.stock_code
),
-- industry_shared_weight for each (industry, member_index M):
--   SUM over stocks S held by M of (total_weight(S, industry) -
--   M.weight_pct(S)) = SUM over other same-industry members N of N's
--   weight on stocks shared with M. Self-pair (M, M) excluded by
--   subtracting M's own weight.
member_industry_shared AS (
    SELECT
        cls.industry_id,
        m.code AS benchmark_code,
        SUM(isw.total_weight - m.weight_pct) AS industry_shared_weight
    FROM holdings m
    JOIN stats.sec_classification cls
        ON cls.code = m.code AND cls.type = 'index'
    JOIN industry_stock_weights isw
        ON isw.industry_id = cls.industry_id
       AND isw.stock_code = m.stock_code
    WHERE cls.industry_id IS NOT NULL
      AND cls.industry_id <> ''
      AND cls.is_active = TRUE
      AND cls.is_industry_not_strategy = TRUE
      {industry_filter}
      AND m.code NOT IN (SELECT code FROM broad_codes)
    GROUP BY cls.industry_id, m.code
)
INSERT INTO {map_table}
    (industry_id, benchmark_code,
     industry_shared_weight, benchmark_shared_weight)
SELECT
    mi.industry_id,
    mi.benchmark_code,
    COALESCE(ROUND(mis.industry_shared_weight, 4), 0) AS industry_shared_weight,
    COALESCE(ROUND(bsw.benchmark_shared_weight, 4), 0) AS benchmark_shared_weight
FROM member_indices mi
LEFT JOIN member_industry_shared mis
    ON mis.industry_id = mi.industry_id
   AND mis.benchmark_code = mi.benchmark_code
LEFT JOIN benchmark_shared bsw
    ON bsw.industry_id = mi.industry_id
   AND bsw.benchmark_code = mi.benchmark_code
"""


def _build_member_index_map_sql(
    industry_filter: str = "",
    on_conflict: str = "",
    tag_industry_filter: str = "",
) -> str:
    """Build the member-index map populate INSERT variant."""
    return _MEMBER_INDEX_MAP_POPULATE_CTE.format(
        composition_ctes=_format_composition_ctes(industry_filter),
        map_table=MAP_TABLE,
        industry_filter=industry_filter,
        tag_industry_filter=tag_industry_filter,
    ) + on_conflict


# ON CONFLICT clause for the map populate INSERT. The map table is
# truncated in force mode, but in incremental mode existing rows from a
# previous run would cause a UniqueViolationError on
# pk_industry_member_index_map (industry_id, benchmark_code). DO UPDATE
# refreshes the composition-derived weights (which only change when
# sec_composition snapshots are refreshed).
_MAP_ON_CONFLICT = """
ON CONFLICT (industry_id, benchmark_code) DO UPDATE SET
    industry_shared_weight  = EXCLUDED.industry_shared_weight,
    benchmark_shared_weight = EXCLUDED.benchmark_shared_weight
"""

# All industries at once.
MEMBER_INDEX_MAP_POPULATE_SQL = _build_member_index_map_sql(
    on_conflict=_MAP_ON_CONFLICT
)


# --- Phase 2: expand mapping table to per-date rows (simple JOIN) --------
# No CTE aggregation — just a cross-join between the mapping table and
# stats.index_basic_stats dates. The (code, date) index on index_basic_stats
# makes this fast.
_MEMBER_INDEX_INSERT_BASE = """
INSERT INTO analysis.industry_attributions
    (industry_id, benchmark_code, date, attribution_type,
     industry_shared_weight, benchmark_shared_weight)
SELECT
    m.industry_id,
    m.benchmark_code,
    ibs.date,
    'trading_amt' AS attribution_type,
    m.industry_shared_weight,
    m.benchmark_shared_weight
FROM {map_table} m
JOIN stats.index_basic_stats ibs
    ON ibs.code = m.benchmark_code AND ibs.close IS NOT NULL
WHERE 1=1
    {date_filter}
    {industry_filter}
"""


def _build_member_index_insert_sql(
    date_filter: str = "",
    industry_filter: str = "",
    on_conflict: str = "",
) -> str:
    """Build a member-index INSERT (phase 2) variant."""
    return _MEMBER_INDEX_INSERT_BASE.format(
        map_table=MAP_TABLE,
        date_filter=date_filter,
        industry_filter=industry_filter,
    ) + on_conflict


# Full recompute (all industries at once) — plain INSERT.
MEMBER_INDEX_INSERT_SQL_FULL = _build_member_index_insert_sql()

# Incremental (all industries at once) — date filter, plain INSERT (target
# dates are pruned to genuinely missing ones + transaction-wrapped).
MEMBER_INDEX_INSERT_SQL_INCREMENTAL = _build_member_index_insert_sql(
    date_filter="AND ibs.date = ANY($1::date[])",
)
