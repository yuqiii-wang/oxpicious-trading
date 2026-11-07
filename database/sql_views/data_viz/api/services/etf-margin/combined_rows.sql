-- Row data for the page's ETF codes (with optional date filtering) —
-- BASE TABLES + per-table code predicates, NOT stats.v_etf_margin: the
-- view's LEFT JOIN chain defeats ANY-array pushdown (full seq scans of the
-- 1M-row etf_adjustment / etf_liquidity_margin partitions, 2.4-3.8s per
-- exact-code fetch 2026-09-29); a code predicate on EVERY table makes each
-- side an index probe (~5ms). INNER JOIN basic_stats + close IS NOT NULL
-- skips bogus identity-only entries. analysis.dividend_est is LEFT JOINed
-- for the dividend estimation curve (code predicate too); etf_tech_stats
-- is not selected, no join. Serves GET /api/etf-margin combined (also the
-- shared baseline bundle via sec-baseline.service).
-- args:
--   codes: value text[] — the page's bare ETF codes ($1, first bound)
--   sec_type_etf: fragment — LABEL_CODE.sec_type.etf (label_dim code: etf)
--   date_where: fragment — optional " AND i.date >= $n::date" /
--     " AND i.date <= $n::date" bounds (numbering continues after $1)

    SELECT i.code, i.name, i.date,
           o.prev_close, o.open, o.high, o.low, o.close, o.eps,
           a.adj_open, a.adj_high, a.adj_low, a.adj_close, a.adj_prev_close,
           a.is_split_event_day, a.action_type, a.implied_dividend_per_share, a.cum_split_factor,
           dt.div_est_price, dt.seg_end_date, dt.seg_next_div,
           lm.trading_shares, lm.trading_amount,
           lm.rz_balance, lm.rq_balance_qty, lm.rq_balance_amt, lm.total_balance
      FROM stats.etf_identity i
      JOIN stats.etf_basic_stats o
        ON o.date = i.date AND o.code = i.code AND o.code = ANY({{codes}}::text[])
      LEFT JOIN stats.etf_adjustment a
        ON a.date = i.date AND a.code = i.code AND a.code = ANY({{codes}}::text[])
      LEFT JOIN analysis.dividend_est dt
        ON dt.date = i.date AND dt.code = i.code
        AND dt.sec_type = {{sec_type_etf}} -- label_dim code: etf
       AND dt.code = ANY({{codes}}::text[])
      LEFT JOIN stats.etf_liquidity_margin lm
        ON lm.date = i.date AND lm.code = i.code AND lm.code = ANY({{codes}}::text[])
     WHERE i.code = ANY({{codes}}::text[]) AND o.close IS NOT NULL{{date_where}}
     ORDER BY i.code, i.date ASC
