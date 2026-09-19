/**
 * StockOhlcChart — shared daily OHLC chart for a single stock.
 *
 * Single source of truth for the stock daily OHLC plot. Used by:
 *   • StockPanel (Stock Baseline page) — wrapped in a ChartCard with a
 *     date-range slider and return badges.
 *   • StockOhlcExpansionChart (composition pie expansion) — wrapped in a Card
 *     with a close button; data fetched on demand.
 *
 * Renders OHLC bars (shared `ohlcSeries`) + MA5/MA20/MA60/MA120 (computed
 * client-side from close — the stock baseline view does not carry precomputed
 * MA columns) + PE ratio on a twin axis (when available, with estimated PE
 * drawn as a faint series). Falls back to a close line when OHLC components
 * are sparse. OHLC + MAs are rebased to % change from the first valid close
 * in "percentage" mode (the default).
 *
 * Margin + liquidity overlays (mirror EtfMarginPanel):
 *   • RZ (融资 cash borrow) green fill UP from middle on a hidden twin axis.
 *   • RQ (融券 sec borrow) red fill DOWN from middle on the same hidden axis.
 *   • Trading-turnover bars (成交金额, 亿元) on a visible right axis, colored
 *     by price-up/down.
 *
 * Dividend events (利润分配/分红) from stats.stock_dividends are overlaid as
 * gold diamond markPoints on the ex-dividend date. The dividend amount is
 * shown in the axis tooltip when the user hovers the event day.
 */
import React, { useCallback, useEffect, useMemo, useRef, memo } from "react";
import EChart from "@/components/EChart";
import {
  makeCodeTrendTooltipFormatter,
  type CodeTrendStreakInfo,
} from "@/components/CodeTrendTooltip";
import { useStore } from "@/store/filters";
import { breakArraysAtGaps, fmtNum, safeMa } from "@/lib/series";
import {
  ohlcSeries,
  rebasePriceArrays,
  formatPriceValue,
  type OhlcMode,
} from "@/lib/ohlc";
import { computeMarginScores } from "@/lib/margin-score";
import {
  MA5_COLOR,
  MA20_COLOR,
  MA60_COLOR,
  MA120_COLOR,
  MUTED_PALETTE,
  PE_COLOR,
  DIVIDEND_COLOR,
  TRIGGER_DATE_COLOR,
  TRIGGER_DATE_FILL,
  TRIGGER_STREAK_FILL,
  UP_COLOR,
  DOWN_COLOR,
  axisColors,
  commonGrid,
  commonDataZoom,
} from "@/theme/chart-palette";
import { baseChartOption, commonTooltip } from "@/shared/charts/base-chart";
import type {
  MovAveSpreadHypeEpisode,
  StockBaselineRow,
  StockDividend,
} from "@shared/types";
import {
  HYPE_ACCENT_COLOR,
  hypeEpisodesToMarkArea,
} from "@/shared/charts/hypeBands";
import type { ECharts, EChartsOption } from "echarts";

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
  confidence: number;
}

/** Stable id of the forecast trigger-day overlay series — the base
 * option always carries an empty placeholder under this id, and the
 * overlay effect merge-updates its data / markArea in place. */
const TRIGGER_SERIES_ID = "trigger-days";

/** Fixed y position of the signal-day rail circles on the hidden [0, 1]
 * trigger axis (0.93 ≈ near the chart top). */
const TRIGGER_RAIL_Y = 0.93;

/** One forecast signal streak period (Recent Movements row-click):
 *  the [start, end] qualifying run behind a merged signal's mid day,
 *  plus its trading-day count (forecast_results streak_days — null for
 *  state-family / pre-migration rows). */
interface HighlightSpan {
  start: string;
  end: string;
  days?: number | null;
}

/** Stable EMPTY prop defaults. Default-parameter `[]` literals create a
 * NEW array identity on every render, which dirties the option memo
 * (tradeSignals is a dep) on EVERY parent re-render — forcing a full
 * ~1 s notMerge rebuild of the 1700-candle chart per state change
 * (spinner flips, chip mounts, row selection; measured 2026-09). */
const NO_DIVIDENDS: StockDividend[] = [];
const NO_TRADE_SIGNALS: OhlcTradeSignal[] = [];
const NO_HIGHLIGHT_DATES: string[] = [];
const NO_HIGHLIGHT_SPANS: HighlightSpan[] = [];

