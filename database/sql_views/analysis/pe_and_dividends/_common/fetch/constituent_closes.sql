-- pe_and_dividends index stage — per-(code, date) close for the
-- index's constituent stocks, exchange suffixes stripped on both
-- sides so mixed code conventions join (cap-weighted constituent
-- yield denominators).
-- args:
--   codes: value text[] — the suffix-stripped constituent codes

        SELECT REGEXP_REPLACE(code, '\.(SS|SZ|SH|BJ|HK)$', '') AS code,
               extract(epoch from date)::float8 AS date,
               close::float8 AS close
        FROM stats.stock_basic_stats
        WHERE close IS NOT NULL
          AND REGEXP_REPLACE(code, '\.(SS|SZ|SH|BJ|HK)$', '') = ANY({{codes}}::text[])
        ORDER BY code, date ASC
        