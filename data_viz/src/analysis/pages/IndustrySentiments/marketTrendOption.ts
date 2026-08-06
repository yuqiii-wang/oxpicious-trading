/**
 * Build the ECharts options for the Market Trend view.
 *
 * Two option builders:
 *   1. buildMarketTrendOption — the combined overview plot (1st chart).
 *      All four broad-market indices' closes rebased to 100 at the window
 *      start, plotted as lines on the left axis, PLUS each index's trading
 *      amount embedded as stacked bars on a right axis (the stack height is
 *      the aggregate capital flow; each segment is one index's proportional
 *      share). A `visibleCodes` filter controls which indices are drawn.
 *   2. buildMarketOhlcOption — one OHLC chart per index (IndexPanel style:
 *      shared `ohlcSeries` + MA5/MA20/MA60/MA120 + trading amount bars on a
 *      twin axis). Rebased to % change in "percentage" mode.
 */
import type { EChartsOption } from "echarts";
import type { ThemeMode } from "@/store/filters";
import type { IndexBaselineRow } from "../../../../shared/types";
import {
  MA5_COLOR,
  MA20_COLOR,
  MA60_COLOR,
  MA120_COLOR,
  UP_COLOR,
  DOWN_COLOR,
  axisColors,
  commonLegend,
  commonGrid,
} from "@/theme/chart-palette";
import { breakArraysAtGaps, fmtNum } from "@/lib/series";
import {
  ohlcSeries,
  rebasePriceArrays,
  formatPriceValue,
  type OhlcMode,
} from "@/lib/ohlc";
import { MARKET_TREND_INDICES } from "./constants";

/** One index's daily baseline rows prepared for plotting. */
interface IndexSeriesData {
  code: string;
  name: string;
  color: string;
  rows: IndexBaselineRow[];
}

/**
 * Build the combined Market Trend overview chart (1st plot).
 *
 * Close lines (rebased to 100 at visible-window start, left axis) for each
 * selected index + trading amount stacked bars (right axis, bottom). The
 * stack height is the sum of the selected indices' trading amounts (亿元);
 * each coloured segment shows that index's proportional contribution.
 *
 * @param allDates     Sorted union of all indices' dates.
 * @param datasets     One IndexSeriesData per index (rows aligned inside).
 * @param visibleCodes Codes to actually draw. Indices not in this list are
 *                     omitted from both the close lines and the stacked bars.
 * @param themeMode    Current theme.
 */
