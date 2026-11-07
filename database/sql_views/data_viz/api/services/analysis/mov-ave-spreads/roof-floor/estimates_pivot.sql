-- MA-Spread "rfest" extras group — the roof/floor LEVEL-ESTIMATE pivot —
-- analysis.mov_ave_spreads_roof_floor_estimates is LONG (one row per
-- (code, date, period, method)); pivoted back to one row per date with
-- roof/floor/breached fields per (method, window), driven off the detail
-- rows' dates so the response is index-aligned with the chart. The pivot
-- FILTER columns and the final-SELECT field list are fragment args
-- (roof-floor.ts builders, keyed by LABEL_CODE.roof_floor_method).
-- Serves GET /api/analysis/mov-ave-spread/extras (metrics=rfest).
-- args:
--   sec_type: value int — static.label_dim sec_type SMALLINT code (secTypeParam)
--   codes: value text[] — the code variants to chart (codeVariants)
--   estimate_pivot: fragment — rfEstimatePivotSql() per-(method, window) FILTER aggregates
--   estimate_select: fragment — rfEstimateSelectSql() est.<field> list
    WITH d AS MATERIALIZED (
      SELECT * FROM analysis.mov_ave_spreads_detail
      WHERE sec_type = {{sec_type}}
        AND code = ANY({{codes}}::text[])
    ),
    est AS MATERIALIZED (
      SELECT
        e.code,
        e.date,
        {{estimate_pivot}}
      FROM analysis.mov_ave_spreads_roof_floor_estimates e
      WHERE e.sec_type = {{sec_type}}
        AND e.code = ANY({{codes}}::text[])
      GROUP BY e.code, e.date
    )
    SELECT
      d.date,
      {{estimate_select}}
    FROM d
    LEFT JOIN est ON est.code = d.code AND est.date = d.date
    ORDER BY d.date ASC
