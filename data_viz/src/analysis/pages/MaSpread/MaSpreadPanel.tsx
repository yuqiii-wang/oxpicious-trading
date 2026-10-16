/**
 * MaSpreadPanel — one card per code: pair chips + two-curve chart with
 * green/red fill between them + date-range slider + Bollinger envelope.
 *
 * Each panel renders (top → bottom):
 *   1. 9 pair chips arranged as a 2-row grid aligned by long MA — the Price
 *      row (Price/MA5 … Price/MA255) above the MA5 row (MA5/MA20 …
 *      MA5/MA255, with the MA5 column empty). Clicking a chip selects
 *      the pair shown in the chart below.
 *   2. Two-curve chart (short + long MA) with green fill when short > long
 *      (growth) and red fill when short < long (decline). The tooltip shows
 *      each series' slope (1st derivative) and curvature (2nd derivative)
 *      — including price's own slope/curvature for Price/MA pairs.
 *   3. Bollinger envelope (Price/MA pairs only): ±k×σ dashed lines around
 *      the long MA, with a faint fill between them. k is selected from a
 *      dropdown in the card's top-right corner (0 = hidden, 2 = standard
 *      Bollinger, max 3, step 0.5). MA5/MA pairs do not show the envelope
 *      (σ is of price, not of an MA-of-MA) and the dropdown is hidden.
 *   4. Roof/Floor Trendlines section beneath the RSI Trends section — a
 *      "Roof/Floor Trendlines" row label (same style as the Trading Amt/MA
 *      label) on its own full-width row, with the window buttons (60 …
 *      1275d — the 20d is not a roof/floor window) in the row's first
 *      column on a new row below it. The buttons keep
 *      the period-column alignment of the pair chips — 60d sits under the
 *      MA60 column, …, 1275d in the last column. Clicking one enables that rolling window's High/Low
 *      envelope on the chart and arms the roof/floor interaction —
 *      clicking a date on the chart draws the trendline through the
 *      window's top + 2nd highs (the roof) and top + 2nd lows (the floor)
 *      from history, converging and stopping at the clicked date (two
 *      points determining a line). The row's "Est levels" toggle adds the
 *      QUANTITATIVE roof/floor level estimate for the chosen date
 *      (analysis.mov_ave_spreads_roof_floor_estimates — trendline /
 *      donchian / quantile / channel / boll / median method chips) as
 *      two horizontal dashed lines. Clicking a window button is
 *      EXCLUSIVE: every other section's picks clear (RSI Trends, High/
 *      Low Streaks, Px-Vol States, Top-10% OHLC Days, Market Regimes,
 *      Trend), the header overlay returns to Amt, an Amt/MA envelope view
 *      falls back to Price/MA5 — and the trading-amt bars yield the
 *      right axis to the window's change-since-last-anchor plot (one
 *      plot, dashed zero central line: Δ Low(Wd) = close − last low
 *      anchor above it as positive, Δ High(Wd) = close − last high
 *      anchor below it as negative).
 *   5. High/Low Streaks section beneath the Roof/Floor Trendlines row —
 *      NESTED
 *      buttons: the first layer holds the band lookback periods
 *      (60/120/255/500/750/1275, period-column aligned with the OHLC
 *      row);
 *      clicking one expands a second layer of band tightness pcts
 *      (1/5/10%). Selecting a pct fills the LATEST date's trailing
 *      period-row window with its top/bottom pct% price zones (light
 *      purple above high_val, light yellow below low_val) and draws the
 *      WHOLE-WINDOW LONG BREAK STREAK per side — the in-window DB streaks
 *      merged into ONE span (first start → last end, gaps between them
 *      tolerated), shaded as a single horizontal band from the window's
 *      constant band edge to the merged extreme. Drawing each DB streak
 *      separately (each to its own peak against its own month's moving
 *      band) fractured into slivers, so only the merged span is drawn.
 *      Toggled per side via the chart legend. Clicking
 *      a chart date anchors the window to the trailing rows before that
 *      date ("show before that date"; click again to return to the
 *      latest). The caption reports the window span and each side's
 *      streak span / streak count / days / extreme.
 *   6. Date-range slider at the bottom of the plot — drives all 9 pairs
 *      (they share one date axis).
 *   7. Px-Vol States section beneath the High/Low Streaks row — the
 *      trading amt × price-change state family (the
 *      analysis_forecasts.px_vol_state engine, recomputed client-side from
 *      the chart rows — the DB stores bucket aggregates only): layer 1 =
 *      price speed (sharp/slow/flat rise/drop), layer 2 = amount state
 *      (increasing/flat/decreasing). Picking one of each shades the
 *      matching dates — green rise / red drop / gray flat, shade depth by
 *      combo strength (sharp × heavy = darkest, e.g. rising price +
 *      increasing amount = strong growth).
 *   8. RSI Trends section beneath the Trading Amt/MA section — the
 *      right-axis RSI overlay's own row (moved out of the header
 *      Amt/Slopes/Curvatures toggle), resting fully OFF: the days row
 *      multi-picks the Wilder-RSI window lines (3/6/10/14/20/60d — any
 *      pick replaces the amt bars while the header toggle sits at Amt;
 *      Slopes/Curvatures keep precedence) AND scopes the pct row's
 *      ranks; the first day pick AUTO-arms the 1% shade, the last day
 *      un-picked takes it off. The pct row single-picks the Top-%
 *      history-rank shade (analysis.mov_ave_rsi_ranks) — split evenly
 *      across the rank axis treated as signed: green where the RSI
 *      ranked within the top pct/2 of the picked windows' own history
 *      (rises), red where within the bottom pct/2 (drops); one
 *      qualifying window draws the base fill, several overlapping on a
 *      date stack it darker.
 *   9. Momentum section beneath the RSI Trends caption — the
 *      lookback-adaptive momentum row (analysis.mov_ave_momentum):
 *      multi-picks the two FIXED anchors (5d / 20d) and the three
 *      volatility-adaptive short/mid/long terms whose lookback n is
 *      derived per (code, market regime) via equal-expected-move
 *      targeting over the regime slice's median EWMA vol. Any pick
 *      replaces the amt bars with the picked terms' lines on the
 *      offset right axis (band-mapped signed ratios, zero guide —
 *      below RSI Trends / Slopes / Curvatures in the slot's
 *      precedence); the caption spells the latest day's effective
 *      lookbacks + regime, and the tooltip adds each adaptive term's
 *      per-date lookback. Its extras group auto-loads with the chart
 *      blocks so the adaptive chips can spell their current effective
 *      lookbacks (e.g. "short (14d)") before any pick.
 *  10. Trend section beneath the Market Regimes row — the signals
 *      tier's trend phase chips (top / bottom / rise / fall, multi-pick;
 *      analysis.trend_days via the trend-spans API — the RSI re-entry
 *      classification shared with the rule cells, replacing the retired
 *      client-side close-vs-MA60 classifier 2026-10-15): top/bottom =
 *      the MA255-gap extremes, rise/fall = the RSI(14) momentum
 *      episodes. Each picked phase shades its own date spans (light red
 *      / green / blue / amber) on every selected pair. The shade
 *      families share ONE slot: picking any shade button (Trend, RSI
 *      pct, High/Low Streaks, Px-Vol, Top-10% OHLC Days, Market
 *      Regimes) clears the other shade rows' picks, so only one
 *      family's shades draw at a time; within a row the multi-picks
 *      stay free.
 *
 * Fetches its own chart data on mount via fetchMovAveSpreadChart(code, secType)
 * — the DEFAULT load carries just the 18 Simple-MA + EMA pair series and the
 * shared per-date tooltip metrics (plus the Momentum section's group, which
 * auto-loads for the chips' lookback labels). The remaining metric groups
 * load ON DEMAND:
 * the first pick of an Amt/MA chip, a Roof/Floor Trendlines button, a
 * High/Low Streaks period, a Px-Vol state, an RSI Trends pct, or a Momentum
 * term fetches that group via fetchMovAveSpreadExtras (a regime button's
 * first pick fetches the spans via fetchMarketRegimeSpans); while any group
 * is in flight every control in the panel is disabled and the plot freezes
 * under a translucent spinner backdrop (BaseChart freezeOnLoading).
 *
 * Every button-group title carries a small info mark (SectionLabel) that
 * opens a popover with the group's description — the texts live in
 * groupDescriptions.ts, keyed by group id.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties } from "react";
import {
  Box,
  Chip,
  MenuItem,
  Select,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import ChartCard from "@/shared/components/ChartCard";
import OhlcModeToggle from "@/shared/components/OhlcModeToggle";
import AnalysisRunButton from "@/components/AnalysisRunButton";
import { BaseChart, useChartThemeMode } from "@/shared/charts/base-chart";
import { useAiAskAddon } from "@/shared/ai-ask";
import type { AiAskSpec } from "@/shared/ai-ask";
import type { ECharts } from "echarts";
import { UP_COLOR, DOWN_COLOR, MUTED_PALETTE } from "@/theme/chart-palette";
import {
  REGIME_ACCENT_COLORS,
  REGIME_LABELS,
  REGIME_SHADE_COLORS,
  ALL_REGIMES,
  regimeSpansToMarkArea,
} from "@/shared/charts/regimeBands";
import {
  ROOF_FLOOR_ESTIMATE_METHODS,
  type MarketRegime,
  type MarketRegimeSpans,
  type MovAveSpreadMomentumDay,
  type MovAveSpreadOhlcGapDay,
  type MovAveSpreadRoofFloorEstimateRow,
  type MovAveSpreadRoofFloorRebound,
  type MovAveSpreadRsiRankDay,
  type RoofFloorEstimateMethod,
} from "@shared/types";
import { fmtNum, fmtPct } from "@/lib/series";
import {
  fetchMovAveSpreadChartPairs,
  fetchMovAveSpreadChartEma,
  fetchMovAveSpreadChartShared,
  fetchMovAveSpreadExtras,
  fetchMarketRegimeSpans,
  fetchTrendSpans,
  invalidateCacheForPrefix,
} from "@/lib/api-client";
import type { OhlcMode } from "@/lib/ohlc";
import type {
  MovAveSpreadChartResponse,
  MovAveSpreadChartPairsResponse,
  MovAveSpreadPairSeries,
  MovAveSpreadSharedMetricsRow,
  MovAveSpreadAmtRow,
  MovAveSpreadOhlcRow,
  MovAveSpreadHighLowStreak,
  MovAveSpreadPriceVsAmtDay,
  MovAveSpreadMetric,
} from "@shared/types";
import type { PanelProps } from "./types";
import { SectionLabel } from "./SectionLabel";
import {
  ROOF_FLOOR_WINDOWS,
  RF_ESTIMATE_METHOD_LABELS,
  RF_ESTIMATE_DEFAULT_METHOD,
  HIGH_LOW_STREAK_PERIODS,
  HIGH_LOW_STREAK_PCTS,
  RSI_WINDOWS,
  RSI_TOP_PCTS,
  MOMENTUM_TERMS,
} from "./constants";
import {
  buildPairOption,
  buildAmtEnvelopeOption,
  shortLabel,
  TREND_TYPES,
  TREND_TYPE_LABELS,
  TREND_ACCENTS,
  TREND_SHADES,
  aggregateReboundAudit,
  type DerivTrendSpec,
  type MomentumTerm,
  type MomentumTrendSpec,
  type ShadeOverlay,
  type TrendSpansRecord,
  type TrendType,
} from "./chartOption";
import { trendSpansToMarkArea } from "@/shared/charts/trendBands";
import {
  computePxVolStates,
  pxVolMatchRuns,
  pxVolRunsToMarkArea,
  pxVolShadeColor,
  pxVolAccentColor,
  pxVolReading,
  PX_VOL_SPEED_OPTIONS,
  PX_VOL_VOL_OPTIONS,
  type PxVolSpeed,
  type PxVolVolState,
} from "./chartOption";
import {
  computeBreakStreaks,
  computeStreakBandWindow,
  type LongBandStreak,
} from "@/shared/charts/streakBands";

/** Bollinger multiplier options for the top-right dropdown (0.0 … 3.0, step 0.5).
 *  0.0 = band hidden; 2.0 = standard Bollinger. */
const BOLL_K_OPTIONS = [0, 0.5, 1, 1.5, 2, 2.5, 3];

/** The RSI Top-% shade overlays' accents — the shared price up/down
 *  tokens (rise = UP_COLOR green, drop = DOWN_COLOR red; distinct from
 *  the RSI window lines' blue→teal MUTED_PALETTE[0..5] and the regime
 *  hues). Every markArea rect fill reuses its token — one qualifying
 *  window draws the flat base opacity, each additional window stacking
 *  on the same date multiplies it (capped; the cssVar tokens cannot
 *  spell their own rgba). */
const RSI_SHADE_BASE_OPACITY = 0.18;

/** The pair chart's right-axis overlay, switched by the Amt | Slopes |
 *  Curvatures group in the card header: "amt" shows the lowkey
 *  trading-amount bars; slope/curv swap them for the DB-stored direct
 *  derivative lines of the price and the 5/20-day MA (or 6/20-day EMA
 *  while an EMA pair chip is selected): value[t] − value[t−1] for the
 *  slope, its diff for the curvature — no averaging. The RSI Trends
 *  overlay left the toggle (it has its own button row beneath the
 *  chart): while its days row holds a pick AND the toggle sits at
 *  "amt", the shared block's Wilder-RSI window lines replace the amt
 *  bars; Slopes/Curvatures keep precedence over it. While a Roof/Floor
 *  Trendlines window button is enabled, its change-since-last-anchor
 *  plot owns the amt slot instead (the click clears the other sections
 *  and this toggle back to Amt) — RSI/derivatives picks made afterwards
 *  take the slot back. */
type ChartOverlay = "amt" | "slope" | "curv";

/**
 * The on-demand metric groups (MovAveSpreadMetric) → the ExtrasState key
 * they populate. Each group feeds one control section and is fetched the
 * first time one of its buttons is picked:
 *   amt → the 5 Amt/MA chips · ohlc + rfaudit → the Roof/Floor
 *   Trendlines row (extrema + the rebound audit) · rfest → the row's
 *   Est-levels toggle · streaks → the High/Low
 *   Streaks row · pxvol → the Px-Vol States row ·
 *   rsiranks → the RSI Trends section's pct row.
 */
const METRIC_STATE_KEY: Record<MovAveSpreadMetric, "amtPairs" | "ohlc" | "rfEstimates" | "rfRebounds" | "highLowStreaks" | "priceVsAmt" | "ohlcGaps" | "rsiRanks" | "momentum"> = {
  amt: "amtPairs",
  ohlc: "ohlc",
  rfest: "rfEstimates",
  rfaudit: "rfRebounds",
  streaks: "highLowStreaks",
  pxvol: "priceVsAmt",
  gaps: "ohlcGaps",
  rsiranks: "rsiRanks",
  momentum: "momentum",
};

