/**
 * Build the ECharts option for the Benchmark Price line chart (1st plot in
 * "Benchmark Attribution" mode).
 *
 * Shows the selected benchmark's daily close as a line. When `industryShades`
 * is non-empty, also overlays each industry's non-this-industry curve as a
 * line + green/red shaded area between it and the benchmark curve:
 *   • Green shade (UP_COLOR): non-this-industry ABOVE benchmark → the
 *     industry's shared stocks were a DRAG on the benchmark (benchmark would
 *     be higher without them).
 *   • Red shade (DOWN_COLOR): non-this-industry BELOW benchmark → the
 *     industry's shared stocks were a BOOST to the benchmark.
 *
 * INDUSTRY CURVE FORMULA (consistent across both modes)
 *   industry(t) = benchmark_close(t) × non_this_industry_rolling_price(t) / 100
 *
 *   - non_this_industry_rolling_price is the 100-based cumulative product of
 *     (1 + non_industry_return) from the benchmark's first close. On the
 *     first date it equals 100, so industry == benchmark (gap = 0).
 *   - Scaling by benchmark_close / 100 puts the curve on the benchmark's
 *     PRICE scale ("src benchmark price + cumulative non-industry changes"),
 *     NOT on a separate 100-based scale.
 *
 *   This ensures Absolute and Percentage modes show the SAME gap (just
 *   scaled by 100 / first_close in Percentage mode). The previous design
 *   used `non_this_industry_price` (daily snapshot) in Absolute mode and
 *   `non_this_industry_rolling_price` rebased separately to 100 in
 *   Percentage mode — two different time series with different rebasing,
 *     which produced inconsistent gaps.
 *
 * The shade is implemented via the same stack trick used by MaSpreadPage:
 *   base = min(benchmark, non_industry) — invisible stack layer
 *   pos  = max(non_industry - benchmark, 0) — green area on top of base
 *   neg  = max(benchmark - non_industry, 0) — red area on top of base
 * Each industry uses its own stack ID so shades can overlap.
 *
 * A vertical markLine indicates the currently selected date. The chart is
 * clickable — onCanvasClick fires with the x-axis category index.
 *
 * priceMode:
 *   "today"   — raw close (industry = close × rolling / 100, same scale).
 *   "rolling" — both benchmark and industry rebased to 100 at the BENCHMARK's
 *               first non-null close in the visible range (same base — no
 *               separate rebasing for the industry curve).
 */
import type { EChartsOption } from "echarts";
import type { ThemeMode } from "@/store/filters";
import type { BenchmarkPriceChartResponse } from "../../../../shared/types";
import {
  UP_COLOR,
  DOWN_COLOR,
  PALETTE_HI,
  MUTED_PALETTE,
  axisColors,
  commonLegend,
  commonGrid,
} from "@/theme/chart-palette";
import { fmtNum } from "@/lib/series";

/** One industry's shade series — values pre-aligned by index with the
 *  benchmark dates array. Always carries `non_this_industry_rolling_price`
 *  (the 100-based cumulative non-industry return factor). The option builder
 *  scales it to the benchmark's price level as `close × rolling / 100`. */
export interface IndustryShadeData {
  industry_id: string;
  industry_label: string;
  /** non_this_industry_rolling_price values, one per benchmark date (aligned
   *  by index). NULL where no non-this-industry data exists. */
  values: Array<number | null>;
}

/** Format a fractional value as a signed percentage string (e.g. 0.05 → "+5%"). */
function fmtPctSigned(v: number | null, digits = 2): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return (v >= 0 ? "+" : "") + fmtNum(v * 100, digits) + "%";
}

