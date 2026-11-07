-- Per-contract final OI (last session ≤ its expiry) — the expiry markers.
-- One row per contract ever quoted (~5k for the biggest CFFEX underlying).
-- Per-expiry lifetime totals (the cohort selector's options) are derived
-- client-side from /oi-daily's per (date × expiry) rows — same numbers,
-- one less full-history aggregation per request. Serves
-- GET /api/options/expiry-meta.
-- args:
--   where_clause: fragment — the scope WHERE conditions joined " AND "
--     (termsWhere result + the fixed "t.date <= t.expiry_date")
      SELECT DISTINCT ON (t.contract_code)
             t.contract_code, i.contract_name, t.option_type, t.expiry_date,
             t.date AS last_date, vo.open_interest
      FROM stats.options_terms t
      JOIN stats.options_identity i ON i.contract_code = t.contract_code AND i.date = t.date
      JOIN stats.options_volume_oi vo ON vo.contract_code = t.contract_code AND vo.date = t.date
      WHERE {{where_clause}}
      ORDER BY t.contract_code, t.date DESC
