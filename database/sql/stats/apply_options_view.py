"""Apply v_options_quote view change: expose underlying_target_type + exchange."""
from __future__ import annotations
import asyncio
from _common.build_commons import setup_utf8_stdout, get_db_or_exit

setup_utf8_stdout()

_VIEW_SQL = """
DROP VIEW IF EXISTS stats.v_options_quote;
CREATE VIEW stats.v_options_quote AS
SELECT
    i.date,
    i.contract_code,
    i.contract_name,
    -- Terms
    t.underlying_code,
    t.underlying_name,
    t.underlying_target_type,
    t.exchange,
    t.option_type,
    t.expiry_month,
    t.expiry_date,
    t.days_to_expiry,
    -- Strike
    s.strike_str,
    s.strike_price_raw,
    s.strike_price,
    s.has_a_suffix,
    -- Settlement
    st.prev_settle,
    st.close,
    st.settle,
    st.pct_change,
    st.prev_settle_norm,
    st.close_norm,
    st.settle_norm,
    st.underlying_close,
    st.moneyness_ratio,
    -- Greeks
    g.implied_vol,
    g.delta,
    g.theta,
    g.gamma,
    g.vega,
    g.rho,
    -- Volume & OI
    vo.volume,
    vo.volume_wan,
    vo.open_interest,
    vo.open_interest_wan,
    -- Aggregate
    a.total_volume_underlying,
    a.total_oi_underlying,
    a.volume_pct,
    a.open_interest_pct,
    a.oi_call_put_ratio,
    a.vol_call_put_ratio,
    a.open_interest_call,
    a.open_interest_put,
    a.volume_call,
    a.volume_put,
    a.oi_total_call_put_ratio
FROM stats.options_identity i
LEFT JOIN stats.options_terms t ON i.date = t.date AND i.contract_code = t.contract_code
LEFT JOIN stats.options_strike s ON i.date = s.date AND i.contract_code = s.contract_code
LEFT JOIN stats.options_settlement st ON i.date = st.date AND i.contract_code = st.contract_code
LEFT JOIN stats.options_greeks g ON i.date = g.date AND i.contract_code = g.contract_code
LEFT JOIN stats.options_volume_oi vo ON i.date = vo.date AND i.contract_code = vo.contract_code
LEFT JOIN stats.options_aggregate a ON i.date = a.date AND i.contract_code = a.contract_code;

COMMENT ON VIEW stats.v_options_quote IS 'Reconstructed options_quote view: JOIN of options_identity + all options sub-tables.';
"""

async def apply():
    conn = await get_db_or_exit()
    try:
        print("[VIEW] Recreating stats.v_options_quote with target_type + exchange …", flush=True)
        await conn.execute(_VIEW_SQL)
        print("    Done.", flush=True)

        rows = await conn.fetch("""
            SELECT underlying_target_type, exchange, COUNT(*) AS n
            FROM stats.v_options_quote
            GROUP BY underlying_target_type, exchange
        """)
        print("[VERIFY] View exposes:", flush=True)
        for r in rows:
            print(f"    {r['underlying_target_type']}/{r['exchange']}: {r['n']} rows", flush=True)
    finally:
        await conn.close()

if __name__ == "__main__":
    asyncio.run(apply())
