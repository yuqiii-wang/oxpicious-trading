-- margins index-series build — RAW unaggregated margin rows of
-- one source table, full history (force mode; the weighted-avg
-- aggregation runs in pandas).
-- args:
--   src_table: fragment — the margin source table

            SELECT code, date, rz_balance, rz_buy
            FROM {{src_table}}
            
            ORDER BY code, date
            