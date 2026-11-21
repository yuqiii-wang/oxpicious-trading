-- Volatility pipeline (analysis.volatility) — daily OHLC bars for the
-- vol panel: open/high/low/close from the sec_type's own basic-stats
-- table (stats.{etf,index,stock}_basic_stats — the same OHLC shape on
-- all three; the table is passed as a fragment arg from the pipeline's
-- config). is_ohl_estimated rows (close-only CSIndex publications
-- before 2024-10-24) carry O ≈ H ≈ L ≈ C — the range-based estimators
-- degenerate toward close-to-close there (kept computable; documented
-- in the table's DDL header).
-- args: bars_table: fragment — SEC_TYPE_SOURCES[sec_type].bars_table
--       codes: value text[] — the run's universe
    SELECT ibs.code,
           extract(epoch from ibs.date)::float8 AS date_epoch,
           ibs.open::float8  AS open,
           ibs.high::float8  AS high,
           ibs.low::float8   AS low,
           ibs.close::float8 AS close
    FROM {{bars_table}} ibs
    WHERE ibs.code = ANY({{codes}}::text[])
    ORDER BY ibs.code, ibs.date
