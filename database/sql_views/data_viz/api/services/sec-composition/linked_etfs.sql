-- Linked ETFs — ETFs tracking a given index (stats.sec_classification
-- type='etf' WHERE parent_index_code = the index), enriched with the latest
-- close price + trading-day count from stats.v_etf_margin. Four CTEs:
--   linked_etfs   — ETF rows whose parent_index_code = the index; also
--                   pulls aum_yi (net asset value in 亿元, from
--                   etf_index_map_all_*.csv via build_classification.py —
--                   available for ALL ETFs, unlike v_etf_margin
--                   trading_amount which is SZSE only).
--   latest        — DISTINCT ON (code) most-recent v_etf_margin row per ETF.
--   counts        — per-ETF trading-day count.
--   feeder_counts — per-ETF feeder fund count from sec_etf_links (lets the
--                   unified table know which rows are expandable without
--                   fetching every feeder list).
-- Serves the Index page's "Linked ETFs" expansion beside the Composition
-- pie (GET /api/sec-composition/linked-etfs). Input is a bare index code.
-- args:
--   index_code: value text — the tracking index code
    WITH linked_etfs AS (
      SELECT sc.code, sc.name, sc.exchange, sc.sector_label, sc.industry_label, sc.aum_yi
        FROM stats.sec_classification sc
       WHERE sc.type = 'etf'
         AND sc.parent_index_code = {{index_code}}
    ),
    latest AS (
      SELECT DISTINCT ON (code) code, date AS latest_date, close AS latest_close, trading_amount AS latest_trading_amount
        FROM stats.v_etf_margin
       WHERE code IN (SELECT code FROM linked_etfs)
       ORDER BY code, date DESC
    ),
    counts AS (
      SELECT code, COUNT(*) AS n_days
        FROM stats.v_etf_margin
       WHERE code IN (SELECT code FROM linked_etfs)
       GROUP BY code
    ),
    feeder_counts AS (
      SELECT l.target_etf_code, COUNT(*) AS feeder_count
        FROM stats.sec_etf_links l
       WHERE l.target_etf_code IN (SELECT code FROM linked_etfs)
       GROUP BY l.target_etf_code
    )
    SELECT le.code,
           le.name,
           le.exchange,
           COALESCE(le.sector_label,    '其他')   AS sector_label,
           COALESCE(le.industry_label, '未分类')  AS industry_label,
           COALESCE(la.latest_date::text, '')    AS latest_date,
           la.latest_close,
           la.latest_trading_amount,
           le.aum_yi,
           COALESCE(co.n_days, 0)                AS n_days,
           COALESCE(fc.feeder_count, 0)          AS feeder_count
      FROM linked_etfs le
      LEFT JOIN latest        la ON la.code = le.code
      LEFT JOIN counts        co ON co.code = le.code
      LEFT JOIN feeder_counts fc ON fc.target_etf_code = le.code
     ORDER BY le.aum_yi DESC NULLS LAST, n_days DESC, le.code
