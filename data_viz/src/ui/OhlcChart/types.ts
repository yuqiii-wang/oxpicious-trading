/**
 * types — shared types + stable constants for the OhlcChart
 * component split (OhlcChart/). The React shell (OhlcChart.tsx),
 * the pure option builder (chartOption.ts) and the trigger-overlay
 * builder (triggerOverlay.ts) all consume this module.
 */
import type { MutableRefObject } from "react";
import type { ECharts } from "echarts";
import type { CodeTrendStreakInfo } from "@/shared/charts/CodeTrendTooltip";
import type { OhlcMode } from "@/lib/ohlc";
import type {
  MarketRegimeSpans,
  StockDailyRow,
  StockDividend,
} from "@shared/types";

/** One historical trade signal to mark on the chart (from
 *  live.live_signals). Buys draw a green up-triangle below the day's low,
 *  sells a red down-triangle above the day's high; several signals of the
 *  same action on one day share a single marker (the axis tooltip lists
 *  every signal of the day). */
export interface OhlcTradeSignal {
  date: string;
  /** "buy" | "sell" (anything else draws nothing). */
  action: string;
  signal_type: string;
  signal_sub_type: string;
  /** The signal's expected-move confidence as a % of price (the live
   *  basis-point confidence / 100) — tooltip text only. */
  confidence: number;
}

/** The clicked forecast bucket's registered signal action (the Recent
 *  Movements forecast table's signal chip — the signals-layer gate's
 *  buy/sell + the chosen rung's expected blended move). Labels the
 *  highlighted trigger days with the trade-signal triangle markers — a
 *  green up-triangle below the day's low for buy, a red down-triangle
 *  above the day's high for sell, the exact `tradeSignals` style — and
 *  reports the action in the axis tooltip. */
export interface OhlcForecastAction {
  /** "buy" | "sell" (anything else labels nothing). */
  action: string;
  /** The strategy's confidence as a fraction (the chosen rung's
   *  sign-aligned blended mean) — tooltip text only. */
  confidence: number | null;
}

/** Stable id of the forecast trigger-day overlay series — the base
 * option always carries an empty placeholder under this id, and the
 * overlay effect merge-updates its data / markArea in place. */
export const TRIGGER_SERIES_ID = "trigger-days";

/** Stable id of the forecast-action triangle overlay series (the
 * highlightAction labels) — same imperative placeholder pattern. */
export const TRIGGER_ACTION_SERIES_ID = "trigger-actions";

/** One forecast-action triangle overlay point — per-item symbol
 *  overrides on the action scatter host reproducing the tradeSignals
 *  markPoint look (triangle at the bar, up below for buy / down above
 *  for sell). */
export interface TriggerActionPoint {
  value: [string, number];
  symbol: "triangle";
  symbolRotate: 0 | 180;
  symbolSize: [number, number];
  symbolOffset: [number, number];
  itemStyle: { color: string };
}

/** Fixed y position of the signal-day rail circles on the hidden [0, 1]
 * trigger axis (0.93 ≈ near the chart top). */
export const TRIGGER_RAIL_Y = 0.93;

/** One forecast signal streak period (Recent Movements row-click):
 *  the [start, end] qualifying run behind a merged signal's mid day,
 *  plus its trading-day count (forecast_results streak_days — null for
 *  state-family / pre-migration rows). */
export interface HighlightSpan {
  start: string;
  end: string;
  days?: number | null;
}

/** The trigger overlay's anchoring geometry — the chart dates (with
 *  gap-break inserts) plus the rebased low / high / close arrays the
 *  action triangles anchor at (below the day's low / above its high).
 *  Stashed by the option builder on every base rebuild, read by the
 *  overlay builder. */
export interface TriggerAnchor {
  dates: string[];
  low: Array<number | null>;
  high: Array<number | null>;
  close: Array<number | null>;
}

/** Ref through which the option builder stashes the current TriggerAnchor
 *  for the overlay builder (assigned inside the option memo — an
 *  idempotent side effect; see chartOption.ts). */
export type TriggerAnchorRef = MutableRefObject<TriggerAnchor | null>;

/** Ref carrying the date → streak context of the trigger overlay's DARK
 *  spans, read by the axis tooltip formatter at hover time. */
export type StreakInfoByDateRef = MutableRefObject<
  Map<string, CodeTrendStreakInfo>
>;

/** Ref carrying the date → forecast-action labels (the clicked bucket's
 *  registered buy/sell), read by the axis tooltip formatter the same way
 *  as the streak context above. */
export type ForecastActionByDateRef = MutableRefObject<
  Map<string, OhlcForecastAction>
>;

/** Stable EMPTY prop defaults. Default-parameter `[]` literals create a
 * NEW array identity on every render, which dirties the option memo
 * (tradeSignals is a dep) on EVERY parent re-render — forcing a full
 * ~1 s notMerge rebuild of the 1700-candle chart per state change
 * (spinner flips, chip mounts, row selection; measured 2026-09). */
export const NO_DIVIDENDS: StockDividend[] = [];
export const NO_TRADE_SIGNALS: OhlcTradeSignal[] = [];
export const NO_HIGHLIGHT_DATES: string[] = [];
export const NO_HIGHLIGHT_SPANS: HighlightSpan[] = [];

