-- ETF Contribution — daily close series for ALL ETFs tracking member
-- indices of the user-selected industries (1st plot; each line = one
-- ETF, rebased to 100 at its own first date client-side). Price uses
-- COALESCE(adj_close, close) so share splits / dividend deliveries don't
-- appear as large drops; trading_amount comes from the daily liquidity
-- table. Ordered by etf_code then date so the frontend can group-by ETF.
-- Serves GET /api/analysis/industry-etf-contribution/etf-price.
-- args:
--   industry_ids: value text[] — the selected industry ids
    WITH member_indices AS (
      SELECT DISTINCT code, industry_id, industry_label
      FROM stats.sec_classification
      WHERE type = 'index'
        AND is_active = TRUE
        AND industry_id = ANY({{industry_ids}}::text[])
        AND industry_id IS NOT NULL
        AND industry_id <> ''
    ),
    etf_links AS (
      SELECT DISTINCT
        sc.code      AS etf_code,
        sc.name      AS etf_name,
        sc.parent_index_code,
        mi.industry_id,
        mi.industry_label
      FROM stats.sec_classification sc
      JOIN member_indices mi ON mi.code = sc.parent_index_code
      WHERE sc.type = 'etf'
        AND sc.is_active = TRUE
        AND sc.is_primary_exchange = TRUE
        AND sc.parent_index_code <> ''
    )
    SELECT
      el.etf_code,
      el.etf_name,
      el.parent_index_code,
      el.industry_id,
      el.industry_label,
      ebs.date,
      COALESCE(ea.adj_close, ebs.close) AS close,
      elm.trading_amount
    FROM etf_links el
    JOIN stats.etf_basic_stats ebs ON ebs.code = el.etf_code
    LEFT JOIN stats.etf_adjustment ea ON ea.code = el.etf_code AND ea.date = ebs.date
    LEFT JOIN stats.etf_liquidity_margin elm ON elm.code = el.etf_code AND elm.date = ebs.date
    ORDER BY el.etf_code, ebs.date ASC
