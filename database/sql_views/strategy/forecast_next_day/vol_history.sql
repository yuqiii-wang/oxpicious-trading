-- The run's codes' trailing realized-vol panel rows (analysis.
-- volatility — the volatility family's value source: hv_20 the 20-row
-- close-to-close realized vol, ewma_vol the RiskMetrics fast vol),
-- oldest-first. The forecast's vol BAR input: the per-code LOCAL
-- trailing-252d quantile tails + the axis's own trailing σ (the depth
-- normalizer) computed over the REAL history (the engine's bars are
-- SHIFTED one row, so the synthetic day never enters its own bar).
-- args:
--   vol_table: fragment — analysis.volatility
--   sec_type: value smallint — the sec_type's SEC_TYPE_CODE
--   codes: value text[] — the run's codes
--   row_limit: value int — the per-code trailing row cap (bar window + 1)
SELECT code, date, hv_20, ewma_vol FROM (
    SELECT v.code AS code, v.date AS date,
           v.hv_20::float8 AS hv_20, v.ewma_vol::float8 AS ewma_vol,
           ROW_NUMBER() OVER (PARTITION BY v.code ORDER BY v.date DESC) AS rn
    FROM {{vol_table}} v
    WHERE v.sec_type = {{sec_type}} AND v.code = ANY({{codes}})
) ranked
WHERE rn <= {{row_limit}}
ORDER BY code, date
