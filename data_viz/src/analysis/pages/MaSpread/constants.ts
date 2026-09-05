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
 * Rolling-OHLC window buttons (trading days) shown beneath the Trading
 * Amt/MA pair row. Mirrors OHLC_WINDOWS in analyze/mov_ave_spread/config.py
 * and the analysis.mov_ave_spreads_detail_ohlc column families — clicking a
 * button enables that window's rolling High/Low envelope and arms the
 * roof/floor trendline interaction (click a date on the chart to draw).
 */
export const OHLC_WINDOWS = [20, 60, 120, 255, 500, 750, 1275] as const;

/**
 * Market-hype check-in window buttons (trading days) shown beneath the OHLC
 * Window row. Mirrors HYPE_CHECKIN_PERIODS in
 * analyze/mov_ave_spread/config.py and the analysis.mov_ave_market_hypes
 * min_checkin_period values. Each window is an episode-span BUCKET:
 * min_checkin_period is the bucket's minimum span and the next window its
 * exclusive maximum (5d: 5-19 rows; 20d: 20-59; 60d: 60-119; 120d: 120-254;
 * 255d: 255-5100 = the whole ±10y base) — one calendar turmoil lands in
 * exactly the bucket matching its length.
 * Clicking a button shades the chart's hyped date periods (light purple) for
 * that check-in window; the latest date's hyped state is reported in the
 * caption below the buttons (the buttons themselves use the standard chip
 * style shared with every other button row).
 */
export const HYPE_WINDOWS = [5, 20, 60, 120, 255] as const;

/**
 * High/low band-BREAK streak lookback buttons (trading rows) — the first
 * layer of the nested High/Low Streaks row. Mirrors
 * HIGH_LOW_PCT_PERIODS in analyze/mov_ave_spread/config.py and the
 * analysis.mov_ave_high_low_pct period values.
 */
export const HIGH_LOW_STREAK_PERIODS = [255, 500, 750, 1275] as const;

/**
 * High/low band-BREAK streak tightness buttons (percent) — the second
 * layer, expanded when a period is selected. pct_type p of the band keeps
 * daily LOWs above their p-th percentile (low_val) and daily HIGHs below
 * their (100-p)-th percentile (high_val); a streak is a maximal run of
 * closes outside the band (up to 5 in-band days bridged).
 */
export const HIGH_LOW_STREAK_PCTS = [1, 5, 10] as const;
