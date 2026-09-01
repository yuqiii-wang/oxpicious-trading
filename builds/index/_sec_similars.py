"""build_sec_similars step — populates stats.sec_similars from index/ETF
compositions + sec_classification.

stats.sec_similars (per (composition_snapshot_date, code, sec_type)):
  For each subject (index or ETF) A with a composition snapshot on date D,
  finds:
    - top-5 similar CODES (individual indices/ETFs) by mutual sharing weight
    - top-5 similar INDUSTRY-CLASSIFIED peer codes by mutual sharing weight,
      greedily selected so the 5 peers come from 5 DIFFERENT industries
    - top-5 DISSIMILAR industry-classified peer codes (lowest mutual sharing
      weight, with classification-distance tie-breaker)

  sec_type: 'index' and 'etf'. PK includes sec_type so both coexist.

  Sharing weight is MUTUAL and symmetric:
      shared_weight_a = SUM(A.weight_pct) over stocks held by BOTH A and B
      shared_weight_b = SUM(B.weight_pct) over stocks held by BOTH A and B
      mutual_sharing_weight = (shared_weight_a + shared_weight_b) / 2

  Industry-classified peers: the "industry_code" columns store SEC CODES
  (individual index/ETF codes), NOT industry_ids. The "industry" qualifier
  means the peer pool is filtered to securities where
  sec_classification.is_industry_not_strategy=true. The subject itself can be
  either industry-primary or strategy-primary. Comparison is code-vs-code
  (same mutual formula), just restricted to industry-classified peers.

  Similar industry-classified peers — DISTINCT-INDUSTRY greedy selection:
  The 1st pick is the peer with the highest mutual sharing weight. The 2nd
  pick is the best peer from a DIFFERENT industry_id than the 1st. The 3rd
  pick is the best peer from a different industry_id than both the 1st and
  2nd, and so on through the 5th. This is implemented as: rank peers within
  each industry (best peer per industry wins), then rank those best-per-
  industry peers by mutual DESC. The top-5 give the 5 most-similar peers
  from 5 different industries.

  Dissimilar industry-classified peers: ranked by LOWEST mutual sharing weight
  (ASC). Tie-breaker: prefer different sector than the subject, then different
  industry, maximizing classification distance.

  Date granularity: `date` is the composition snapshot_date (one row per
  (snapshot_date, code, sec_type)), NOT a trading day.

  Point-in-time: for a subject snapshot date D, each comparison code B
  contributes from its LATEST composition snapshot with snapshot_date <= D.

  stock_code normalization: LEFT(stock_code, 6) to ignore .SS/.SZ suffixes.

Incremental mode (default): only composition snapshot dates present in
stats.sec_composition but missing from stats.sec_similars (for the given
sec_type) are (re)computed.

--date mode (forced_date set): the missing-date skip is bypassed — the
forced snapshot date is recomputed even when already present (existing rows
are refreshed through the upsert write path; no truncation, no deletes).
The forced date is validated once against the UNION of composition snapshot
dates of both sec_types (exits(1) when NO sec_type has it); a sec_type
without a snapshot at the forced date is a logged no-op.

--force mode: truncate sec_similars WHERE sec_type=<sec_type>, then recompute.
"""
from typing import Optional, Set

import datetime

import pandas as pd

from _common.build_commons import (
    copy_or_upsert_split_async,
    forced_date_scope,
    rec_col,
    rec_cols,
)
from builds._commons.row_emission import records_from_frame

TABLE = "stats.sec_similars"
SOURCE_TABLE = "stats.sec_composition"

# Source types / sec types to build, in execution order.
SEC_TYPES = ("index", "etf")


