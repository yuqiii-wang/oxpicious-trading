/**
 * ECharts option builder for the MA-spread strategy backtest chart.
 *
 * Renders:
 *   1. OHLC candlesticks (primary y-axis, left)
 *   2. MA5 + MA60 lines (primary y-axis)
 *   3. Trading amount bars (secondary y-axis, right)
 *   4. BUY markers (green up-arrows) at fill_price on exec_date
 *   5. SELL markers (red down-arrows) at fill_price on exec_date
 *   6. Rich tooltip on hover showing full decision details
 *   7. Total return % as a graphic text annotation
 */
import type { EChartsOption } from "echarts";
import type { ThemeMode } from "@/store/filters";
import { ohlcSeries } from "@/lib/ohlc";
import { fmtNum, fmtPct } from "@/lib/series";
import {
  MA5_COLOR,
  MA60_COLOR,
  UP_COLOR,
  DOWN_COLOR,
  axisColors,
  commonLegend,
  commonGrid,
  commonDataZoom,
} from "@/theme/chart-palette";
import type {
  StrategyBacktestResponse,
  StrategyDecision,
  StrategyOhlcRow,
} from "../../shared/types";

interface BuildOptionParams {
  data: StrategyBacktestResponse;
  themeMode: ThemeMode;
}

export function buildMaSpreadStrategyOption({
  data,
  themeMode,
}: BuildOptionParams): EChartsOption {
  const c = axisColors(themeMode);
  const dates = data.ohlc.map((r) => r.date);

  // OHLC data: [open, close, low, high]
  const ohlcData = data.ohlc.map((r) => [r.open, r.close, r.low, r.high]);

  // MA lines
  const ma5Data = data.ohlc.map((r) => r.ma5);
  const ma60Data = data.ohlc.map((r) => r.ma60);

  // Trading amount bars
  const amtData = data.ohlc.map((r) => r.trading_amount);

  // Build a date→index map for placing B/S markers on the correct x position.
  const dateIdx = new Map<string, number>();
  dates.forEach((d, i) => dateIdx.set(d, i));

  // Buy/Sell marker data: [xIndex, fillPrice, decision] — scatter series
  // use the category index as x so they align with the OHLC bars.
  const buyMarkers: Array<[number, number, StrategyDecision]> = [];
  const sellMarkers: Array<[number, number, StrategyDecision]> = [];
  for (const d of data.decisions) {
    const idx = dateIdx.get(d.exec_date);
    if (idx == null) continue;
    if (d.side === "BUY") {
      buyMarkers.push([idx, d.fill_price, d]);
    } else {
      sellMarkers.push([idx, d.fill_price, d]);
    }
  }

  // Total return annotation text
  const retPct = data.summary.total_return_pct;
  const retColor = retPct >= 0 ? UP_COLOR : DOWN_COLOR;
  const retText = `Total Return: ${retPct >= 0 ? "+" : ""}${fmtPct(retPct)}  (${fmtNum(data.summary.n_buys)}B / ${fmtNum(data.summary.n_sells)}S)`;

  // Tooltip formatter for B/S markers — shows full decision detail.
  const markerTooltipFormatter = (params: unknown): string => {
    const p = params as { data?: { decision?: StrategyDecision } };
    const d = p.data?.decision;
    if (!d) return "";
    const sideColor = d.side === "BUY" ? UP_COLOR : DOWN_COLOR;
    const pnlStr = d.side === "SELL"
      ? `<br/>Realized P&L: <b style="color:${d.realized_pnl >= 0 ? UP_COLOR : DOWN_COLOR}">${d.realized_pnl >= 0 ? "+" : ""}${fmtNum(d.realized_pnl, 2)}</b>`
      : "";
    return [
      `<b style="color:${sideColor}">${d.side} #${d.decision_no}</b>`,
      `Signal: ${d.signal_date} → Exec: ${d.exec_date}`,
      `Confidence: ${fmtNum(d.qty, 1)} / 100 @ ${fmtNum(d.fill_price, 4)}`,
      `Gross: ${fmtNum(d.gross_value, 2)} | Comm: ${fmtNum(d.commission, 2)} | Fees: ${fmtNum(d.fees, 2)}`,
      `Position: ${fmtNum(d.position_before, 6)} → ${fmtNum(d.position_after, 6)}`,
      `Cash: ${fmtNum(d.cash_before, 2)} → ${fmtNum(d.cash_after, 2)}`,
      pnlStr,
      `<span style="color:${c.textColor};font-size:11px">${d.signal_reason}</span>`,
    ].join("<br/>");
  };

  return {
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
      data: ["OHLC", "MA5", "MA60", "Trading Amt", "BUY", "SELL"],
    }),
    grid: commonGrid({ top: 60, bottom: 50, left: 60, right: 70 }),
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
        name: "Price",
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
        const lines: string[] = [`<b>${date}</b>`];
        for (const p of arr) {
          if (p.seriesName === "OHLC") {
            const [o, cl, l, h] = p.data as Array<number | null>;
            lines.push(`${p.marker}O ${fmtNum(o)}  H ${fmtNum(h)}  L ${fmtNum(l)}  C ${fmtNum(cl)}`);
          } else if (p.seriesName === "MA5") {
            lines.push(`${p.marker}MA5: ${fmtNum(p.data as number)}`);
          } else if (p.seriesName === "MA60") {
            lines.push(`${p.marker}MA60: ${fmtNum(p.data as number)}`);
          } else if (p.seriesName === "Trading Amt") {
            lines.push(`${p.marker}Amt: ${fmtNum((p.data as number) / 1e8, 2)}亿`);
          }
        }
        // Check if this date has a B/S decision — append decision info.
        const decision = data.decisions.find((d) => d.exec_date === date);
        if (decision) {
          const sc = decision.side === "BUY" ? UP_COLOR : DOWN_COLOR;
          lines.push(`<b style="color:${sc}">${decision.side} #${decision.decision_no}</b> @ ${fmtNum(decision.fill_price, 4)} | ${decision.signal_reason}`);
        }
        return lines.join("<br/>");
      },
    },
    dataZoom: commonDataZoom({}, 60, 100),
    series: [
      ohlcSeries(ohlcData, { name: "OHLC", yAxisIndex: 0, z: 10 }),
      {
        name: "MA5",
        type: "line",
        data: ma5Data,
        yAxisIndex: 0,
        smooth: false,
        symbol: "none",
        lineStyle: { color: MA5_COLOR, width: 1.2 },
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
        z: 5,
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
        z: 1,
      },
      // BUY markers — green up-arrow scatter at fill_price
      {
        name: "BUY",
        type: "scatter",
        data: buyMarkers.map(([idx, price, d]) => ({
          value: [idx, price],
          decision: d,
        })),
        xAxisIndex: 0,
        yAxisIndex: 0,
        symbol: "triangle",
        symbolSize: 14,
        itemStyle: { color: UP_COLOR, borderColor: "#fff", borderWidth: 1 },
        z: 20,
        tooltip: { formatter: markerTooltipFormatter },
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
      // SELL markers — red down-arrow scatter at fill_price
      {
        name: "SELL",
        type: "scatter",
        data: sellMarkers.map(([idx, price, d]) => ({
          value: [idx, price],
          decision: d,
        })),
        xAxisIndex: 0,
        yAxisIndex: 0,
        symbol: "pin",
        symbolSize: 14,
        itemStyle: { color: DOWN_COLOR, borderColor: "#fff", borderWidth: 1 },
        z: 20,
        tooltip: { formatter: markerTooltipFormatter },
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
    ],
  };
}
