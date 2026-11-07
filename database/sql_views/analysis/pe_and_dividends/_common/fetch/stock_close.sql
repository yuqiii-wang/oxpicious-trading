-- pe_and_dividends stock stage — per-(code, date) close + pe +
-- eps from stock_basic_stats (epoch float8 dates, close non-null).
-- args:
--   codes: value text[] — the active stock codes

        SELECT code,
               extract(epoch from date)::float8 AS date,
               close::float8 AS close, pe::float8 AS pe,
               -- COALESCE to NaN: an all-NULL column (no-PE codes) would
               -- otherwise build an object-dtype all-None column in the
               -- frame — NaN keeps it float64 end to end.
               COALESCE(eps::float8, 'NaN'::float8) AS eps
        FROM stats.stock_basic_stats
        WHERE code = ANY({{codes}}::text[])
          AND close IS NOT NULL
        ORDER BY code, date ASC
        