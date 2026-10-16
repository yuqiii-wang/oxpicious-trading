/**
 * Shared constants for the MA-Spread analysis page sub-modules.
 */

/**
 * Page size — number of MaSpreadPanel cards shown per page. Kept small
 * because each panel renders one chart with a date-range slider and a row
 * of 9 pair chips, so larger pages get unwieldy.
 */
export const PAGE_SIZE = 1;

/**
 * The Roof/Floor Trendlines row's windows (trading days) — the clickable
 * period buttons. The 20d is NOT a roof/floor window (too short: its
 * top + 2nd extrema sit almost on the same bar, so the trendlines
 * degenerate) — it lives only in the DB table (the shared tooltip
 * metrics' 20d pivot reads the same lines table).
 */
export const ROOF_FLOOR_WINDOWS = [60, 120, 255, 500, 750, 1275] as const;

/**
 * Chip labels of the Roof/Floor section's Est-levels method picker — one
 * chip per estimate method of analysis.mov_ave_spreads_roof_floor_
 * estimates (ROOF_FLOOR_ESTIMATE_METHODS in shared/types; median = the
 * cross-method consensus and the default pick).
 */
export const RF_ESTIMATE_METHOD_LABELS: Record<string, string> = {
  median: "Median",
  trendline: "Trendline",
  donchian: "Donchian",
  quantile: "Quantile",
  channel: "Channel",
  boll: "Boll",
};

/** The default estimate method (the cross-method consensus). */
export const RF_ESTIMATE_DEFAULT_METHOD = "median";

/**
 * High/low band-BREAK streak lookback buttons (trading rows) — the first
 * layer of the nested High/Low Streaks row. Mirrors
 * HIGH_LOW_PCT_PERIODS in analysis/mov_ave_spread/config.py and the
 * analysis.mov_ave_high_low_pct period values.
 */
export const HIGH_LOW_STREAK_PERIODS = [60, 120, 255, 500, 750, 1275] as const;

/**
 * High/low band-BREAK streak tightness buttons (percent) — the second
 * layer, expanded when a period is selected. pct_type p of the band keeps
 * daily LOWs above their p-th percentile (low_val) and daily HIGHs below
 * their (100-p)-th percentile (high_val); a streak is a maximal run of
 * closes outside the band (up to 5 in-band days bridged).
 */
export const HIGH_LOW_STREAK_PCTS = [1, 5, 10] as const;

/**
 * Wilder-RSI trend-line windows (trading days) of the chart's RSI Trends
 * overlay. Mirrors RSI_WINDOWS in analysis/analysis_forecasts/config/buckets.py
 * and the analysis.mov_ave_rsi rsi_*days columns — one drawn line per window
 * (short → long), fed by the chart response's shared block.
 */
export const RSI_WINDOWS = [3, 6, 10, 14, 20, 60] as const;

/**
 * The RSI Trends section's Top-% shade row (analysis.mov_ave_rsi_ranks
 * consumption): a pick SPLITS the pct evenly across the two ends of the
 * code's own RSI-rank axis (treated as a signed scale) — green bands where
 * the RSI ranked within the top pct/2 of its OWN history (rises; per window
 * the cutoff is rank ≤ ceil(pct/2 × N_w), N_w = the window's count of
 * ranked days), red where within the bottom pct/2 (drops; rank >
 * N_w − cutoff). The section's days row scopes the ranks — picked windows
 * only; no day picked walks any window. Off via the row's Off entry.
 */
export const RSI_TOP_PCTS = [0.01, 0.02, 0.03, 0.05, 0.1] as const;

/**
 * The Momentum section's term buttons — the two fixed-anchor lookbacks
 * (5/20 trading days) plus the three ADAPTIVE terms whose lookback n is
 * derived per (code, market regime) from the regime slice's median
 * volatility via equal-expected-move targeting (analysis.mov_ave_
 * momentum's lb_short / lb_mid / lb_long columns record each day's
 * effective lookback). Multi-pick; the picked terms' lines replace the
 * amt bars on the right axis (below RSI Trends / Slopes / Curvatures in
 * the slot's precedence).
 */
export const MOMENTUM_TERMS = ["5d", "20d", "short", "mid", "long"] as const;
