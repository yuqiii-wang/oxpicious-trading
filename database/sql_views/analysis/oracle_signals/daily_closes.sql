-- Oracle pipeline (analysis.oracle_signals) — daily closes for the
-- run's codes from the sec_type's own basic-stats table
-- (stats.{etf,index,stock}_basic_stats — passed as a fragment arg from
-- the pipeline's config). CLOSES only — the hindsight oracle reads the
-- price path, nothing else. Dates ride the epoch-float8 DB-read
-- convention (converted at the frame boundary via epoch_col_to_dt64);
-- close is float64 end-to-end.
-- args: bars_table: fragment — SEC_TYPE_SOURCES[sec_type].bars_table
--       codes: value text[] — the run's universe
    SELECT ibs.code,
           extract(epoch from ibs.date)::float8 AS date_epoch,
           ibs.close::float8 AS close
    FROM {{bars_table}} ibs
    WHERE ibs.code = ANY({{codes}}::text[])
      AND ibs.close IS NOT NULL
    ORDER BY ibs.code, ibs.date
