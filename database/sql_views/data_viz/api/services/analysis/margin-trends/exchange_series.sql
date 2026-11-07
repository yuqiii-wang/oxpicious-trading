-- Margin Trends 'exchange' attribution — the exchange's OWN market-wide
-- margin totals from analysis.exchange_margin_summary (ONE series, code =
-- the exchange code 'SS'/'SZ'; the industry_id param carries the exchange
-- code). These are the OFFICIAL exchange summary totals. The mapped
-- underlying (user-selectable broad-market index, ?underlying=) is
-- resolved + validated INSIDE the SQL — a missing / non-broad code falls
-- back to 沪深300 (000300) so one round-trip both resolves and guards the
-- code; close/trading_amount/regime join from the underlying's
-- index_basic_stats / market_regimes rows.
-- Serves GET /api/analysis/margin-trends/industry-series?attribution=exchange
-- args:
--   exchange: value text — the exchange code ('SS' / 'SZ')
--   underlying: value text — the requested broad-market underlying code
--   default_underlying: fragment — DEFAULT_EXCHANGE_UNDERLYING ("000300",
--     substituted inside the ELSE string literal)
--   regime_decode: fragment — labelDecodeSql("regime", "r.regime")
  WITH underlying AS (
    SELECT u.code,
           COALESCE(NULLIF(sc.name, ''), u.code) AS name
    FROM (
      -- The requested underlying survives only when it carries a
      -- broad-market tag (the tag lookup itself yields the code or NULL →
      -- COALESCE falls back to 沪深300): missing / non-broad codes resolve
      -- to the default inside the SQL, one round-trip resolves + guards.
      SELECT COALESCE(
        (SELECT t.code FROM stats.sec_index_tags t
          WHERE t.code = {{underlying}}::text AND t.is_broad_market = TRUE
          LIMIT 1),
        '{{default_underlying}}') AS code
    ) u
    LEFT JOIN LATERAL (
      SELECT name FROM stats.sec_classification
      WHERE code = u.code AND type = 'index' AND is_active = TRUE
      LIMIT 1
    ) sc ON TRUE
  )
  SELECT em.exchange, em.date, em.rz_balance, em.rz_buy,
         ib.close, ib.trading_amount, mr.regime,
         (SELECT code FROM underlying) AS underlying_code,
         (SELECT name FROM underlying) AS underlying_name
  FROM analysis.exchange_margin_summary em
  LEFT JOIN LATERAL (
    SELECT close, trading_amount FROM stats.index_basic_stats b
    WHERE b.code = (SELECT code FROM underlying) AND b.date = em.date
    LIMIT 1
  ) ib ON TRUE
  LEFT JOIN LATERAL (
    SELECT {{regime_decode}} AS regime
    FROM stats.market_regimes r
    -- sec_type stores its static.label_dim SMALLINT code (1 = index)
    WHERE r.code = (SELECT code FROM underlying) AND r.date = em.date AND r.sec_type = 1
    LIMIT 1
  ) mr ON TRUE
  WHERE em.exchange = {{exchange}}::text
  ORDER BY em.date
