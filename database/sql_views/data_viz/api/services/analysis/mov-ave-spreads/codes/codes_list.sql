-- MA-Spread codes list — one row per asset code with first/last date,
-- n_dates, and the latest snapshot of all 9 gap_values (for sparkline /
-- sort), from the PRECOMPUTED per-code summary table
-- analysis.mov_ave_spreads_codes (maintained by analysis.mov_ave_spread's
-- refresh_codes_summary). Also the industry branch's entity-universe
-- source for the themes trees.
-- Serves GET /api/analysis/mov-ave-spread/codes (and the industry branch
-- of /mov-ave-spread/themes + /strategy-themes).
-- args:
--   sec_type: value int — static.label_dim sec_type SMALLINT code (secTypeParam)
    SELECT
      c.code,
      c.name,
      c.first_date,
      c.last_date,
      c.n_dates,
      c.latest_price_vs_ma5   AS price_vs_ma5,
      c.latest_price_vs_ma20  AS price_vs_ma20,
      c.latest_price_vs_ma60  AS price_vs_ma60,
      c.latest_price_vs_ma120 AS price_vs_ma120,
      c.latest_price_vs_ma255 AS price_vs_ma255,
      c.latest_ma5_vs_ma20    AS ma5_vs_ma20,
      c.latest_ma5_vs_ma60    AS ma5_vs_ma60,
      c.latest_ma5_vs_ma120   AS ma5_vs_ma120,
      c.latest_ma5_vs_ma255   AS ma5_vs_ma255,
      GREATEST(c.max_p5, c.max_p20, c.max_p60, c.max_p120, c.max_p255,
               c.max_5_20, c.max_5_60, c.max_5_120, c.max_5_255) AS max_gain,
      LEAST(c.min_p5, c.min_p20, c.min_p60, c.min_p120, c.min_p255,
            c.min_5_20, c.min_5_60, c.min_5_120, c.min_5_255)    AS max_loss,
      (GREATEST(c.max_p5, c.max_p20, c.max_p60, c.max_p120, c.max_p255,
                c.max_5_20, c.max_5_60, c.max_5_120, c.max_5_255)
       - LEAST(c.min_p5, c.min_p20, c.min_p60, c.min_p120, c.min_p255,
               c.min_5_20, c.min_5_60, c.min_5_120, c.min_5_255)) AS max_spread
    FROM analysis.mov_ave_spreads_codes c
    WHERE c.sec_type = {{sec_type}}
    ORDER BY max_spread DESC NULLS LAST, c.code
