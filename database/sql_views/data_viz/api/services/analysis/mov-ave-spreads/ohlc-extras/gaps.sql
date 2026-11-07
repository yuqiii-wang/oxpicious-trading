-- MA-Spread "gaps" extras group — one (sec_type, code)'s daily OHLC gap
-- anatomy + pattern block (analysis.mov_ave_spreads_detail_ohlc_gaps — PK
-- (code, sec_type, date) on HASH(code) partitions; estimated rows are
-- excluded at build time). NUMERIC legs cast float8 server-side.
-- Serves GET /api/analysis/mov-ave-spread/extras (metrics=gaps).
-- args:
--   sec_type: value int — static.label_dim sec_type SMALLINT code (secTypeParam)
--   codes: value text[] — the code variants to chart (codeVariants)
    SELECT
      g.date,
      g.gap_open::float8  AS gap_open,
      g.intra::float8     AS intra,
      g.uw::float8        AS uw,
      g.lw::float8        AS lw,
      g.uw_top10,
      g.lw_top10,
      g.pattern
    FROM analysis.mov_ave_spreads_detail_ohlc_gaps g
    WHERE g.sec_type = {{sec_type}}
      AND g.code = ANY({{codes}}::text[])
    ORDER BY g.date
