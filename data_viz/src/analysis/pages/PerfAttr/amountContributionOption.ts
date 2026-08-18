/**
 * Build the ECharts option for the Index Trading Amt Contribution chart
 * (ETF-market turnover over time).
 *
 * Renders two area lines comparing benchmark vs subject INDEX-LEVEL ETF
 * turnover (per-index aggregate from stats.index_exts, precomputed by
 * build_index_exts.py = Σ etf_liquidity_margin.trading_amount across ALL
 * ETFs tracking each index via stats.sec_classification.parent_index_code).
 *
 * A display-mode toggle (chartMode) applies to both expanded plots (this
 * one and the close-price comparison):
 *   • "absolute"   — raw 亿元 values on a shared y-axis (default). Best for
 *                    comparing the relative SIZE of the two ETF markets.
 *   • "percentage" — both curves rebased to 0% at the first date where both
 *                    have non-null, non-zero amounts. Best for comparing
 *                    relative GROWTH in turnover over time. The tooltip still
 *                    surfaces the raw 亿元 value in parentheses.
 *
 * The tooltip surfaces the bench/code liquidity ratio (DB-GENERATED
 * etf_trading_amount_ratio_benchmark_to_code), the subject's share (1/ratio), AND
 * the 5-day moving average of the ratio (etf_trading_amount_ratio_benchmark_to_code_ma5,
 * precomputed by analyze_sec_alloc_perf_attribution.py) as a dedicated line
 * — the former standalone MA5 chart has been consolidated into this tooltip.
 * Shares the same date range (slider-sliced `filteredChartData`) as the
 * close-price plot.
 */
import type { EChartsOption } from "echarts";
import type { ThemeMode } from "@/store/filters";
import type { PerfAttrChartResponse } from "@shared/types";
import {
  MUTED_PALETTE,
  SUBTITLE_COLOR,
  axisColors,
  commonDataZoom,
  commonLegend,
  commonGrid,
} from "@/theme/chart-palette";
import { fmtNum } from "@/lib/series";
import type { ChartMode } from "./types";

