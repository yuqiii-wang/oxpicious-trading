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
 */
import { useMemo } from "react";
import EChart from "@/components/EChart";
import { useStore } from "@/store/filters";
import { breakArraysAtGaps, fmtNum, safeMa } from "@/lib/series";
import {
  ohlcSeries,
  rebasePriceArrays,
  formatPriceValue,
  type OhlcMode,
} from "@/lib/ohlc";
import {
  MA5_COLOR,
  MA20_COLOR,
  MA60_COLOR,
  MA120_COLOR,
  MUTED_PALETTE,
  PE_COLOR,
  axisColors,
  commonLegend,
  commonGrid,
} from "@/theme/chart-palette";
import type { StockBaselineRow } from "../../shared/types";
import type { EChartsOption } from "echarts";

interface Props {
  /** Daily OHLC + PE rows for one stock (already windowed by the caller). */
  rows: StockBaselineRow[];
  /** OHLC display mode — "percentage" rebases OHLC + MAs to % change from the
   *  first valid close; "absolute" shows raw prices. */
  ohlcMode: OhlcMode;
  /** Chart height in px. Defaults to 250 (matches StockPanel). */
  height?: number;
}

export default function StockOhlcChart({ rows, ohlcMode, height = 250 }: Props) {
  const themeMode = useStore((s) => s.themeMode);

  const option = useMemo<EChartsOption>(() => {
    const c = axisColors(themeMode);
    const dates = rows.map((r) => r.date);
    const open = rows.map((r) => r.open);
    const high = rows.map((r) => r.high);
    const low = rows.map((r) => r.low);
    const close = rows.map((r) => r.close);
    const pe = rows.map((r) => r.pe);
    const isPeEstimatedNum = rows.map((r) => (r.is_pe_estimated ? 1 : 0));
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
    ]);

    // Data order: [open, close, low, high] (low before high — matches the
    // shared ohlcRenderItem destructuring `const [o, cl, l, h] = value`).
    const candleData: Array<Array<number | null>> = broken.dates.map((_, i) => [
      broken.arrays[0][i],
      broken.arrays[3][i],
      broken.arrays[2][i],
      broken.arrays[1][i],
    ]);

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
    if (hasPe) {
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
    ];
    if (hasPe) {
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
        yAxisIndex: 1,
        data: peActual,
        smooth: false,
        symbol: "none",
        lineStyle: { color: PE_COLOR, width: 1.1, opacity: 0.85 },
        z: 6,
      });
      series.push({
        type: "line",
        name: "PE (est)",
        yAxisIndex: 1,
        data: peEstimated,
        smooth: false,
        symbol: "none",
        connectNulls: false,
        lineStyle: { color: PE_COLOR, width: 1.1, opacity: 0.4 },
        z: 6,
      });
    }

    return {
      backgroundColor: "transparent",
      animation: false,
      grid: commonGrid({ left: 50, right: hasPe ? 60 : 50, bottom: 28 }),
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
            value?: Array<number | null> | number;
          }>;
          if (arr.length === 0) return "";
          const dateStr = (arr[0].axisValue as string) || "";
          let html = `<div style="font-weight:600;margin-bottom:4px">${dateStr}</div>`;
          const isPriceSeries = (name: string) =>
            name === "OHLC" || name === "Close" || name.startsWith("MA");
          for (const p of arr) {
            if (p.value == null) continue;
            const name = p.seriesName ?? "";
            if (Array.isArray(p.value)) {
              const [o, cl, l, h] = p.value;
              if (o == null && cl == null && l == null && h == null) continue;
              html += `<div>${p.marker ?? ""} ${name}: O=${formatPriceValue(o, ohlcMode)} H=${formatPriceValue(h, ohlcMode)} L=${formatPriceValue(l, ohlcMode)} C=${formatPriceValue(cl, ohlcMode)}</div>`;
            } else {
              const v = p.value as number;
              if (!Number.isFinite(v)) continue;
              const vstr = isPriceSeries(name)
                ? formatPriceValue(v, ohlcMode)
                : name === "PE" || name === "PE (est)" ? fmtNum(v, 2) : fmtNum(v);
              html += `<div>${p.marker ?? ""} ${name}: <b>${vstr}</b></div>`;
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
      yAxis,
      series,
    };
  }, [rows, themeMode, ohlcMode]);

  return <EChart option={option} height={height} />;
}
