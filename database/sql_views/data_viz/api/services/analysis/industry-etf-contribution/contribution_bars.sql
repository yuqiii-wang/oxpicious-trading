-- ETF Contribution — one row per ETF for a given (industry_id, date),
-- each row carrying the ETF's trading_amount (capital flow) and
-- fractional daily return (LATERAL prev-day close), plus the industry
-- aggregate from analysis.industry_etf_contribution (pool_size = 3, the
-- 'all' slice) for context. target_date NULL falls back to the latest
-- materialized date for the industry. Drives the 2nd+ plots in
-- "ETF Contribution" mode (one bar chart per selected industry).
-- Serves GET /api/analysis/industry-etf-contribution/etf-bars.
-- args:
--   industry_id: value text — the industry whose ETF bars to list
--   target_date_param: fragment — the raw `$2::date` placeholder. The
--     original SQL numbered the date as the LAST pg param while it
--     appears FIRST in the text, but the loader numbers value slots by
--     first text use — so the placeholder is carried verbatim and the
--     service binds the params array itself
    WITH target_date AS (
      SELECT COALESCE(
        {{target_date_param}},
        (SELECT MAX(date) FROM analysis.industry_etf_contribution
         WHERE industry_id = {{industry_id}}::text AND pool_size = 3)
      ) AS max_date
    ),
    member_indices AS (
      SELECT DISTINCT code
      FROM stats.sec_classification
      WHERE type = 'index' AND is_active = TRUE AND industry_id = {{industry_id}}::text
    ),
    etf_codes AS (
      SELECT DISTINCT
        sc.code AS etf_code,
        sc.name AS etf_name,
        sc.parent_index_code
      FROM stats.sec_classification sc
      JOIN member_indices mi ON mi.code = sc.parent_index_code
      WHERE sc.type = 'etf'
        AND sc.is_active = TRUE
        AND sc.is_primary_exchange = TRUE
        AND sc.parent_index_code <> ''
    )
    SELECT
      ec.etf_code,
      ec.etf_name,
      ec.parent_index_code,
      elm.trading_amount,
      (ebs.close - pb.close) / NULLIF(pb.close, 0) AS etf_return,
      ld.max_date AS date,
      sc2.industry_label,
      iec.industry_etf_trading_amount,
      iec.industry_etf_trading_amount_ma5,
      iec.industry_etf_trading_amount_ma20
    FROM etf_codes ec
    CROSS JOIN target_date ld
    LEFT JOIN LATERAL (
      SELECT trading_amount FROM stats.etf_liquidity_margin
      WHERE code = ec.etf_code AND date = ld.max_date
    ) elm ON true
    LEFT JOIN LATERAL (
      SELECT close FROM stats.etf_basic_stats
      WHERE code = ec.etf_code AND date = ld.max_date
    ) ebs ON true
    LEFT JOIN LATERAL (
      SELECT close FROM stats.etf_basic_stats
      WHERE code = ec.etf_code AND date < ld.max_date
      ORDER BY date DESC LIMIT 1
    ) pb ON true
    LEFT JOIN LATERAL (
      SELECT industry_label
      FROM stats.sec_classification
      WHERE industry_id = {{industry_id}}::text AND type = 'index'
        AND industry_label IS NOT NULL
      LIMIT 1
    ) sc2 ON true
    LEFT JOIN LATERAL (
      SELECT industry_etf_trading_amount,
             industry_etf_trading_amount_ma5,
             industry_etf_trading_amount_ma20
      FROM analysis.industry_etf_contribution
      WHERE industry_id = {{industry_id}}::text AND pool_size = 3 AND date = ld.max_date
    ) iec ON true
    ORDER BY elm.trading_amount DESC NULLS LAST