/** On-demand metric groups loaded so far (per code + refresh). */
interface ExtrasState {
  amtPairs?: MovAveSpreadPairSeries[];
  ohlc?: MovAveSpreadOhlcRow[];
  rfEstimates?: MovAveSpreadRoofFloorEstimateRow[];
  rfRebounds?: MovAveSpreadRoofFloorRebound[];
  highLowStreaks?: MovAveSpreadHighLowStreak[];
  priceVsAmt?: MovAveSpreadPriceVsAmtDay[];
  ohlcGaps?: MovAveSpreadOhlcGapDay[];
  rsiRanks?: MovAveSpreadRsiRankDay[];
  momentum?: MovAveSpreadMomentumDay[];
}

/** The momentum lines' colors — one per term, MUTED_PALETTE tokens
 *  (distinct from the RSI window lines' blue→teal [0..5] ramp; the two
 *  overlays are never drawn simultaneously). */
const MOMENTUM_TERM_COLORS: Record<MomentumTerm, string> = {
  "5d": MUTED_PALETTE[1],
  "20d": MUTED_PALETTE[2],
  short: MUTED_PALETTE[5],
  mid: MUTED_PALETTE[6],
  long: MUTED_PALETTE[7],
};

/** Term → the momentum row's value column. */
const MOMENTUM_VALUE_KEYS: Record<
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
const MOMENTUM_LB_KEYS = {
  short: "lb_short",
  mid: "lb_mid",
  long: "lb_long",
} as const;

/** Chip label of a momentum term — the adaptive short/mid/long spell
 *  their CURRENT effective lookback (the latest row's lb_*) once the
 *  group has landed, e.g. "short (14d)"; the fixed anchors stay 5d/20d. */
function momentumChipLabel(
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
const LONG_MA_ORDER = [5, 20, 60, 120, 255] as const;

/**
 * Long-EMA column order for the 9 EMA pair chips. EMA windows are
 * 6/20/60/120/255 (EMA6 replaces MA5 — EMAs use 6 instead of 5 as the
 * short window). Same structure as LONG_MA_ORDER but with 6 in column 0.
 */
const LONG_EMA_ORDER = [6, 20, 60, 120, 255] as const;

/**
 * Shared period-column grid — one column per period (5, 20, 60, 120, 255,
 * 500, 750, 1275) so every button row aligns vertically by period. Pair
 * rows only fill the first 5 columns (their long MAs stop at 255); the
 * Roof/Floor Trendlines row spans the full width (its 60d … 1275d buttons
 * continue into the extra columns under their matching periods).
 */
const PERIOD_GRID_SX: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(8, minmax(0, 1fr))",
  gap: 0.75,
  alignItems: "center",
};

/**
 * Shared Chip sx for every period button (pair chips + roof/floor-window
 * buttons): fill its grid cell with a centered label, compact size.
 */
const PERIOD_CHIP_SX: CSSProperties = {
  fontSize: "0.7rem",
  height: 24,
  width: "100%",
  display: "flex",
  justifyContent: "center",
};

