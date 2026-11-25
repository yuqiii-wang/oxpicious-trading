-- Oracle — one security's full pivot-action history from
-- analysis.oracle_signals (v4: one row per graded local min/max —
-- buy at a local minimum, sell at a local maximum, per-side one
-- action per 5-day window, each pivot graded 0-100 by its favorable
-- move on both 20-row legs (confidence; NULL while the forward
-- window is unrealized);
-- pivots below the 2% no-action floor are not in the table at all;
-- the ext_* legs carry the next OPPOSITE PLACED pivot, the row's
-- hindsight swing target, NULL at the frontier), joined with the
-- pivot date's raw close from the sec_type's own bars source (the
-- pipeline's own bars table — every pivot date exists there, so the
-- join never drops rows; close feeds the UI's price overlay + marker
-- placement). The action decodes through the label index
-- (oracle_action) — no CASE. The UI slices via dataZoom — the whole
-- history is served.
-- Serves GET /api/analysis/oracle/series
-- args:
--   codes: value text[] — codeVariants(target) (bare + suffixed forms)
--   sec_type: value smallint — LABEL_CODE.sec_type[secType]
--   bars_table: fragment — BARS_TABLE[secType] (stats.*_basic_stats)
--   action_decode: fragment — labelDecodeSql("oracle_action", "v.action")
    SELECT
      v.date,
      {{action_decode}} AS action,
      v.window_days,
      v.ext_date,
      v.ext_offset,
      v.ext_price,
      v.confidence,
      b.close
    FROM analysis.oracle_signals v
    LEFT JOIN {{bars_table}} b
      ON b.code = v.code AND b.date = v.date
    WHERE v.sec_type = {{sec_type}}
      AND v.code = ANY({{codes}}::text[])
    ORDER BY v.date ASC