# Combined SQL: similar codes + similar industries (distinct-industry) +
# dissimilar industries in one query, pivoted into the wide sec_similars
# table format.
#
# Parameterized by {src} (source table), {source_type} (sec_composition
# source_type), and {sec_type} (sec_classification type + output sec_type).
#
# Common CTEs (target_dates, eff, holdings, subjects, subj_class) are shared.
# Three independent ranking CTEs (ranked, similar_ind_ranked,
# dissimilar_ind_ranked) are LEFT JOINed in the final SELECT.
_SQL_SEC_SIMILARS_TEMPLATE = """
WITH target_dates(d) AS (
    SELECT unnest($1::date[])
),
eff AS (
    SELECT td.d, sc.code, MAX(sc.snapshot_date) AS eff_date
    FROM target_dates td
    JOIN {src} sc
      ON sc.source_type = '{source_type}'
     AND sc.stock_code IS NOT NULL
     AND sc.snapshot_date <= td.d
    GROUP BY td.d, sc.code
),
holdings AS (
    SELECT e.d, e.code,
           LEFT(sc.stock_code, 6) AS normalized_code,
           sc.weight_pct
    FROM eff e
    JOIN {src} sc
      ON sc.code = e.code
     AND sc.source_type = '{source_type}'
     AND sc.stock_code IS NOT NULL
     AND sc.snapshot_date = e.eff_date
),
subjects AS (
    SELECT DISTINCT td.d, sc.code
    FROM target_dates td
    JOIN {src} sc
      ON sc.source_type = '{source_type}'
     AND sc.stock_code IS NOT NULL
     AND sc.snapshot_date = td.d
),
subj_class AS (
    SELECT s.d, s.code,
           sc.sector_id, sc.industry_id, sc.is_industry_not_strategy
    FROM subjects s
    LEFT JOIN stats.sec_classification sc
      ON sc.code = s.code AND sc.type = '{sec_type}'
),

-- === SIMILAR CODES (top-5 individual indices/ETFs) ===
pairs AS (
    SELECT s.d, s.code AS code_a, h2.code AS code_b,
           SUM(h1.weight_pct) AS shared_weight_a,
           SUM(h2.weight_pct) AS shared_weight_b
    FROM subjects s
    JOIN holdings h1 ON h1.d = s.d AND h1.code = s.code
    JOIN holdings h2 ON h2.d = s.d AND h2.normalized_code = h1.normalized_code
    WHERE h2.code <> s.code
    GROUP BY s.d, s.code, h2.code
),
ranked AS (
    SELECT d, code_a, code_b,
           (shared_weight_a + shared_weight_b) / 2.0 AS mutual_shared_weight,
           ROW_NUMBER() OVER (
               PARTITION BY d, code_a
               ORDER BY (shared_weight_a + shared_weight_b) / 2.0 DESC, code_b
           ) AS rn
    FROM pairs
),

-- === INDUSTRY-CLASSIFIED PEERS (similar + dissimilar) ===
-- "industry_code" columns store SEC CODES (individual index/ETF codes), NOT
-- industry_ids. The "industry" qualifier means the peer pool is filtered to
-- securities where sec_classification.is_industry_not_strategy=true. The
-- subject itself can be either industry-primary or strategy-primary.
industry_peers AS (
    SELECT DISTINCT e.d, e.code
    FROM eff e
    JOIN stats.sec_classification sc
      ON sc.code = e.code AND sc.type = '{sec_type}' AND sc.is_industry_not_strategy = true
),
-- Pairs between subjects and industry-classified peers only
industry_pairs AS (
    SELECT s.d, s.code AS code_a, h2.code AS code_b,
           (SUM(h1.weight_pct) + SUM(h2.weight_pct)) / 2.0 AS mutual
    FROM subjects s
    JOIN holdings h1 ON h1.d = s.d AND h1.code = s.code
    JOIN holdings h2 ON h2.d = s.d AND h2.normalized_code = h1.normalized_code
    JOIN industry_peers ip ON ip.d = s.d AND ip.code = h2.code
    WHERE h2.code <> s.code
    GROUP BY s.d, s.code, h2.code
),
-- All industry-classified peers per subject (for dissimilar: includes 0-weight)
all_industry_peers AS (
    SELECT s.d, s.code, ip.code AS peer_code
    FROM subjects s
    JOIN industry_peers ip ON ip.d = s.d
    WHERE ip.code <> s.code
),

-- Similar industry-classified peers: DISTINCT-INDUSTRY greedy top-5.
-- Step 1: attach each peer's industry_id.
ind_pairs_with_ind AS (
    SELECT ip.d, ip.code_a, ip.code_b, ip.mutual,
           sc_b.industry_id
    FROM industry_pairs ip
    JOIN stats.sec_classification sc_b
      ON sc_b.code = ip.code_b AND sc_b.type = '{sec_type}'
),
-- Step 2: within each (d, code_a, industry_id), keep only the best peer
-- (highest mutual). This collapses same-industry peers to a single
-- representative.
ind_ranked_within AS (
    SELECT d, code_a, code_b, mutual, industry_id,
           ROW_NUMBER() OVER (
               PARTITION BY d, code_a, industry_id
               ORDER BY mutual DESC, code_b
           ) AS within_ind_rn
    FROM ind_pairs_with_ind
),
best_per_ind AS (
    SELECT d, code_a, code_b, mutual, industry_id
    FROM ind_ranked_within
    WHERE within_ind_rn = 1
),
-- Step 3: rank the best-per-industry peers by mutual DESC. The top-5 are
-- the 5 most-similar peers from 5 DIFFERENT industries (greedy distinct-
-- industry selection).
similar_ind_ranked AS (
    SELECT d, code_a, code_b, mutual,
           ROW_NUMBER() OVER (
               PARTITION BY d, code_a
               ORDER BY mutual DESC, code_b
           ) AS rn
    FROM best_per_ind
),

-- Dissimilar industry-classified peers: rank by mutual ASC, tie-break by
-- classification distance (different sector > different industry > same)
dissimilar_ind_ranked AS (
    SELECT aip.d, aip.code, aip.peer_code AS code_b,
           COALESCE(ip.mutual, 0) AS mutual,
           ROW_NUMBER() OVER (
               PARTITION BY aip.d, aip.code
               ORDER BY
                   COALESCE(ip.mutual, 0) ASC,
                   CASE WHEN sc_b.sector_id IS DISTINCT FROM sc_a.sector_id
                        THEN 0 ELSE 1 END,
                   CASE WHEN sc_b.industry_id IS DISTINCT FROM sc_a.industry_id
                        THEN 0 ELSE 1 END,
                   aip.peer_code
           ) AS rn
    FROM all_industry_peers aip
    LEFT JOIN industry_pairs ip
      ON ip.d = aip.d AND ip.code_a = aip.code AND ip.code_b = aip.peer_code
    LEFT JOIN subj_class sc_a ON sc_a.d = aip.d AND sc_a.code = aip.code
    LEFT JOIN stats.sec_classification sc_b
      ON sc_b.code = aip.peer_code AND sc_b.type = '{sec_type}'
)

SELECT
    s.d AS date,
    s.code AS code,
    '{sec_type}' AS sec_type,
    r1.code_b  AS similar_1st_code_by_sharing_weights,
    r2.code_b  AS similar_2nd_code_by_sharing_weights,
    r3.code_b  AS similar_3rd_code_by_sharing_weights,
    r4.code_b  AS similar_4th_code_by_sharing_weights,
    r5.code_b  AS similar_5th_code_by_sharing_weights,
    r1.mutual_shared_weight AS similar_1st_code_sharing_weight_pct,
    r2.mutual_shared_weight AS similar_2nd_code_sharing_weight_pct,
    r3.mutual_shared_weight AS similar_3rd_code_sharing_weight_pct,
    r4.mutual_shared_weight AS similar_4th_code_sharing_weight_pct,
    r5.mutual_shared_weight AS similar_5th_code_sharing_weight_pct,
    si1.code_b AS similar_1st_industry_code_by_sharing_weights,
    si2.code_b AS similar_2nd_industry_code_by_sharing_weights,
    si3.code_b AS similar_3rd_industry_code_by_sharing_weights,
    si4.code_b AS similar_4th_industry_code_by_sharing_weights,
    si5.code_b AS similar_5th_industry_code_by_sharing_weights,
    si1.mutual AS similar_1st_industry_code_sharing_weight_pct,
    si2.mutual AS similar_2nd_industry_code_sharing_weight_pct,
    si3.mutual AS similar_3rd_industry_code_sharing_weight_pct,
    si4.mutual AS similar_4th_industry_code_sharing_weight_pct,
    si5.mutual AS similar_5th_industry_code_sharing_weight_pct,
    di1.code_b AS dissimilar_1st_industry_code_by_sharing_weights,
    di2.code_b AS dissimilar_2nd_industry_code_by_sharing_weights,
    di3.code_b AS dissimilar_3rd_industry_code_by_sharing_weights,
    di4.code_b AS dissimilar_4th_industry_code_by_sharing_weights,
    di5.code_b AS dissimilar_5th_industry_code_by_sharing_weights,
    di1.mutual AS dissimilar_1st_industry_code_sharing_weight_pct,
    di2.mutual AS dissimilar_2nd_industry_code_sharing_weight_pct,
    di3.mutual AS dissimilar_3rd_industry_code_sharing_weight_pct,
    di4.mutual AS dissimilar_4th_industry_code_sharing_weight_pct,
    di5.mutual AS dissimilar_5th_industry_code_sharing_weight_pct
FROM subjects s
LEFT JOIN ranked r1
  ON r1.d = s.d AND r1.code_a = s.code AND r1.rn = 1
LEFT JOIN ranked r2
  ON r2.d = s.d AND r2.code_a = s.code AND r2.rn = 2
LEFT JOIN ranked r3
  ON r3.d = s.d AND r3.code_a = s.code AND r3.rn = 3
LEFT JOIN ranked r4
  ON r4.d = s.d AND r4.code_a = s.code AND r4.rn = 4
LEFT JOIN ranked r5
  ON r5.d = s.d AND r5.code_a = s.code AND r5.rn = 5
LEFT JOIN similar_ind_ranked si1
  ON si1.d = s.d AND si1.code_a = s.code AND si1.rn = 1
LEFT JOIN similar_ind_ranked si2
  ON si2.d = s.d AND si2.code_a = s.code AND si2.rn = 2
LEFT JOIN similar_ind_ranked si3
  ON si3.d = s.d AND si3.code_a = s.code AND si3.rn = 3
LEFT JOIN similar_ind_ranked si4
  ON si4.d = s.d AND si4.code_a = s.code AND si4.rn = 4
LEFT JOIN similar_ind_ranked si5
  ON si5.d = s.d AND si5.code_a = s.code AND si5.rn = 5
LEFT JOIN dissimilar_ind_ranked di1
  ON di1.d = s.d AND di1.code = s.code AND di1.rn = 1
LEFT JOIN dissimilar_ind_ranked di2
  ON di2.d = s.d AND di2.code = s.code AND di2.rn = 2
LEFT JOIN dissimilar_ind_ranked di3
  ON di3.d = s.d AND di3.code = s.code AND di3.rn = 3
LEFT JOIN dissimilar_ind_ranked di4
  ON di4.d = s.d AND di4.code = s.code AND di4.rn = 4
LEFT JOIN dissimilar_ind_ranked di5
  ON di5.d = s.d AND di5.code = s.code AND di5.rn = 5
ORDER BY s.d, s.code
"""


