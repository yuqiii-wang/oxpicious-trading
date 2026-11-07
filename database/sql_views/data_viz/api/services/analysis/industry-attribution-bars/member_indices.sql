-- Benchmark Attribution — all member indices' code_sec_shared_weight for
-- ONE (industry_id, benchmark_code, date), each bar = one member index.
-- Source: stats.cross_stats (sec_type='index' pair grain) joined with
-- stats.sec_classification for industry membership and
-- stats.index_identity for display names. target_date NULL falls back to
-- the latest materialized cross_stats date for the benchmark. Drives the
-- per-industry bar charts in "Benchmark Attribution" mode.
-- Serves GET /api/analysis/industry-attribution/member-indices.
-- args:
--   industry_id: value text — the industry whose member indices to list
--   benchmark_code_param: fragment — the raw `$2::text` placeholder (two
--     occurrences). The original SQL numbered the params by array order
--     (industry $1, benchmark $2, date $3) which differs from their
--     first-use TEXT order, but the loader numbers value slots by first
--     text use — so the placeholders are carried verbatim and the
--     service binds the params array itself
--   target_date_param: fragment — the raw `$3::date` placeholder (same
--     reason as benchmark_code_param)
    WITH target_date AS (
      SELECT COALESCE(
        {{target_date_param}},
        (SELECT MAX(date) FROM stats.cross_stats
         WHERE benchmark_code = {{benchmark_code_param}}
           AND sec_type = 'index')
      ) AS max_date
    )
    SELECT
      sa.code,
      ii.name AS name,
      sa.code_sec_shared_weight,
      sa.benchmark_sec_shared_weight,
      sc.industry_label,
      bi.name AS benchmark_name,
      sit.is_broad_market,
      ld.max_date AS date
    FROM stats.cross_stats sa
    JOIN stats.sec_classification sc
      ON sc.code = sa.code AND sc.type = 'index'
      AND sc.is_active = TRUE
      AND sc.industry_id = {{industry_id}}::text
    CROSS JOIN target_date ld
    LEFT JOIN LATERAL (
      SELECT DISTINCT ON (code) name
      FROM stats.index_identity
      WHERE code = sa.code
      ORDER BY code, date DESC
    ) ii ON true
    LEFT JOIN LATERAL (
      SELECT DISTINCT ON (code) name
      FROM stats.index_identity
      WHERE code = sa.benchmark_code
      ORDER BY code, date DESC
    ) bi ON true
    LEFT JOIN LATERAL (
      SELECT BOOL_OR(is_broad_market) AS is_broad_market
      FROM stats.sec_index_tags
      WHERE code = sa.benchmark_code
    ) sit ON true
    WHERE sa.benchmark_code = {{benchmark_code_param}}
      AND sa.sec_type = 'index'
      AND sa.date = ld.max_date
    ORDER BY sa.code_sec_shared_weight DESC NULLS LAST
