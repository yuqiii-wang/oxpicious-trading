-- MA-Spread market-regime SPANS of one (sec_type, code) — one row per
-- CONTIGUOUS same-regime run, straight from the stats.market_regime_spans
-- TABLE (the contiguous runs builds.market_regimes materializes over the
-- daily stats.market_regimes registry — formerly a per-query
-- gaps-and-islands VIEW). Replaces the retired mov_ave_market_hypes
-- episode query; the UI shades the spans per regime. The regime SMALLINT
-- code decodes to its label here (labels never ride frames).
-- Serves GET /api/analysis/market-regimes.
-- args:
--   sec_type: value int — static.label_dim sec_type SMALLINT code (secTypeParam)
--   codes: value text[] — the code variants to chart (codeVariants)
--   regime_decode: fragment — labelDecodeSql("regime", "s.regime")
    SELECT
      {{regime_decode}} AS regime,
      s.start_date,
      s.end_date,
      s.span_days::int AS span_days
    FROM stats.market_regime_spans s
    WHERE s.sec_type = {{sec_type}}
      AND s.code = ANY({{codes}}::text[])
    ORDER BY s.regime, s.start_date
