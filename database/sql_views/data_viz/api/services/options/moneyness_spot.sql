-- Per-date underlying spot closes backing the moneyness skew series
-- (analysis.options_spot_daily; built by the same pipeline as
-- moneyness_groups.sql). Shares the parameter list with the groups query.
-- Serves GET /api/options/oi-moneyness.
-- args:
--   underlying_code: value text — the stripped underlying code ($1)
--   range_clause: fragment — optional " AND date >= $n::date" /
--     " AND date <= $n::date" bounds (numbering continues after $1)
      SELECT date, close::float8 AS close
      FROM analysis.options_spot_daily
      WHERE underlying_code = {{underlying_code}}{{range_clause}}
      ORDER BY date ASC
