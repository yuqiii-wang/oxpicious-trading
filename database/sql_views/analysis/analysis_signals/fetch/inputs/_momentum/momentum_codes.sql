-- Codes carrying at least one momentum row for the sec_type — the
-- momentum universe pre-filter's momentum leg (a code without momentum
-- data can never enter a tail bucket).
-- args:
--   momentum_table: fragment — analysis.mov_ave_momentum
--   sec_type: value int — the sec_type smallint code
SELECT DISTINCT code FROM {{momentum_table}} WHERE sec_type = {{sec_type}}
