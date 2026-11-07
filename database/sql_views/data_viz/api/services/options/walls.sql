-- Options wall zones — analysis.options_walls (wall_type='zone' only; the
-- legacy 80pct / large_num wall types were removed from the backend).
-- wall_low/high/center are in RAW strike units (same scale as
-- v_options_quote.strike_price). Serves GET /api/options/walls.
-- args:
--   where_clause: fragment — the scope WHERE conditions joined " AND "
--     (starts with the fixed "wall_type = 'zone'")
    SELECT date, option_type, underlying_code, expiry_date, wall_type,
           wall_strike, wall_oi,
           wall_low, wall_high, wall_center,
           mass_share, gap_pct, days_persisted, state, strength_score
    FROM analysis.options_walls
    WHERE {{where_clause}}
    ORDER BY date ASC, expiry_date ASC, option_type ASC
