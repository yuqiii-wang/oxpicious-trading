-- Options quote rows for one or more specific trading dates — the same
-- 6-way join as combined.sql (see its header for the terms-driven plan
-- rationale and session settings). The (underlying_code, date) terms index
-- narrows each date to a few hundred rows, then per-row index probes into
-- the joined sub-tables (~90ms per date vs ~1-4s for any full-history
-- aggregate). Feeds the wall / smile / greeks panels and, batched, the
-- StatTable columns. Serves GET /api/options/snapshot-rows.
-- args:
--   where_clause: fragment — the scope WHERE conditions (termsWhere +
--     "t.date = ANY($n::date[])"), joined " AND " (never empty)
    SELECT
        t.date, i.contract_code, i.contract_name, i.exchange,
        t.underlying_code, t.underlying_name, t.option_type,
        t.expiry_month, t.expiry_date, t.days_to_expiry,
        s.strike_price, st.settle, st.underlying_close, st.moneyness_ratio,
        vo.open_interest, vo.volume,
        g.implied_vol, g.delta, g.theta, g.gamma, g.vega, g.rho
    FROM stats.options_terms t
    JOIN stats.options_identity i
        ON i.contract_code = t.contract_code AND i.date = t.date
    LEFT JOIN stats.options_strike s
        ON s.contract_code = t.contract_code AND s.date = t.date
    LEFT JOIN stats.options_settlement st
        ON st.contract_code = t.contract_code AND st.date = t.date
    LEFT JOIN stats.options_greeks g
        ON g.contract_code = t.contract_code AND g.date = t.date
    LEFT JOIN stats.options_volume_oi vo
        ON vo.contract_code = t.contract_code AND vo.date = t.date
 WHERE {{where_clause}} ORDER BY t.date ASC, t.contract_code ASC
