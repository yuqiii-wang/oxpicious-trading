-- OI-weighted moneyness skew groups for ONE underlying — read from the
-- materialized analysis.options_moneyness_series table (built by
-- ``python -m analysis.options`` PIPELINE 8, replicating the former live
-- compute's semantics; A/B-verified 2026-10-04). The materialized read is
-- an indexed per-underlying scan (single-digit ms) vs the former live
-- query's 5-way join over the full options history (~4.8s cold). Serves
-- GET /api/options/oi-moneyness.
-- args:
--   underlying_code: value text — the stripped underlying code ($1)
--   range_clause: fragment — optional " AND date >= $n::date" /
--     " AND date <= $n::date" bounds (numbering continues after $1)
      SELECT date, expiry_month, expiry_date, n_valid,
             e_m::float8 AS e_m,
             wsum_k::float8 AS wsum_k, wsum::float8 AS wsum,
             call_oi::float8 AS call_oi, put_oi::float8 AS put_oi,
             call_otm_oi::float8 AS call_otm_oi, put_otm_oi::float8 AS put_otm_oi
      FROM analysis.options_moneyness_series
      WHERE underlying_code = {{underlying_code}}{{range_clause}}
      ORDER BY date ASC, expiry_month ASC