COLUMNS = [
    "date", "code", "sec_type",
    "similar_1st_code_by_sharing_weights",
    "similar_2nd_code_by_sharing_weights",
    "similar_3rd_code_by_sharing_weights",
    "similar_4th_code_by_sharing_weights",
    "similar_5th_code_by_sharing_weights",
    "similar_1st_code_sharing_weight_pct",
    "similar_2nd_code_sharing_weight_pct",
    "similar_3rd_code_sharing_weight_pct",
    "similar_4th_code_sharing_weight_pct",
    "similar_5th_code_sharing_weight_pct",
    "similar_1st_industry_code_by_sharing_weights",
    "similar_2nd_industry_code_by_sharing_weights",
    "similar_3rd_industry_code_by_sharing_weights",
    "similar_4th_industry_code_by_sharing_weights",
    "similar_5th_industry_code_by_sharing_weights",
    "similar_1st_industry_code_sharing_weight_pct",
    "similar_2nd_industry_code_sharing_weight_pct",
    "similar_3rd_industry_code_sharing_weight_pct",
    "similar_4th_industry_code_sharing_weight_pct",
    "similar_5th_industry_code_sharing_weight_pct",
    "dissimilar_1st_industry_code_by_sharing_weights",
    "dissimilar_2nd_industry_code_by_sharing_weights",
    "dissimilar_3rd_industry_code_by_sharing_weights",
    "dissimilar_4th_industry_code_by_sharing_weights",
    "dissimilar_5th_industry_code_by_sharing_weights",
    "dissimilar_1st_industry_code_sharing_weight_pct",
    "dissimilar_2nd_industry_code_sharing_weight_pct",
    "dissimilar_3rd_industry_code_sharing_weight_pct",
    "dissimilar_4th_industry_code_sharing_weight_pct",
    "dissimilar_5th_industry_code_sharing_weight_pct",
]


