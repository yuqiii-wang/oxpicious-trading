-- The underlying's 5-min spot bars for one date (non-NULL closes) — the
-- Live Data Options chart's spot line.
-- args:
--   codes: value text[] — codeVariants(underlying) (suffixed ETF + bare forms)
--   date: value text — trading day (YYYY-MM-DD)
--   spot_table: fragment — resolveSpotTable(target_type)
SELECT to_char(time, 'HH24:MI') AS time, close::float8 AS close
         FROM {{spot_table}}
        WHERE code = ANY({{codes}}::text[]) AND date = {{date}}::date AND close IS NOT NULL
        ORDER BY time
