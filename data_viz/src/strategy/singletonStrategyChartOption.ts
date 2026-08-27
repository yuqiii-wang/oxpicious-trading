/**
 * ECharts option builder for the singleton strategy backtest chart.
 *
 * Renders:
 *   1. OHLC candlesticks (primary y-axis, left)
 *   2. MA5 + MA60 lines (primary y-axis)
 *   3. Trading amount bars (secondary y-axis, right)
 *   4. BUY markers (green dots; dark green for top-3 confidence) at fill_price
 *   5. SELL markers (red dots; dark red for top-3 losses) at fill_price
 *   6. LAST DAY SELL marker (purple dot; larger) at the projected fill_price
 *   7. Rich tooltip on hover showing full decision details
 *   8. Total return % as a graphic text annotation
 *
 * Normalization: every price-derived series (OHLC, MA5, MA60, B/S marker
 * positions) is rebased so the FIRST BUY fill = 100. The anchor
 * (summary.first_buy_fill_price, precomputed by the Python backtest and
 * stored on strategy_results) is the SAME value each trade_decision's
 * normalized_fill_price is rebased against, so the chart's OHLC/MA frame
 * and the per-decision normalized index stay consistent. A dashed "Base 100"
 * reference line is drawn at y=100. Trading amount is a volume measure on
 * its own axis and is NOT rebased. Tooltips always show ACTUAL prices
 * (looked up from the raw OHLC row / decision.fill_price) so real values
 * remain visible despite the rebased axis. If there is no BUY (anchor is
 * null), the chart falls back to absolute prices.
 */
import React from "react";
import type { EChartsOption } from "echarts";
import type { ThemeMode } from "@/store/filters";
import { ohlcSeries } from "@/lib/ohlc";
import { fmtNum, fmtPct } from "@/lib/series";
import { renderReactElement, tooltipComponents } from "@/lib/react-tooltip-renderer";
import {
  MA5_COLOR,
  MA60_COLOR,
  UP_COLOR,
  DOWN_COLOR,
  PE_COLOR,
  axisColors,
  commonLegend,
  commonGrid,
  commonDataZoom,
} from "@/theme/chart-palette";
import { createMarkerTooltipFormatter } from "./tooltips";
import type {
  StrategyBacktestResponse,
  StrategyDecision,
  StrategyOhlcRow,
  StrategyPeriodType,
} from "@shared/types";

/**
 * Selected risk-analytics period — set when the user clicks a bar in the
 * RiskPanel chart. The main OHLC chart shades the corresponding date range
 * green (gain) or red (loss) so the viewer can see which trading days drove
 * that period's P&L.
 */
export interface SelectedPeriod {
  periodType: StrategyPeriodType;
  periodValue: string;
  /** true → gain (green shade), false → loss (red shade). */
  isGain: boolean;
}

interface BuildOptionParams {
  data: StrategyBacktestResponse;
  themeMode: ThemeMode;
  /** Currently-selected risk period (null = no shading). */
  selectedPeriod?: SelectedPeriod | null;
}

/**
 * Map a "YYYY-MM-DD" date string to its period label, mirroring the Python
 * `strategy._risks.periods.period_value` helper (year=YYYY, season=YYYY-Qn,
 * month=YYYY-MM). Used to find the OHLC x-axis range that falls inside a
 * selected risk period.
 */
function dateToPeriodValue(dateStr: string, periodType: StrategyPeriodType): string {
  const y = dateStr.slice(0, 4);
  if (periodType === "year") return y;
  if (periodType === "month") return dateStr.slice(0, 7);
  const m = parseInt(dateStr.slice(5, 7), 10);
  const q = Math.floor((m - 1) / 3) + 1;
  return `${y}-Q${q}`;
}

/**
 * Find the inclusive [startIdx, endIdx] range of OHLC dates whose period label
 * matches `periodValue`. Returns null if no OHLC date falls in this period
 * (e.g. period predates the backtest or lies outside the dataZoom window).
 */
function findPeriodRange(
  dates: string[],
  periodType: StrategyPeriodType,
  periodValue: string,
): [number, number] | null {
  let start = -1;
  let end = -1;
  for (let i = 0; i < dates.length; i++) {
    if (dateToPeriodValue(dates[i], periodType) === periodValue) {
      if (start < 0) start = i;
      end = i;
    }
  }
  if (start < 0) return null;
  return [start, end];
}

