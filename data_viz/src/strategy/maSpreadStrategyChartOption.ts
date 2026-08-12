/**
 * ECharts option builder for the MA-spread strategy backtest chart.
 *
 * Renders:
 *   1. OHLC candlesticks (primary y-axis, left)
 *   2. MA5 + MA60 lines (primary y-axis)
 *   3. Trading amount bars (secondary y-axis, right)
 *   4. BUY markers (green dots; dark green for top-3 confidence) at fill_price
 *   5. SELL markers (red dots; dark red for top-3 losses) at fill_price
 *   6. Rich tooltip on hover showing full decision details
 *   7. Total return % as a graphic text annotation
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
import type { EChartsOption } from "echarts";
import type { ThemeMode } from "@/store/filters";
import { ohlcSeries } from "@/lib/ohlc";
import { fmtNum, fmtPct } from "@/lib/series";
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

  // ---- Normalization: rebase every price-derived series to 100 at the
  // first BUY fill. The anchor (first_buy_fill_price) is precomputed by the
  // Python backtest and stored on strategy_results; it's the SAME value each
  // trade_decision.normalized_fill_price is rebased against, so the chart's
  // OHLC/MA frame and the per-decision normalized index stay consistent.
  // Tooltips keep showing actual prices (from data.ohlc / decision.fill_price).
  // Trading amount is a volume measure on its own axis and is NOT rebased.
  // If there is no BUY (first_buy_fill_price is null), fall back to actual
  // prices (scale=1).
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
  // highlight coloring (dark green / dark red). Computed client-side from the
  // decisions array — same logic as the Python risk pipeline.
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

  // Buy/Sell marker data: [xIndex, fillPrice, decision] — scatter series
  // use the category index as x so they align with the OHLC bars. The
  // plotted fillPrice is rebased; the decision object keeps actual prices
  // (and its precomputed normalized_fill_price) for the tooltip.
  const buyMarkers: Array<[number, number, StrategyDecision]> = [];
  const sellMarkers: Array<[number, number, StrategyDecision]> = [];
  for (const d of data.decisions) {
    const idx = dateIdx.get(d.exec_date);
    if (idx == null) continue;
    const plotPrice = norm(d.fill_price) ?? d.fill_price;
    if (d.side === "BUY") {
      buyMarkers.push([idx, plotPrice, d]);
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
  // fill_price is the ACTUAL trade price; the precomputed normalized_fill_price
  // (base=100 at first BUY) is appended in parentheses when normalization is
  // on so the user can connect the marker's y-position to its real price.
  // normalized_mean_buy_price (the cost basis) is shown for SELLs alongside
  // the realized_pnl so the viewer can see what avg buy price the SELL is
  // exiting against.
  const markerTooltipFormatter = (params: unknown): string => {
    const p = params as { data?: { decision?: StrategyDecision } };
    const d = p.data?.decision;
    if (!d) return "";
    const sideColor = d.side === "BUY" ? UP_COLOR : DOWN_COLOR;
    const pnlStr = d.side === "SELL"
      ? `<br/>Realized P&L: <b style="color:${d.realized_pnl >= 0 ? UP_COLOR : DOWN_COLOR}">${d.realized_pnl >= 0 ? "+" : ""}${fmtNum(d.realized_pnl, 2)}</b>` +
        ` | Mean Buy idx: ${fmtNum(d.normalized_mean_buy_price, 1)}`
      : "";
    const priceStr = isNormalized
      ? `${fmtNum(d.fill_price, 4)} (idx ${fmtNum(d.normalized_fill_price, 1)})`
      : fmtNum(d.fill_price, 4);
    // Confidence: BUY = qty; SELL = (qty / total_qty_before) * 100.
    const confidence = d.side === "BUY"
      ? d.qty
      : d.total_qty_before > 0
        ? (d.qty / d.total_qty_before) * 100
        : 0;
    return [
      `<b style="color:${sideColor}">${d.side} #${d.decision_no}</b>`,
      `Exec: ${d.exec_date}`,
      `Confidence: ${fmtNum(confidence, 1)} / 100 | Qty: ${fmtNum(d.qty, 2)} @ ${priceStr}`,
      `Position: ${fmtNum(d.position_before, 2)} → ${fmtNum(d.position_after, 2)}`,
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
      data: ["OHLC", "MA5", "MA60", "Trading Amt", "Total P&L", "BUY", "SELL"],
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
        const lines: string[] = [`<b>${date}</b>`];
        for (const p of arr) {
          if (p.seriesName === "OHLC") {
            // Tooltips show ACTUAL prices (row.*), not the rebased plot
            // values (p.data), so the user always sees real OHLC numbers.
            lines.push(`${p.marker}O ${fmtNum(row.open)}  H ${fmtNum(row.high)}  L ${fmtNum(row.low)}  C ${fmtNum(row.close)}`);
          } else if (p.seriesName === "MA5") {
            lines.push(`${p.marker}MA5: ${fmtNum(row.ma5)}`);
          } else if (p.seriesName === "MA60") {
            lines.push(`${p.marker}MA60: ${fmtNum(row.ma60)}`);
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
        // Daily portfolio state — unrealized_pnl (as if all remaining position
        // sold at the day's close) + total_pnl (realized + unrealized) +
        // return_rate (ANNUALIZED return on capital, ×255) + annualized Sharpe
        // ratios (×√255, rf=0) of daily Δtotal_pnl.
        const daily = dailyByDate.get(date);
        if (daily) {
          const upColor = daily.unrealized_pnl >= 0 ? UP_COLOR : DOWN_COLOR;
          const tpColor = daily.total_pnl >= 0 ? UP_COLOR : DOWN_COLOR;
          const rrColor = daily.return_rate >= 0 ? UP_COLOR : DOWN_COLOR;
          lines.push(
            `Unrealized: <b style="color:${upColor}">${daily.unrealized_pnl >= 0 ? "+" : ""}${fmtNum(daily.unrealized_pnl, 2)}</b>` +
            ` | Total: <b style="color:${tpColor}">${daily.total_pnl >= 0 ? "+" : ""}${fmtNum(daily.total_pnl, 2)}</b>` +
            ` | Pos: ${fmtNum(daily.position_value, 2)} (${fmtNum(daily.total_qty, 1)})`
          );
          lines.push(
            `Return: <b style="color:${rrColor}">${daily.return_rate >= 0 ? "+" : ""}${fmtNum(daily.return_rate * 100, 2)}%</b>/yr` +
            ` | Hold: ${fmtNum(daily.normalized_mean_buy_period, 1)}d` +
            ` | Sharpe: full=${fmtNum(daily.sharpe_ratio, 2)}` +
            ` | 255d=${fmtNum(daily.sharpe_ratio_255d, 2)}` +
            ` | 500d=${fmtNum(daily.sharpe_ratio_500d, 2)}`
          );
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
        // Base-100 reference line: anchors the first BUY fill at y=100 so
        // the viewer can instantly see gains (above) vs losses (below) from
        // the entry point. Only drawn when normalization is active.
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
      // Total P&L curve — cumulative realized + unrealized P&L in normalized
      // money, plotted on its own y-axis (index 2). Null before the first BUY
      // (no daily row). Sharpe ratios derived from Δtotal_pnl are shown in the
      // axis tooltip alongside the daily portfolio state.
      {
        name: "Total P&L",
        type: "line",
        data: totalPnlData,
        yAxisIndex: 2,
        smooth: false,
        symbol: "none",
        lineStyle: { color: PE_COLOR, width: 1.5 },
        z: 6,
      },
      // BUY markers — green dots at fill_price (dark green for top-3 confidence)
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
      // SELL markers — red dots at fill_price (dark red for top-3 losses)
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
