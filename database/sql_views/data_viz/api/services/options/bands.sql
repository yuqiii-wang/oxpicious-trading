-- Options Bands — options-implied bounded-fluctuation band events from
-- analysis.options_bands. Serves GET /api/options/bands.
-- args:
--   where_clause: fragment — the scope WHERE conditions joined " AND "
--     (starts with the fixed "underlying_code = $1")
    SELECT
      date,
      underlying_close::float8 AS underlying_close,
      corridor_low::float8 AS corridor_low,
      corridor_high::float8 AS corridor_high,
      oi_total::float8 AS oi_total,
      spot_pct::float8 AS spot_pct,
      gap_low_pct::float8 AS gap_low_pct,
      gap_high_pct::float8 AS gap_high_pct,
      pos_state,
      lean,
      event,
      new_hi_40d,
      new_lo_40d,
      edge_sig,
      n_expiries,
      skew_all_above,
      skew_all_below,
      min_em::float8 AS min_em,
      mean_em::float8 AS mean_em,
      em5_pct::float8 AS em5_pct,
      ret5_pct::float8 AS ret5_pct,
      exhausted_5d
    FROM analysis.options_bands
    WHERE {{where_clause}}
    ORDER BY date ASC
