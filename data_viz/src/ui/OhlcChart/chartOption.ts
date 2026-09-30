/**
 * chartOption — the pure base-option builder for OhlcChart.
 * Extracted verbatim from the component's option useMemo: everything from
 * raw rows to the complete EChartsOption (candles/MA/margin/amount/PE +
 * dividend and trade-signal markPoints + regime shading + the two
 * trigger-overlay placeholder series). The only side effect is the
 * documented TriggerAnchor stash (see the assignment near the end).
 */
import type { EChartsOption } from "echarts";
import {
  makeCodeTrendTooltipFormatter,
} from "@/shared/charts/CodeTrendTooltip";
import { breakArraysAtGaps, fmtNum, safeMa } from "@/lib/series";
import {
  ohlcSeries,
  rebasePriceArrays,
  formatPriceValue,
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
  UP_COLOR,
  DOWN_COLOR,
  axisColors,
  commonGrid,
  commonDataZoom,
} from "@/theme/chart-palette";
import { baseChartOption, commonTooltip } from "@/shared/charts/base-chart";
import type { StockDailyRow, StockDividend } from "@shared/types";
import {
  REGIME_ACCENT_COLORS,
  REGIME_LABELS,
  REGIME_SHADE_COLORS,
  SHADED_REGIMES,
  regimeSpansToMarkArea,
} from "@/shared/charts/regimeBands";
import type {
  ForecastActionByDateRef,
  OhlcTradeSignal,
  StreakInfoByDateRef,
  TriggerAnchorRef,
} from "./types";
import { TRIGGER_ACTION_SERIES_ID, TRIGGER_SERIES_ID } from "./types";
import type { OhlcMode } from "@/lib/ohlc";
import type { ThemeMode } from "@/store/filters";
import type { MarketRegimeSpans } from "@shared/types";

/** Index of the raw trading-amount array inside the breakArraysAtGaps
 *  call below (rides along so the Amount bars stay aligned with the
 *  gap-inserted category axis). */
const BROKEN_AMT_IDX = 10;

export interface OhlcOptionArgs {
  rows: StockDailyRow[];
  ohlcMode: OhlcMode;
  themeMode: ThemeMode;
  dataZoomStart?: number;
  dataZoomEnd?: number;
  dividends: StockDividend[];
  regimeSpans?: Partial<MarketRegimeSpans>;
  tradeSignals: OhlcTradeSignal[];
  /** Anchor stash — assigned on every build, read by buildTriggerOverlay. */
  triggerAnchorRef: TriggerAnchorRef;
  /** Date → streak context of the overlay's dark spans, read by the axis
   *  tooltip formatter at hover time (refreshed by buildTriggerOverlay). */
  streakInfoByDateRef: StreakInfoByDateRef;
  /** Date → the overlay's forecast-action labels, read the same way. */
  forecastActionByDateRef: ForecastActionByDateRef;
}

export function buildOhlcOption({
  rows, ohlcMode, themeMode, dataZoomStart, dataZoomEnd,
  dividends, regimeSpans, tradeSignals,
  triggerAnchorRef, streakInfoByDateRef, forecastActionByDateRef,
}: OhlcOptionArgs): EChartsOption {
  const c = axisColors(themeMode);
  const enableDataZoom = dataZoomStart !== undefined;
  const dates = rows.map((r) => r.date);
  const open = rows.map((r) => r.open);
  const high = rows.map((r) => r.high);
  const low = rows.map((r) => r.low);
  const close = rows.map((r) => r.close);
  const pe = rows.map((r) => r.pe);
  const isPeEstimatedNum = rows.map((r) => (r.is_pe_estimated ? 1 : 0));
  // Liquidity + margin data (from stock_liquidity_margin via v_stock_daily)
  const tradingAmount = rows.map((r) => r.trading_amount);
  // Compute MA client-side — the stock daily view does not carry
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

  // --- Market-regime shading (per-regime full-height bands) ------------
  // Empty-data series hosting each regime's markArea (the same
  // rect-legend + markArea pattern as the MA-Spread charts) — sits
  // behind everything; each regime's legend entry toggles its shade.
  if (regimeSpans) {
    for (const r of SHADED_REGIMES) {
      const spans = regimeSpans[r];
      if (!spans || spans.length === 0) continue;
      series.push({
        type: "line",
        name: REGIME_LABELS[r],
        yAxisIndex: 0,
        data: [],
        markArea: {
          silent: true,
          data: regimeSpansToMarkArea(spans, REGIME_SHADE_COLORS[r]),
        },
        itemStyle: { color: REGIME_ACCENT_COLORS[r], opacity: 0.45 },
        z: 0,
      });
    }
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
  // Forecast-action triangle host (the clicked bucket's registered
  // buy/sell per highlighted day) — placeholder ONLY, applied
  // imperatively like the trigger host above. Rides the PRICE axis so
  // the triangles can anchor at the day's low / high exactly like the
  // tradeSignals markPoints; the anchors sit within the candles'
  // extent, so the scale:true axis never moves.
  series.push({
    type: "scatter",
    id: TRIGGER_ACTION_SERIES_ID,
    name: "Forecast action",
    yAxisIndex: 0,
    data: [],
    silent: true,
    z: 9,
  });
  // Date → chart-row lookup for the overlay effect (chart dates with
  // gap-break inserts). Assigning a ref inside the memo is an
  // idempotent side effect — the overlay memo below re-reads it
  // (keyed on this memo's output) so the two never diverge.
  triggerAnchorRef.current = {
    dates: broken.dates,
    low: broken.arrays[2],
    high: broken.arrays[1],
    close: broken.arrays[3],
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
        forecastActionByDateRef,
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
}
