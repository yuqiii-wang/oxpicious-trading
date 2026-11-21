-- The run's codes' trailing FIXED-ANCHOR momentum axes (analysis.
-- mov_ave_momentum, the momentum family's value source) as log-return
-- columns — ln(1.0 + momentum_{axis}), the family fetch's own boundary
-- transform — oldest-first. The forecast's momentum BAR input: the
-- per-code adaptive trailing-window quantile tails + the axis's own
-- trailing σ (the depth normalizer) computed over the REAL history
-- (the engine's bars are SHIFTED one row). Only the FIXED anchors
-- (5days / 20days) are close-derivable for a synthetic day; the
-- vol-adaptive axes (short/mid/long) are upstream-chosen lookbacks and
-- stay out of the forecast scope.
-- args:
--   momentum_table: fragment — analysis.mov_ave_momentum
--   sec_type: value smallint — the sec_type's SEC_TYPE_CODE
--   codes: value text[] — the run's codes
--   row_limit: value int — the per-code trailing row cap (bar window + 1)
--   logret_raw_cols: fragment — the ln(1+momentum_{axis}) ::float8 select list
--   logret_cols: fragment — the outer plain column names
SELECT code, date, {{logret_cols}} FROM (
    SELECT m.code AS code, m.date AS date,{{logret_raw_cols}},
           ROW_NUMBER() OVER (PARTITION BY m.code ORDER BY m.date DESC) AS rn
    FROM {{momentum_table}} m
    WHERE m.sec_type = {{sec_type}} AND m.code = ANY({{codes}})
) ranked
WHERE rn <= {{row_limit}}
ORDER BY code, date
