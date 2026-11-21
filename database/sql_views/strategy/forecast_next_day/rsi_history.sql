-- The run's codes' trailing analysis.mov_ave_rsi rows (the mov_rsi
-- family's value source), oldest-first — the forecast's RSI BAR input:
-- the per-code adaptive trailing-window quantile tails computed over
-- the REAL history (the engine's bars are SHIFTED one row, so the
-- synthetic day never enters its own bar — the live tier's
-- fetch_rsi_tail_bars convention, widened to the run's codes at once).
-- args:
--   rsi_table: fragment — analysis.mov_ave_rsi
--   sec_type: value smallint — the sec_type's SEC_TYPE_CODE
--   codes: value text[] — the run's codes
--   row_limit: value int — the per-code trailing row cap (bar window + 1)
--   rsi_raw_cols: fragment — the rsi_{W}days ::float8 select list
--   rsi_cols: fragment — the outer plain column names
SELECT code, date, {{rsi_cols}} FROM (
    SELECT r.code AS code, r.date AS date,{{rsi_raw_cols}},
           ROW_NUMBER() OVER (PARTITION BY r.code ORDER BY r.date DESC) AS rn
    FROM {{rsi_table}} r
    WHERE r.sec_type = {{sec_type}} AND r.code = ANY({{codes}})
) ranked
WHERE rn <= {{row_limit}}
ORDER BY code, date
