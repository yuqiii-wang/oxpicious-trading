-- Latest stats.sec_similars row (date <= today) for ONE index — the
-- currently-effective similar-indices row (date in sec_similars is the
-- COMPOSITION snapshot_date, quarterly, not a trading day; carry-forward
-- pattern as index_exts.stock_num). Serves
-- GET /api/sec-composition/similar-indices.
-- args:
--   code: value text — the subject index code

    SELECT date::text
      FROM stats.sec_similars
     WHERE code = {{code}}
       AND sec_type = 'index'
       AND date <= CURRENT_DATE
     ORDER BY date DESC
     LIMIT 1