export function buildMarketTrendOption(
  allDates: string[],
  datasets: IndexSeriesData[],
  visibleCodes: string[],
  themeMode: ThemeMode,
): EChartsOption {
  const c = axisColors(themeMode);
  const visible = datasets.filter((ds) => visibleCodes.includes(ds.code));

  // --- Close lines (rebased to 100 at window start) -------------------
  // Build per-index raw close aligned to allDates, then rebase each to 100
  // at its first non-null close within the window.
  const closeSeries = visible.map((ds) => {
    const closeByDate = new Map<string, number | null>();
    for (const r of ds.rows) closeByDate.set(r.date, r.close);
    const rawCloses: Array<number | null> = allDates.map(
      (d) => closeByDate.get(d) ?? null,
    );
    // Rebase to 100 at first valid close.
    let base: number | null = null;
    for (const v of rawCloses) {
      if (v != null && Number.isFinite(v) && Math.abs(v) > 1e-9) {
        base = v;
        break;
      }
    }
    const rebased: Array<number | null> = base == null
      ? rawCloses.map(() => null)
      : rawCloses.map((v) =>
          v == null || !Number.isFinite(v) ? null : (v / base) * 100,
        );
    return { name: ds.name, color: ds.color, rebased };
  });

  // --- Trading amount stacked bars (proportional aggregation) ----------
  // Per-index trading amount (亿元) aligned to allDates.
  const amtLookups = visible.map((ds) => {
    const m = new Map<string, number>();
    for (const r of ds.rows) {
      if (r.trading_amount != null && Number.isFinite(r.trading_amount)) {
        m.set(r.date, r.trading_amount / 1e8);
      }
    }
    return m;
  });
  const amtSeriesData = amtLookups.map((lookup) =>
    allDates.map((d) => {
      const v = lookup.get(d);
      return v == null ? 0 : v;
    }),
  );
  // Per-date total for tooltip.
  const totalsByDate = allDates.map((_, i) =>
    amtSeriesData.reduce((sum, arr) => sum + (arr[i] ?? 0), 0),
  );

  // --- X-axis: year-month ticks (3-month interval) --------------------
  const displayMonths = new Set<string>();
  {
    const orderedMonths: string[] = [];
    const seen = new Set<string>();
    for (const d of allDates) {
      const ym = d.slice(0, 7);
      if (!seen.has(ym)) {
        seen.add(ym);
        orderedMonths.push(ym);
      }
    }
    for (let i = 0; i < orderedMonths.length; i += 3) {
      displayMonths.add(orderedMonths[i]);
    }
  }
  const firstDateOfMonth = new Set<string>();
  {
    let prev = "";
    for (const d of allDates) {
      const ym = d.slice(0, 7);
      if (ym !== prev) {
        firstDateOfMonth.add(d);
        prev = ym;
      }
    }
  }

  // --- Series: stacked bars first (z=1), then close lines (z=3) -------
  const series: EChartsOption["series"] = [
    // Trading amount stacked bars (one bar series per visible index).
    ...visible.map((ds, di) => ({
      name: `${ds.name} Amt`,
      type: "bar" as const,
      stack: "market_trend_amt",
      yAxisIndex: 1,
      data: amtSeriesData[di],
      itemStyle: { color: ds.color, opacity: 0.35 },
      emphasis: { focus: "series" as const },
      barWidth: "90%",
      z: 1 + di,
    })),
    // Close lines (rebased to 100).
    ...closeSeries.map((s, i) => ({
      name: s.name,
      type: "line" as const,
      smooth: false,
      showSymbol: false,
      yAxisIndex: 0,
      data: s.rebased,
      lineStyle: { width: 2, color: s.color },
      itemStyle: { color: s.color },
      z: 10 + i,
    })),
  ];

  return {
    backgroundColor: "transparent",
    animation: false,
    grid: commonGrid({ left: 56, right: 56, bottom: 32, top: 32 }),
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "cross", snap: true },
      backgroundColor: c.tooltipBg,
      borderColor: c.splitLineColor,
      textStyle: { color: c.textColor, fontSize: 11 },
      formatter: (params: unknown) => {
        const arr = (Array.isArray(params) ? params : [params]) as Array<{
          dataIndex?: number;
          axisValue?: string;
          marker?: string;
          seriesName?: string;
          value?: number | null;
        }>;
        if (arr.length === 0) return "";
        const idx0 = arr[0].dataIndex ?? 0;
        const dateStr = allDates[idx0] ?? (arr[0].axisValue as string) ?? "";
        if (!dateStr) return "";
        const total = totalsByDate[idx0] ?? 0;
        let html = `<div style="font-weight:600">${dateStr}</div>`;
        if (total > 0) {
          html += `<div style="margin-top:2px;opacity:0.7">Total Amt: ${fmtNum(total)} 亿</div>`;
        }
        // Separate close-line entries from amount-bar entries.
        const closeRows: string[] = [];
        const amtRows: string[] = [];
        for (const p of arr) {
          const name = p.seriesName ?? "";
          const v = p.value;
          if (v == null || !Number.isFinite(v)) continue;
          if (name.endsWith(" Amt")) {
            const pct = total > 0 ? (v / total) * 100 : 0;
            amtRows.push(
              `<div>${p.marker ?? ""} ${name.replace(" Amt", "")}: <b>${fmtNum(v)} 亿</b> (${fmtNum(pct, 1)}%)</div>`,
            );
          } else {
            const pct = v - 100;
            closeRows.push(
              `<div>${p.marker ?? ""} ${name}: <b style="color:${p.marker?.includes("color") ? "" : ""}">${pct >= 0 ? "+" : ""}${fmtNum(pct, 2)}%</b></div>`,
            );
          }
        }
        if (closeRows.length) html += `<div style="margin-top:4px">${closeRows.join("")}</div>`;
        if (amtRows.length) html += `<div style="margin-top:4px;opacity:0.85">${amtRows.join("")}</div>`;
        return html;
      },
    },
    legend: commonLegend(themeMode, {
      data: [
        ...closeSeries.map((s) => s.name),
        ...visible.map((ds) => `${ds.name} Amt`),
      ],
    }),
    xAxis: {
      type: "category",
      data: allDates,
      boundaryGap: true,
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: {
        color: c.textColor,
        fontSize: 9,
        interval: (_idx: number, value: string) =>
          displayMonths.has(value.slice(0, 7)) && firstDateOfMonth.has(value),
        formatter: (v: string) => v.slice(0, 7),
      },
      splitLine: { show: false },
    },
    yAxis: [
      {
        type: "value",
        scale: true,
        name: "Rebased (start = 100)",
        nameTextStyle: { color: c.textColor, fontSize: 9 },
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: {
          color: c.textColor,
          fontSize: 9,
          formatter: (v: number) => fmtNum(v, 0),
        },
        splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.4 } },
      },
      {
        type: "value",
        scale: true,
        name: "Trading Amt (亿)",
        nameTextStyle: { color: c.textColor, fontSize: 9 },
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: { color: c.textColor, fontSize: 9, formatter: (v: number) => fmtNum(v) },
        splitLine: { show: false },
      },
    ],
    series,
  };
}

