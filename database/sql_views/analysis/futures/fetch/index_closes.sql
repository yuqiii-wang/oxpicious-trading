-- futures — index close series for the index futures' underlying
-- codes (epoch float8 dates, close non-null).
-- args:
--   codes: value text[] — the underlying index codes

        SELECT extract(epoch from date)::float8 AS date,
               code, close::float8 AS close
        FROM stats.index_basic_stats
        WHERE code = ANY({{codes}}::text[]) AND close IS NOT NULL
        ORDER BY code, date
    