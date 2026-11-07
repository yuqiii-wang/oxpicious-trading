-- MA-Spread "ohlc" extras group — the FULL 7-window rolling-extrema pivot
-- (anchors + dates + line slopes + change/days since the last extreme)
-- from analysis.mov_ave_spreads_roof_and_floor_lines, pivoted back to the
-- per-window names, driven off the detail rows' dates (index-aligned with
-- the chart response). The ohlc prefetch/pivot CTE and the final-SELECT
-- pivot columns are fragment args (ohlc-pivot.ts builders).
-- Serves GET /api/analysis/mov-ave-spread/extras (metrics=ohlc).
-- args:
--   sec_type: value int — static.label_dim sec_type SMALLINT code (secTypeParam)
--   codes: value text[] — the code variants to chart (codeVariants)
--   ohlc_ctes: fragment — ohlcPivotCteSql(ROOF_FLOOR_PERIODS)
--   ohlc_select: fragment — ohlcPivotSelectSql(ROOF_FLOOR_PERIODS, "")
    WITH d AS MATERIALIZED (
      SELECT * FROM analysis.mov_ave_spreads_detail
      WHERE sec_type = {{sec_type}}
        AND code = ANY({{codes}}::text[])
    ),
    {{ohlc_ctes}}
    SELECT
      d.date,
      {{ohlc_select}}
    FROM d
    LEFT JOIN ohlc ON ohlc.code = d.code AND ohlc.date = d.date
    ORDER BY d.date ASC
