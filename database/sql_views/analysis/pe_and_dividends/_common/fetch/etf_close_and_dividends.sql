-- pe_and_dividends etf stage — per-(code, date) close + pe + eps
-- (etf_basic_stats) + implied_dividend_per_share (etf_adjustment,
-- COALESCE 0), epoch float8 dates, close non-null.
-- args:
--   codes: value text[] — the active ETF codes

        SELECT
            b.code,
            extract(epoch from b.date)::float8 AS date,
            b.close::float8 AS close,
            b.pe::float8 AS pe,
            -- COALESCE to NaN: an all-NULL column (no-PE codes) would
            -- otherwise build an object-dtype all-None column in the
            -- frame — NaN keeps it float64 end to end.
            COALESCE(b.eps::float8, 'NaN'::float8) AS eps,
            COALESCE(a.implied_dividend_per_share, 0)::float8 AS implied_dividend_per_share
        FROM stats.etf_basic_stats b
        LEFT JOIN stats.etf_adjustment a
            ON a.date = b.date AND a.code = b.code
        WHERE b.code = ANY({{codes}}::text[])
          AND b.close IS NOT NULL
        ORDER BY b.code, b.date ASC
        