export function buildAmountContributionOption(
  data: PerfAttrChartResponse,
  themeMode: ThemeMode,
  chartMode: ChartMode = "absolute",
): EChartsOption {
  const c = axisColors(themeMode);
  const dates = data.rows.map((r) => r.date);
  // Divide yuan by 1e8 → 亿元 for readable y-axis values.
  const benchmarkAmountsRaw = data.rows.map((r) =>
    r.benchmark_etf_trading_amount == null ? null : r.benchmark_etf_trading_amount / 1e8,
  );
  const codeAmountsRaw = data.rows.map((r) =>
    r.code_etf_trading_amount == null ? null : r.code_etf_trading_amount / 1e8,
  );
  // Watermark condition: no ETFs linked to either the benchmark or the code
  // (subject) index. Both linked_etfs arrays are empty → the "Index Trading
  // Amt contribution" concept is meaningless for this pair.
  const noEtfLinked =
    data.benchmark_linked_etfs.length === 0 && data.code_linked_etfs.length === 0;
  const benchmarkEtfNums = data.rows.map((r) => r.benchmark_etf_num);
  const codeEtfNums = data.rows.map((r) => r.code_etf_num);
  // DB-generated liquidity ratio (benchmark_etf_trading_amount / code_etf_trading_amount).
  const ratios = data.rows.map((r) => r.etf_trading_amount_ratio);
  // 5-day moving average of the ratio (precomputed by the analyze script).
  const ratioMa5s = data.rows.map((r) => r.etf_trading_amount_ratio_ma5);

  const subjectName = data.name || data.code;
  const benchmarkName = data.benchmark_name || data.benchmark_code;
  const benchLabel = `${benchmarkName} ETF Amt`;
  const codeLabel = `${subjectName} ETF Amt`;

  // ---- Percentage mode: rebase each curve independently to 0% at the first
  //      date where it has a non-null, non-zero amount. This ensures that a
  //      benchmark with no tracking ETF (all-null benchmark_etf_trading_amount) does
  //      NOT blank out the code ETF amount line — each series gets its own
  //      base. When both share the same first date, they naturally align at
  //      0% together. ----
  const isPercentage = chartMode === "percentage";
  let benchmarkAmounts: (number | null)[] = benchmarkAmountsRaw;
  let codeAmounts: (number | null)[] = codeAmountsRaw;
  let baseDate: string | null = null;

  if (isPercentage) {
    // Find first non-null, non-zero for benchmark.
    let benchBaseIdx = -1;
    let benchBase: number | null = null;
    for (let i = 0; i < benchmarkAmountsRaw.length; i++) {
      const b = benchmarkAmountsRaw[i];
      if (b != null && b !== 0) {
        benchBaseIdx = i;
        benchBase = b;
        break;
      }
    }
    // Find first non-null, non-zero for code.
    let codeBaseIdx = -1;
    let codeBase: number | null = null;
    for (let i = 0; i < codeAmountsRaw.length; i++) {
      const co = codeAmountsRaw[i];
      if (co != null && co !== 0) {
        codeBaseIdx = i;
        codeBase = co;
        break;
      }
    }
    // Rebase benchmark series.
    if (benchBaseIdx >= 0 && benchBase != null) {
      baseDate = dates[benchBaseIdx];
      benchmarkAmounts = benchmarkAmountsRaw.map((v, i) => {
        if (i < benchBaseIdx || v == null) return null;
        return (v / benchBase! - 1) * 100;
      });
    } else {
      benchmarkAmounts = benchmarkAmountsRaw.map(() => null);
    }
    // Rebase code series independently.
    if (codeBaseIdx >= 0 && codeBase != null) {
      if (baseDate == null) baseDate = dates[codeBaseIdx];
      codeAmounts = codeAmountsRaw.map((v, i) => {
        if (i < codeBaseIdx || v == null) return null;
        return (v / codeBase! - 1) * 100;
      });
    } else {
      codeAmounts = codeAmountsRaw.map(() => null);
    }
  }

  const fmtPct = (v: number | null): string =>
    v == null ? "—" : (v >= 0 ? "+" : "") + fmtNum(v, 2) + "%";

  const yAxisName = isPercentage
    ? (baseDate ? `% change (base: ${baseDate})` : "% change")
    : "Index ETF Amt (亿元)";
  const yAxisLabelFormatter = isPercentage
    ? (v: number) => (v >= 0 ? "+" : "") + fmtNum(v, 1) + "%"
    : (v: number) => fmtNum(v, 1);

  return {
    backgroundColor: "transparent",
    animation: false,
    grid: commonGrid({ left: 56, right: 56, bottom: 50 }),
    dataZoom: commonDataZoom(),
    // Watermark shown when neither the benchmark nor the subject index has
    // any tracking ETF — the "Index Trading Amt contribution" concept is
    // meaningless for this pair.
    graphic: noEtfLinked
      ? ({
          type: "text",
          left: "center",
          top: "middle",
          style: {
            text: "no etf linked to both selected indices",
            fontSize: 13,
            fontWeight: 500,
            fill: SUBTITLE_COLOR,
            opacity: 0.5,
            textAlign: "center" as const,
          },
        } as EChartsOption["graphic"])
      : undefined,
    tooltip: {
      trigger: "axis",
      backgroundColor: c.tooltipBg,
      borderColor: c.splitLineColor,
      textStyle: { color: c.textColor, fontSize: 11 },
      formatter: (params: unknown) => {
        const arr = (Array.isArray(params) ? params : [params]) as Array<{
          dataIndex?: number;
        }>;
        if (arr.length === 0) return "";
        const idx = arr[0].dataIndex ?? 0;
        const ba = benchmarkAmounts[idx];
        const ca = codeAmounts[idx];
        const baRaw = benchmarkAmountsRaw[idx];
        const caRaw = codeAmountsRaw[idx];
        const er = ratios[idx];
        const erMa5 = ratioMa5s[idx];
        const benchNum = benchmarkEtfNums[idx];
        const codeNum = codeEtfNums[idx];
        // Subject's SHARE of the benchmark ETF market = 1 / ratio (only
        // meaningful when both amounts are non-null and ratio > 0).
        const share = er == null || er === 0 ? null : 1 / er;
        const shareMa5 = erMa5 == null || erMa5 === 0 ? null : 1 / erMa5;
        // In percentage mode the main value is the % change, with the raw
        // 亿元 shown in parentheses for reference. In absolute mode the raw
        // 亿元 is the main value.
        const benchValStr = isPercentage
          ? `${fmtPct(ba)} <span style="opacity:0.5">(${baRaw == null ? "—" : fmtNum(baRaw, 2) + " 亿"})</span>`
          : `${ba == null ? "—" : fmtNum(ba, 2) + " 亿"}`;
        const codeValStr = isPercentage
          ? `${fmtPct(ca)} <span style="opacity:0.5">(${caRaw == null ? "—" : fmtNum(caRaw, 2) + " 亿"})</span>`
          : `${ca == null ? "—" : fmtNum(ca, 2) + " 亿"}`;
        return `
          <div style="font-weight:600">${dates[idx]}</div>
          <div style="margin-top:2px">${benchLabel}: <b style="color:${MUTED_PALETTE[1]}">${benchValStr}</b>${benchNum == null ? "" : ` <span style="opacity:0.6">(${benchNum} ETFs)</span>`}</div>
          <div>${codeLabel}: <b style="color:${MUTED_PALETTE[0]}">${codeValStr}</b>${codeNum == null ? "" : ` <span style="opacity:0.6">(${codeNum} ETFs)</span>`}</div>
          <div style="margin-top:2px;opacity:0.85">Ratio (bench/code): <b style="color:${MUTED_PALETTE[1]}">${er == null ? "—" : fmtNum(er, 4)}</b>${share == null ? "" : ` · share ${fmtNum(share, 4)}`}</div>
          <div style="opacity:0.85">MA5 Ratio: <b style="color:${MUTED_PALETTE[0]}">${erMa5 == null ? "—" : fmtNum(erMa5, 4)}</b>${shareMa5 == null ? "" : ` · share ${fmtNum(shareMa5, 4)}`}</div>
        `;
      },
    },
    legend: commonLegend(themeMode, {
      itemWidth: 12,
      itemHeight: 7,
      data: [benchLabel, codeLabel],
      formatter: (name: string) => (name.length > 16 ? name.slice(0, 15) + "…" : name),
    }),
    xAxis: {
      type: "category",
      data: dates,
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: {
        color: c.textColor,
        fontSize: 9,
        formatter: (v: string) => v.slice(0, 7),
      },
      splitLine: { show: false },
    },
    yAxis: {
      type: "value",
      name: yAxisName,
      nameTextStyle: { color: c.textColor, fontSize: 9 },
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: {
        color: c.textColor,
        fontSize: 9,
        formatter: yAxisLabelFormatter,
      },
      splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.4 } },
    },
    series: [
      {
        name: benchLabel,
        type: "line",
        showSymbol: false,
        smooth: false,
        data: benchmarkAmounts,
        lineStyle: { width: 1.5, color: MUTED_PALETTE[1] },
        itemStyle: { color: MUTED_PALETTE[1] },
        connectNulls: true,
        areaStyle: { opacity: 0.12 },
      },
      {
        name: codeLabel,
        type: "line",
        showSymbol: false,
        smooth: false,
        data: codeAmounts,
        lineStyle: { width: 1.5, color: MUTED_PALETTE[0] },
        itemStyle: { color: MUTED_PALETTE[0] },
        connectNulls: true,
        areaStyle: { opacity: 0.12 },
      },
    ],
  };
}
