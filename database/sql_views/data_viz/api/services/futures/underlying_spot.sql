-- Underlying index spot closes over the product's date span (index futures
-- only) — the combined response's spot_price overlay.
-- args:
--   code: value text — the underlying index code
--   min_date: value text — first product trading date (YYYY-MM-DD)
--   max_date: value text — last product trading date (YYYY-MM-DD)

    SELECT date, close
    FROM stats.index_basic_stats
    WHERE code = {{code}}
      AND date >= {{min_date}}
      AND date <= {{max_date}}
    ORDER BY date
