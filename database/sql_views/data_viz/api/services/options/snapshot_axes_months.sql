-- DISTINCT expiry months + the scope's MAX(date) OVER () — the wall
-- panel's unified expiry-month axis and the client cache's version anchor
-- for the response (it carries no per-row dates, so the version rides the
-- same scan). Cached per underlying/venue like snapshot_axes_strikes.
-- Serves GET /api/options/snapshot-axes.
-- args:
--   where_clause: fragment — conditional scope WHERE (termsWhere result
--     incl. the WHERE keyword) or ""
      SELECT DISTINCT t.expiry_month, MAX(t.date) OVER () AS latest_date
      FROM stats.options_terms t
      {{where_clause}}