export interface OhlcChartProps {
  /** Daily OHLC + PE rows for one stock (already windowed by the caller). */
  rows: StockDailyRow[];
  /** OHLC display mode — "percentage" rebases OHLC + MAs to % change from the
   *  first valid close; "absolute" shows raw prices. */
  ohlcMode: OhlcMode;
  /** Chart height in px. Defaults to 250 (matches StockPanel). */
  height?: number;
  /** Dividend events for this stock (all dates — not windowed). When
   *  provided, gold diamond markers are drawn on ex-dividend dates that fall
   *  inside the visible rows. Defaults to [] (no markers). */
  dividends?: StockDividend[];
  /** Initial dataZoom start % (0–100). When provided, an in-chart dataZoom
   *  (inside + slider) is rendered and `rows` should be the FULL history (the
   *  dataZoom owns windowing). When omitted, no dataZoom is rendered and the
   *  caller owns windowing (e.g. StockOhlcExpansionChart). */
  dataZoomStart?: number;
  /** Initial dataZoom end % (0–100). Defaults to 100 when dataZoomStart is set. */
  dataZoomEnd?: number;
  /** Optional callback fired when the user clicks any date on the chart. */
  onDateClick?: (date: string) => void;
  /** Market-regime SPANS to shade per regime (full-height bands in the
   *  regime's color — hot purple / panic red / quiet blue), as the
   *  endpoint's per-regime span map (Partial<MarketRegimeSpans>; calm is
   *  dropped by the shared regimeBands helper). One chart series per
   *  regime. Empty/omitted = no shading. */
  regimeSpans?: Partial<MarketRegimeSpans>;
  /** Historical trade signals to mark: green up-triangle (buy) below the
   *  low / red down-triangle (sell) above the high, and a per-day signal
   *  block in the axis tooltip. Same (date, action) pairs share a marker. */
  tradeSignals?: OhlcTradeSignal[];
  /** Forecast trigger DAYS to mark — small purple circles above each
   *  date's high (fallback close): the analysis_forecasts.forecast_results.
   *  trigger_dates of a clicked forecast row — under the 2026-09
   *  streak-merge, the merged signals' MID days. Dates outside the
   *  visible rows are skipped. Default [] (no markers). */
  highlightDates?: string[];
  /** Forecast signal STREAK periods to shade (relatively dark purple
   *  full-height bands): the [start, end] qualifying-run spans behind
   *  the highlighted mid dates (forecast_results streak_starts /
   *  streak_ends). Overlapping spans are union-merged; dates outside
   *  the visible rows are clipped. Hovering a shaded day reports the
   *  run in the axis tooltip — its trading-day count (`days`,
   *  forecast_results streak_days) and the span. Default [] (no
   *  shading). */
  highlightSpans?: HighlightSpan[];
  /** Forward forecast window (trading rows) shaded from each highlighted
   *  SIGNAL day — the period the clicked row's forward change was
   *  measured over (+1/+5/+20/+60; the clicked horizon). Each day
   *  shades [day, day + n]; overlapping windows are union-merged so the
   *  light purple shade stays uniform (dense buckets would otherwise
   *  stack the translucent fill darker). Default 1. */
  highlightHorizonDays?: number;
  /** The clicked forecast bucket's registered signal action — when set,
   *  every highlighted trigger day ALSO draws a trade-signal triangle
   *  (OhlcForecastAction: green up-triangle below the day's low for buy,
   *  red down-triangle above the day's high for sell — the tradeSignals
   *  style) and the axis tooltip reports the action. Null/undefined =
   *  no labels (the bucket never registered). Rides the incremental
   *  trigger overlay — no base-chart rebuild on a row click. */
  highlightAction?: OhlcForecastAction | null;
  /** Fired after the trigger overlay's setOption has been applied (or
   *  determined to be a no-op) — the "rendering done" gate the Recent
   *  Movements page uses to unfreeze the forecast table after a row
   *  click. Stable identity (useCallback) keeps the overlay effect from
   *  re-running. */
  onHighlightSettled?: () => void;
  /** Fired whenever the chart's VISIBLE date window changes — dataZoom
   *  slider/inside drags, and every rows rebuild (which re-anchors the
   *  window). Carries { start, end } date strings of the visible range
   *  (null when there are no rows). A sibling chart (the AI page's
   *  date-event strip) windows itself to the same slider with this.
   *  Only meaningful when dataZoomStart is set. Stable identity
   *  (useCallback) avoids needless re-binds. */
  onVisibleRangeChange?: (range: { start: string; end: string } | null) => void;
  /** Center the dataZoom window on this date (keeping the current window
   *  span) — the AI page's date-event strip click jumps the trend's slider
   *  to the event day. Pass a fresh { date, seq } per request (the same
   *  seq is never re-applied); non-trading dates snap to the next trading
   *  day. Only meaningful when dataZoomStart is set. */
  focusDateRequest?: { date: string; seq: number } | null;
  /** Passthrough fired with the live instance once the chart is ready —
   *  lets an embedding card capture it (e.g. AI Ask screenshots) without
   *  touching the option / zoom logic below. */
  onChartReady?: (instance: ECharts) => void;
}
