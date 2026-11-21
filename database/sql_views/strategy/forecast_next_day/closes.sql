-- The run's codes' trailing daily closes (the mov_ave_spread price
-- convention — ETF adjusted close when available), oldest-first, with
-- the analysis engines' own OHLC-estimated-row exclusion. The forecast
-- module's ONE price history read: the σ20 ladder, the synthetic-day
-- value recompute (Wilder RSI / boll z / momentum anchors / MA gaps /
-- hv / ewma) and the close ladder itself all build on these rows.
-- args:
--   price_base: fragment — the PRICE_SOURCE base table/alias per sec_type
--   price_expr: fragment — the PRICE_SOURCE price expression per sec_type
--   ohl_est_filter: fragment — the OHL_EST_FILTER exclusion per sec_type
--   codes: value text[] — the run's codes
--   row_limit: value int — the per-code trailing row cap (covers the
--     2520d bar windows + the 252d MA gap windows + warmup)
SELECT code, date, close FROM (
    SELECT b.code AS code, b.date AS date,
           {{price_expr}}::float8 AS close,
           ROW_NUMBER() OVER (PARTITION BY b.code ORDER BY b.date DESC) AS rn
    FROM {{price_base}}
    WHERE b.code = ANY({{codes}})
      AND b.close IS NOT NULL{{ohl_est_filter}}
) ranked
WHERE rn <= {{row_limit}}
ORDER BY code, date
