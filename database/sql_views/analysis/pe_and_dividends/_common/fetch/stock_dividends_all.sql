-- pe_and_dividends — ALL stock dividend events (small table,
-- loaded whole to avoid code-format mismatches between
-- sec_composition and stock_dividends suffix conventions).
-- args: none

            SELECT code,
                   extract(epoch from ex_dividend_date)::float8 AS ex_dividend_date,
                   dividend_per_share_pre_tax::float8 AS dividend_per_share_pre_tax
            FROM stats.stock_dividends
            WHERE dividend_per_share_pre_tax IS NOT NULL
              AND dividend_per_share_pre_tax > 0
            ORDER BY code, ex_dividend_date ASC
            