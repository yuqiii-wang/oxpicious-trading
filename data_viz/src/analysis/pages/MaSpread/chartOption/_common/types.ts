/**
 * Shared types for the MaSpread chart-option builders — the top-level
 * orchestrators and the per-button-group subdirectories all import from
 * here (groups never import another group's internals).
 */
import { axisColors } from "@/theme/chart-palette";
import type { MovAveSpreadPairSeries } from "@shared/types";
import type { OhlcMode } from "@/lib/ohlc";
import type { TrendBand } from "../trendBands";

/** The axis color tokens axisColors(mode) resolves for the theme. */
export type AxisColors = ReturnType<typeof axisColors>;

/** The mutable series array the option builders push into. */
export type SeriesList = Extract<EChartsOptionSeries, unknown[]>;
type EChartsOptionSeries = import("echarts").EChartsOption["series"];

/** Trading amount display mode — "off" hides the bars (the header
 *  overlay switch's non-amt positions), "lowkey" shows them. */
export type TradingAmtMode = "off" | "lowkey";

/** One full-height markArea band overlay: precomputed rects + a legend
 *  label + the accent color (the Px-Vol combo, the market-regime spans,
 *  and the RSI Top-% ranks share the shape). `opacity` (0-1) optionally
 *  translucent-izes the rect fill per datum — the palette-token accents
 *  that cannot spell their own rgba use it (e.g. the RSI rank shades). */
export interface ShadeOverlay {
  label: string;
  data: Array<
    [
      { xAxis: string; itemStyle: { color: string; opacity?: number } },
      { xAxis: string },
    ]
  >;
  accent: string;
}

/** The Slopes/Curvatures header-toggle overlay: per-date derivative
 *  lines drawn on the pair chart's right axis (Price + MA5/20/60, or
 *  Price + EMA6/20/60 while an EMA pair is selected). Built by the panel
 *  from the loaded pair rows; consumed by buildPairOption (series + axis)
 *  and the tooltip (one compact readout row). */
export interface DerivTrendSpec {
  /** Which derivative the lines carry. */
  mode: "slope" | "curvature";
  /** Drawn lines — name matches the legend entry. */
  lines: Array<{ name: string; data: Array<number | null>; color: string }>;
}

/** Context shared by the pair chart's tooltip sections (built by
 *  buildPairOption; each button group's tooltipRows reads its own
 *  fields). */
export interface TooltipContext {
  dates: string[];
  shorts: Array<number | null>;
  longs: Array<number | null>;
  /** Price slope (the only persisted slope) — non-null only on Price/*
   *  pairs' rows. */
  shortSlopes: Array<number | null>;
  /** Price curvature — non-null only on Price/* pairs' rows. */
  shortCurvs: Array<number | null>;
  longStds: Array<number | null>;
  opens: Array<number | null>;
  highs: Array<number | null>;
  lows: Array<number | null>;
  tradingAmts: Array<number | null>;
  rsi3: Array<number | null>;
  rsi6: Array<number | null>;
  rsi10: Array<number | null>;
  rsi14: Array<number | null>;
  rsi20: Array<number | null>;
  rsi60: Array<number | null>;
  amtMaSlopeOfLong: Array<number | null>;
  amtMarketShareOfLong: Array<number | null>;
  ohlcOpens: Array<number | null>;
  ohlcHighs: Array<number | null>;
  ohlcLows: Array<number | null>;
  /** Enabled rolling-OHLC window (trading days) — null when the OHLC-window
   *  overlay is off. */
  roofFloorWindow: number | null;
  /** Per-date OHLC extrema arrays for the enabled window (from the top-level
   *  chartData.ohlc rows) — null when the overlay is off. */
  roofFloorWinArrays: {
    open: Array<number | null>;
    high: Array<number | null>;
    highDate: Array<string | null>;
    high2nd: Array<number | null>;
    high2ndDate: Array<string | null>;
    highSlope: Array<number | null>;
    low: Array<number | null>;
    lowDate: Array<string | null>;
    low2nd: Array<number | null>;
    low2ndDate: Array<string | null>;
    lowSlope: Array<number | null>;
    /** close minus the LAST high anchor's value (the later-dated of the
     *  window's two high anchors — the 2nd whenever present, else the
     *  top); sign = direction since it. */
    changeSinceLastHigh: Array<number | null>;
    /** Trading days between `date` and that last high anchor date. */
    daysSinceLastHigh: Array<number | null>;
    /** close minus the LAST low anchor's value (the mirror on the low
     *  side); sign = direction since it. */
    changeSinceLastLow: Array<number | null>;
    /** Trading days between `date` and that last low anchor date. */
    daysSinceLastLow: Array<number | null>;
  } | null;
  /** Top-10% OHLC Days: the gaps store's per-date anatomy, index-aligned
   *  with dates — null when the group is off or not loaded. */
  ohlcGapInfo: {
    gap: Array<number | null>;
    intra: Array<number | null>;
    uw: Array<number | null>;
    lw: Array<number | null>;
    pattern: Array<string | null>;
  } | null;
  trendBands: TrendBand[];
  hasTrendBands: boolean;
  hasBase: boolean;
  baseVal: number | null;
  showBoll: boolean;
  upperData: Array<number | null>;
  lowerData: Array<number | null>;
  bollBase: Array<number | null>;
  pair: MovAveSpreadPairSeries;
  isPricePair: boolean;
  sName: string;
  lName: string;
  tradingAmtMode: "off" | "lowkey";
  ohlcMode: OhlcMode;
  /** Slopes/Curvatures overlay lines — null when that header toggle is
   *  off (see DerivTrendSpec). */
  derivTrend: DerivTrendSpec | null;
}