/**
 * Build a per-index OHLC chart option (IndexPanel style).
 *
 * Series: OHLC (shared `ohlcSeries`) + MA5/MA20/MA60/MA120 + trading amount
 * bars (twin right axis, 亿元). OHLC + MAs are rebased to % change from the
 * first valid close in "percentage" mode. Trading amount is never rebased.
 */
export function buildMarketOhlcOption(
  data: IndexSeriesData,
  ohlcMode: OhlcMode,
  themeMode: ThemeMode,
): EChartsOption {
  const c = axisColors(themeMode);
  const rows = data.rows;
  const dates = rows.map((r) => r.date);
  const open = rows.map((r) => r.open);
  const high = rows.map((r) => r.high);
  const low = rows.map((r) => r.low);
  const close = rows.map((r) => r.close);
  // trading_amount is stored in yuan — convert to 亿元 for display.
  const tradingAmount = rows.map((r) =>
    r.trading_amount == null ? null : r.trading_amount / 1e8,
  );
  const ma5 = rows.map((r) => r.ma5);
  const ma20 = rows.map((r) => r.ma20);
  const ma60 = rows.map((r) => r.ma60);
  const ma120 = rows.map((r) => r.ma120);

  // Rebase price-derived arrays (OHLC + MAs) to % change in percentage mode.
  const { rebased } = rebasePriceArrays(
    { open, high, low, close, ma5, ma20, ma60, ma120 },
    ohlcMode,
  );

  const broken = breakArraysAtGaps(dates, [
    rebased.open, rebased.high, rebased.low, rebased.close,
    rebased.ma5, rebased.ma20, rebased.ma60, rebased.ma120,
    tradingAmount,
  ]);

  // Trading amount bars coloured by price-up/down.
  const amtData = broken.arrays[8].map((v, i) => {
    const o = broken.arrays[0][i];
    const cl = broken.arrays[3][i];
    const up = o != null && cl != null ? cl >= o : true;
    return { value: v, itemStyle: { color: up ? UP_COLOR : DOWN_COLOR, opacity: 0.35 } };
  });

  // OHLC data order: [open, close, low, high] (matches ohlcRenderItem).
  const candleData: Array<Array<number | null>> = broken.dates.map((_, i) => [
    broken.arrays[0][i],
    broken.arrays[3][i],
    broken.arrays[2][i],
    broken.arrays[1][i],
  ]);

  // Detect whether OHLC is available — fall back to a close line otherwise.
  const ohlcCount = rows.filter(
    (r) => r.open != null && r.high != null && r.low != null && r.close != null,
  ).length;
  const hasOhlc = ohlcCount > 0 && ohlcCount >= rows.length * 0.5;

  const isPriceSeries = (name: string) =>
    name === "OHLC" || name === "Close" || name.startsWith("MA");

  return {
    backgroundColor: "transparent",
    animation: false,
    grid: commonGrid({ left: 50, right: 50, bottom: 28, top: 32 }),
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "cross", snap: true },
      backgroundColor: c.tooltipBg,
      borderColor: c.splitLineColor,
      textStyle: { color: c.textColor, fontSize: 11 },
      formatter: (params: unknown) => {
        const arr = (Array.isArray(params) ? params : [params]) as Array<{
          axisValue?: string;
          marker?: string;
          seriesName?: string;
          value?: number | Array<number | string | null>;
        }>;
        if (arr.length === 0) return "";
        const dateStr = (arr[0].axisValue as string) || "";
        let html = `<div style="font-weight:600;margin-bottom:4px">${data.name} · ${dateStr}</div>`;
        for (const p of arr) {
          if (p.value == null) continue;
          const name = p.seriesName ?? "";
          if (Array.isArray(p.value)) {
            const [o, cl, l, h] = p.value as Array<number | null>;
            if (o == null && cl == null && l == null && h == null) continue;
            html += `<div>${p.marker ?? ""} ${name}: O=${formatPriceValue(o, ohlcMode)} H=${formatPriceValue(h, ohlcMode)} L=${formatPriceValue(l, ohlcMode)} C=${formatPriceValue(cl, ohlcMode)}</div>`;
          } else {
            const v = p.value as number;
            if (!Number.isFinite(v)) continue;
            const vstr = isPriceSeries(name)
              ? formatPriceValue(v, ohlcMode)
              : fmtNum(v);
            const unit = name === "Trading Amt" ? " (亿元)" : "";
            html += `<div>${p.marker ?? ""} ${name}: <b>${vstr}${unit}</b></div>`;
          }
        }
        return html;
      },
    },
    legend: commonLegend(themeMode, { type: "scroll" }),
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
    yAxis: [
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
      {
        type: "value",
        scale: true,
        name: "Trading Amt (亿)",
        nameTextStyle: { color: c.textColor, fontSize: 9 },
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: { color: c.textColor, fontSize: 9, formatter: (v: number) => fmtNum(v) },
        splitLine: { show: false },
      },
    ],
    series: [
      ...(hasOhlc
        ? [ohlcSeries(candleData, { name: "OHLC", yAxisIndex: 0, z: 5 })]
        : [{
            type: "line" as const,
            name: "Close",
            yAxisIndex: 0,
            data: broken.arrays[3],
            smooth: false,
            symbol: "none",
            lineStyle: { color: data.color, width: 1.3 },
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
      {
        type: "line",
        name: "MA20",
        yAxisIndex: 0,
        data: broken.arrays[5],
        smooth: false,
        symbol: "none",
        lineStyle: { color: MA20_COLOR, width: 0.9 },
        z: 4,
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
      {
        type: "bar",
        name: "Trading Amt",
        yAxisIndex: 1,
        data: amtData,
        barWidth: "90%",
        z: 1,
      },
    ],
  };
}

/** Build the IndexSeriesData view used by both option builders. */
export function toIndexSeriesData(
  code: string,
  rows: IndexBaselineRow[],
): IndexSeriesData {
  const meta = MARKET_TREND_INDICES.find((m) => m.code === code);
  return {
    code,
    name: meta?.name ?? code,
    color: meta?.color ?? "#2980b9",
    rows,
  };
}
