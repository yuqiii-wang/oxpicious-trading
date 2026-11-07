-- Market crowdedness — latest display name per member stock (stats.
-- stock_identity) for the top_stocks tooltip evidence (the name falls
-- back to the code caller-side).
-- args:
--   stock_codes: value text[] — the sorted member stock codes
    SELECT DISTINCT ON (code) code AS stock_code, name
    FROM stats.stock_identity
    WHERE code = ANY({{stock_codes}}::text[])
    ORDER BY code, date DESC
