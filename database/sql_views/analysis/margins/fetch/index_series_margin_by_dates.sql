-- margins index-series build — RAW unaggregated margin rows of
-- one source table scoped to the incremental target dates.
-- args:
--   src_table: fragment — the margin source table
--   dates: value date[] — the target dates to fetch

            SELECT code, date, rz_balance, rz_buy
            FROM {{src_table}}
            WHERE date = ANY({{dates}}::date[])
            ORDER BY code, date
            