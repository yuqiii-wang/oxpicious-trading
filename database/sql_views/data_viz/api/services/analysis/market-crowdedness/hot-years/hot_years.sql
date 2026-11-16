-- Market Crowdedness — the year-pinned hot-set variants available for
-- one security (DISTINCT hot_year from the frozen-roster table; the
-- dropdown lists them newest first).
-- Serves GET /api/analysis/market-crowdedness/hot-years
-- args:
--   codes: value text[] — codeVariants(target) (bare + suffixed forms)
--   sec_type: value smallint — LABEL_CODE.sec_type[secType]
    SELECT DISTINCT hot_year
    FROM analysis.market_crowdedness_hot_sets
    WHERE sec_type = {{sec_type}}::smallint
      AND code = ANY({{codes}}::text[])
    ORDER BY hot_year DESC
