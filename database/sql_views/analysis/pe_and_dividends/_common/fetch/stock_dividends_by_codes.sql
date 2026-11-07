-- pe_and_dividends — stock dividend events filtered to the given
-- codes via REGEXP_REPLACE suffix-stripped matching (handles
-- mixed suffix conventions between the two tables).
-- args:
--   codes: value text[] — the stock codes to fetch dividends for

            SELECT code,
                   extract(epoch from ex_dividend_date)::float8 AS ex_dividend_date,
                   dividend_per_share_pre_tax::float8 AS dividend_per_share_pre_tax
            FROM stats.stock_dividends
            WHERE dividend_per_share_pre_tax IS NOT NULL
              AND dividend_per_share_pre_tax > 0
              AND REGEXP_REPLACE(code, '\.(SS|SZ|SH|BJ|HK)$', '') = ANY({{codes}}::text[])
            ORDER BY code, ex_dividend_date ASC
            