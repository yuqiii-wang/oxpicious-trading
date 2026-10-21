/**
 * Shared constants for the MA-Spread analysis page sub-modules.
 */
import type { CSSProperties } from "react";
import { MUTED_PALETTE } from "@/theme/chart-palette";
import type {
  MovAveSpreadMomentumDay,
} from "@shared/types";
import type { MomentumTerm } from "./chartOption";

/**
 * Page size — number of MaSpreadPanel cards shown per page. Kept small
 * because each panel renders one chart with a date-range slider and a row
 * of 9 pair chips, so larger pages get unwieldy.
 */
export const PAGE_SIZE = 1;

/** Bollinger multiplier options for the top-right dropdown (0.0 … 3.0, step 0.5).
 *  0.0 = band hidden; 2.0 = standard Bollinger. */
export const BOLL_K_OPTIONS = [0, 0.5, 1, 1.5, 2, 2.5, 3];

/** The RSI Top-% shade overlays' base opacity — one qualifying window draws
 *  the flat base fill, each additional window stacking on the same date
 *  multiplies it (capped; see shadeBuilders.buildRsiRankShades). */
export const RSI_SHADE_BASE_OPACITY = 0.18;

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

/**
 * The momentum lines' colors — one per term, MUTED_PALETTE tokens
 * (distinct from the RSI window lines' blue→teal [0..5] ramp; the two
 * overlays are never drawn simultaneously).
 */
export const MOMENTUM_TERM_COLORS: Record<MomentumTerm, string> = {
  "5d": MUTED_PALETTE[1],
  "20d": MUTED_PALETTE[2],
  short: MUTED_PALETTE[5],
  mid: MUTED_PALETTE[6],
  long: MUTED_PALETTE[7],
};

/** Term → the momentum row's value column. */
export const MOMENTUM_VALUE_KEYS: Record<
  MomentumTerm,
  "momentum_5days" | "momentum_20days" | "momentum_short" | "momentum_mid" | "momentum_long"
> = {
  "5d": "momentum_5days",
  "20d": "momentum_20days",
  short: "momentum_short",
  mid: "momentum_mid",
  long: "momentum_long",
};

/** Adaptive term → the momentum row's effective-lookback column. */
export const MOMENTUM_LB_KEYS = {
  short: "lb_short",
  mid: "lb_mid",
  long: "lb_long",
} as const;

/** Chip label of a momentum term — the adaptive short/mid/long spell
 *  their CURRENT effective lookback (the latest row's lb_*) once the
 *  group has landed, e.g. "short (14d)"; the fixed anchors stay 5d/20d. */
export function momentumChipLabel(
  term: MomentumTerm,
  rows: MovAveSpreadMomentumDay[] | undefined,
): string {
  if (term === "5d" || term === "20d") return term;
  const last = rows?.at(-1);
  return last == null ? term : `${term} (${last[MOMENTUM_LB_KEYS[term]]}d)`;
}

/**
 * Long-MA column order used to lay out the 9 pair chips as a 2-row grid
 * aligned by long MA (so Price/MA60 and MA5/MA60 share one column). The
 * MA5 row leaves the MA5 column empty (no MA5/MA5 pair exists).
 */
export const LONG_MA_ORDER = [5, 20, 60, 120, 255] as const;

/**
 * Long-EMA column order for the 9 EMA pair chips. EMA windows are
 * 6/20/60/120/255 (EMA6 replaces MA5 — EMAs use 6 instead of 5 as the
 * short window). Same structure as LONG_MA_ORDER but with 6 in column 0.
 */
export const LONG_EMA_ORDER = [6, 20, 60, 120, 255] as const;

/**
 * Shared period-column grid — one column per period (5, 20, 60, 120, 255,
 * 500, 750, 1275) so every button row aligns vertically by period. Pair
 * rows only fill the first 5 columns (their long MAs stop at 255); the
 * Roof/Floor Trendlines row spans the full width (its 60d … 1275d buttons
 * continue into the extra columns under their matching periods).
 */
export const PERIOD_GRID_SX: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(8, minmax(0, 1fr))",
  gap: 0.75,
  alignItems: "center",
};

/**
 * Shared Chip sx for every period button (pair chips + roof/floor-window
 * buttons): fill its grid cell with a centered label, compact size.
 */
export const PERIOD_CHIP_SX: CSSProperties = {
  fontSize: "0.7rem",
  height: 24,
  width: "100%",
  display: "flex",
  justifyContent: "center",
};
