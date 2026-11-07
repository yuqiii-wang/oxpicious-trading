-- Dominant zone wall per (date, side) — the skew panel's wall shades pull
-- the whole per-expiry zone history through /walls and collapse it
-- client-side (buildChainWallZones) to ONE zone per (date, side) ranked by
-- the weighted rule strength_score*1e9 + wall_oi (null strength as -1).
-- This endpoint runs the same collapse in SQL (DISTINCT ON, the same
-- weighted rank) so the panel ships one row per (date, side) — the Options
-- Trend cohort chart keeps the per-expiry /walls rows. Validity mirrors the
-- client filter exactly (zone rows with a complete strike range and
-- positive wall OI only); the final expiry_date tiebreak reproduces the
-- client's first-seen-wins order. Serves
-- GET /api/options/walls-dominant.
-- args:
--   where_clause: fragment — the scope WHERE conditions joined " AND "
--     (starts with the fixed zone-validity conditions)
    SELECT DISTINCT ON (date, option_type)
           date, option_type, underlying_code, expiry_date, wall_type,
           wall_strike, wall_oi, wall_low, wall_high, wall_center,
           mass_share, gap_pct, days_persisted, state, strength_score
    FROM analysis.options_walls
    WHERE {{where_clause}}
    ORDER BY date ASC, option_type ASC,
             (COALESCE(strength_score, -1)::float8 * 1e9
              + COALESCE(wall_oi, 0)::float8) DESC,
             expiry_date ASC
