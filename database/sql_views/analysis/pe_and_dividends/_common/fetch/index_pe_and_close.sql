-- pe_and_dividends index stage — per-(code, date) PE + close for
-- the active index universe (index_valuation LEFT-joined onto
-- index_basic_stats; epoch float8 dates; close non-null).
-- args:
--   codes: value text[] — the active index codes

        SELECT
            b.code,
            extract(epoch from b.date)::float8 AS date,
            b.close::float8 AS close,
            v.pe::float8 AS pe
        FROM stats.index_basic_stats b
        LEFT JOIN stats.index_valuation v
            ON v.date = b.date AND v.code = b.code
        WHERE b.code = ANY({{codes}}::text[])
          AND b.close IS NOT NULL
        ORDER BY b.code, b.date ASC
        