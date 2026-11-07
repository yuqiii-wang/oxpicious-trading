-- margins index stage — the per-(index_code, date) weighted-avg
-- RONGZI series from analysis.margin_index_series (the vectorized
-- build's output table), reshaped to the compute's (code, date,
-- rz_balance, rz_buy, n_members) contract.
-- args: none

        SELECT
            index_code            AS code,
            date,
            index_margin_balance AS rz_balance,
            index_margin_buy      AS rz_buy,
            n_with_balance       AS n_members
        FROM analysis.margin_index_series
        ORDER BY index_code, date
        