interface Props {
  /** Daily OHLC + PE rows for one stock (already windowed by the caller). */
  rows: StockBaselineRow[];
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
  /** Optional callback fired when the user clicks any date on the chart.
   *  Used by the PE & Dividend analysis page to highlight the matching
   *  month-end row in the stats table. */
  onDateClick?: (date: string) => void;
  /** Market-hype EPISODES to shade (light purple full-height bands), as a
   *  sorted DISJOINT span list — callers merge all check-in windows with
   *  mergeHypeEpisodesAllWindows (shared hypeBands helper) so overlapping
   *  windows' shades don't stack. Empty/omitted = no shading. */
  hypeEpisodes?: MovAveSpreadHypeEpisode[];
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

function StockOhlcChart({ rows, ohlcMode, height = 250, dividends = NO_DIVIDENDS, dataZoomStart, dataZoomEnd, onDateClick, hypeEpisodes, tradeSignals = NO_TRADE_SIGNALS, highlightDates = NO_HIGHLIGHT_DATES, highlightSpans = NO_HIGHLIGHT_SPANS, highlightHorizonDays = 1, onHighlightSettled, onVisibleRangeChange, focusDateRequest, onChartReady }: Props) {
  const themeMode = useStore((s) => s.themeMode);

  // Chart x-axis dates (with gap-break inserts) — used by the onCanvasClick
  // handler to map a click index back to a date string. Mirrors the broken
  // dates computed inside the option useMemo.
  const chartDates = useMemo(
    () => breakArraysAtGaps(rows.map((r) => r.date), [rows.map(() => null)]).dates,
    [rows],
  );

  // Anchor stash for the trigger overlay effect (declared before the
  // option build that assigns it) — the chart dates (with gap-break
  // inserts), refreshed on every base rebuild.
  const triggerAnchorRef = useRef<{ dates: string[] } | null>(null);

  // Date → streak context of the trigger overlay's DARK spans, read by
  // the axis tooltip formatter at hover time (declared here so the
  // option closure can reference it; the overlay memo below refreshes
  // it on every highlight change — no option rebuild on a row click).
  const streakInfoByDateRef = useRef<Map<string, CodeTrendStreakInfo>>(new Map());

  const option = useMemo<EChartsOption>(() => {
    const c = axisColors(themeMode);
    const enableDataZoom = dataZoomStart !== undefined;
    const dates = rows.map((r) => r.date);
    const open = rows.map((r) => r.open);
    const high = rows.map((r) => r.high);
    const low = rows.map((r) => r.low);
    const close = rows.map((r) => r.close);
    const pe = rows.map((r) => r.pe);
    const isPeEstimatedNum = rows.map((r) => (r.is_pe_estimated ? 1 : 0));
    // Liquidity + margin data (from stock_liquidity_margin via v_stock_baseline)
    const tradingAmount = rows.map((r) => r.trading_amount);
    // Compute MA client-side — the stock baseline view does not carry
    // precomputed MA columns (only OHLC + pct_change + PE).
    const ma5 = safeMa(close, 5);
    const ma20 = safeMa(close, 20);
    const ma60 = safeMa(close, 60);
    const ma120 = safeMa(close, 120);

    // Detect whether OHLC is available — when most rows have all four
    // components, render an OHLC chart; otherwise fall back to a close line.
    const hasOhlc = (() => {
      if (rows.length === 0) return false;
      const ohlcCount = rows.filter(
        (r) => r.open != null && r.high != null && r.low != null && r.close != null,
      ).length;
      return ohlcCount > 0 && ohlcCount >= rows.length * 0.5;
    })();

    // PE is rendered only when at least one non-null, non-zero sample exists
    // (0 is treated as a placeholder, not a real PE).
    const hasPe = rows.some((r) => r.pe != null && r.pe !== 0);

    // Margin availability — any row with non-null, non-zero rz_balance.
    const hasMargin = rows.some(
      (r) => r.rz_balance != null && r.rz_balance !== 0,
    );

    // Rebase price-derived arrays (OHLC + MAs) to % change in percentage mode.
    // pe and isPeEstimatedNum are NOT price-derived — kept in absolute units.
    const { rebased } = rebasePriceArrays(
      { open, high, low, close, ma5, ma20, ma60, ma120 },
      ohlcMode,
    );

    const broken = breakArraysAtGaps(dates, [
      rebased.open, rebased.high, rebased.low, rebased.close,
      rebased.ma5, rebased.ma20, rebased.ma60, rebased.ma120,
      pe, isPeEstimatedNum,
      // Raw trading amount rides along so the Amount bars stay aligned
      // with the gap-inserted category axis — built from the raw rows it
      // would drift one slot left per long-holiday break and run out of
      // bars at the right edge (the last k slots empty, k = break count).
      tradingAmount,
    ]);
    const BROKEN_AMT_IDX = 10;

    // Data order: [open, close, low, high] (low before high — matches the
    // shared ohlcRenderItem destructuring `const [o, cl, l, h] = value`).
    const candleData: Array<Array<number | null>> = broken.dates.map((_, i) => [
      broken.arrays[0][i],
      broken.arrays[3][i],
      broken.arrays[2][i],
      broken.arrays[1][i],
    ]);

    // --- Margin scores (RZ up, RQ down) ---------------------------------
    const marginRows = rows.map((r) => ({
      date: r.date,
      rz_balance: r.rz_balance,
      rq_balance_amt: r.rq_balance_amt,
    }));
    const marginScores = computeMarginScores(marginRows);
    const rzScore = marginScores.map((m) => m.rz_score);
    const rqScore = marginScores.map((m) => m.rq_score);

    // Date → raw balance (yuan) lookups for tooltip display. The plotted
    // series values are shifted/clipped scores (not raw yuan), so the tooltip
    // must re-resolve the actual balance by date to show a meaningful figure.
    const rzByDate = new Map<string, number | null>(
      rows.map((r) => [r.date, r.rz_balance]),
    );
    const rqByDate = new Map<string, number | null>(
      rows.map((r) => [r.date, r.rq_balance_amt]),
    );
    // Date → EPS (yuan/share) lookup for tooltip display. EPS is a per-row
    // attribute (close / pe), not a plotted series, so the tooltip resolves it
    // by date — mirroring the rz/rq balance lookups above.
    const epsByDate = new Map<string, number | null>(
      rows.map((r) => [r.date, r.eps]),
    );

    // Dynamic axis limits for hidden margin axis — matches Python's
    // ax_rzrq.set_ylim(-max_of * 1.15, max_of * 1.15)
    const marginVals = [...rzScore, ...rqScore].filter(
      (v): v is number => v != null && Number.isFinite(v),
    );
    const maxAbs = marginVals.length > 0 ? Math.max(...marginVals.map(Math.abs)) : 0;
    const marginAxisRange = Math.max(1e-6, maxAbs) * 1.15;

    // --- Trading-turnover bars (亿元) -----------------------------------
    // trading_amount is stored in yuan — convert to 亿元 (/1e8) for display.
    // Bar color: green when close >= open (price-up), red otherwise.
    // Values come from the BROKEN amount array (NaN at gap markers) so the
    // bars line up 1:1 with broken.dates; the up/down color reads the
    // broken open/close on the same slot.
    const amtData = broken.arrays[BROKEN_AMT_IDX].map((v, i) => {
      const o = broken.arrays[0][i];
      const cl = broken.arrays[3][i];
      const up = o != null && Number.isFinite(o) && cl != null && Number.isFinite(cl) && cl >= o;
      return {
        value: v != null && Number.isFinite(v) ? v / 1e8 : null,
        itemStyle: { color: up ? UP_COLOR : DOWN_COLOR, opacity: 0.4 },
      };
    });

    // --- Dividend event markers ------------------------------------------
    // Build a date → rebased-close lookup so we can place each marker at the
    // close price of the ex-dividend day. Markers ride on the MA20 line series
    // (the custom OHLC renderItem cannot host markPoint). Only dividends whose
    // ex_dividend_date falls inside the visible window are drawn.
    const dateToCloseIdx = new Map<string, number>();
    broken.dates.forEach((d, i) => dateToCloseIdx.set(d, i));
    const markPointData: Array<{
      name: string;
      coord: [string, number];
      itemStyle: { color: string };
      symbol: string;
      symbolRotate?: number;
      symbolSize: number | [number, number];
      symbolOffset?: [number, number];
    }> = [];
    const dividendByDate = new Map<string, StockDividend>();
    for (const d of dividends) {
      const idx = dateToCloseIdx.get(d.ex_dividend_date);
      if (idx === undefined) continue; // ex-div date not in visible window
      const y = broken.arrays[3][idx]; // rebased close (arrays[3] = close)
      if (y == null || !Number.isFinite(y)) continue;
      markPointData.push({
        name: "Dividend",
        coord: [d.ex_dividend_date, y],
        itemStyle: { color: DIVIDEND_COLOR },
        symbol: "diamond",
        symbolSize: 11,
      });
      dividendByDate.set(d.ex_dividend_date, d);
    }

    // --- Trade-signal markers (buy below the low, sell above the high) ---
    // Same markPoint host as the dividends. Anchors use the rebased low /
    // high so the markers hug the bar in both OHLC modes, falling back to
    // the close when the row has no low/high. Same-(date, action) signals
    // share ONE marker — the axis tooltip lists each day's signals.
    const signalsByDate = new Map<string, OhlcTradeSignal[]>();
    if (tradeSignals.length > 0) {
      const byDateAction = new Map<string, OhlcTradeSignal>();
      for (const s of tradeSignals) {
        const buy = s.action === "buy";
        const sell = s.action === "sell";
        if (!buy && !sell) continue;
        const idx = dateToCloseIdx.get(s.date);
        if (idx === undefined) continue; // signal day not in visible window
        const anchor = (buy ? broken.arrays[2][idx] : broken.arrays[1][idx])
          ?? broken.arrays[3][idx]; // arrays[2]=low, [1]=high, [3]=close
        if (anchor == null || !Number.isFinite(anchor)) continue;
        const key = `${s.date}|${s.action}`;
        if (!byDateAction.has(key)) {
          byDateAction.set(key, s);
          markPointData.push({
            name: buy ? "Buy" : "Sell",
            coord: [s.date, anchor],
            itemStyle: { color: buy ? UP_COLOR : DOWN_COLOR },
            // Up-triangle below the bar for buys, down-triangle above for
            // sells (symbolOffset px: +y down, -y up).
            symbol: "triangle",
            symbolRotate: buy ? 0 : 180,
            symbolSize: [11, 10],
            symbolOffset: buy ? [0, 12] : [0, -12],
          });
        }
        const list = signalsByDate.get(s.date) ?? [];
        list.push(s);
        signalsByDate.set(s.date, list);
      }
    }
    const markPoint = markPointData.length
      ? { data: markPointData, label: { show: false } }
      : undefined;

    // --- Y axes ----------------------------------------------------------
    // Axis layout (index → role):
    //   0  left     price (% or yuan)
    //   1  hidden   margin scores (RZ ≥0 up, RQ ≤0 down) — only when hasMargin
    //   2  right    trading amount (亿元) — always present (0 when no data)
    //   3  right    PE (offset) — only when hasPe
    //
    // When margin is absent the hidden axis is omitted so indices shift down
    // by one. The series yAxisIndex values below account for this.
    const yAxis: EChartsOption["yAxis"] = [
      {
        type: "value",
        scale: true,
        name: ohlcMode === "percentage" ? "%" : "Price",
        nameTextStyle: { color: c.textColor, fontSize: 9 },
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: {
          color: c.textColor,
          fontSize: 9,
          formatter: (v: number) => formatPriceValue(v, ohlcMode),
        },
        splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.4 } },
      },
    ];
    // Hidden margin axis (index 1 when present)
    let marginAxisIdx = -1;
    if (hasMargin) {
      marginAxisIdx = (yAxis as Array<unknown>).length;
      (yAxis as Array<unknown>).push({
        type: "value",
        scale: true,
        show: false,
        min: -marginAxisRange,
        max: marginAxisRange,
      });
    }
    // Trading-amount axis (right, visible). Index = 1 when no margin, else 2.
    const amtAxisIdx = (yAxis as Array<unknown>).length;
    (yAxis as Array<unknown>).push({
      type: "value",
      scale: true,
      name: "Amt (亿)",
      nameTextStyle: { color: c.textColor, fontSize: 9 },
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: {
        color: c.textColor,
        fontSize: 9,
        formatter: (v: number) => fmtNum(v) + " 亿",
      },
      splitLine: { show: false },
    });
    // PE axis (right, offset). Index follows amount axis.
    let peAxisIdx = -1;
    if (hasPe) {
      peAxisIdx = (yAxis as Array<unknown>).length;
      (yAxis as Array<unknown>).push({
        type: "value",
        scale: true,
        name: "PE",
        nameTextStyle: { color: PE_COLOR, fontSize: 9 },
        axisLine: { lineStyle: { color: PE_COLOR } },
        axisLabel: { color: PE_COLOR, fontSize: 9, formatter: (v: number) => fmtNum(v) },
        splitLine: { show: false },
        offset: 40,
      });
    }
    // Trigger-overlay rail axis (hidden, FIXED [0, 1] extent) — the
    // signal-day circles ride this rail so overlay data changes can
    // NEVER dirty the price axis extent and re-render the 1700-candle
    // custom series (measured ~850 ms per update when they shared
    // yAxisIndex 0). Fixed min/max = the axis is extent-stable by
    // construction.
    const triggerAxisIdx = (yAxis as Array<unknown>).length;
    (yAxis as Array<unknown>).push({
      type: "value",
      min: 0,
      max: 1,
      show: false,
      splitLine: { show: false },
    });

    const series: EChartsOption["series"] = [
      ...(hasOhlc
        ? [ohlcSeries(candleData, { name: "OHLC", yAxisIndex: 0, z: 5 })]
        : [{
            type: "line" as const,
            name: "Close",
            yAxisIndex: 0,
            data: broken.arrays[3],
            smooth: false,
            symbol: "none",
            lineStyle: { color: MUTED_PALETTE[0], width: 1.3 },
            z: 5,
          }]),
      {
        type: "line",
        name: "MA5",
        yAxisIndex: 0,
        data: broken.arrays[4],
        smooth: false,
        symbol: "none",
        lineStyle: { color: MA5_COLOR, width: 0.8 },
        z: 4,
      },
      // MA20 carries the dividend + trade-signal markPoints — the custom
      // OHLC series cannot host markPoint, so a standard line series must
      // carry it. MA20 is a good visual anchor (always present, sits near
      // the price).
      {
        type: "line",
        name: "MA20",
        yAxisIndex: 0,
        data: broken.arrays[5],
        smooth: false,
        symbol: "none",
        lineStyle: { color: MA20_COLOR, width: 0.9 },
        z: 4,
        markPoint,
      },
      {
        type: "line",
        name: "MA60",
        yAxisIndex: 0,
        data: broken.arrays[6],
        smooth: false,
        symbol: "none",
        lineStyle: { color: MA60_COLOR, width: 0.8, type: "dashed" },
        z: 4,
      },
      {
        type: "line",
        name: "MA120",
        yAxisIndex: 0,
        data: broken.arrays[7],
        smooth: false,
        symbol: "none",
        lineStyle: { color: MA120_COLOR, width: 0.7, type: "dotted" },
        z: 4,
      },
    ];

    // --- Margin fills (RZ up green, RQ down red) ------------------------
    if (hasMargin && marginAxisIdx >= 0) {
      const brokenM = breakArraysAtGaps(dates, [rzScore, rqScore]);
      series.push({
        type: "line",
        name: "cash borrow balance",
        yAxisIndex: marginAxisIdx,
        data: brokenM.arrays[0],
        smooth: false,
        symbol: "none",
        lineStyle: { color: UP_COLOR, width: 0.6, opacity: 0.7 },
        areaStyle: { color: UP_COLOR, opacity: 0.36 },
        z: 3,
      });
      series.push({
        type: "line",
        name: "sec borrow balance",
        yAxisIndex: marginAxisIdx,
        data: brokenM.arrays[1],
        smooth: false,
        symbol: "none",
        lineStyle: { color: DOWN_COLOR, width: 0.6, opacity: 0.7 },
        areaStyle: { color: DOWN_COLOR, opacity: 0.36 },
        z: 3,
      });
    }

    // --- Trading-turnover bars (visible right axis) ---------------------
    series.push({
      type: "bar",
      name: "Amount",
      yAxisIndex: amtAxisIdx,
      data: amtData,
      barWidth: "90%",
      z: 1,
    });

    // --- Market-hype shading (light purple full-height bands) -----------
    // Empty-data series hosting the markArea (the same
    // rect-legend + markArea pattern as the MA-Spread charts) — sits
    // behind everything; the "Hyped" legend entry toggles the shading.
    if (hypeEpisodes && hypeEpisodes.length > 0) {
      series.push({
        type: "line",
        name: "Hyped",
        yAxisIndex: 0,
        data: [],
        markArea: {
          silent: true,
          data: hypeEpisodesToMarkArea(hypeEpisodes),
        },
        itemStyle: { color: HYPE_ACCENT_COLOR, opacity: 0.45 },
        z: 0,
      });
    }

    // --- Forecast trigger-day overlay host (clicked forecast row) --------
    // Placeholder ONLY — the actual signal-day rail points and the
    // forward-window markArea are applied imperatively by the
    // triggerOverlay effect below (merge setOption on this placeholder)
    // so a row click NEVER rebuilds the heavy base chart (1700+ custom
    // OHLC candles through a notMerge setOption cost ~850 ms per click
    // — measured 2026-09). The host rides the FIXED [0,1] trigger rail
    // axis, so overlay updates cannot dirty the price axis and re-render
    // the candles either. The always-present host keeps the series list
    // stable: the incremental merge is a cheap data swap on ONE series.
    series.push({
      type: "scatter",
      id: TRIGGER_SERIES_ID,
      name: "Trigger days",
      yAxisIndex: triggerAxisIdx,
      data: [],
      symbol: "circle",
      symbolSize: 5.5,
      itemStyle: { color: TRIGGER_DATE_COLOR },
      silent: true,
      z: 8,
    });
    // Date → chart-row lookup for the overlay effect (chart dates with
    // gap-break inserts). Assigning a ref inside the memo is an
    // idempotent side effect — the overlay memo below re-reads it
    // (keyed on this memo's output) so the two never diverge.
    triggerAnchorRef.current = {
      dates: broken.dates,
    };

    if (hasPe && peAxisIdx >= 0) {
      // Separate PE into actual (solid) and estimated (faint) series. Null or
      // 0 values are suppressed so missing/placeholder PE samples do not
      // render on the chart.
      const peActual = broken.arrays[8].map((val, i) =>
        broken.arrays[9][i] === 1 || val == null || val === 0 ? null : val
      );
      const peEstimated = broken.arrays[8].map((val, i) =>
        broken.arrays[9][i] === 1 && val != null && val !== 0 ? val : null
      );
      series.push({
        type: "line",
        name: "PE",
        yAxisIndex: peAxisIdx,
        data: peActual,
        smooth: false,
        symbol: "none",
        lineStyle: { color: PE_COLOR, width: 1.1, opacity: 0.85 },
        z: 6,
      });
      series.push({
        type: "line",
        name: "PE (est)",
        yAxisIndex: peAxisIdx,
        data: peEstimated,
        smooth: false,
        symbol: "none",
        connectNulls: false,
        lineStyle: { color: PE_COLOR, width: 1.1, opacity: 0.4 },
        z: 6,
      });
    }

    return baseChartOption(themeMode, {
      grid: commonGrid({ left: 50, right: hasPe ? 60 : 50, bottom: enableDataZoom ? 50 : 28 }),
      ...(enableDataZoom
        ? { dataZoom: commonDataZoom({}, dataZoomStart, dataZoomEnd ?? 100) }
        : {}),
      tooltip: commonTooltip(themeMode, {
        // Shared code-trend tooltip (CodeTrendTooltip.tsx): every stat row
        // carries its daily movement "(+/-change)" vs the previous tick.
        // broken.amount is passed in the plotted 亿 scale (raw yuan / 1e8).
        formatter: makeCodeTrendTooltipFormatter({
          ohlcMode,
          broken: {
            dates: broken.dates,
            open: broken.arrays[0],
            high: broken.arrays[1],
            low: broken.arrays[2],
            close: broken.arrays[3],
            ma5: broken.arrays[4],
            ma20: broken.arrays[5],
            ma60: broken.arrays[6],
            ma120: broken.arrays[7],
            pe: broken.arrays[8],
            amount: broken.arrays[BROKEN_AMT_IDX].map(
              (v) => (v != null && Number.isFinite(v) ? v / 1e8 : null),
            ),
          },
          rzByDate,
          rqByDate,
          epsByDate,
          dividendByDate,
          signalsByDate,
          streakInfoByDateRef,
        }),
      }),
      // legend: base default commonLegend(mode) == the previous explicit
      // commonLegend(themeMode, { type: "scroll" }).
      xAxis: {
        type: "category",
        data: broken.dates,
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: {
          color: c.textColor,
          fontSize: 8,
          formatter: (v: string) => v.slice(0, 7),
          interval: Math.max(1, Math.floor(broken.dates.length / 8)),
        },
        splitLine: { show: false },
      },
      yAxis,
      series,
    });
  }, [rows, themeMode, ohlcMode, dividends, dataZoomStart, dataZoomEnd, hypeEpisodes, tradeSignals]);

  // ---- Forecast trigger-day overlay (imperative incremental update) -----
  // The overlay payload for the placeholder series above: pinpoint
  // circles on the fixed signal rail (TRIGGER_RAIL_Y of the [0, 1]
  // trigger axis — near the chart top) at each merged signal's MID day,
  // the UNION-MERGED light signal+forward-window markArea spans
  // (highlightHorizonDays = the +1/+5/+20/+60 period the clicked row's
  // forward change was measured over; overlapping windows merge so the
  // light purple shade stays uniform no matter how dense the bucket),
  // and the UNION-MERGED RELATIVELY DARK streak-period spans
  // (highlightSpans — each signal's qualifying run [start, end] behind
  // the mid). Built LIGHT (no chart rebuild) from the date anchor
  // stashed by the base option build; keyed on `option` so it re-reads
  // a refreshed anchor after every base rebuild.
  const triggerOverlay = useMemo(() => {
    const anchor = triggerAnchorRef.current;
    const empty = { points: [] as Array<[string, number]>, markArea: undefined };
    if (!anchor || (highlightDates.length === 0 && highlightSpans.length === 0)) {
      streakInfoByDateRef.current = new Map();
      return empty;
    }
    const rowIdx = new Map(anchor.dates.map((d, i) => [d, i]));
    const lastRow = anchor.dates.length - 1;
    // Per-date streak context for the axis tooltip: every date inside a
    // qualifying-run span maps to that run's day count + full [start,
    // end] (the span dates, not the clipped ones). Runs of one bucket
    // are disjoint, so first span wins on the (impossible) overlap.
    const streakInfo = new Map<string, CodeTrendStreakInfo>();
    for (const s of highlightSpans) {
      let a = rowIdx.get(s.start);
      const b = rowIdx.get(s.end);
      if (b === undefined) continue;
      if (a === undefined) {
        // start predates the chart — clip to the first visible row
        a = 0;
      }
      for (let i = a; i <= b; i++) {
        const d = anchor.dates[i];
        if (d != null && !streakInfo.has(d)) {
          streakInfo.set(d, { days: s.days ?? null, start: s.start, end: s.end });
        }
      }
    }
    streakInfoByDateRef.current = streakInfo;
    const points: Array<[string, number]> = [];
    // Light spans: mid day through its forward forecast window.
    const fwd: Array<[number, number]> = [];
    for (const d of new Set(highlightDates)) {
      const i = rowIdx.get(d);
      if (i === undefined) continue; // signal day not on the chart
      points.push([d, TRIGGER_RAIL_Y]);
      const H = Math.max(1, highlightHorizonDays);
      fwd.push([i, Math.min(i + H, lastRow)]);
    }
    // Dark spans: each signal's qualifying streak period, clipped to
    // the visible rows (the run may extend beyond the chart window).
    const dark: Array<[number, number]> = [];
    for (const s of highlightSpans) {
      let a = rowIdx.get(s.start);
      const b = rowIdx.get(s.end);
      if (b === undefined) continue;
      if (a === undefined) {
        // start predates the chart — clip to the first visible row
        a = 0;
      }
      dark.push([a, b]);
    }
    const merge = (spans: Array<[number, number]>): Array<[number, number]> => {
      const sorted = [...spans].sort((x, y) => x[0] - y[0] || x[1] - y[1]);
      const merged: Array<[number, number]> = [];
      for (const s of sorted) {
        const last = merged[merged.length - 1];
        if (last && s[0] <= last[1] + 1) {
          if (s[1] > last[1]) last[1] = s[1];
        } else {
          merged.push([s[0], s[1]]);
        }
      }
      return merged;
    };
    const mergedFwd = merge(fwd);
    const mergedDark = merge(dark);
    if (mergedFwd.length === 0 && mergedDark.length === 0) return empty;
    const markArea = {
      silent: true,
      data: [
        ...mergedFwd.map(
          ([a, b]) =>
            [
              { xAxis: anchor.dates[a], itemStyle: { color: TRIGGER_DATE_FILL } },
              { xAxis: anchor.dates[b] },
            ] as [{ xAxis: string; itemStyle: object }, { xAxis: string }],
        ),
        ...mergedDark.map(
          ([a, b]) =>
            [
              { xAxis: anchor.dates[a], itemStyle: { color: TRIGGER_STREAK_FILL } },
              { xAxis: anchor.dates[b] },
            ] as [{ xAxis: string; itemStyle: object }, { xAxis: string }],
        ),
      ],
    };
    return { points, markArea };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [highlightDates, highlightSpans, highlightHorizonDays, option]);

  // Apply the overlay with a MERGE setOption on the placeholder series
  // only — a row click costs a data swap on one scatter series instead
  // of a full notMerge rebuild of every candle. `option` in the deps
  // re-applies the overlay after a base rebuild (notMerge wiped it).
  // An empty→empty transition skips the setOption entirely (the no-op
  // merge still costs ~80 ms on a 1700-category chart); either way the
  // caller is told the overlay has settled so it can unfreeze the UI.
  const chartRef = useRef<ECharts | null>(null);
  const overlayEmptyRef = useRef(true);

  // ---- Visible-window reporter (onVisibleRangeChange) ---------------------
  // Maps the dataZoom's live start/end % back onto the category-axis dates
  // and reports the visible range. Fired on ECharts 'datazoom' events AND
  // on every rows rebuild (the option re-anchor). The callback lives in a
  // ref so the chart.on binding (made once) always calls the freshest
  // closure without re-binding on parent re-renders.
  const visibleRangeCbRef = useRef(onVisibleRangeChange);
  useEffect(() => {
    visibleRangeCbRef.current = onVisibleRangeChange;
  }, [onVisibleRangeChange]);

  const emitVisibleRange = useCallback(() => {
    const cb = visibleRangeCbRef.current;
    if (!cb) return;
    const chart = chartRef.current;
    const n = chartDates.length;
    if (!chart || n === 0) {
      cb(null);
      return;
    }
    try {
      const dz = (
        chart.getOption() as
          | { dataZoom?: Array<{ start?: number; end?: number }> }
          | undefined
      )?.dataZoom?.[0];
      const s = typeof dz?.start === "number" ? dz.start : 0;
      const e = typeof dz?.end === "number" ? dz.end : 100;
      const startIdx = Math.max(0, Math.min(n - 1, Math.floor((s / 100) * (n - 1))));
      const endIdx = Math.max(0, Math.min(n - 1, Math.ceil((e / 100) * (n - 1))));
      cb({ start: chartDates[startIdx], end: chartDates[endIdx] });
    } catch {
      // Instance disposed mid-read (HMR / unmount) — skip this report.
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chartDates]);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const handler = () => emitVisibleRange();
    chart.on("datazoom", handler);
    return () => {
      chart.off("datazoom", handler);
    };
  }, [emitVisibleRange]);

  // Initial report + re-report after rebuilds (the option effect re-anchors
  // the window; 'datazoom' alone never fires for those).
  useEffect(() => {
    emitVisibleRange();
  }, [emitVisibleRange]);

  // ---- focusDateRequest — jump the dataZoom window to a date --------------
  // Centers the CURRENT window span on the requested date (non-trading dates
  // snap forward to the next trading day) and dispatches the dataZoom action
  // — which moves BOTH dataZoom components (inside + slider) and re-fires
  // 'datazoom', so onVisibleRangeChange reporters stay in sync. Guarded by
  // the request's seq so a click is applied exactly once.
  const lastFocusSeqRef = useRef(-1);
  useEffect(() => {
    const req = focusDateRequest;
    if (!req || req.seq === lastFocusSeqRef.current) return;
    const chart = chartRef.current;
    const n = chartDates.length;
    if (!chart || n === 0) return;
    let idx = chartDates.indexOf(req.date);
    if (idx < 0) {
      idx = chartDates.findIndex((d) => d >= req.date);
      if (idx < 0) idx = n - 1;
    }
    try {
      const dz = (
        chart.getOption() as
          | { dataZoom?: Array<{ start?: number; end?: number }> }
          | undefined
      )?.dataZoom?.[0];
      const span =
        typeof dz?.start === "number" && typeof dz?.end === "number"
          ? Math.max(dz.end - dz.start, 1)
          : 100;
      const pos = (idx / Math.max(n - 1, 1)) * 100;
      let start = pos - span / 2;
      let end = pos + span / 2;
      if (start < 0) {
        start = 0;
        end = span;
      }
      if (end > 100) {
        end = 100;
        start = 100 - span;
      }
      chart.dispatchAction({ type: "dataZoom", start, end });
      lastFocusSeqRef.current = req.seq;
    } catch {
      // Instance disposed mid-dispatch (HMR / unmount) — skip this request.
    }
  }, [focusDateRequest, chartDates]);
  useEffect(() => {
    const chart = chartRef.current;
    const empty = triggerOverlay.points.length === 0 && !triggerOverlay.markArea;
    try {
      if (chart && !(empty && overlayEmptyRef.current)) {
        chart.setOption(
          {
            series: [
              {
                id: TRIGGER_SERIES_ID,
                data: triggerOverlay.points,
                markArea: triggerOverlay.markArea ?? { silent: true, data: [] },
              },
            ],
          } as unknown as EChartsOption,
          { notMerge: false },
        );
      }
    } finally {
      // Always settle — the caller's freeze must never stick, even if
      // the chart threw (disposed mid-update, HMR, ...).
      overlayEmptyRef.current = empty;
      onHighlightSettled?.();
    }
  }, [triggerOverlay, onHighlightSettled]);

  return (
    <EChart
      option={option}
      height={height}
      onReady={(c) => {
        chartRef.current = c;
        onChartReady?.(c);
      }}
      onCanvasClick={onDateClick ? (idx) => {
        const date = chartDates[idx];
        if (date) onDateClick(date);
      } : undefined}
    />
  );
}

// Identity-stable props (stable empty defaults upstream + memoized
// chartOptions in callers) let unrelated parent re-renders — chip
// mounts, spinner flips, row selection — skip this subtree entirely.
export default memo(StockOhlcChart);