export function buildBenchmarkPriceOption(
  data: BenchmarkPriceChartResponse,
  themeMode: ThemeMode,
  selectedDate: string | null,
  industryShades: IndustryShadeData[] = [],
  priceMode: "rolling" | "today" = "today",
  range?: [number, number],
): EChartsOption {
  const c = axisColors(themeMode);
  const allDates = data.rows.map((r) => r.date);
  const allCloses = data.rows.map((r) => r.close);
  const allReturns = data.rows.map((r) => r.daily_return);
  const totalN = allDates.length;

  // ---- Apply visible range (slider) ----
  const startIdx = range ? Math.max(0, Math.min(range[0], totalN - 1)) : 0;
  const endIdx = range ? Math.max(startIdx, Math.min(range[1], totalN - 1)) : totalN - 1;
  const dates = allDates.slice(startIdx, endIdx + 1);
  const closes = allCloses.slice(startIdx, endIdx + 1);
  const returns = allReturns.slice(startIdx, endIdx + 1);
  const n = dates.length;

  // ---- Compute benchmark values based on priceMode ----
  // In "rolling" mode, rebase closes to 100 at the first non-null close
  // WITHIN the visible range. This is the SINGLE base used for BOTH the
  // benchmark line and the industry shade — no separate rebasing.
  let benchmarkValues: Array<number | null>;
  let firstClose: number | null = null;
  if (priceMode === "rolling") {
    firstClose = closes.find((v) => v != null && v !== 0) ?? null;
    benchmarkValues = closes.map((v) =>
      v != null && firstClose ? (v / firstClose) * 100 : null,
    );
  } else {
    benchmarkValues = closes;
  }

  // ---- Find the selected date index for markLine ----
  let selectedIdx = n - 1;
  if (selectedDate) {
    const found = dates.indexOf(selectedDate);
    if (found >= 0) selectedIdx = found;
  }

  // ---- Build series array ----
  const series: EChartsOption["series"] = [];

  // Collect each industry's display values + sliced rolling values so the
  // tooltip formatter can look them up by industry index.
  const indDisplayValuesByIndustry: Array<Array<number | null>> = [];
  const indRollingSliced: Array<Array<number | null>> = [];

  // 1. Benchmark line (visible, no stack)
  const benchmarkName = priceMode === "rolling" ? `${data.name} (100-based)` : data.name;
  series.push({
    name: benchmarkName,
    type: "line",
    data: benchmarkValues,
    showSymbol: false,
    symbol: "circle",
    symbolSize: 6,
    lineStyle: { color: PALETTE_HI, width: 1.5 },
    itemStyle: { color: PALETTE_HI },
    z: 10,
    markLine: {
      symbol: ["none", "none"],
      silent: true,
      label: {
        show: true,
        position: "insideEndTop",
        color: c.textColor,
        fontSize: 9,
        formatter: () => dates[selectedIdx] ?? "",
      },
      lineStyle: {
        color: UP_COLOR,
        type: "dashed",
        width: 1.5,
      },
      data: [{ xAxis: selectedIdx }],
    },
  });

  // 2. Per-industry shade series + visible line
  for (let i = 0; i < industryShades.length; i++) {
    const ind = industryShades[i];
    // Slice industry rolling_price values to match the visible range
    const rawRolling = ind.values.slice(startIdx, endIdx + 1);
    indRollingSliced.push(rawRolling);

    // ---- INDUSTRY CURVE = src benchmark price × rolling / 100 ----
    // On the first date rolling == 100 → industry == benchmark (gap = 0).
    // As cumulative non_industry_return diverges from benchmark_return,
    // rolling moves away from 100 and the gap grows.
    //
    // In "rolling" mode we rebase BOTH curves to 100 at the BENCHMARK's
    // first non-null close (firstClose) — same base, so the gap ratio is
    // preserved exactly across modes.
    const indValues: Array<number | null> = new Array(n).fill(null);
    for (let j = 0; j < n; j++) {
      const close = closes[j];
      const rolling = rawRolling[j];
      if (close == null || rolling == null) continue;
      indValues[j] = close * rolling / 100;
    }

    // Apply percentage rebasing to the industry curve using the SAME base
    // as the benchmark (firstClose). This preserves the gap ratio.
    let indDisplayValues: Array<number | null> = indValues;
    if (priceMode === "rolling" && firstClose != null) {
      indDisplayValues = indValues.map((v) =>
        v != null ? (v / firstClose) * 100 : null,
      );
    }
    indDisplayValuesByIndustry.push(indDisplayValues);

    const indColor = MUTED_PALETTE[i % MUTED_PALETTE.length];
    const stackId = `indShade_${i}`;

    // Compute base/pos/neg arrays for the shade (using display values so
    // the shade aligns with the visible curves).
    const baseData: Array<number | null> = new Array(n).fill(null);
    const posData: Array<number | null> = new Array(n).fill(null);
    const negData: Array<number | null> = new Array(n).fill(null);

    for (let j = 0; j < n; j++) {
      const bv = benchmarkValues[j];
      const iv = indDisplayValues[j];
      if (bv == null || iv == null) continue;
      const diff = iv - bv;
      baseData[j] = Math.min(bv, iv);
      if (diff >= 0) {
        posData[j] = diff;
        negData[j] = 0;
      } else {
        posData[j] = 0;
        negData[j] = -diff;
      }
    }

    // Industry visible line (on top, no stack)
    series.push({
      name: ind.industry_label,
      type: "line",
      data: indDisplayValues,
      showSymbol: false,
      lineStyle: { color: indColor, width: 1.2, type: "dashed" },
      itemStyle: { color: indColor },
      z: 8,
    });

    // Base (invisible) — stack layer
    series.push({
      name: `_base_${i}`,
      type: "line",
      data: baseData,
      stack: stackId,
      symbol: "none",
      lineStyle: { opacity: 0 },
      z: 1,
      tooltip: { show: false },
    });

    // Positive delta (green shade) — stack layer
    series.push({
      name: `_pos_${i}`,
      type: "line",
      data: posData,
      stack: stackId,
      symbol: "none",
      lineStyle: { opacity: 0 },
      areaStyle: { color: UP_COLOR, opacity: 0.4 },
      z: 2,
      tooltip: { show: false },
    });

    // Negative delta (red shade) — stack layer
    series.push({
      name: `_neg_${i}`,
      type: "line",
      data: negData,
      stack: stackId,
      symbol: "none",
      lineStyle: { opacity: 0 },
      areaStyle: { color: DOWN_COLOR, opacity: 0.4 },
      z: 2,
      tooltip: { show: false },
    });
  }

  // ---- Y-axis config depends on priceMode ----
  const yAxisName = priceMode === "rolling" ? "Rebased (100)" : "Close";

  // ---- Legend: benchmark + industry labels (skip internal _base/_pos/_neg) ----
  const legendData = [benchmarkName, ...industryShades.map((d) => d.industry_label)];

  return {
    backgroundColor: "transparent",
    animation: false,
    grid: commonGrid({ left: 64, right: 24, bottom: 48, top: 32 }),
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "cross" },
      backgroundColor: c.tooltipBg,
      borderColor: c.splitLineColor,
      textStyle: { color: c.textColor, fontSize: 11 },
      formatter: (params: unknown) => {
        const arr = (Array.isArray(params) ? params : [params]) as Array<{
          dataIndex?: number;
          seriesName?: string;
        }>;
        if (arr.length === 0) return "";
        const idx = arr[0].dataIndex ?? 0;
        const dt = dates[idx] ?? "—";
        const bv = benchmarkValues[idx];
        const rt = returns[idx];
        const rsign = rt == null ? "" : rt >= 0 ? "▲ " : "▼ ";
        let html = `
          <div style="font-weight:600">${data.name} (${data.code})</div>
          <div style="margin-top:2px">${dt}</div>
          <div>${priceMode === "rolling" ? "Rebased" : "Close"}: <b>${bv == null ? "—" : fmtNum(bv, 2)}</b></div>
          <div>${rsign}Daily Return: <b style="color:${rt == null ? c.textColor : rt >= 0 ? UP_COLOR : DOWN_COLOR}">${fmtPctSigned(rt, 2)}</b></div>
        `;
        // Append each industry's non-this-industry value + gap
        // Gap is expressed as a PERCENTAGE of the benchmark — this is
        // invariant across modes (rolling_price / 100 - 1).
        for (let i = 0; i < industryShades.length; i++) {
          const ind = industryShades[i];
          const rolling = indRollingSliced[i]?.[idx] ?? null;
          const iv = indDisplayValuesByIndustry[i]?.[idx] ?? null;
          if (rolling == null || iv == null) {
            html += `<div style="opacity:0.6">${ind.industry_label}: —</div>`;
          } else {
            // gap_pct = (industry - benchmark) / benchmark = rolling/100 - 1
            const gapPct = rolling / 100 - 1;
            const gapColor = gapPct >= 0 ? UP_COLOR : DOWN_COLOR;
            const gapSign = gapPct >= 0 ? "▲ " : "▼ ";
            html += `<div>${ind.industry_label}: <b>${fmtNum(iv, 2)}</b> <span style="opacity:0.7">${gapSign}gap: <b style="color:${gapColor}">${fmtPctSigned(gapPct, 2)}</b></span></div>`;
          }
        }
        return html;
      },
    },
    legend: commonLegend(themeMode, {
      itemWidth: 12,
      itemHeight: 7,
      data: legendData,
    }),
    xAxis: {
      type: "category",
      data: dates,
      boundaryGap: false,
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: {
        color: c.textColor,
        fontSize: 9,
        formatter: (v: string) => v.slice(5), // MM-DD (year in tooltip)
      },
      splitLine: { show: false },
    },
    yAxis: {
      type: "value",
      scale: true, // auto-scale to data range (don't start from 0) so shades are visible
      name: yAxisName,
      nameTextStyle: { color: c.textColor, fontSize: 9 },
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: {
        color: c.textColor,
        fontSize: 9,
        formatter: (v: number) => fmtNum(v, priceMode === "rolling" ? 0 : 0),
      },
      splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.4 } },
    },
    series,
  };
}
