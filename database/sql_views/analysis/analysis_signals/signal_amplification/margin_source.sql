-- The signal-amplification tier's margin source frame (the 'margin'
-- parent type, 2026-11-20): one row per (code, date) with the day's
-- 融资余额 margin balance — etf/stock read their own
-- stats.*_liquidity_margin.rz_balance (NULL rows = no margin data
-- ingested for the code, excluded here); indices read the
-- analysis.margins package's weighted constituent aggregate
-- (analysis.margin_index_series.index_margin_balance). Codes absent
-- from the source simply carry no margin anatomy (the engine's LEFT
-- join leaves the margin columns NULL — no margin amplifier days).
-- serves: analysis.analysis_signals.signal_amplification (MarginAnatomy)
-- args:
--   codes: value text[] — the sorted requested codes ($1)
--   since: value date — the lower date bound ($2)
--   margin_from: fragment — the FROM clause (the sec_type's margin source table, aliased m)
--   code_expr: fragment — the margin source's code column (m.code / m.index_code)
--   balance_expr: fragment — the margin-balance expression (m.rz_balance / m.index_margin_balance)
SELECT {{code_expr}} AS code,
       extract(epoch from m.date)::float8 AS date,
       {{balance_expr}}::float8 AS margin_balance
FROM {{margin_from}}
WHERE {{code_expr}} = ANY({{codes}}::text[])
  AND m.date >= {{since}}
  AND {{balance_expr}} IS NOT NULL
ORDER BY {{code_expr}}, m.date ASC
