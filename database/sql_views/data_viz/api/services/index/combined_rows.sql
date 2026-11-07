-- Row data for the page's indices (with optional date filtering) — BASE
-- TABLES + per-table code predicates, NOT stats.v_index_daily: the view's
-- LEFT JOIN chain defeats ANY-array pushdown (full scans of the
-- index_basic_stats / index_valuation / index_tech_stats partitions,
-- 0.68s cold per exact-code fetch 2026-09-29); a code predicate on EVERY
-- table makes each side an index probe (~17ms). INNER JOIN basic_stats
-- skips bogus identity-only rows (incomplete same-day entries with NULL
-- close). Serves GET /api/index/combined (also the shared baseline bundle
-- via sec-baseline.service).
-- args:
--   codes: value text[] — the page's index codes ($1)
--   date_where: fragment — optional " AND i.date >= $n::date" /
--     " AND i.date <= $n::date" bounds (numbering continues after $1)

    SELECT i.code, i.date,
           bs.open, bs.high, bs.low, bs.close, bs.trading_shares, bs.trading_amount, bs.change_pct,
           bs.has_intraday_5mins,
           v.pe, v.cons_number,
           t.ma5, t.ma20, t.ma60, t.ma120, t.ma255
      FROM stats.index_identity i
      JOIN stats.index_basic_stats bs
        ON bs.date = i.date AND bs.code = i.code AND bs.code = ANY({{codes}}::text[])
      LEFT JOIN stats.index_valuation v
        ON v.date = i.date AND v.code = i.code AND v.code = ANY({{codes}}::text[])
      LEFT JOIN stats.index_tech_stats t
        ON t.date = i.date AND t.code = i.code AND t.code = ANY({{codes}}::text[])
     WHERE i.code = ANY({{codes}}::text[]){{date_where}}
     ORDER BY i.code, i.date ASC