async def _build_for_sec_type(conn, sec_type: str, force: bool,
                              forced_date: Optional[datetime.date] = None) -> None:
    """Populate stats.sec_similars for a single sec_type ('index' or 'etf').

    Incremental when force=False (missing composition snapshot dates only);
    full recompute when force=True (truncate sec_type rows first). With
    forced_date (--date mode), only that snapshot date is recomputed — the
    missing-date skip is bypassed (existing rows are refreshed via the
    upsert path) and no truncation/deletes happen.
    """
    tag = f"[SEC_SIMILARS:{sec_type}]"
    source_type = sec_type  # sec_composition.source_type == sec_similars.sec_type

    # ---- Step 1: detect missing composition snapshot dates ----------
    source_rows = await conn.fetch(
        f"SELECT DISTINCT snapshot_date FROM {SOURCE_TABLE} "
        f"WHERE source_type = $1 AND stock_code IS NOT NULL",
        source_type,
    )
    source_dates: Set[datetime.date] = {
        r["snapshot_date"] for r in source_rows if r["snapshot_date"]
    }

    if forced_date is not None:
        # --date mode: bypass the missing-date skip — the forced snapshot
        # date is ALWAYS recomputed (existing rows refreshed via the upsert
        # path). The union-level availability gate in build_sec_similars
        # already exited(1) when no sec_type has the date, so a sec_type
        # without a snapshot at it is a logged no-op.
        if forced_date not in source_dates:
            print(f"{tag} [DATE MODE] no '{source_type}' composition snapshot at "
                  f"{forced_date}; nothing to do for this sec_type", flush=True)
            return
        target_dates: Optional[Set[datetime.date]] = {forced_date}
    elif force:
        print(f"\n{tag} Force mode: truncating {TABLE} "
              f"WHERE sec_type='{sec_type}'...", flush=True)
        await conn.execute(
            f"DELETE FROM {TABLE} WHERE sec_type = $1", sec_type
        )
        target_dates = source_dates
    else:
        existing_rows = await conn.fetch(
            f"SELECT DISTINCT date FROM {TABLE} WHERE sec_type = $1",
            sec_type,
        )
        existing_dates = {r["date"] for r in existing_rows}
        target_dates = source_dates - existing_dates

    print(f"{tag} {len(source_dates)} composition snapshot dates "
          f"in {SOURCE_TABLE} (source_type='{source_type}')", flush=True)
    if forced_date is not None:
        print(f"{tag} [DATE MODE] -> recompute snapshot date {forced_date} "
              f"(missing-date skip bypassed)", flush=True)
    elif force:
        print(f"{tag} force mode -> recompute all dates", flush=True)
    else:
        print(f"{tag} {len(target_dates)} dates missing from {TABLE} "
              f"(sec_type='{sec_type}')", flush=True)
        if not target_dates:
            print(f"{tag} DB is up to date; nothing to do.", flush=True)
            return

    if not source_dates:
        print(f"{tag} No source data; nothing to do.", flush=True)
        return

    # ---- Step 2: compute top-3 similar codes + industries -----------
    dates_param = sorted(target_dates) if target_dates else sorted(source_dates)
    print(f"{tag} Computing similar codes + similar/dissimilar industries "
          f"for {len(dates_param)} snapshot dates...", flush=True)
    sql = _SQL_SEC_SIMILARS_TEMPLATE.format(
        src=SOURCE_TABLE, source_type=source_type, sec_type=sec_type,
    )
    rows = await conn.fetch(sql, dates_param)
    n_codes = len(set(rec_col(rows, "code")))
    print(f"{tag} -> {len(rows):,} rows across {n_codes} {sec_type}s "
          f"across {len(dates_param)} snapshot dates", flush=True)

    # ---- Step 3: upsert --------------------------------------------
    print(f"{tag} Upserting into {TABLE}...", flush=True)
    if not rows:
        print(f"{tag} -> no data to insert.", flush=True)
        return

    # Whole-column extraction + column-major row emission (no per-row dict walks)
    df = pd.DataFrame(rec_cols(rows))
    data = records_from_frame(df, COLUMNS)
    n_copied, n_upserted = await copy_or_upsert_split_async(
        conn, TABLE, data,
        key_columns=["date", "code", "sec_type"],
    )
    total = n_copied + n_upserted
    via = "COPY" if n_copied > 0 and n_upserted == 0 else \
          f"COPY+upsert ({n_copied}+{n_upserted})" if n_copied > 0 else \
          "upsert"
    print(f"{tag} -> upserted {total:,} rows via {via}", flush=True)