/** Apply alpha transparency to a #RRGGBB hex color → rgba() string. */
function withAlphaHex(hex: string, alpha: number): string {
  const m = /^#([0-9a-fA-F]{6})$/.exec(hex.trim());
  if (!m) return hex;
  const r = parseInt(m[1].slice(0, 2), 16);
  const g = parseInt(m[1].slice(2, 4), 16);
  const b = parseInt(m[1].slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

/** Strip the ``__MIX__<json>__`` prefix from a mixed-mode signal_reason,
 *  returning only the human-readable part. Binary-mode reasons (no prefix)
 *  are returned unchanged. The structured per-algo breakdown tooltip lives
 *  ONLY in the DecisionTable (not in the in-plot tooltip). */
const MIX_PREFIX = "__MIX__";
function stripMixPrefix(reason: string | null | undefined): string {
  if (!reason || !reason.startsWith(MIX_PREFIX)) return reason ?? "";
  const rest = reason.slice(MIX_PREFIX.length);
  const end = rest.indexOf("__");
  if (end < 0) return reason;
  return rest.slice(end + 2);
}

export function buildSingletonStrategyOption({
  data,
  themeMode,
  selectedPeriod = null,
}: BuildOptionParams): EChartsOption {
  const c = axisColors(themeMode);
  const ohlcDates = data.ohlc.map((r) => r.date);
  const dates = ohlcDates;

  // ---- Selected-period shading: if a risk-analytics bar is selected,
  // compute the OHLC x-axis index range that falls inside that period and
  // shade it green (gain) or red (loss) via a markArea on the OHLC series.
  const periodRange = selectedPeriod
    ? findPeriodRange(dates, selectedPeriod.periodType, selectedPeriod.periodValue)
    : null;
  const periodShadeColor = selectedPeriod
    ? (selectedPeriod.isGain ? UP_COLOR : DOWN_COLOR)
    : null;

  // ---- Normalization: rebase every price-derived series to 100 at the
  // first BUY fill. The anchor (first_buy_fill_price) is precomputed by the
  // Python backtest and stored on strategy_results; it's the SAME value each
  // trade_decision.normalized_fill_price is rebased against, so the chart's
  // OHLC/MA frame and the per-decision normalized index stay consistent.
  const normBase = data.summary.first_buy_fill_price;
  const isNormalized = normBase != null && normBase > 0;
  const normScale = isNormalized ? 100 / normBase! : 1;
  const norm = (v: number | null | undefined): number | null =>
    v != null && Number.isFinite(v) ? v * normScale : null;

  // OHLC data: [open, close, low, high] — rebased when normalized.
  const ohlcData = data.ohlc.map((r) =>
    [norm(r.open), norm(r.close), norm(r.low), norm(r.high)] as Array<number | null>);

  // MA lines — rebased in lockstep (same scale) so they stay aligned with
  // the candle closes and keep their relative shape.
  const ma5Data = data.ohlc.map((r) => norm(r.ma5));
  const ma60Data = data.ohlc.map((r) => norm(r.ma60));

  // Trading amount bars — NOT rebased (different unit, own y-axis).
  const amtData = data.ohlc.map((r) => r.trading_amount);

  // Build a date→index map for placing B/S markers on the correct x position.
  const dateIdx = new Map<string, number>();
  dates.forEach((d, i) => dateIdx.set(d, i));

  // Build a date→dailyRow map for tooltip display of unrealized_pnl.
  const dailyByDate = new Map<string, typeof data.daily[number]>();
  for (const d of data.daily) dailyByDate.set(d.trade_date, d);

  // Total P&L curve — aligned to OHLC dates (null before first BUY / when
  // no daily row exists). Plotted on its own y-axis (index 2) since it's in
  // normalized money, a different scale from both price (~100) and trading amt.
  const totalPnlData = data.ohlc.map((r) => {
    const daily = dailyByDate.get(r.date);
    return daily ? daily.total_pnl : null;
  });

  // Identify top-3 highest-confidence BUYs and top-3 biggest-loss SELLs for
  // highlight coloring (dark green / dark red).
  const topConfBuyNos = new Set<number>();
  data.decisions
    .filter((d) => d.side === "BUY")
    .sort((a, b) => b.qty - a.qty)
    .slice(0, 3)
    .forEach((d) => topConfBuyNos.add(d.decision_no));

  const topLossSellNos = new Set<number>();
  data.decisions
    .filter((d) => d.side === "SELL")
    .sort((a, b) => a.realized_pnl - b.realized_pnl)
    .slice(0, 3)
    .forEach((d) => topLossSellNos.add(d.decision_no));

  const DARK_GREEN = "#006400";
  const DARK_RED = "#8B0000";
  // Last-day sell (final liquidation at the projected price) — purple.
  const LAST_DAY_SELL_COLOR = "#9575cd";
  const isLastDaySell = (d: StrategyDecision): boolean =>
    !!d.signal_reason?.startsWith("LAST DAY SELL");

  // Separate markers: regular SELLs vs the single LAST DAY SELL.
  const buyMarkers: Array<[number, number, StrategyDecision]> = [];
  const sellMarkers: Array<[number, number, StrategyDecision]> = [];
  const lastDaySellMarkers: Array<[number, number, StrategyDecision]> = [];
  for (const d of data.decisions) {
    const idx = dateIdx.get(d.exec_date);
    if (idx == null) continue;
    const plotPrice = norm(d.fill_price) ?? d.fill_price;
    if (d.side === "BUY") {
      buyMarkers.push([idx, plotPrice, d]);
    } else if (isLastDaySell(d)) {
      lastDaySellMarkers.push([idx, plotPrice, d]);
    } else {
      sellMarkers.push([idx, plotPrice, d]);
    }
  }

  // Total return annotation text
  const retPct = data.summary.total_return_pct;
  const retColor = retPct >= 0 ? UP_COLOR : DOWN_COLOR;
  const anchorDate = data.summary.first_buy_date;
  const retText = `Total Return: ${retPct >= 0 ? "+" : ""}${fmtPct(retPct)}  (${fmtNum(data.summary.n_buys)}B / ${fmtNum(data.summary.n_sells)}S)${isNormalized && anchorDate ? `  | Base=100 @ ${anchorDate}` : ""}`;

  // Tooltip formatter for B/S markers — shows full decision detail.
  const markerTooltipFormatter = createMarkerTooltipFormatter({
    upColor: UP_COLOR,
    downColor: DOWN_COLOR,
    textColor: c.textColor,
    isNormalized,
    fmtNum,
    stripMixPrefix,
  });

  return {
    animation: false,
    backgroundColor: "transparent",
    title: {
      text: `${data.code} ${data.name}`,
      subtext: retText,
      subtextStyle: { color: retColor, fontSize: 14, fontWeight: 700 },
      left: 10,
      top: 4,
      textStyle: { color: c.textColor, fontSize: 14 },
    },
    legend: commonLegend(themeMode, {
      data: [
        "OHLC", "MA5", "MA60", "Trading Amt", "Total P&L", "BUY", "SELL",
        "LAST DAY SELL",
      ],
    }),
    grid: commonGrid({ top: 60, bottom: 50, left: 60, right: 130 }),
    xAxis: {
      type: "category",
      data: dates,
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: { color: c.textColor, fontSize: 10 },
      splitLine: { show: false },
      boundaryGap: true,
      axisTick: { alignWithLabel: true },
    },
    yAxis: [
      {
        type: "value",
        name: isNormalized ? "Price (Base=100)" : "Price",
        position: "left",
        scale: true,
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: { color: c.textColor, fontSize: 10 },
        splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed" } },
        nameTextStyle: { color: c.textColor, fontSize: 10 },
      },
      {
        type: "value",
        name: "Trading Amt",
        position: "right",
        scale: true,
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: {
          color: c.textColor,
          fontSize: 10,
          formatter: (v: number) => fmtNum(v / 1e8, 1) + "亿",
        },
        splitLine: { show: false },
        nameTextStyle: { color: c.textColor, fontSize: 10 },
      },
      {
        type: "value",
        name: "Total P&L",
        position: "right",
        offset: 60,
        scale: true,
        axisLine: { lineStyle: { color: PE_COLOR } },
        axisLabel: { color: c.textColor, fontSize: 10 },
        splitLine: { show: false },
        nameTextStyle: { color: PE_COLOR, fontSize: 10 },
      },
    ],
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "cross" },
      backgroundColor: c.tooltipBg,
      borderColor: c.axisLineColor,
      textStyle: { color: c.textColor, fontSize: 11 },
      formatter: (params: unknown) => {
        const arr = params as Array<{
          seriesName: string;
          data: unknown;
          marker: string;
          dataIndex: number;
        }>;
        if (!Array.isArray(arr) || arr.length === 0) return "";
        const di = arr[0].dataIndex ?? 0;
        const date = dates[di] ?? "";
        const row: StrategyOhlcRow | undefined = data.ohlc[di];
        if (!row) return "";
        const children: React.ReactNode[] = [
          tooltipComponents.Header({ children: date }),
        ];
        for (const p of arr) {
          if (p.seriesName === "OHLC") {
            children.push(React.createElement(React.Fragment, null,
              `${p.marker}O ${fmtNum(row.open)}  H ${fmtNum(row.high)}  L ${fmtNum(row.low)}  C ${fmtNum(row.close)}`));
          } else if (p.seriesName === "MA5") {
            children.push(React.createElement(React.Fragment, null, `${p.marker}MA5: ${fmtNum(row.ma5)}`));
          } else if (p.seriesName === "MA60") {
            children.push(React.createElement(React.Fragment, null, `${p.marker}MA60: ${fmtNum(row.ma60)}`));
          } else if (p.seriesName === "Trading Amt") {
            children.push(React.createElement(React.Fragment, `${p.marker}Amt: ${fmtNum((p.data as number) / 1e8, 2)}亿`));
          }
        }
        const decision = data.decisions.find((d) => d.exec_date === date);
        if (decision) {
          const sc = decision.side === "BUY"
            ? UP_COLOR
            : isLastDaySell(decision)
              ? LAST_DAY_SELL_COLOR
              : DOWN_COLOR;
          children.push(
            React.createElement("b", { style: { color: sc } },
              `${decision.side} #${decision.decision_no}`),
            ` @ ${fmtNum(decision.fill_price, 4)} | ${stripMixPrefix(decision.signal_reason)}`,
          );
        }
        const daily = dailyByDate.get(date);
        if (daily) {
          const upColor = daily.unrealized_pnl >= 0 ? UP_COLOR : DOWN_COLOR;
          const tpColor = daily.total_pnl >= 0 ? UP_COLOR : DOWN_COLOR;
          const rrColor = daily.return_rate >= 0 ? UP_COLOR : DOWN_COLOR;
          children.push(
            React.createElement(React.Fragment, null, [
              "Unrealized: ",
              React.createElement("b", { style: { color: upColor } },
                `${daily.unrealized_pnl >= 0 ? "+" : ""}${fmtNum(daily.unrealized_pnl, 2)}`),
              " | Total: ",
              React.createElement("b", { style: { color: tpColor } },
                `${daily.total_pnl >= 0 ? "+" : ""}${fmtNum(daily.total_pnl, 2)}`),
              ` | Pos: ${fmtNum(daily.position_value, 2)} (${fmtNum(daily.total_qty, 1)})`,
            ]),
          );
          children.push(
            React.createElement(React.Fragment, null, [
              "Return: ",
              React.createElement("b", { style: { color: rrColor } },
                `${daily.return_rate >= 0 ? "+" : ""}${fmtNum(daily.return_rate * 100, 2)}%`),
              "/yr",
              ` | Hold: ${fmtNum(daily.normalized_mean_buy_period, 1)}d`,
              ` | Sharpe: full=${fmtNum(daily.sharpe_ratio, 2)}`,
              ` | 255d=${fmtNum(daily.sharpe_ratio_255d, 2)}`,
              ` | 500d=${fmtNum(daily.sharpe_ratio_500d, 2)}`,
            ]),
          );
        }
        return renderReactElement(React.createElement(React.Fragment, null, children));
      },
    },
    dataZoom: commonDataZoom({}, 60, 100),
    series: [
      {
        ...ohlcSeries(ohlcData, { name: "OHLC", yAxisIndex: 0, z: 10 }),
        emphasis: { disabled: true },
      },
      {
        name: "MA5",
        type: "line",
        data: ma5Data,
        yAxisIndex: 0,
        smooth: false,
        symbol: "none",
        lineStyle: { color: MA5_COLOR, width: 1.2 },
        emphasis: { disabled: true },
        z: 5,
      },
      {
        name: "MA60",
        type: "line",
        data: ma60Data,
        yAxisIndex: 0,
        smooth: false,
        symbol: "none",
        lineStyle: { color: MA60_COLOR, width: 1.2 },
        emphasis: { disabled: true },
        z: 5,
        ...(isNormalized ? {
          markLine: {
            symbol: "none",
            silent: true,
            lineStyle: { color: c.axisLineColor, type: "dashed", width: 1 },
            label: {
              formatter: "Base 100",
              color: c.textColor,
              fontSize: 9,
              position: "insideEndTop",
            },
            data: [{ yAxis: 100 }],
          },
        } : {}),
        ...(periodRange && periodShadeColor ? {
          markArea: {
            silent: true,
            z: 0,
            itemStyle: {
              color: withAlphaHex(periodShadeColor, 0.22),
            },
            label: {
              show: true,
              formatter: selectedPeriod.periodValue,
              color: periodShadeColor,
              fontSize: 10,
              fontWeight: 700,
              position: "insideTopLeft",
            },
            data: [[
              { xAxis: periodRange[0] },
              { xAxis: periodRange[1] },
            ]],
          },
        } : {}),
      },
      {
        name: "Trading Amt",
        type: "bar",
        data: amtData,
        yAxisIndex: 1,
        barWidth: "60%",
        itemStyle: {
          color: (params: { dataIndex: number }) => {
            const r = data.ohlc[params.dataIndex];
            if (!r || r.close == null || r.open == null) return c.splitLineColor;
            return r.close >= r.open
              ? "rgba(39, 174, 96, 0.25)"
              : "rgba(192, 57, 43, 0.25)";
          },
        },
        emphasis: { disabled: true },
        z: 1,
      },
      {
        name: "Total P&L",
        type: "line",
        data: totalPnlData,
        yAxisIndex: 2,
        smooth: false,
        symbol: "none",
        lineStyle: { color: PE_COLOR, width: 1.5 },
        emphasis: { disabled: true },
        z: 6,
      },
      {
        name: "BUY",
        type: "scatter",
        data: buyMarkers.map(([idx, price, d]) => ({
          value: [idx, price],
          decision: d,
          itemStyle: {
            color: topConfBuyNos.has(d.decision_no) ? DARK_GREEN : UP_COLOR,
            borderColor: "#fff",
            borderWidth: 1,
          },
        })),
        xAxisIndex: 0,
        yAxisIndex: 0,
        symbol: "circle",
        symbolSize: 10,
        z: 20,
        tooltip: { formatter: markerTooltipFormatter },
        emphasis: { disabled: true },
        label: {
          show: true,
          position: "bottom",
          formatter: "B",
          fontSize: 9,
          fontWeight: 700,
          color: UP_COLOR,
          distance: -3,
        },
      },
      {
        name: "SELL",
        type: "scatter",
        data: sellMarkers.map(([idx, price, d]) => ({
          value: [idx, price],
          decision: d,
          itemStyle: {
            color: topLossSellNos.has(d.decision_no) ? DARK_RED : DOWN_COLOR,
            borderColor: "#fff",
            borderWidth: 1,
          },
        })),
        xAxisIndex: 0,
        yAxisIndex: 0,
        symbol: "circle",
        symbolSize: 10,
        z: 20,
        tooltip: { formatter: markerTooltipFormatter },
        emphasis: { disabled: true },
        label: {
          show: true,
          position: "top",
          formatter: "S",
          fontSize: 9,
          fontWeight: 700,
          color: DOWN_COLOR,
          distance: -3,
        },
      },
      {
        name: "LAST DAY SELL",
        type: "scatter",
        data: lastDaySellMarkers.map(([idx, price, d]) => ({
          value: [idx, price],
          decision: d,
          itemStyle: {
            color: LAST_DAY_SELL_COLOR,
            borderColor: "#fff",
            borderWidth: 2,
          },
        })),
        xAxisIndex: 0,
        yAxisIndex: 0,
        symbol: "circle",
        symbolSize: 16,
        z: 30,
        tooltip: { formatter: markerTooltipFormatter },
        emphasis: { disabled: true },
        label: {
          show: true,
          position: "top",
          formatter: "LDS",
          fontSize: 10,
          fontWeight: 700,
          color: LAST_DAY_SELL_COLOR,
          distance: -4,
        },
      },
    ],
  };
}