export function MaSpreadPanel({ code, name, secType }: PanelProps) {
  // Reactive light/dark theme — single source of truth for every option
  // builder call below (no prop drilling).
  const themeMode = useChartThemeMode();

  // ---- Chart data (three table-aligned blocks, loaded in PARALLEL) ------
  // pairs is the REQUIRED block (the default Price/MA5 chart, the MA chips,
  // and the shared date axis all come from it — its landing ends `loading`).
  // ema (the EMA detail table → EMA chip grid) and shared (RSI + OHLC pivot
  // + amt metrics → tooltip) are best-effort overlays that merge into the
  // composed response whenever they land: a failed block just leaves its
  // feature absent instead of failing the panel.
  const [pairsBlock, setPairsBlock] = useState<MovAveSpreadChartPairsResponse | null>(null);
  const [emaPairs, setEmaPairs] = useState<MovAveSpreadPairSeries[] | null>(null);
  const [sharedRows, setSharedRows] = useState<MovAveSpreadSharedMetricsRow[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // The composed response the option builders consume — identical shape to
  // the monolithic GET /chart payload (9 MA pairs first, then the EMA pairs
  // once its block lands; shared defaults to [] until its block lands).
  const chartData = useMemo<MovAveSpreadChartResponse | null>(
    () =>
      pairsBlock
        ? {
            code: pairsBlock.code,
            name: pairsBlock.name,
            pairs: [...pairsBlock.pairs, ...(emaPairs ?? [])],
            shared: sharedRows ?? [],
          }
        : null,
    [pairsBlock, emaPairs, sharedRows],
  );

  // Bumped by the per-security AnalysisRunButton after a rebuild run —
  // retriggers the chart fetch (the cache entry is invalidated first in
  // the completion handler).
  const [refreshKey, setRefreshKey] = useState(0);

  // ---- On-demand metric groups (see METRIC_STATE_KEY) ---------------------
  // The default chart load carries just the 18 pair series + the shared
  // tooltip metrics. The Amt/MA pairs, OHLC extrema, streak rows, and
  // px-vol states load lazily: picking a section's button fetches its
  // group, and while ANY group is in flight every control is disabled and
  // the plot shows the shared spinner (see extrasLoading).
  const [extras, setExtras] = useState<ExtrasState>({});
  const [loadingMetrics, setLoadingMetrics] = useState<MovAveSpreadMetric[]>([]);
  const [metricError, setMetricError] = useState<string | null>(null);
  const extrasLoading = loadingMetrics.length > 0;
  const amtLoaded = extras.amtPairs != null;

  // Which of the 9 pairs is shown in the single plot (default 0 = Price/MA5).
  const [selectedPairIdx, setSelectedPairIdx] = useState(0);

  // The first pair's rows — used for the card subtitle (date range + bar count)
  // and as the x-axis dates for the chart (all pairs share one date axis).
  const firstPairRows = chartData?.pairs[0]?.rows ?? [];

  // Whether this security has analysis rows — drives the bold highlight of
  // the per-security build button (AnalysisRunButton). Loading counts as
  // "present" so the button doesn't bold-flicker on every fetch.
  const hasAnalysisData = loading || firstPairRows.length > 0;

  // Refetch after a per-security analysis rebuild (AnalysisRunButton):
  // drop the cached chart + extras responses, then bump the refresh key.
  // The chart prefix covers all three block URLs (pairs/ema/shared) plus
  // the legacy monolithic chart endpoint.
  const handleAnalysisRunCompleted = useCallback(() => {
    invalidateCacheForPrefix("/api/analysis/mov-ave-spread/chart");
    invalidateCacheForPrefix("/api/analysis/mov-ave-spread/extras");
    setRefreshKey((k) => k + 1);
  }, []);

  // Fetch ONE on-demand metric group (no-op when already loaded or in
  // flight). Resolves after the group lands — callers chain their button's
  // state change onto it so the pick applies the moment the spinner stops.
  // Fetch the not-yet-loaded subset of metric groups in ONE /extras call;
  // resolves after they land so callers chain their button's state change
  // onto it.
  const ensureMetrics = useCallback(
    (metrics: MovAveSpreadMetric[]): Promise<void> => {
      const missing = metrics.filter((m) => extras[METRIC_STATE_KEY[m]] == null);
      if (missing.length === 0) return Promise.resolve();
      setLoadingMetrics((prev) => [
        ...prev,
        ...missing.filter((m) => !prev.includes(m)),
      ]);
      setMetricError(null);
      return fetchMovAveSpreadExtras(code, secType, missing)
        .then((d) => {
          setExtras((prev) => {
            const next: ExtrasState = { ...prev };
            const src = d as unknown as Record<string, unknown>;
            const dst = next as unknown as Record<string, unknown>;
            for (const m of missing) {
              dst[METRIC_STATE_KEY[m]] = src[METRIC_STATE_KEY[m]];
            }
            return next;
          });
        })
        .catch((e: Error) => {
          setMetricError(e.message);
        })
        .finally(() => {
          setLoadingMetrics((prev) => prev.filter((m) => !missing.includes(m)));
        });
    },
    [extras, code, secType],
  );

  const ensureMetric = useCallback(
    (metric: MovAveSpreadMetric): Promise<void> => ensureMetrics([metric]),
    [ensureMetrics],
  );

  // Bollinger multiplier k in MA ± k×σ. Default 2 (standard Bollinger).
  // 0 hides the envelope. Affects Price/MA and Price/EMA pairs (ma_short === 0);
  // MA5/MA and EMA6/EMA pairs don't get the envelope and the dropdown is hidden.
  // Options: 0, 0.5, 1, 1.5, 2, 2.5, 3 (step 0.5).
  const [bollingerK, setBollingerK] = useState(2);

  // The chart's right-axis overlay switch (the Amt | Slopes | Curvatures
  // group in the header): "amt" shows the lowkey trading-amount bars + Amt
  // secondary axis, "slope"/"curv" the family's derivative lines (see the
  // derivTrend memo below — no extra fetch, the pair rows already carry
  // the values). The RSI Trends overlay left the toggle: it is driven by
  // the RSI Trends section's days row beneath the chart and rides the
  // "amt" slot (rsiLinesActive below).
  const [chartOverlay, setChartOverlay] = useState<ChartOverlay>("amt");

  // OHLC display mode — "percentage" (default) rebases OHLC + MAs to % change
  // from the first valid close; "absolute" shows raw prices.
  const [ohlcMode, setOhlcMode] = useState<OhlcMode>("percentage");

  // Enabled roof/floor window (trading days) — null = off. Selected via
  // the Roof/Floor Trendlines row beneath the Trading Amt/MA section
  // (ROOF_FLOOR_WINDOWS holds the clickable windows).
  const [roofFloorWindow, setRoofFloorWindow] = useState<number | null>(null);

  // Clicked chart date index (into the full rows — shared date axis). The
  // roof/floor trendlines of the enabled window are drawn to (and stop at)
  // this date. Clicking the same date again clears it.
  const [anchorIdx, setAnchorIdx] = useState<number | null>(null);

  // Roof/floor Est-levels overlay (needs a roof/floor window enabled):
  // rfEstOn toggles the two horizontal estimated roof/floor lines at the
  // CHOSEN date (anchorIdx, latest row when no date clicked) from the
  // "rfest" extras group (analysis.mov_ave_spreads_roof_floor_estimates);
  // rfEstMethod picks the quantitative method (median = cross-method
  // consensus default).
  const [rfEstOn, setRfEstOn] = useState(false);
  const [rfEstMethod, setRfEstMethod] = useState<RoofFloorEstimateMethod>(
    RF_ESTIMATE_DEFAULT_METHOD,
  );
  // The toggle click — fetch-through pattern (first pick loads the
  // "rfest" group, the toggle applies when it lands).
  const handleRfEstToggle = useCallback(() => {
    const apply = () => setRfEstOn((v) => !v);
    if (extras.rfEstimates == null) {
      void ensureMetric("rfest").then(apply);
    } else {
      apply();
    }
  }, [ensureMetric, extras.rfEstimates]);

  // High/Low Streaks nested buttons: layer 1 = band lookback period
  // (trading rows, null = row off), layer 2 = band tightness pct (percent,
  // null = no shading yet). Selecting a period expands the pct layer;
  // clicking the active period again collapses it (and clears the pct).
  const [streakPeriod, setStreakPeriod] = useState<number | null>(null);
  const [streakPct, setStreakPct] = useState<number | null>(null);

  // Px-Vol States nested buttons (trading amt × price change): layer 1 =
  // price speed (sharp/slow/flat rise/drop), layer 2 = trading-amount state
  // (increasing/flat/decreasing). Single-select per row; BOTH rows must be
  // picked before the chart shades the matching dates. The shades' color
  // strength follows the combo's weight (sharp × heavy = darkest).
  const [pxVolSpeed, setPxVolSpeed] = useState<PxVolSpeed | null>(null);
  const [pxVolVol, setPxVolVol] = useState<PxVolVolState | null>(null);

  // Trend section state: multi-pick of the signals tier's trend phases
  // (top / bottom / rise / fall — analysis.trend_days, the RSI re-entry
  // classification shared with the rule cells, fetched per code via the
  // trend-spans API; REPLACES the retired client-side close-vs-MA60
  // classifier 2026-10-15). Each picked phase shades its own spans;
  // date-based, so the shades draw on every selected pair. The spans
  // also feed the tooltip's per-date trend readout.
  const [trendPicks, setTrendPicks] = useState<Set<TrendType>>(new Set());
  const [trendSpans, setTrendSpans] = useState<TrendSpansRecord | null>(null);

  // Tracks the panel's current (secType, code) so a slow trend-span
  // response for a previous code never lands after a switch (the
  // regime-span precedent).
  const trendCodeKeyRef = useRef(`${secType}:${code}`);
  useEffect(() => {
    trendCodeKeyRef.current = `${secType}:${code}`;
    setTrendSpans(null);
    const reqKey = `${secType}:${code}`;
    // fetched eagerly (the tooltip readout wants them on any hover) —
    // one small endpoint, window the API's 10y max (the panel charts
    // full history)
    fetchTrendSpans(secType, code, 3650)
      .then((d) => {
        if (trendCodeKeyRef.current === reqKey) setTrendSpans(d.spans ?? {});
      })
      .catch(() => {
        if (trendCodeKeyRef.current === reqKey) setTrendSpans({});
      });
  }, [code, secType]);

  // The shade families share ONE slot: activating any shade row's button
  // clears every OTHER shade row's picks, so only one family's shades draw
  // at a time (trend, RSI Top-% pct, High/Low Streaks, Px-Vol States,
  // Top-10% OHLC Days, Market Regimes). Within a row the multi-picks stay
  // free (regimes, trend phases, gap marks, px-vol legs). The setter refs
  // for the later-declared rows (setGapMarks / setRegimePicks /
  // setRsiTopPct) are only touched when the callback fires — after render
  // has initialized them.
  const clearShadeGroupsExcept = useCallback(
    (keep: "trend" | "rsiPct" | "streaks" | "pxvol" | "gaps" | "regimes") => {
      if (keep !== "trend") setTrendPicks(new Set());
      if (keep !== "rsiPct") setRsiTopPct(null);
      if (keep !== "streaks") {
        setStreakPeriod(null);
        setStreakPct(null);
      }
      if (keep !== "pxvol") {
        setPxVolSpeed(null);
        setPxVolVol(null);
      }
      if (keep !== "gaps") setGapMarks({ spike: false, flush: false });
      if (keep !== "regimes") setRegimePicks(new Set());
    },
    [],
  );

  const togglePxVolSpeed = useCallback((s: PxVolSpeed) => {
    if (pxVolSpeed == null && pxVolVol == null) {
      clearShadeGroupsExcept("pxvol");
    }
    setPxVolSpeed((prev) => (prev === s ? null : s));
  }, [pxVolSpeed, pxVolVol, clearShadeGroupsExcept]);

  const togglePxVolVol = useCallback((v: PxVolVolState) => {
    if (pxVolSpeed == null && pxVolVol == null) {
      clearShadeGroupsExcept("pxvol");
    }
    setPxVolVol((prev) => (prev === v ? null : v));
  }, [pxVolSpeed, pxVolVol, clearShadeGroupsExcept]);

  // A trend-phase pick that turns the section ON takes the shade slot (the
  // other shade rows' picks clear); further same-row picks just extend the
  // shaded set, and un-picking leaves the other rows off.
  const toggleTrendPick = useCallback(
    (t: TrendType) => {
      if (!trendPicks.has(t)) clearShadeGroupsExcept("trend");
      setTrendPicks((prev) => {
        const next = new Set(prev);
        if (next.has(t)) next.delete(t);
        else next.add(t);
        return next;
      });
    },
    [trendPicks, clearShadeGroupsExcept],
  );

  // Market Regimes buttons (stats.market_regimes): multi-select among ALL
  // regimes — calm included (its grey shade marks the market's calm
  // stretches; the other charts keep calm as the never-shaded background
  // via the shared SHADED_REGIMES). The first pick fetches the code's
  // contiguous same-regime spans once (GET /api/analysis/market-regimes)
  // and caches them per code.
  const [regimePicks, setRegimePicks] = useState<Set<MarketRegime>>(new Set());
  const [regimeSpans, setRegimeSpans] = useState<Partial<MarketRegimeSpans> | null>(null);
  const [regimesLoading, setRegimesLoading] = useState(false);

  // Tracks the panel's current (secType, code) so a slow regime-span
  // response for a previous code never lands after a switch.
  const regimeCodeKeyRef = useRef(`${secType}:${code}`);
  useEffect(() => {
    regimeCodeKeyRef.current = `${secType}:${code}`;
  }, [code, secType]);

  // Picking a regime fetches the spans ONCE (imperatively — an effect that
  // both sets and depends on its own loading flag would kill its in-flight
  // fetch on the very re-render it triggers, leaving the row stuck
  // disabled with the shades never drawn).
  const toggleRegime = useCallback(
    (r: MarketRegime) => {
      if (regimePicks.size === 0) clearShadeGroupsExcept("regimes");
      setRegimePicks((prev) => {
        const next = new Set(prev);
        if (next.has(r)) next.delete(r);
        else next.add(r);
        return next;
      });
      if (regimeSpans != null || regimesLoading) return;
      setRegimesLoading(true);
      const reqKey = `${secType}:${code}`;
      fetchMarketRegimeSpans(code, secType)
        .then((d) => {
          if (regimeCodeKeyRef.current === reqKey) setRegimeSpans(d.spans ?? {});
        })
        .catch(() => {
          if (regimeCodeKeyRef.current === reqKey) setRegimeSpans({});
        })
        .finally(() => {
          if (regimeCodeKeyRef.current === reqKey) setRegimesLoading(false);
        });
    },
    [code, secType, regimePicks, regimeSpans, regimesLoading, clearShadeGroupsExcept],
  );

  useEffect(() => {
    setRegimePicks(new Set());
    setRegimeSpans(null);
    setRegimesLoading(false);
    setRsiTopPct(null);
    setTrendPicks(new Set());
  }, [code, secType]);

  const toggleStreakPeriod = useCallback((w: number) => {
    if (streakPeriod == null) clearShadeGroupsExcept("streaks");
    setStreakPeriod((prev) => {
      if (prev === w) {
        setStreakPct(null);
        return null;
      }
      return w;
    });
  }, [streakPeriod, clearShadeGroupsExcept]);

  const toggleStreakPct = useCallback((p: number) => {
    setStreakPct((prev) => (prev === p ? null : p));
  }, []);

  // ---- Click-through handlers: the FIRST pick of an unloaded section
  // loads its metric group first and applies the pick as soon as the fetch
  // resolves; later picks toggle directly. While the fetch is in flight
  // the freeze disables every control, so at most one load runs at a time.

  const handleStreakPeriodClick = useCallback(
    (w: number) => {
      if (extras.highLowStreaks == null) {
        void ensureMetric("streaks").then(() => toggleStreakPeriod(w));
      } else {
        toggleStreakPeriod(w);
      }
    },
    [ensureMetric, extras.highLowStreaks, toggleStreakPeriod],
  );

  const handlePxVolSpeedClick = useCallback(
    (s: PxVolSpeed) => {
      if (extras.priceVsAmt == null) {
        void ensureMetric("pxvol").then(() => togglePxVolSpeed(s));
      } else {
        togglePxVolSpeed(s);
      }
    },
    [ensureMetric, extras.priceVsAmt, togglePxVolSpeed],
  );

  const handlePxVolVolClick = useCallback(
    (v: PxVolVolState) => {
      if (extras.priceVsAmt == null) {
        void ensureMetric("pxvol").then(() => togglePxVolVol(v));
      } else {
        togglePxVolVol(v);
      }
    },
    [ensureMetric, extras.priceVsAmt, togglePxVolVol],
  );

  // Top-10% OHLC Days toggles (the gaps store's wick top-decile flags):
  // spike fade = uw_top10 (mark at the day's high), flush recover =
  // lw_top10 (mark at the day's low). Independent — a day can be both.
  const [gapMarks, setGapMarks] = useState<{ spike: boolean; flush: boolean }>({
    spike: false,
    flush: false,
  });
  const handleGapMarkClick = useCallback(
    (kind: "spike" | "flush") => {
      if (!gapMarks.spike && !gapMarks.flush) clearShadeGroupsExcept("gaps");
      if (extras.ohlcGaps == null) {
        void ensureMetric("gaps").then(() =>
          setGapMarks((prev) => ({ ...prev, [kind]: !prev[kind] })),
        );
      } else {
        setGapMarks((prev) => ({ ...prev, [kind]: !prev[kind] }));
      }
    },
    [ensureMetric, extras.ohlcGaps, gapMarks, clearShadeGroupsExcept],
  );

  // RSI Trends section state (the button row beneath the chart — the
  // overlay left the header toggle). The section rests fully OFF: no day
  // picked, no pct. Clicking a day turns the overlay on and AUTO-selects
  // the 1% shade; un-picking the last day returns to off.
  //   rsiDays — the days row's multi-pick of Wilder-RSI window lines
  //     (while non-empty AND the header toggle sits at "amt", the picked
  //     RSI lines replace the trading-amt bars) AND the shade's rank
  //     scope.
  //   rsiTopPct — the pct row's Top-% history-rank shade
  //     (analysis.mov_ave_rsi_ranks), split evenly green top-half rises /
  //     red bottom-half drops; null = off. A fresh pick loads the
  //     "rsiranks" extras group; the pick applies when it resolves.
  const [rsiDays, setRsiDays] = useState<number[]>([]);
  // the RSI Trends population toggle (upper-right of the section, once a
  // day is picked): null = all days; "typical" / "re-entry" = the rsi
  // analysis layer's is_typical populations only
  const [rsiPopulation, setRsiPopulation] = useState<
    "typical" | "re-entry" | null
  >(null);
  const [rsiTopPct, setRsiTopPct] = useState<number | null>(null);
  // Pure toggle (functional update — rapid clicks can't race the closure);
  // the arm/clear side effects live in the effect below.
  const toggleRsiDay = useCallback((w: number) => {
    setRsiDays((prev) =>
      prev.includes(w) ? prev.filter((d) => d !== w) : [...prev, w],
    );
  }, []);
  // The section's lifecycle: the FIRST day pick (empty → non-empty)
  // auto-arms the 1% shade — fetching the rsiranks group first when
  // needed — while a manual pct pick/clear afterwards sticks; the resting
  // state (no day picked) always takes the shade off.
  const prevDaysLenRef = useRef(0);
  useEffect(() => {
    const wasEmpty = prevDaysLenRef.current === 0;
    prevDaysLenRef.current = rsiDays.length;
    if (rsiDays.length === 0) {
      setRsiTopPct(null);
      return;
    }
    if (wasEmpty && rsiTopPct == null) {
      // The auto-arm IS a pct activation — it takes the shade slot (the
      // other shade rows' picks clear).
      clearShadeGroupsExcept("rsiPct");
      if (extras.rsiRanks == null) {
        void ensureMetric("rsiranks").then(() =>
          setRsiTopPct((p) => p ?? 0.01),
        );
      } else {
        setRsiTopPct(0.01);
      }
    }
  }, [rsiDays, rsiTopPct, ensureMetric, extras.rsiRanks, clearShadeGroupsExcept]);
  const handleRsiTopPctSelect = useCallback(
    (pct: number) => {
      if (pct === 0) {
        setRsiTopPct(null);
        return;
      }
      clearShadeGroupsExcept("rsiPct");
      if (extras.rsiRanks == null) {
        void ensureMetric("rsiranks").then(() => setRsiTopPct(pct));
      } else {
        setRsiTopPct(pct);
      }
    },
    [ensureMetric, extras.rsiRanks, clearShadeGroupsExcept],
  );
  // The RSI lines ride the toggle's "amt" slot (Slopes/Curvatures keep
  // precedence): active while the days row holds a pick and no derivative
  // overlay is toggled on. The pct shades are independent of the toggle.
  const rsiLinesActive = chartOverlay === "amt" && rsiDays.length > 0;

  // Momentum section state (the row beneath RSI Trends). The section
  // rests fully OFF: rsiDays empty, no term picked. Its "momentum"
  // extras group AUTO-LOADS with the chart blocks (the adaptive chips'
  // lookback labels need it); a term pick applies once the group is
  // present — and if the auto-load failed, the pick re-fetches it.
  // Un-picking the last term returns the row to off. The picked terms'
  // lines take the right-axis amt slot BELOW the RSI lines in the
  // precedence (Slopes/Curvatures still override both).
  const [momentumTerms, setMomentumTerms] = useState<MomentumTerm[]>([]);
  const toggleMomentumTerm = useCallback(
    (term: MomentumTerm) => {
      const apply = () =>
        setMomentumTerms((prev) =>
          prev.includes(term)
            ? prev.filter((t) => t !== term)
            : [...prev, term],
        );
      if (extras.momentum == null) {
        void ensureMetric("momentum").then(apply);
      } else {
        apply();
      }
    },
    [ensureMetric, extras.momentum],
  );
  const momentumLinesActive =
    chartOverlay === "amt" &&
    rsiDays.length === 0 &&
    momentumTerms.length > 0 &&
    (extras.momentum?.length ?? 0) > 0;

  // Fetch the chart blocks on mount and whenever the code/sec_type changes.
  // All three blocks fire AT ONCE (parallel-then-render): the panel renders
  // as soon as the REQUIRED pairs block lands, while the EMA chips and the
  // tooltip metrics appear when their blocks settle. The on-demand metric
  // groups reset with it — a new code starts from the blocks-only load.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setPairsBlock(null);
    setEmaPairs(null);
    setSharedRows(null);
    setExtras({});
    setLoadingMetrics([]);
    setMetricError(null);
    fetchMovAveSpreadChartPairs(code, secType)
      .then((d) => {
        if (cancelled) return;
        setPairsBlock(d);
        setSelectedPairIdx(0);
        setAnchorIdx(null);
        setLoading(false);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setError(e.message);
        setLoading(false);
      });
    fetchMovAveSpreadChartEma(code, secType)
      .then((d) => {
        if (!cancelled) setEmaPairs(d.pairs);
      })
      .catch((e) => {
        console.warn(`[ma-spread] ema block failed for ${code} — EMA chips hidden:`, e);
      });
    fetchMovAveSpreadChartShared(code, secType)
      .then((d) => {
        if (!cancelled) setSharedRows(d.shared);
      })
      .catch((e) => {
        console.warn(`[ma-spread] shared block failed for ${code} — tooltip metrics hidden:`, e);
      });
    // The Momentum section's group auto-loads with the blocks (NOT
    // pick-gated like the other groups): the adaptive short/mid/long
    // chips spell their CURRENT effective lookbacks (latest day), known
    // only once the rows land. Same loading/freeze machinery as
    // ensureMetric; a failure leaves the group unloaded — clicking a
    // term retries through the normal path.
    setLoadingMetrics((prev) =>
      prev.includes("momentum") ? prev : [...prev, "momentum"],
    );
    fetchMovAveSpreadExtras(code, secType, ["momentum"])
      .then((d) => {
        if (!cancelled) setExtras((prev) => ({ ...prev, momentum: d.momentum }));
      })
      .catch((e: Error) => {
        if (!cancelled) setMetricError(e.message);
      })
      .finally(() => {
        if (!cancelled)
          setLoadingMetrics((prev) => prev.filter((m) => m !== "momentum"));
      });
    return () => {
      cancelled = true;
    };
  }, [code, secType, refreshKey]);

  // Canvas-level chart click: sets the anchor date. Armed while an OHLC
  // window or the High/Low Streaks row is enabled; the roof/floor
  // trendlines and the streak window re-anchor to the clicked date.
  // Clicking the already-selected date clears the anchor (toggle, back to
  // the latest date's window), clicking another date moves it.
  const handleCanvasClick = useCallback(
    (dataIdx: number) => {
      if (roofFloorWindow == null && streakPeriod == null) return;
      setAnchorIdx((prev) => (prev === dataIdx ? null : dataIdx));
    },
    [roofFloorWindow, streakPeriod],
  );

  // The full pairs list — the 18 default series plus the 5 Amt/MA series
  // once the "amt" extras group has loaded (no slicing — the chart's
  // in-chart dataZoom handles viewport control). Used for the pair chips,
  // the pair index lookup, and the chart option builder.
  const pairs = useMemo(
    () => [...(chartData?.pairs ?? []), ...(extras.amtPairs ?? [])],
    [chartData, extras.amtPairs],
  );

  // Lookup from `${kind}-${ma_short}-${ma_long}` → index in pairs, used to
  // place each pair chip in its long-MA column of the 2-row pair grid.
  // kind prefix ("price" | "ema" | "amt") separates the 3 pair families
  // that share the same (ma_short, ma_long) — e.g. Price/MA20 (price-0-20)
  // vs Price/EMA20 (ema-0-20).
  const pairIndexMap = useMemo(() => {
    const m = new Map<string, number>();
    pairs.forEach((p, i) => {
      const kind = p.kind ?? "price";
      m.set(`${kind}-${p.ma_short}-${p.ma_long}`, i);
    });
    return m;
  }, [pairs]);

  // ---- High/Low Streaks data ("streaks" extras group — loaded on demand
  // the first time a High/Low Streaks period button is picked) ----
  // FLAT per-streak list across ALL (period, pctType) combos — the nested
  // buttons select a combo, and the WINDOW-CONFINED subset is merged per
  // DB band-break streak rows (analysis.mov_ave_high_low_pct_streaks,
  // tested against each month's OWN moving band) — NOT used for shading
  // (the break bands are detected client-side vs the anchor window's
  // static edge, see longStreaks below); they only gate the buttons'
  // availability caption.
  const highLowStreaks = extras.highLowStreaks ?? null;
  const hasStreakData = highLowStreaks != null && highLowStreaks.length > 0;

  // The anchor-date band window shown by default (latest date) or when a
  // chart date is clicked (trailing streakPeriod rows before it) — bounds
  // both the light zones and the per-streak break bands.
  const streakWin = useMemo(() => {
    if (streakPeriod == null || streakPct == null) return null;
    return computeStreakBandWindow(
      firstPairRows,
      streakPeriod,
      streakPct,
      anchorIdx,
    );
  }, [firstPairRows, streakPeriod, streakPct, anchorIdx]);

  // The BREAK STREAKS for the selected (period, pct) combo — detected
  // CLIENT-SIDE against the anchor window's own static band edges (the
  // same edges the light zones draw): days whose close (short_value on
  // the price pairs) is above high_val / below low_val, consolidated
  // with the ≤5-day in-band bridge. This guarantees the shading only
  // ever covers price the chart shows inside the drawn zone — the DB
  // streak rows (tested against each month's OWN moving band) would
  // shade old breakouts that sit below today's static edge. Each band
  // spans the window's whole vertical excursion (constant band edge →
  // the window's top/bottom price, same extent as the light zones).
  const longStreaks = useMemo<{
    high: LongBandStreak[];
    low: LongBandStreak[];
  } | null>(() => {
    if (streakPeriod == null || streakPct == null || streakWin == null) return null;
    return computeBreakStreaks(firstPairRows, streakWin);
  }, [firstPairRows, streakWin]);

  // ---- Px-Vol States data (the analysis.mov_ave_price_vs_amt
  // registry — the px_vol family's DATE-LEVEL source of truth; the
  // forecast buckets in analysis_forecasts.px_vol_state store
  // aggregates only) ----
  // Per-date (speed, vol) state served by the chart endpoint
  // (chartData.priceVsAmt). Falls back to a client-side replication of
  // the registry's computation (computePxVolStates) for cached
  // responses that predate the field. Computed lazily (either Px-Vol
  // button picked).
  const pxVolStates = useMemo(() => {
    if (pxVolSpeed == null && pxVolVol == null) return null;
    const dbDays = extras.priceVsAmt;
    if (dbDays != null) {
      const byDate = new Map(dbDays.map((d) => [d.date, d]));
      return firstPairRows.map((r) => {
        const d = byDate.get(r.date);
        return d != null ? { speed: d.speed, vol: d.vol } : null;
      });
    }
    return computePxVolStates(
      firstPairRows.map((r) => r.short_value),
      firstPairRows.map((r) => r.trading_amount),
    );
  }, [chartData, extras.priceVsAmt, firstPairRows, pxVolSpeed, pxVolVol]);

  // The selected combo's consecutive matched runs + the chart overlay
  // (legend label + markArea rects shaded by the combo's strength color).
  const pxVolRuns = useMemo(() => {
    if (pxVolSpeed == null || pxVolVol == null || pxVolStates == null) return [];
    return pxVolMatchRuns(
      firstPairRows.map((r) => r.date),
      pxVolStates,
      pxVolSpeed,
      pxVolVol,
    );
  }, [firstPairRows, pxVolStates, pxVolSpeed, pxVolVol]);

  const pxVolShade = useMemo(() => {
    if (pxVolSpeed == null || pxVolVol == null || pxVolRuns.length === 0) return null;
    const speedOpt = PX_VOL_SPEED_OPTIONS.find((o) => o.key === pxVolSpeed);
    const volOpt = PX_VOL_VOL_OPTIONS.find((o) => o.key === pxVolVol);
    const label = `PxVol(${speedOpt?.label ?? pxVolSpeed}·${volOpt?.label ?? pxVolVol})`;
    return {
      label,
      accent: pxVolAccentColor(pxVolSpeed),
      data: pxVolRunsToMarkArea(pxVolRuns, pxVolShadeColor(pxVolSpeed, pxVolVol)),
    };
  }, [pxVolSpeed, pxVolVol, pxVolRuns]);

  // The picked regimes' span overlays (one legend series each — the
  // regimeBands palette's per-regime shade color, calm's grey included).
  const regimeShades = useMemo(() => {
    if (regimePicks.size === 0 || regimeSpans == null) return [];
    const out: Array<{ label: string; data: ReturnType<typeof regimeSpansToMarkArea>; accent: string }> = [];
    for (const r of ALL_REGIMES) {
      if (!regimePicks.has(r)) continue;
      const spans = regimeSpans[r] ?? [];
      out.push({
        label: `Regime(${REGIME_LABELS[r]})`,
        accent: REGIME_ACCENT_COLORS[r],
        data: regimeSpansToMarkArea(spans, REGIME_SHADE_COLORS[r]),
      });
    }
    return out;
  }, [regimePicks, regimeSpans]);

  // Clamp selectedPairIdx to valid range.
  const safePairIdx = Math.min(selectedPairIdx, Math.max(0, pairs.length - 1));
  const selectedPair = pairs[safePairIdx];
  // True when an Amt/MA pair is selected — price chips are frozen (disabled).
  const amtPairSelected = selectedPair?.kind === "amt";

  // Enabling a Roof/Floor Trendlines window is EXCLUSIVE: every other
  // section's picks clear (RSI Trends, High/Low Streaks, Px-Vol States,
  // Top-10% OHLC Days, Market Regimes, Trend), the header overlay returns
  // to Amt, and an Amt/MA envelope view falls back to Price/MA5 — so the
  // window's roof/floor overlays own the chart and its change-since-
  // last-anchor plot owns the right axis. Toggling the same window off
  // just clears it (the other sections stay cleared until re-picked);
  // the section's own "shade" chip and the clicked anchor survive.
  // (Defined here because the amt-envelope fallback reads
  // amtPairSelected.)
  const handleRoofFloorWindowClick = useCallback(
    (w: number) => {
      const turningOn = roofFloorWindow !== w;
      const apply = () => {
        if (!turningOn) {
          setRoofFloorWindow(null);
          return;
        }
        setChartOverlay("amt");
        setRsiDays([]);
        setRsiTopPct(null);
        setMomentumTerms([]);
        setStreakPeriod(null);
        setStreakPct(null);
        setPxVolSpeed(null);
        setPxVolVol(null);
        setGapMarks({ spike: false, flush: false });
        setRegimePicks(new Set());
        setTrendPicks(new Set());
        if (amtPairSelected) setSelectedPairIdx(0);
        setRoofFloorWindow(w);
      };
      if (extras.ohlc == null) {
        void ensureMetrics(["ohlc", "rfaudit"]).then(apply);
      } else if (extras.rfRebounds == null) {
        void ensureMetric("rfaudit").then(apply);
      } else {
        apply();
      }
    },
    [ensureMetric, ensureMetrics, extras.ohlc, extras.rfRebounds, roofFloorWindow, amtPairSelected],
  );

  // ---- Slopes/Curvatures overlay lines (the header toggle's two
  // derivative modes). THREE DIRECT derivative curves read from the
  // DB-STORED columns (no client-side computation, no averaging):
  //   Price  — price_slope / price_curvature (P[t] − P[t−1] and its diff)
  //   MA5/EMA6 + MA20/EMA20 — the slope of the moving-average LINE
  //            itself (maW[t] − maW[t−1] / emaW[t] − emaW[t−1],
  //            curvature = that slope's diff), straight from the
  //            ma{5,20}_* / ema{6,20}_* detail columns.
  // The family follows the pair switch: an EMA chip swaps the MA curves
  // to EMA6/EMA20; a Simple-MA chip (the default) shows MA5/MA20.
  const derivTrend = useMemo<DerivTrendSpec | null>(() => {
    if (chartOverlay !== "slope" && chartOverlay !== "curv") return null;
    const mode = chartOverlay === "slope" ? "slope" : "curvature";
    const suffix = mode === "slope" ? "Slope" : "Curv";
    const isEma = selectedPair?.kind === "ema";
    // The Price/{W} pairs: short leg = price (short_slope /
    // short_curvature carry the DB's price derivatives), long leg = the
    // window's MA/EMA (long_slope / long_curvature carry its stored
    // derivative).
    const anchor = pairs[pairIndexMap.get(isEma ? "ema-0-6" : "price-0-5") ?? -1];
    const long20 = pairs[pairIndexMap.get(isEma ? "ema-0-20" : "price-0-20") ?? -1];
    const pick = (
      p: typeof anchor,
      leg: "short" | "long",
    ): Array<number | null> =>
      (p?.rows ?? []).map((r) =>
        mode === "slope"
          ? leg === "short"
            ? r.short_slope
            : r.long_slope
          : leg === "short"
            ? r.short_curvature
            : r.long_curvature,
      );
    const shortName = isEma ? "EMA6" : "MA5";
    const longName = isEma ? "EMA20" : "MA20";
    return {
      mode,
      lines: [
        { name: `Price ${suffix}`, data: pick(anchor, "short"), color: MUTED_PALETTE[0] },
        { name: `${shortName} ${suffix}`, data: pick(anchor, "long"), color: MUTED_PALETTE[1] },
        { name: `${longName} ${suffix}`, data: pick(long20, "long"), color: MUTED_PALETTE[2] },
      ],
    };
  }, [chartOverlay, selectedPair, pairs, pairIndexMap]);

  // ---- Momentum overlay lines (the Momentum section's terms row). The
  // "momentum" extras rows are aligned to the selected pair's dates via
  // a date→row Map (the same alignment the Top-10% OHLC shades use);
  // the adaptive terms' per-date lookbacks ride along for the tooltip's
  // "short·14d" suffixes. One line per PICKED term, in the constants'
  // canonical order.
  const momentumTrend = useMemo<MomentumTrendSpec | null>(() => {
    if (!momentumLinesActive || selectedPair == null) return null;
    const rows = extras.momentum;
    if (rows == null || rows.length === 0) return null;
    const byDate = new Map(rows.map((r) => [r.date, r]));
    const n = selectedPair.rows.length;
    const lbs: NonNullable<MomentumTrendSpec["lbs"]> = {
      short: new Array<number | null>(n).fill(null),
      mid: new Array<number | null>(n).fill(null),
      long: new Array<number | null>(n).fill(null),
    };
    for (let i = 0; i < n; i++) {
      const r = byDate.get(selectedPair.rows[i].date);
      if (!r) continue;
      lbs.short[i] = r.lb_short;
      lbs.mid[i] = r.lb_mid;
      lbs.long[i] = r.lb_long;
    }
    const order = MOMENTUM_TERMS as readonly MomentumTerm[];
    const picked = order.filter((t) => momentumTerms.includes(t));
    return {
      lines: picked.map((term) => ({
        name: `Mom ${term}`,
        term,
        color: MOMENTUM_TERM_COLORS[term],
        data: selectedPair.rows.map(
          (r) =>
            (byDate.get(r.date)?.[MOMENTUM_VALUE_KEYS[term]] as
              | number
              | null
              | undefined) ?? null,
        ),
      })),
      lbs,
    };
  }, [momentumLinesActive, extras.momentum, momentumTerms, selectedPair]);

  // The Top-10% OHLC Days SHADES — light red bands over spike-fade
  // days (upper wick in its trailing-252d top decile), light green over
  // flush-recover days (lower wick likewise). Consecutive flagged dates
  // merge into one band (single-day bands allowed); rendered through
  // the shared markArea overlay path (same shape as regimeShades).
  const { shades: ohlcGapShades, counts: ohlcGapCounts } = useMemo(() => {
    const empty = { shades: [] as Array<{
      label: string;
      data: Array<
        [
          { xAxis: string; itemStyle: { color: string } },
          { xAxis: string },
        ]
      >;
      accent: string;
    }>, counts: { spike: 0 as number, flush: 0 as number } };
    if (!gapMarks.spike && !gapMarks.flush) return empty;
    if (extras.ohlcGaps == null || selectedPair == null) return empty;
    const byDate = new Map(extras.ohlcGaps.map((d) => [d.date, d]));
    const kinds = [
      {
        kind: "spike" as const,
        on: gapMarks.spike,
        flag: "uw_top10" as const,
        label: "Spike fade (top-10% uw)",
        accent: "rgb(192, 57, 43)",
        rgba: "rgba(192, 57, 43, 0.16)",
      },
      {
        kind: "flush" as const,
        on: gapMarks.flush,
        flag: "lw_top10" as const,
        label: "Flush recover (top-10% lw)",
        accent: "rgb(39, 174, 96)",
        rgba: "rgba(39, 174, 96, 0.16)",
      },
    ];
    const dates = selectedPair.rows.map((r) => r.date);
    const shades: Array<{
      label: string;
      data: Array<
        [
          { xAxis: string; itemStyle: { color: string } },
          { xAxis: string },
        ]
      >;
      accent: string;
    }> = [];
    const counts: Record<"spike" | "flush", number> = { spike: 0, flush: 0 };
    for (const k of kinds) {
      if (!k.on) continue;
      // consecutive flagged dates merge into one band (single-day
      // bands allowed — the marked days are scattered by nature)
      const runs: Array<{ start: string; end: string; days: number }> = [];
      let cur: { start: string; end: string; days: number } | null = null;
      let flagged = 0;
      for (const dt of dates) {
        const d = byDate.get(dt);
        if (d != null && d[k.flag] === true) {
          flagged += 1;
          if (cur == null) {
            cur = { start: dt, end: dt, days: 1 };
            runs.push(cur);
          } else {
            cur.end = dt;
            cur.days += 1;
          }
        } else {
          cur = null;
        }
      }
      counts[k.kind] = flagged;
      if (runs.length === 0) continue;
      shades.push({
        label: k.label,
        accent: k.accent,
        data: runs.map((r) => [
          { xAxis: r.start, itemStyle: { color: k.rgba } },
          { xAxis: r.end },
        ]),
      });
    }
    return { shades, counts };
  }, [gapMarks, extras.ohlcGaps, selectedPair]);

  // The RSI Top-% SHADES (the RSI Trends section's pct row): the picked
  // pct is SPLIT EVENLY across the two ends of the code's own RSI-rank
  // axis (analysis.mov_ave_rsi_ranks — ONE rank family, rank 1 = the
  // code's highest RSI reading, dense 1..N; the axis is treated as a
  // signed scale): green shades mark the dates ranking within the top
  // pct/2 of the window's own history (rises, UP_COLOR), red the bottom
  // pct/2 (drops, DOWN_COLOR). Per window the half cutoff is
  // ceil(pct/2 × N_w) with N_w the window's count of ranked days = the
  // max stored rank; the bottom side qualifies at rank > N_w − cutoff.
  //
  // DEPTH: every picked window contributes the SAME base strength and
  // transparency (one qualifying window = the flat base fill), and each
  // ADDITIONAL picked window whose ranks qualify on the same date stacks
  // the shade darker — per-date qualifying-window COUNT per side, merged
  // into runs of equal count, rect opacity = base × count (capped). The
  // days row scopes the ranks (picked windows only; rsi3 1% and rsi60 1%
  // shade different date sets), and a single side's date may qualify for
  // both sides via different windows — the green and red overlays then
  // overlap (each independently legend-toggleable). The overlays draw in
  // every right-axis mode once a pct is picked.
  const rsiRankShades = useMemo<ShadeOverlay[]>(() => {
    if (rsiTopPct == null) return [];
    const ranks = extras.rsiRanks;
    if (ranks == null || ranks.length === 0 || selectedPair == null) return [];
    // The days row scopes the rank windows: picked windows only (sorted),
    // or all six when the row is empty.
    const shadeWindows =
      rsiDays.length > 0
        ? [...rsiDays].sort((a, b) => a - b)
        : [...RSI_WINDOWS];
    // Per-window N_w = the count of ranked (non-null-RSI) days — the max
    // stored rank, exact because the ranks are dense 1..N per window.
    const totals = new Map<number, number>();
    for (const r of ranks) {
      for (const w of shadeWindows) {
        const rk = r[`rank_rsi_${w}days`];
        if (rk != null && rk > (totals.get(w) ?? 0)) totals.set(w, rk);
      }
    }
    // The picked pct split evenly: half for the top (rise) end, half for
    // the bottom (drop) end — one cutoff per window serves both sides.
    const cutoffs = new Map<number, number>();
    for (const [w, n] of totals) {
      cutoffs.set(w, Math.max(1, Math.ceil((rsiTopPct / 2) * n)));
    }
    // Per-date qualifying-window count per side — the shade's depth.
    const counts = new Map<string, { rise: number; drop: number }>();
    for (const r of ranks) {
      let rise = 0;
      let drop = 0;
      for (const w of shadeWindows) {
        const cutoff = cutoffs.get(w);
        const rk = r[`rank_rsi_${w}days`];
        const n = totals.get(w);
        if (cutoff == null || n == null || rk == null) continue;
        if (rk <= cutoff) rise += 1;
        else if (rk > n - cutoff) drop += 1;
      }
      if (rise > 0 || drop > 0) counts.set(r.date, { rise, drop });
    }
    const byDateHas = new Set(counts.keys());
    const dates = selectedPair.rows.map((r) => r.date);
    // Consecutive dates of EQUAL count merge into one band; a count
    // change starts a new band (single-day bands allowed — the marked
    // days are scattered by nature).
    const buildCountedRuns = (
      side: "rise" | "drop",
    ): Array<{ start: string; end: string; count: number }> => {
      const runs: Array<{ start: string; end: string; count: number }> = [];
      let cur: { start: string; end: string; count: number } | null = null;
      for (const dt of dates) {
        const c = byDateHas.has(dt) ? counts.get(dt)![side] : 0;
        if (c > 0) {
          if (cur != null && cur.count === c) {
            cur.end = dt;
          } else {
            cur = { start: dt, end: dt, count: c };
            runs.push(cur);
          }
        } else {
          cur = null;
        }
      }
      return runs;
    };
    const shadeRects = (
      runs: Array<{ start: string; end: string; count: number }>,
      accent: string,
    ): ShadeOverlay["data"] =>
      runs.map((r) => [
        {
          xAxis: r.start,
          itemStyle: {
            color: accent,
            opacity: Math.min(RSI_SHADE_BASE_OPACITY * r.count, 0.8),
          },
        },
        { xAxis: r.end },
      ]);
    const riseRuns = buildCountedRuns("rise");
    const dropRuns = buildCountedRuns("drop");
    // The half pct in percent (e.g. picked 1% → 0.5) for the legend labels.
    const halfPct = Math.round(rsiTopPct * 500) / 10;
    // A single picked window names it in the legend (RSI3 Rise 0.5% vs
    // RSI60's — the comparison the days row exists for); multi/none stays
    // generic.
    const wLabel =
      shadeWindows.length === 1 ? `RSI${shadeWindows[0]} ` : "";
    const out: ShadeOverlay[] = [];
    if (riseRuns.length > 0) {
      out.push({
        label: `${wLabel}Rise ${halfPct}%`,
        accent: UP_COLOR,
        data: shadeRects(riseRuns, UP_COLOR),
      });
    }
    if (dropRuns.length > 0) {
      out.push({
        label: `${wLabel}Drop ${halfPct}%`,
        accent: DOWN_COLOR,
        data: shadeRects(dropRuns, DOWN_COLOR),
      });
    }
    return out;
  }, [rsiTopPct, rsiDays, extras.rsiRanks, selectedPair]);

  // The Trend section's SHADES — the signals tier's spans
  // (analysis.trend_days, the RSI re-entry classification; date-based
  // rects — no pair-row alignment needed, so the shades draw on the
  // selected pair whatever it is). Each picked phase contributes one
  // legend series of merged markArea rects in its light fill —
  // rendered through the shared shade-overlay path alongside the
  // regime / gap / RSI-rank overlays.
  const trendShades = useMemo<ShadeOverlay[]>(() => {
    if (trendPicks.size === 0 || trendSpans == null) return [];
    const out: ShadeOverlay[] = [];
    for (const t of TREND_TYPES) {
      if (!trendPicks.has(t)) continue;
      const spans = trendSpans[t];
      if (!spans || spans.length === 0) continue;
      out.push({
        label: `Trend(${TREND_TYPE_LABELS[t]})`,
        accent: TREND_ACCENTS[t],
        data: trendSpansToMarkArea(spans, TREND_SHADES[t]),
      });
    }
    return out;
  }, [trendPicks, trendSpans]);

  // Optional secondary stat row from the latest snapshot of all 9 pairs —
  // surfaced as a small caption so the user can scan the page quickly.
  const latestSummary = chartData?.pairs[safePairIdx]?.rows.slice(-1)[0] ?? null;

  const subtitle = chartData
    ? `${chartData.code} · ${chartData.name || name || "—"} · ${firstPairRows.length} bars` +
      (firstPairRows.length > 0
        ? ` · ${firstPairRows[0].date} → ${firstPairRows[firstPairRows.length - 1].date}`
        : "")
    : `${code} · ${name || "—"}`;

  // Bollinger dropdown + Amt | RSI Trends overlay switch + OHLC mode toggle
  // shown in the card header's top-right corner.
  const bollAction = !loading && !error && selectedPair ? (
    <Stack direction="row" alignItems="center" spacing={1} sx={{ mr: 0.5 }}>
      {(selectedPair.ma_short === 0 || selectedPair.kind === "amt") && (
        <Stack direction="row" alignItems="center" spacing={0.5}>
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ fontSize: "0.7rem", whiteSpace: "nowrap" }}
          >
            Bollinger
          </Typography>
          <Select
            size="small"
            value={bollingerK}
            onChange={(e) => setBollingerK(e.target.value as number)}
            sx={{
              height: 26,
              fontSize: "0.75rem",
              "& .MuiSelect-select": { py: 0.25, px: 1, fontSize: "0.75rem" },
            }}
            renderValue={(v) =>
              v === 0 ? "Off" : `${Number(v).toFixed(1)}σ`
            }
          >
            {BOLL_K_OPTIONS.map((k) => (
              <MenuItem key={k} value={k} sx={{ fontSize: "0.75rem", py: 0.25 }}>
                {k === 0 ? "Off (0.0)" : `${k.toFixed(1)}σ`}
              </MenuItem>
            ))}
          </Select>
        </Stack>
      )}
      {/* Amt | Slopes | Curvatures — the header switch for the chart's
          right-axis overlay (mirrors the OhlcModeToggle group beside it).
          Slopes/Curvatures draw the DB-stored direct derivative lines of
          the price and the 5/20-day MA (6/20-day EMA while an EMA pair
          chip is selected) — value[t] − value[t−1] and its diff, no
          averaging. The RSI Trends overlay moved out (its own button row
          beneath the chart) and rides the "amt" slot via its days row.
          Disabled while an Amt/MA pair's envelope view is shown (that
          view's whole plot IS amount). */}
      <ToggleButtonGroup
        size="small"
        exclusive
        value={chartOverlay}
        onChange={(_, v: ChartOverlay | null) => {
          if (v) setChartOverlay(v);
        }}
        disabled={amtPairSelected}
      >
        <ToggleButton value="amt" sx={{ px: 1, py: 0.25, fontSize: "0.7rem" }}>
          Amt
        </ToggleButton>
        <ToggleButton value="slope" sx={{ px: 1, py: 0.25, fontSize: "0.7rem" }}>
          Slopes
        </ToggleButton>
        <ToggleButton value="curv" sx={{ px: 1, py: 0.25, fontSize: "0.7rem" }}>
          Curvatures
        </ToggleButton>
      </ToggleButtonGroup>
      <OhlcModeToggle value={ohlcMode} onChange={setOhlcMode} />
    </Stack>
  ) : undefined;

  // Chart option for the selected pair — null while loading / on error / when
  // the pair has no rows; BaseChart then renders the shared spinner / error
  // Alert / empty placeholder instead of the chart. Memoized so the AI Ask
  // plot info derives from the same object the chart renders.
  const chartOption = useMemo(
    () =>
      !loading && !error && selectedPair && selectedPair.rows.length > 0
        ? amtPairSelected
          ? buildAmtEnvelopeOption({
              pair: selectedPair as Omit<MovAveSpreadPairSeries, "rows"> & { rows: MovAveSpreadAmtRow[] },
              shared: chartData?.shared ?? null,
              themeMode,
              ohlcMode,
              bollingerK,
              longStreaks,
              streakPeriod,
              streakPct,
              streakAnchorIdx: anchorIdx,
              pxVolShade,
              regimeShades: [...regimeShades, ...ohlcGapShades, ...trendShades],
              ohlcGapRows: extras.ohlcGaps ?? null,
              ohlcGapActive: gapMarks.spike || gapMarks.flush,
            })
          : buildPairOption({
              pair: selectedPair,
              shared: chartData?.shared ?? null,
              themeMode,
              bollingerK,
              tradingAmtMode:
                chartOverlay === "amt" && rsiDays.length === 0 && !momentumLinesActive
                  ? "lowkey"
                  : "off",
              rsiTrendMode: rsiLinesActive,
              rsiWindows: rsiDays,
              rsiPopulation,
              derivTrend,
              momentumTrend,
              ohlcMode,
              roofFloorWindow,
              roofFloorClickIdx: anchorIdx,
              roofFloorRows: extras.ohlc ?? null,
              roofFloorEstRows: extras.rfEstimates ?? null,
              roofFloorEstMethod: rfEstOn ? rfEstMethod : null,
              roofFloorRebounds: extras.rfRebounds ?? null,
              longStreaks,
              streakPeriod,
              streakPct,
              streakAnchorIdx: anchorIdx,
              pxVolShade,
              regimeShades: [
                ...regimeShades,
                ...ohlcGapShades,
                ...rsiRankShades,
                ...trendShades,
              ],
              ohlcGapRows: extras.ohlcGaps ?? null,
              ohlcGapActive: gapMarks.spike || gapMarks.flush,
              trendSpans,
            })
        : null,
    [
      loading,
      error,
      selectedPair,
      amtPairSelected,
      chartData,
      extras.ohlc,
      themeMode,
      bollingerK,
      trendSpans,
      chartOverlay,
      rsiDays,
      rsiPopulation,
      rsiLinesActive,
      derivTrend,
      momentumTrend,
      momentumLinesActive,
      ohlcMode,
      roofFloorWindow,
      anchorIdx,
      extras.ohlc,
      extras.rfEstimates,
      extras.rfRebounds,
      rfEstOn,
      rfEstMethod,
      longStreaks,
      streakPeriod,
      streakPct,
      pxVolShade,
      regimeShades,
      ohlcGapShades,
      rsiRankShades,
      trendShades,
      extras.ohlcGaps,
      gapMarks.spike,
      gapMarks.flush,
    ],
  );

  // ---- AI Ask — wire the OUTER card (the chart body is a bare BaseChart).
  // State carries every in-plot control's CURRENT value so the modal / LLM
  // describe the view on screen.
  const maAiAskRef = useRef<ECharts | null>(null);
  const shortName = selectedPair ? shortLabel(selectedPair.ma_short, selectedPair.kind) : "";
  const longName = selectedPair
    ? selectedPair.kind === "ema"
      ? `EMA${selectedPair.ma_long}`
      : `MA${selectedPair.ma_long}`
    : "";
  const streakLabel =
    streakPeriod != null && streakPct != null ? `Streak(${streakPeriod}d·${streakPct}%)` : "";
  // Primitive (not the firstPairRows array) so the spec memo below stays
  // identity-stable.
  const anchorDate =
    anchorIdx != null && firstPairRows[anchorIdx]
      ? firstPairRows[anchorIdx].date
      : undefined;
  const aiAskSpec = useMemo<AiAskSpec>(
    () => ({
      intro:
        `MA-spread study for ${code} — pair ${selectedPair?.pair_label ?? "—"}: short curve vs ` +
        "long MA, green fill when short > long, red when short < long; tooltips report each " +
        "series' slope and curvature. Control overlays: Bollinger ±kσ envelope on the long " +
        "MA, click-anchored rolling High/Low trendlines (roof/floor), extreme↔2nd-extreme " +
        "shade zones (all roof/floor windows at once), break-streak zones, " +
        "Px-Vol state shading (price speed × amount state). % mode: OHLC + MAs rebased to " +
        "% change from the first valid close; Amt pairs switch to an amount-envelope view " +
        "with a lowkey price reference. Indication: green fill = momentum regime long; " +
        "roof/floor breach = breakout; a deep streak = stretched — fade-prone.",
      instruments: [{ code, name: name || undefined }],
      series: amtPairSelected
        ? [
            { name: "Price", description: "lowkey (high+low)/2 close reference for the amt-envelope view" },
            { name: "Amt Above", unit: "亿", description: "daily trading amount at/above the selected Amt MA" },
            { name: "Amt Below", unit: "亿", description: "daily trading amount below the selected Amt MA" },
            ...[5, 20, 60, 120, 255].map((w) => ({
              name: `Amt MA${w}`,
              unit: "亿",
              description: `${w}-day trading-amount moving average (selected window emphasized)`,
            })),
          ]
        : [
            { name: shortName, description: "the pair's short leg (price or short MA/EMA)" },
            { name: longName, description: "the pair's long moving average" },
            ...(rsiLinesActive
              ? rsiDays.slice().sort((a, b) => a - b).map((w) => ({
                  name: `RSI${w}`,
                  description: `${w}-day Wilder RSI trend line on the offset right axis — RSI 0-100 hugs the bottom quarter of the shared plot (dashed 70/30 guides)`,
                }))
              : derivTrend != null
                ? derivTrend.lines.map((l) => ({
                    name: l.name,
                    description:
                      `${l.name.replace(/ (Slope|Curv)$/, "")} ${derivTrend.mode === "slope" ? "1st derivative — daily diff of the price / MA / EMA line" : "2nd derivative — diff of that daily diff"}, DB-stored direct diff, raw price units per trading day, dashed zero guide`,
                  }))
                : roofFloorWindow != null
                  ? [
                      { name: `Δ Low(${roofFloorWindow}d)`, description: "close − the LAST low anchor's value (change_since_last_low): floor-blue bars ABOVE the dashed zero central line, positive = price above the floor anchor; crossing below it = floor break" },
                      { name: `Δ High(${roofFloorWindow}d)`, description: "close − the LAST high anchor's value (change_since_last_high): roof-orange bars BELOW the zero central line, negative = price below the roof anchor; crossing above it = roof breakout" },
                    ]
                  : [
                    { name: "Amt Up", unit: "亿", description: "daily trading amount on up days (lowkey bars)" },
                    { name: "Amt Down", unit: "亿", description: "daily trading amount on down days (lowkey bars)" },
                  ]),
            { name: `Upper (+${bollingerK}σ)`, description: "long MA + k×σ Bollinger band edge" },
            { name: `Lower (−${bollingerK}σ)`, description: "long MA − k×σ Bollinger band edge" },
            ...(roofFloorWindow != null
              ? [
                  { name: `High(${roofFloorWindow}d)`, description: "rolling OHLC window high" },
                  { name: `Low(${roofFloorWindow}d)`, description: "rolling OHLC window low" },
                  { name: `Roof(${roofFloorWindow}d)`, description: "trendline through the window's top+2nd highs, stopping at the anchor date" },
                  { name: `Floor(${roofFloorWindow}d)`, description: "trendline through the window's top+2nd lows, stopping at the anchor date" },
                ]
              : []),
            ...(roofFloorWindow != null && rfEstOn
              ? [
                  { name: `Est roof (${rfEstMethod})`, description: `estimated resistance level AT the chosen date under the ${rfEstMethod} method (analysis.mov_ave_spreads_roof_floor_estimates; median = the cross-method consensus of trendline/donchian/quantile/channel/boll) — horizontal dashed roof-orange line` },
                  { name: `Est floor (${rfEstMethod})`, description: "the mirror estimated support level — horizontal dashed floor-blue line at the same chosen date" },
                ]
              : []),
            ...(streakLabel
              ? [
                  { name: `High ${streakLabel}`, description: "merged span of closes above the anchor window's static top pct zone" },
                  { name: `Low ${streakLabel}`, description: "merged span of closes below the anchor window's static bottom pct zone" },
                ]
              : []),
            ...(pxVolShade ? [{ name: pxVolShade.label, description: "dates matching the selected price-speed × amount-state combo" }] : []),
            ...(trendPicks.size > 0
              ? TREND_TYPES.filter((t) => trendPicks.has(t)).map((t) => ({
                  name: `Trend(${TREND_TYPE_LABELS[t]})`,
                  description:
                    "the signals tier's trend spans (analysis.trend_days: RSI re-entry momentum episodes + MA255-gap extremes) shaded in the phase's light fill (top red / bottom green / rise blue / fall amber)",
                }))
              : []),
          ],
      state: {
        pair: selectedPair?.pair_label ?? "—",
        ohlc_mode: ohlcMode === "percentage" ? "% (rebased to first valid close)" : "absolute",
        bollinger_k: bollingerK,
        lower_overlay:
          (rsiLinesActive
            ? `rsi trends (${rsiDays.slice().sort((a, b) => a - b).join("/")}d window lines replace the amt bars)`
            : derivTrend != null
              ? `${derivTrend.mode === "slope" ? "slopes" : "curvatures"} (Price + ${selectedPair?.kind === "ema" ? "EMA6/EMA20" : "MA5/MA20"} DB-stored derivative lines — value[t] − value[t−1], no averaging — replace the amt bars; the MA/EMA curves follow the selected pair chip)`
              : roofFloorWindow != null
                ? "change-since-last-anchor plot (the enabled roof/floor window's Δ Low / Δ High bars around a dashed zero central line — Δ Low positive above = price off the floor anchor, Δ High negative below = price off the roof anchor — replace the amt bars)"
                : "amt (lowkey bars)") +
          (rsiTopPct != null
            ? ` + RSI top-${Math.round(rsiTopPct * 100)}% history-rank shades split evenly: green where the RSI ranked within the top ${Math.round(rsiTopPct * 500) / 10}% of its own ${rsiDays.length > 0 ? `${rsiDays.slice().sort((a, b) => a - b).join("/")}d window` : "history (any window)"} (rises), red where within the bottom ${Math.round(rsiTopPct * 500) / 10}% (drops); base fill per qualifying window, darker where multiple picked windows' extremes overlap`
            : "") +
          (trendPicks.size > 0
            ? ` + signals-tier trend phase shades (${TREND_TYPES.filter((t) => trendPicks.has(t)).map((t) => TREND_TYPE_LABELS[t]).join("/")})`
            : ""),
        ohlc_window: roofFloorWindow ?? "off",
        anchor_date: anchorDate ?? "none",
        rf_estimate:
          roofFloorWindow != null && rfEstOn
            ? `${rfEstMethod} @ ${anchorDate ?? "latest date"}`
            : "off",
        streak_combo: streakLabel || "off",
        px_vol: pxVolShade?.label ?? "off",
        trend:
          trendPicks.size > 0
            ? TREND_TYPES.filter((t) => trendPicks.has(t))
                .map((t) => TREND_TYPE_LABELS[t])
                .join("/")
            : "off",
      },
      notes: [
        "All 9 pairs share one date axis; the in-chart dataZoom windows the view.",
        "Break streaks are detected client-side against the anchor window's static band edge (≤5-day in-band gaps bridged).",
        "Shade families are mutually exclusive — one shade source at a time (trend / RSI pct / streaks / px-vol / OHLC days / regimes); a pick in one clears the others.",
      ],
    }),
    [
      code,
      name,
      selectedPair,
      amtPairSelected,
      shortName,
      longName,
      bollingerK,
      roofFloorWindow,
      rfEstOn,
      rfEstMethod,
      streakLabel,
      pxVolShade,
      ohlcMode,
      rsiDays,
      rsiLinesActive,
      derivTrend,
      anchorDate,
      rsiTopPct,
      trendPicks,
    ],
  );
  const aiAskAddon = useAiAskAddon({
    title: selectedPair ? selectedPair.pair_label : "MA-Spread",
    subtitle,
    option: chartOption,
    spec: aiAskSpec,
    getInstance: () => maAiAskRef.current,
  });

  // Render a single pair chip (used in the 2-row pair grid). The chip fills
  // its grid column: display:flex overrides MUI's default inline-flex so
  // width:100% takes effect, and the label is centered within.
  // Price and EMA chips are always loaded with the default chart. Amt/MA
  // chips belong to the "amt" extras group: the FIRST pick loads the group
  // (freezing every control until it resolves) and selects the chip as
  // soon as the data lands. Clicking an already-active Amt/MA chip toggles
  // it off ("unclick") and recovers the normal OHLC price style by falling
  // back to Price/MA5 (pair index 0).
  const renderPairChip = (
    pair: MovAveSpreadPairSeries,
    idx: number,
  ) => {
    const active = idx === safePairIdx;
    const isAmt = pair.kind === "amt";
    return (
      <Chip
        label={pair.pair_label}
        clickable
        size="small"
        disabled={extrasLoading}
        color={active ? "primary" : "default"}
        variant={active ? "filled" : "outlined"}
        onClick={() => {
          if (isAmt && !amtLoaded) {
            void ensureMetric("amt").then(() => setSelectedPairIdx(idx));
            return;
          }
          // Toggle off an active Amt/MA chip → recover OHLC price style.
          if (active && isAmt) {
            setSelectedPairIdx(0);
          } else {
            setSelectedPairIdx(idx);
          }
        }}
        sx={PERIOD_CHIP_SX}
      />
    );
  };

  return (
    <ChartCard
      title={selectedPair ? selectedPair.pair_label : "MA-Spread"}
      subtitle={subtitle}
      titleAddon={aiAskAddon}
      action={
        bollAction ? (
          <Stack direction="row" alignItems="center">
            {bollAction}
            <AnalysisRunButton
              module="mov_ave_spread"
              secType={secType}
              code={code}
              hasData={hasAnalysisData}
              onCompleted={handleAnalysisRunCompleted}
            />
          </Stack>
        ) : (
          <AnalysisRunButton
            module="mov_ave_spread"
            secType={secType}
            code={code}
            hasData={hasAnalysisData}
            onCompleted={handleAnalysisRunCompleted}
          />
        )
      }
    >
      {/* Chart body (loading / error / empty placeholders + the EChart) is
          owned by the shared BaseChart, embedded bare inside this card; the
          pair-chip / window / streak / px-vol controls go in its children
          slot (rendered above the chart body in every state). */}
      <BaseChart
        variant="bare"
        option={chartOption}
        height={420}
        loading={loading || extrasLoading || regimesLoading}
        freezeOnLoading
        error={error}
        emptyText={
          selectedPair
            ? `No data for ${selectedPair.pair_label} in this date range.`
            : "No data"
        }
        onCanvasClick={handleCanvasClick}
        onReady={(c) => {
          maAiAskRef.current = c;
        }}
      >
      {/* Pair chips — Simple MA section (2 rows) + Exponential MA section
          (2 rows) + optional Trading Amt/MA row. Moved to the top of the
          card so the time slider can sit at the bottom. */}
      {!loading && !error && pairs.length > 0 && (
        <Box sx={{ mt: 1, mb: 0.5 }}>
          {/* ---- Simple MA section ---- */}
          <SectionLabel id="pairsSimple" standalone suffix=" — click to switch" />
          <Box sx={PERIOD_GRID_SX}>
            {/* Price row (ma_short = 0): one chip per long-MA column.
                Always clickable — selecting one recovers the normal OHLC
                price style (exits the amt-envelope view). */}
            {LONG_MA_ORDER.map((maLong, col) => {
              const idx = pairIndexMap.get(`price-0-${maLong}`);
              return (
                <Box key={`price-${col}`} sx={{ gridColumn: col + 1 }}>
                  {idx != null && renderPairChip(pairs[idx], idx)}
                </Box>
              );
            })}
            {/* MA5 row (ma_short = 5): no MA5/MA5 pair — col 0 left empty.
                Always clickable. */}
            {LONG_MA_ORDER.map((maLong, col) => {
              if (maLong === 5) {
                return <Box key={`ma5-empty-${col}`} sx={{ gridColumn: col + 1 }} />;
              }
              const idx = pairIndexMap.get(`price-5-${maLong}`);
              return (
                <Box key={`ma5-${col}`} sx={{ gridColumn: col + 1 }}>
                  {idx != null && renderPairChip(pairs[idx], idx)}
                </Box>
              );
            })}
          </Box>

          {/* ---- Exponential MA section ---- */}
          <SectionLabel id="pairsEma" standalone mt={1} suffix=" — click to switch" />
          <Box sx={PERIOD_GRID_SX}>
            {/* Price/EMA row (ma_short = 0): one chip per long-EMA column. */}
            {LONG_EMA_ORDER.map((emaLong, col) => {
              const idx = pairIndexMap.get(`ema-0-${emaLong}`);
              return (
                <Box key={`ema-price-${col}`} sx={{ gridColumn: col + 1 }}>
                  {idx != null && renderPairChip(pairs[idx], idx)}
                </Box>
              );
            })}
            {/* EMA6/EMA row (ma_short = 6): no EMA6/EMA6 pair — col 0 empty. */}
            {LONG_EMA_ORDER.map((emaLong, col) => {
              if (emaLong === 6) {
                return <Box key={`ema6-empty-${col}`} sx={{ gridColumn: col + 1 }} />;
              }
              const idx = pairIndexMap.get(`ema-6-${emaLong}`);
              return (
                <Box key={`ema6-${col}`} sx={{ gridColumn: col + 1 }}>
                  {idx != null && renderPairChip(pairs[idx], idx)}
                </Box>
              );
            })}
          </Box>

          {/* ---- Trading Amt/MA section ---- */}
          <Box sx={{ ...PERIOD_GRID_SX, mt: 1 }}>
            {/* Row label */}
            <SectionLabel
              id="tradingAmt"
              active={amtPairSelected}
              suffix={
                extrasLoading && !amtLoaded
                  ? " — loading…"
                  : !amtLoaded
                    ? " — click an Amt/MA chip to load"
                    : ""
              }
            />
            {LONG_MA_ORDER.map((maLong, col) => {
              const idx = pairIndexMap.get(`amt--1-${maLong}`);
              if (idx == null && !amtLoaded) {
                // Placeholder chip — the "amt" extras group isn't loaded
                // yet. Clicking it fetches the group (freezing every
                // control until it resolves) and selects the pair as
                // soon as it lands (amt series append after the 18
                // defaults, in this row's column order).
                return (
                  <Box key={`amt-${col}`} sx={{ gridColumn: col + 1 }}>
                    <Chip
                      label={`Amt/MA${maLong}`}
                      size="small"
                      clickable
                      disabled={extrasLoading}
                      color="default"
                      variant="outlined"
                      onClick={() => {
                        void ensureMetric("amt").then(() =>
                          setSelectedPairIdx(18 + col),
                        );
                      }}
                      sx={PERIOD_CHIP_SX}
                    />
                  </Box>
                );
              }
              return (
                <Box key={`amt-${col}`} sx={{ gridColumn: col + 1 }}>
                  {idx != null && renderPairChip(pairs[idx], idx)}
                </Box>
              );
            })}
          </Box>

          {/* ---- RSI Trends buttons (days multi-select + pct single-select) ----
              The right-axis RSI overlay's own row (moved out of the header
              Amt/Slopes/Curvatures toggle). The section rests fully OFF:
              the DAYS row multi-picks the Wilder-RSI window lines
              (3/6/10/14/20/60d) — while any day is picked and the header
              toggle sits at Amt, the picked RSI lines replace the
              trading-amt bars — and the FIRST day pick auto-arms the 1%
              shade (un-picking the last day takes it off). The PCT row
              single-picks the Top-% history-rank shade (split evenly green
              top-half rises / red bottom-half drops; clicking the active
              pct again clears it); the picked days scope the ranks and
              overlapping windows stack the shade darker. Both rows sit on
              the section grid's period columns (3-7) like the other rows. */}
          <Box sx={{ ...PERIOD_GRID_SX, mt: 1, position: "relative" }}>
            {/* Population toggle — the section's upper-right corner, once
                an RSI trend is selected: typical = the first-touch
                episodes' days (each episode's first tail day AND its
                gap-1 continuation days — the deep path); re-entry = the
                same-episode re-registrations' days (each re-qualification
                AND its continuations); neither = all days. */}
            {rsiDays.length > 0 && (
              <Stack
                direction="row"
                spacing={0.5}
                sx={{ position: "absolute", top: -30, right: 0 }}
              >
                <Chip
                  label="typical"
                  size="small"
                  clickable
                  color={rsiPopulation === "typical" ? "secondary" : "default"}
                  variant={rsiPopulation === "typical" ? "filled" : "outlined"}
                  onClick={() =>
                    setRsiPopulation(
                      rsiPopulation === "typical" ? null : "typical",
                    )
                  }
                  sx={PERIOD_CHIP_SX}
                />
                <Chip
                  label="re-entry"
                  size="small"
                  clickable
                  color={rsiPopulation === "re-entry" ? "secondary" : "default"}
                  variant={rsiPopulation === "re-entry" ? "filled" : "outlined"}
                  onClick={() =>
                    setRsiPopulation(
                      rsiPopulation === "re-entry" ? null : "re-entry",
                    )
                  }
                  sx={PERIOD_CHIP_SX}
                />
              </Stack>
            )}
            {/* Row label */}
            <SectionLabel
              id="rsiTrends"
              active={rsiDays.length > 0 || rsiTopPct != null}
            />
            {/* Days row — multi-select, columns 3-8. */}
            {RSI_WINDOWS.map((w, col) => (
              <Chip
                key={w}
                label={`${w}d`}
                size="small"
                clickable
                color={rsiDays.includes(w) ? "primary" : "default"}
                variant={rsiDays.includes(w) ? "filled" : "outlined"}
                onClick={() => toggleRsiDay(w)}
                sx={{ gridColumn: col + 3, ...PERIOD_CHIP_SX }}
              />
            ))}
            {/* Pct row — single-select toggles, columns 3-7 (no Off entry:
                the active pct toggles off by clicking it again). */}
            {RSI_TOP_PCTS.map((p, col) => (
              <Chip
                key={p}
                label={`${Math.round(p * 100)}%`}
                size="small"
                clickable
                color={rsiTopPct === p ? "primary" : "default"}
                variant={rsiTopPct === p ? "filled" : "outlined"}
                onClick={() => handleRsiTopPctSelect(rsiTopPct === p ? 0 : p)}
                sx={{ gridColumn: col + 3, ...PERIOD_CHIP_SX }}
              />
            ))}
          </Box>
          {rsiTopPct != null && (
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ display: "block", mt: 0.5, fontSize: "0.65rem" }}
            >
              Top {Math.round(rsiTopPct * 100)}% split — green bands where the
              RSI ranked within the top {Math.round(rsiTopPct * 500) / 10}% of
              its own{" "}
              {rsiDays.length > 0
                ? `${rsiDays.slice().sort((a, b) => a - b).join("/")}d`
                : "history (any window)"}{" "}
              (rises), red where within the bottom{" "}
              {Math.round(rsiTopPct * 500) / 10}% (drops); one qualifying
              window draws the base fill, each extra window overlapping the
              date stacks it darker
              {rsiDays.length > 0
                ? ` · drawing RSI ${rsiDays.slice().sort((a, b) => a - b).join("/")}d lines`
                : " · pick days above to scope the ranks and draw the lines"}
            </Typography>
          )}

          {/* ---- Momentum buttons (terms multi-select) ----
              The lookback-adaptive momentum row: the two fixed anchors
              (5d / 20d) plus the three volatility-adaptive short/mid/
              long terms whose lookback varies per (code, regime) — see
              analysis.mov_ave_momentum. The adaptive chips spell their
              CURRENT effective lookback (latest day, e.g. "short (14d)")
              once the auto-loaded group lands. Any pick replaces the amt
              bars on the right axis (below RSI Trends / Slopes /
              Curvatures in the slot's precedence); the caption spells
              the latest day's effective lookbacks + the regime they were
              derived for. */}
          <Box sx={{ ...PERIOD_GRID_SX, mt: 1 }}>
            {/* Row label */}
            <SectionLabel
              id="momentum"
              active={momentumTerms.length > 0}
              suffix={
                loadingMetrics.includes("momentum") ? " — loading…" : ""
              }
            />
            {MOMENTUM_TERMS.map((term, col) => (
              <Chip
                key={term}
                label={momentumChipLabel(term, extras.momentum)}
                size="small"
                clickable
                disabled={extrasLoading}
                color={momentumTerms.includes(term) ? "primary" : "default"}
                variant={momentumTerms.includes(term) ? "filled" : "outlined"}
                onClick={() => toggleMomentumTerm(term)}
                sx={{ gridColumn: col + 3, ...PERIOD_CHIP_SX }}
              />
            ))}
          </Box>
          {momentumTerms.length > 0 &&
            extras.momentum != null &&
            extras.momentum.length > 0 &&
            (() => {
              const last = extras.momentum!.at(-1)!;
              const vol =
                last.vol_regime != null
                  ? `σ ${(last.vol_regime * 100).toFixed(2)}%/d · `
                  : "";
              return (
                <Typography
                  variant="caption"
                  color="text.secondary"
                  sx={{ display: "block", mt: 0.5, fontSize: "0.65rem" }}
                >
                  momentum = price[t]/price[t−n] − 1 · latest {last.date}:{" "}
                  {REGIME_LABELS[last.regime]} ({vol}
                  short {last.lb_short}d · mid {last.lb_mid}d · long{" "}
                  {last.lb_long}d — lookbacks adapt to the regime's median
                  volatility; hover the chart for per-date lookbacks)
                </Typography>
              );
            })()}

          {/* ---- Roof/floor window buttons ----
              Row label ("Roof/Floor Trendlines") spans the full grid width
              on its own row — same style as the Trading Amt/MA label — with
              the window buttons on a new row below it. The buttons keep the
              period-column alignment of the pair chips: 60d sits under the
              MA60 column, …, 1275d in the last column (columns 1-2 stay
              empty — the 20d is not a roof/floor window). */}
          <Box sx={{ ...PERIOD_GRID_SX, mt: 1 }}>
            {/* Row label */}
            <SectionLabel id="roofFloorWindow" active={roofFloorWindow != null} />
            {/* Est-levels toggle (column 1, free while the window buttons
                sit at columns 3-8): enables the two horizontal estimated
                roof/floor lines at the chosen date under the picked
                method. Loads the "rfest" extras group on first pick. */}
            {roofFloorWindow != null && (
              <Chip
                label="Est levels"
                size="small"
                clickable
                disabled={extrasLoading}
                color={rfEstOn ? "secondary" : "default"}
                variant={rfEstOn ? "filled" : "outlined"}
                onClick={handleRfEstToggle}
                sx={{ gridColumn: 1, ...PERIOD_CHIP_SX }}
              />
            )}
            {ROOF_FLOOR_WINDOWS.map((w, col) => (
              <Chip
                key={w}
                label={`${w}d`}
                size="small"
                clickable
                disabled={extrasLoading}
                color={roofFloorWindow === w ? "primary" : "default"}
                variant={roofFloorWindow === w ? "filled" : "outlined"}
                onClick={() => handleRoofFloorWindowClick(w)}
                sx={{ gridColumn: col + 3, ...PERIOD_CHIP_SX }}
              />
            ))}
          </Box>
          {roofFloorWindow != null && (
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ display: "block", mt: 0.5, fontSize: "0.65rem" }}
            >
              click a date on the chart to draw roof/floor trendlines ·
              right axis: Δ since last anchor (Δ Low above the zero line ·
              Δ High below) replaces the amt bars
              {anchorIdx != null && firstPairRows[anchorIdx]
                ? ` · anchor ${firstPairRows[anchorIdx].date}`
                : ""}
            </Typography>
          )}
          {/* Rebound audit captions (the "rfaudit" extras group, loaded
              with the first window pick): the selected window's rebound
              frequency/amplitude + retest hold/break funnel for both
              sides, from the code's own recorded extremes. */}
          {roofFloorWindow != null && extras.rfRebounds != null &&
            (() => {
              const evs = extras.rfRebounds ?? [];
              const line = (side: "floor" | "roof"): string => {
                const s = aggregateReboundAudit(evs, roofFloorWindow, side);
                if (s.nEvents === 0) {
                  return `no recorded ${side} events at ${roofFloorWindow}d`;
                }
                const parts = [
                  `${s.nEvents} extremes`,
                  `${s.nRebounded}/${s.nEvents} rebounded (${Math.round(s.reboundPct ?? 0)}%` +
                    (s.avgReboundPct != null ? `, avg ${s.avgReboundPct.toFixed(1)}%` : "") +
                    ")",
                ];
                if (s.nRetested > 0) {
                  parts.push(
                    `${s.nRetested} retested → ${s.nHeld} held / ${s.nBroken} broken` +
                      (s.nHeld > 0
                        ? ` (${s.nHeldLaterBroken}/${s.nHeld} holds later broken)`
                        : "") +
                      (s.avgBouncePct != null
                        ? ` · bounce avg ${s.avgBouncePct.toFixed(1)}%`
                        : "") +
                      (s.avgDepthPct != null
                        ? ` · depth avg ${s.avgDepthPct.toFixed(1)}%`
                        : ""),
                  );
                }
                return parts.join(" · ");
              };
              const cap = { variant: "caption", color: "text.secondary", sx: { display: "block", fontSize: "0.65rem" } } as const;
              return (
                <>
                  <Typography {...cap} sx={{ ...cap.sx, mt: 0.5 }}>
                    rebound audit ({roofFloorWindow}d · this code): floor — {line("floor")}
                  </Typography>
                  <Typography {...cap}>
                    roof — {line("roof")} · ▲/▼ = rebounded extreme before the chosen date, near the median roof/floor (±5%) · source: analysis.mov_ave_spreads_roof_floor_rebounds
                  </Typography>
                </>
              );
            })()}
          {/* Est-levels method picker — one chip per quantitative method
              (columns 3-8), shown while the toggle is on. The picked
              method's roof/floor estimates for the chosen date (anchor
              date, latest row by default) draw as the two horizontal
              dashed lines. */}
          {roofFloorWindow != null && rfEstOn && (
            <Box sx={{ ...PERIOD_GRID_SX, mt: 0.5 }}>
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{
                  gridColumn: "1 / span 2",
                  fontSize: "0.7rem",
                  alignSelf: "center",
                }}
              >
                est method
              </Typography>
              {ROOF_FLOOR_ESTIMATE_METHODS.map((m, col) => (
                <Chip
                  key={m}
                  label={RF_ESTIMATE_METHOD_LABELS[m] ?? m}
                  size="small"
                  clickable
                  disabled={extrasLoading}
                  color={rfEstMethod === m ? "secondary" : "default"}
                  variant={rfEstMethod === m ? "filled" : "outlined"}
                  onClick={() => setRfEstMethod(m)}
                  sx={{ gridColumn: col + 3, ...PERIOD_CHIP_SX }}
                />
              ))}
            </Box>
          )}
          {roofFloorWindow != null && rfEstOn &&
            extras.rfEstimates != null &&
            extras.rfEstimates.length === firstPairRows.length &&
            firstPairRows.length > 0 &&
            (() => {
              const idx = anchorIdx ?? firstPairRows.length - 1;
              const row = extras.rfEstimates![idx];
              const roof =
                row?.[`roof_${rfEstMethod}_${roofFloorWindow}d`] ?? null;
              const floor =
                row?.[`floor_${rfEstMethod}_${roofFloorWindow}d`] ?? null;
              const date = firstPairRows[idx]?.date ?? "—";
              return (
                <Typography
                  variant="caption"
                  color="text.secondary"
                  sx={{ display: "block", mt: 0.5, fontSize: "0.65rem" }}
                >
                  est levels @ {date} · {roofFloorWindow}d ·{" "}
                  {RF_ESTIMATE_METHOD_LABELS[rfEstMethod] ?? rfEstMethod}:
                  {roof != null && floor != null ? (
                    <>
                      {" "}roof {fmtNum(roof)} · floor {fmtNum(floor)}{" "}
                      (two dashed lines — click a date to move them)
                    </>
                  ) : (
                    " no estimate for this date/method — try another method"
                  )}
                </Typography>
              );
            })()}
          {roofFloorWindow != null && rfEstOn &&
            (extras.rfEstimates == null ||
              extras.rfEstimates.length !== firstPairRows.length) && (
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ display: "block", mt: 0.5, fontSize: "0.65rem" }}
            >
              {extras.rfEstimates == null
                ? "est levels loading…"
                : "no estimate rows for this code — run python -m analysis.mov_ave_spread to build analysis.mov_ave_spreads_roof_floor_estimates"}
            </Typography>
          )}

          {/* ---- High/Low Streaks buttons (nested, single-select) ----
              Same grid and chip style as the other button rows: the row
              label spans the full width, then the first layer holds the
              band lookback periods (60/120/255/500/750/1275d) aligned
              with the OHLC row's matching columns. Clicking a period
              EXPANDS the second layer — band tightness pcts (1/5/10%)
              on the row beneath — and clicking the active period
              collapses it again. Selecting a pct fills the LATEST date's
              trailing-period window with its top/bottom pct% price zones
              (light purple above high_val, light yellow below low_val)
              and draws that combo's break streaks darker inside;
              clicking a chart date anchors the window to the trailing
              rows before that date. */}
          <Box sx={{ ...PERIOD_GRID_SX, mt: 1 }}>
            {/* Row label */}
            <SectionLabel id="highLowStreaks" active={streakPeriod != null} />
            {/* Layer 1 — periods, columns 3-8 (aligned with the OHLC row's
                60d/120d/255d/500d/750d/1275d buttons). */}
            {HIGH_LOW_STREAK_PERIODS.map((w, col) => (
              <Chip
                key={w}
                label={`${w}d`}
                size="small"
                clickable
                disabled={extrasLoading}
                color={streakPeriod === w ? "primary" : "default"}
                variant={streakPeriod === w ? "filled" : "outlined"}
                onClick={() => handleStreakPeriodClick(w)}
                sx={{ gridColumn: col + 3, ...PERIOD_CHIP_SX }}
              />
            ))}
            {/* Layer 2 — pcts, expanded beneath the periods when one is
                active (columns 3-5, under the first three periods). */}
            {streakPeriod != null &&
              HIGH_LOW_STREAK_PCTS.map((p, col) => (
                <Chip
                  key={p}
                  label={`${p}%`}
                  size="small"
                  clickable
                  disabled={extrasLoading}
                  color={streakPct === p ? "primary" : "default"}
                  variant={streakPct === p ? "filled" : "outlined"}
                  onClick={() => toggleStreakPct(p)}
                  sx={{ gridColumn: col + 3, ...PERIOD_CHIP_SX }}
                />
              ))}
          </Box>
          {streakPeriod != null && streakPct == null && (
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ display: "block", mt: 0.5, fontSize: "0.65rem" }}
            >
              pick a band tightness (pct) to shade break streaks
            </Typography>
          )}
          {streakPeriod != null && streakPct != null && (
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ display: "block", mt: 0.5, fontSize: "0.65rem" }}
            >
              light purple/yellow = trailing {streakPeriod}d top/bottom{" "}
              {streakPct}% zones
              {streakWin ? ` (${streakWin.startDate} → ${streakWin.endDate})` : ""},
              darker = break streaks (each shaded over its own span ·
              ≤5-day in-band gaps bridged) ·{" "}
              {longStreaks
                ? (longStreaks.high.length > 0 || longStreaks.low.length > 0
                    ? [
                        longStreaks.high.length > 0
                          ? `high ${longStreaks.high.length} streak${longStreaks.high.length === 1 ? "" : "s"} · ${longStreaks.high.reduce((a, s) => a + s.days, 0)}d · peak ${fmtNum(longStreaks.high.reduce((a, s) => Math.max(a, s.extreme), -Infinity))} · ${longStreaks.high.slice(0, 2).map((s) => `${s.startDate.slice(2)}→${s.endDate.slice(2)}`).join(", ")}${longStreaks.high.length > 2 ? `, +${longStreaks.high.length - 2} more` : ""}`
                          : "high none",
                        longStreaks.low.length > 0
                          ? `low ${longStreaks.low.length} streak${longStreaks.low.length === 1 ? "" : "s"} · ${longStreaks.low.reduce((a, s) => a + s.days, 0)}d · trough ${fmtNum(longStreaks.low.reduce((a, s) => Math.min(a, s.extreme), Infinity))} · ${longStreaks.low.slice(0, 2).map((s) => `${s.startDate.slice(2)}→${s.endDate.slice(2)}`).join(", ")}${longStreaks.low.length > 2 ? `, +${longStreaks.low.length - 2} more` : ""}`
                          : "low none",
                      ].join(" · ")
                    : "no streaks in this window")
                : "no window"}
              {streakWin
                ? anchorIdx != null
                  ? ` · anchored to ${streakWin.endDate} (click again to clear)`
                  : " · click a chart date to anchor the window"
                : ""}
            </Typography>
          )}
          {extras.highLowStreaks != null && !hasStreakData && (
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ display: "block", mt: 0.5, fontSize: "0.65rem" }}
            >
              no streak data for this code — run{" "}
              <code>python -m analysis.mov_ave_spread</code> to build
              analysis.mov_ave_high_low_pct_streaks
            </Typography>
          )}

          {/* ---- Px-Vol States buttons (nested, single-select per row) ----
              The trading amt × price-change state family — shades come
              from the analysis.mov_ave_price_vs_amt registry (the
              analysis_forecasts.px_vol_state engine's date-level
              source of truth): layer 1 = the day's price SPEED
              (sharp/slow/flat rise/drop — t = ret_1d / the code's own
              trailing 255-row σ_ret), layer 2 = the day's
              trading-amount LEVEL STATE (up/flat/down vs the code's
              own trailing-year amount distribution — z-scored log
              amount). Picking one of each shades the chart over
              the dates satisfying BOTH legs; the shade hue follows the
              price direction (green rise / red drop / gray flat) and
              its DEPTH follows the combo's strength — sharp×heavy
              (e.g. rising price + high amount = strong growth)
              shades darkest, weaker speeds / amount states lighten
              toward pastel. */}
          <Box sx={{ ...PERIOD_GRID_SX, mt: 1 }}>
            {/* Row label */}
            <SectionLabel
              id="pxVolStates"
              active={pxVolSpeed != null || pxVolVol != null}
            />
            {/* Layer 1 — price speed (columns 1-5). */}
            {PX_VOL_SPEED_OPTIONS.map((o, col) => (
              <Chip
                key={o.key}
                label={o.label}
                size="small"
                clickable
                disabled={extrasLoading}
                color={pxVolSpeed === o.key ? "primary" : "default"}
                variant={pxVolSpeed === o.key ? "filled" : "outlined"}
                onClick={() => handlePxVolSpeedClick(o.key)}
                sx={{ gridColumn: col + 1, ...PERIOD_CHIP_SX }}
              />
            ))}
            {/* Layer 2 — trading-amount state (columns 1-3), always visible:
                the two rows are independent picks of ONE state family, not
                a progressive drill-down like High/Low Streaks. */}
            {PX_VOL_VOL_OPTIONS.map((o, col) => (
              <Chip
                key={o.key}
                label={o.label}
                size="small"
                clickable
                disabled={extrasLoading}
                color={pxVolVol === o.key ? "primary" : "default"}
                variant={pxVolVol === o.key ? "filled" : "outlined"}
                onClick={() => handlePxVolVolClick(o.key)}
                sx={{ gridColumn: col + 1, ...PERIOD_CHIP_SX }}
              />
            ))}
          </Box>
          {/* ---- Top-10% OHLC Days buttons (independent toggles) ----
              The gaps store's wick top-decile flags
              (analysis.mov_ave_spreads_detail_ohlc_gaps): spike fade =
              the day's upper wick >= its trailing-252d 90th percentile
              (mark at the day's HIGH), flush recover = the lower wick
              likewise (mark at the day's LOW). The group's data lands
              on the first toggle pick (the /extras "gaps" metric) and
              is then filtered client-side. */}
          <Box sx={{ ...PERIOD_GRID_SX, mt: 1 }}>
            <SectionLabel id="ohlcGaps" active={gapMarks.spike || gapMarks.flush} />
            <Chip
              label="spike fade"
              size="small"
              clickable
              disabled={extrasLoading}
              color={gapMarks.spike ? "primary" : "default"}
              variant={gapMarks.spike ? "filled" : "outlined"}
              onClick={() => handleGapMarkClick("spike")}
              sx={{ gridColumn: 3, ...PERIOD_CHIP_SX }}
            />
            <Chip
              label="flush recover"
              size="small"
              clickable
              disabled={extrasLoading}
              color={gapMarks.flush ? "primary" : "default"}
              variant={gapMarks.flush ? "filled" : "outlined"}
              onClick={() => handleGapMarkClick("flush")}
              sx={{ gridColumn: 4, ...PERIOD_CHIP_SX }}
            />
          </Box>
          {(gapMarks.spike || gapMarks.flush) && (
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ display: "block", mt: 0.5, fontSize: "0.65rem" }}
            >
              {[
                gapMarks.spike
                  ? `spike fade shaded ×${ohlcGapCounts.spike} (light red)`
                  : null,
                gapMarks.flush
                  ? `flush recover shaded ×${ohlcGapCounts.flush} (light green)`
                  : null,
              ]
                .filter(Boolean)
                .join(" · ")}{" "}
              — wick in the trailing-252d top decile (≥120 obs); tooltip
              shows the day's gap legs
            </Typography>
          )}

          {/* ---- Market Regimes buttons (multi-select) ---- The
              stats.market_regimes daily label's contiguous spans (the
              market_regime_spans table): calm = the market's quiet
              background stretches, hot = elevated volatility WITH volume
              expansion, panic = elevated volatility without it, quiet =
              volume expansion without price movement. Each picked regime
              shades its spans in its palette color (calm grey / hot
              purple / panic red / quiet blue — the same accents the
              forecast tables color their regime cells with). Multi-select
              — regimes can overlap in time (a hot span followed by a
              quiet span). */}
          <Box sx={{ ...PERIOD_GRID_SX, mt: 1 }}>
            <SectionLabel
              id="marketRegimes"
              active={regimePicks.size > 0}
              suffix={regimesLoading ? " — loading…" : ""}
            />
            {ALL_REGIMES.map((r, col) => (
              <Chip
                key={r}
                label={REGIME_LABELS[r]}
                size="small"
                clickable
                disabled={extrasLoading || regimesLoading}
                color={regimePicks.has(r) ? "primary" : "default"}
                variant={regimePicks.has(r) ? "filled" : "outlined"}
                onClick={() => toggleRegime(r)}
                sx={{
                  gridColumn: col + 1,
                  ...PERIOD_CHIP_SX,
                  ...(regimePicks.has(r)
                    ? {
                        color: REGIME_ACCENT_COLORS[r],
                        borderColor: REGIME_ACCENT_COLORS[r],
                      }
                    : {}),
                }}
              />
            ))}
          </Box>
          {regimePicks.size > 0 && (
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ display: "block", mt: 0.5, fontSize: "0.65rem" }}
            >
              {regimeSpans == null
                ? "fetching the code's regime spans…"
                : ALL_REGIMES.filter((r) => regimePicks.has(r))
                    .map((r) => {
                      const spans = regimeSpans[r] ?? [];
                      const days = spans.reduce((a, sp) => a + sp.spanDays, 0);
                      return `${REGIME_LABELS[r]}: ${
                        spans.length === 0
                          ? "no spans"
                          : `${spans.length} span${spans.length === 1 ? "" : "s"} · ${days}d · last ${spans[spans.length - 1].endDate}`
                      }`;
                    })
                    .join(" · ") + " · source: stats.market_regimes (vol_z / amt_z vs own trailing 255d, bars ±1.0σ)"}
            </Typography>
          )}

          {/* ---- Trend buttons (the signals tier's trend phases,
              multi-select) ---- The four phases of analysis.trend_days
              (fetched via the trend-spans API — the RSI re-entry
              classification shared with the rule cells; REPLACES the
              retired client-side close-vs-MA60 classifier 2026-10-15):
              top / bottom = the MA255-gap extremes, rise / fall = the
              RSI(14) momentum episodes. Each picked phase shades its
              own date spans in its light fill — drawn on every selected
              pair (date-based rects). A pick takes the panel's single
              shade slot: the other shade rows' picks clear (RSI pct,
              High/Low Streaks, Px-Vol, Top-10% OHLC Days, Market
              Regimes), and picking any of those clears the trend
              picks. */}
          <Box sx={{ ...PERIOD_GRID_SX, mt: 1 }}>
            <SectionLabel id="trendBands" active={trendPicks.size > 0} />
            {TREND_TYPES.map((t, col) => (
              <Chip
                key={t}
                label={TREND_TYPE_LABELS[t]}
                size="small"
                clickable
                color={trendPicks.has(t) ? "primary" : "default"}
                variant={trendPicks.has(t) ? "filled" : "outlined"}
                onClick={() => toggleTrendPick(t)}
                sx={{
                  gridColumn: col + 1,
                  ...PERIOD_CHIP_SX,
                  ...(trendPicks.has(t)
                    ? {
                        color: TREND_ACCENTS[t],
                        borderColor: TREND_ACCENTS[t],
                      }
                    : {}),
                }}
              />
            ))}
          </Box>
          {trendPicks.size > 0 && (
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ display: "block", mt: 0.5, fontSize: "0.65rem" }}
            >
              signals-tier trend spans shaded (analysis.trend_days) — top /
              bottom: MA255-gap extremes · rise / fall: RSI(14) re-entry
              momentum episodes (RSI re-cross of 50 after a deep ≤30/≥70
              anchor flips the trend within 40d) · unknown = background ·
              dates from the rules tier, drawn on every pair
            </Typography>
          )}
          {(pxVolSpeed != null || pxVolVol != null) && (
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ display: "block", mt: 0.5, fontSize: "0.65rem" }}
            >
              {pxVolSpeed == null || pxVolVol == null
                ? "pick both a price speed and an amount state to shade matching dates"
                : `${PX_VOL_SPEED_OPTIONS.find((o) => o.key === pxVolSpeed)?.label} × ${
                    PX_VOL_VOL_OPTIONS.find((o) => o.key === pxVolVol)?.label
                  } — ${pxVolReading(pxVolSpeed, pxVolVol)} · ${
                    pxVolRuns.length === 0
                      ? "no matching dates"
                      : `${pxVolRuns.reduce((a, r) => a + r.days, 0)} matching ${
                          pxVolRuns.reduce((a, r) => a + r.days, 0) === 1 ? "day" : "days"
                        } in ${pxVolRuns.length} run${pxVolRuns.length === 1 ? "" : "s"}` +
                        ` · longest ${Math.max(...pxVolRuns.map((r) => r.days))}d` +
                        ` · last ${pxVolRuns[pxVolRuns.length - 1].endDate}`
                  } · thresholds t ±2.0σ (sharp) / ±1.26σ (slow) · z_amt(log-level) +2.0 / −0.92 · source: ${
                    extras.priceVsAmt != null
                      ? "analysis.mov_ave_price_vs_amt"
                      : "client-side replication (registry rows not loaded)"
                  }`}
            </Typography>
          )}
          {metricError != null && (
            <Typography
              variant="caption"
              color="error"
              sx={{ display: "block", mt: 0.5, fontSize: "0.65rem" }}
            >
              metric load failed: {metricError} — click the section's button
              again to retry
            </Typography>
          )}
        </Box>
      )}
      </BaseChart>

      {/* Latest-snapshot summary line for the selected pair. */}
      {!loading && !error && latestSummary && (
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ display: "block", mt: 0.5, fontSize: "0.7rem" }}
        >
          {selectedPair?.pair_label} @ {latestSummary.date} · short {fmtNum(latestSummary.short_value)} ·
          long {fmtNum(latestSummary.long_value)} · gap{" "}
          <Box
            component="span"
            sx={{
              color: latestSummary.gap_value == null ? "text.disabled" : UP_COLOR,
              fontWeight: 600,
            }}
          >
            {latestSummary.gap_value == null
              ? "—"
              : fmtPct(latestSummary.gap_value * 100, 2)}
          </Box>
        </Typography>
      )}
    </ChartCard>
  );
}