async def build_sec_similars(conn, force: bool = False,
                             forced_date: Optional[datetime.date] = None) -> None:
    """Populate stats.sec_similars for both 'index' and 'etf' sec_types.

    Incremental when force=False (missing composition snapshot dates only);
    full recompute when force=True (truncate per sec_type first). With
    forced_date (--date mode), only that snapshot date is recomputed — the
    missing-date skip is bypassed (existing rows are refreshed via the
    upsert path) and no truncation/deletes happen.

    Skip semantics: when force=False and every composition snapshot date in
    stats.sec_composition is already present in stats.sec_similars for a given
    sec_type, that sec_type is a no-op — safe to re-run.

    --date availability gate: the forced date must exist among the UNION of
    composition snapshot dates of both sec_types (forced_date_scope exits(1)
    otherwise). A sec_type without a snapshot at the forced date is a logged
    no-op inside _build_for_sec_type.
    """
    if forced_date is not None:
        union_dates: Set[datetime.date] = set()
        for st in SEC_TYPES:
            rows = await conn.fetch(
                f"SELECT DISTINCT snapshot_date FROM {SOURCE_TABLE} "
                f"WHERE source_type = $1 AND stock_code IS NOT NULL",
                st,
            )
            union_dates |= {r["snapshot_date"] for r in rows if r["snapshot_date"]}
        forced_date_scope(
            union_dates, forced_date,
            source_label=f"{SOURCE_TABLE} snapshot dates",
        )
        print(f"[SEC_SIMILARS] [DATE MODE] forcing snapshot date {forced_date}",
              flush=True)
    for sec_type in SEC_TYPES:
        await _build_for_sec_type(conn, sec_type, force, forced_date=forced_date)
