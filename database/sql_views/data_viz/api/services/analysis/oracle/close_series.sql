-- Oracle — the FULL daily close series for one security from the
-- sec_type's own bars source (every session, not just the decision
-- dates — the UI's price-overlay line runs on the whole history and
-- the hindsight markers land on the extremum dates between decision
-- dates).
-- Serves GET /api/analysis/oracle/series
-- args:
--   codes: value text[] — codeVariants(target) (bare + suffixed forms)
--   bars_table: fragment — BARS_TABLE[secType] (stats.*_basic_stats)
    SELECT b.date, b.close
    FROM {{bars_table}} b
    WHERE b.code = ANY({{codes}}::text[])
      AND b.close IS NOT NULL
    ORDER BY b.date ASC
