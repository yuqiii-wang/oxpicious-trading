-- margins — full per-(code, date) rz_balance + rz_buy history for
-- the universe codes (MA windows need full history; the rongzi
-- columns only — rongqin intentionally excluded per spec).
-- args:
--   src_table: fragment — the sec_type's margin source table
--       (stats.etf_liquidity_margin / stats.stock_liquidity_margin)
--   codes: value text[] — the universe codes

        SELECT code, date, rz_balance, rz_buy
        FROM {{src_table}}
        WHERE code = ANY({{codes}}::text[])
        ORDER BY code, date
        