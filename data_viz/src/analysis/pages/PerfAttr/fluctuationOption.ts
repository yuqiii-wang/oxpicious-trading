/**
 * Build the ECharts option for the Fluctuation Attribution chart.
 *
 * Vertical grouped bar chart — one pair of bars per benchmark:
 *   Bar 1 (left  Y-axis): shared-weight contribution = benchmark_return ×
 *                         (code_sec_shared_weight / 100). Both values are
 *                         FRACTIONAL (benchmark_return is a fractional daily
 *                         return, e.g. 0.0125 = +1.25%; shared_weight is in
 *                         %, e.g. 57.166). The product is a scale-invariant
 *                         return attribution: "the benchmark's % return scaled
 *                         by how much the subject overlaps via composition."
 *                         Green if benchmark rose, red if dropped.
 *   Bar 2 (right Y-axis): code_sec_shared_weight (% overlap with subject).
 *
 * Computed on-the-fly from PerfAttrAttributionResponse (latest date per
 * benchmark). Returns are NOT stored in the DB — they're computed in the
 * attribution SQL via LATERAL joins to stats.index_basic_stats.
 *
 * Clicking a bar selects that benchmark for the time-series charts below.
 */
import type { EChartsOption } from "echarts";
import type { ThemeMode } from "@/store/filters";
import type { PerfAttrAttributionResponse } from "../../../../shared/types";
import {
  UP_COLOR,
  DOWN_COLOR,
  MUTED_PALETTE,
  SUBTITLE_COLOR,
  axisColors,
  commonLegend,
  commonGrid,
} from "@/theme/chart-palette";
import { fmtNum, fmtYi } from "@/lib/series";

export function buildFluctuationOption(
  data: PerfAttrAttributionResponse,
  themeMode: ThemeMode,
  showBroadMarket = true,
): EChartsOption {
  const c = axisColors(themeMode);
  // Sort by effective contribution (fractional return × overlap fraction).
  let sorted = [...data.benchmarks].sort((a, b) => {
    const ar = a.benchmark_return ?? 0;
    const br = b.benchmark_return ?? 0;
    const aw = a.code_sec_shared_weight ?? 0;
    const bw = b.code_sec_shared_weight ?? 0;
    const aeff = ar * (aw / 100);
    const beff = br * (bw / 100);
    if (aeff >= 0 && beff < 0) return -1;
    if (aeff < 0 && beff >= 0) return 1;
    return beff - aeff;
  });

  if (!showBroadMarket) {
    sorted = sorted.filter((b) => b.is_broad_market !== true);
  }

  // Drop benchmarks with no shared weight or null return.
  sorted = sorted.filter(
    (b) => b.code_sec_shared_weight != null && b.benchmark_return != null,
  );

  const labels = sorted.map((b) => b.benchmark_name || b.benchmark_code);
  const returns = sorted.map((b) => b.benchmark_return);
  const sharedWts = sorted.map((b) => b.code_sec_shared_weight);
  const benchmarkAmounts = sorted.map((b) => b.benchmark_etf_trading_amount);
  const codeAmounts = sorted.map((b) => b.code_etf_trading_amount);
  const etfRatios = sorted.map((b) => b.etf_trading_amount_ratio);
  const activeReturns = sorted.map((b) => b.active_return);
  const codes = sorted.map((b) => b.benchmark_code);

  // Contribution = fractional_return × (shared_weight / 100).
  const contrib = sorted.map((b, i) => {
    const r = returns[i];
    const w = sharedWts[i];
    if (r == null || w == null) return null as number | null;
    return r * (w / 100);
  });

  const maxAbsContrib = contrib.reduce(
    (m, v) => (v == null ? m : Math.max(m, Math.abs(v))),
    0,
  );
  const LABEL_MIN_RATIO = 0.08;

  const returnColors = returns.map((v) =>
    v == null ? c.axisLineColor : v >= 0 ? UP_COLOR : DOWN_COLOR,
  );

  const contribLabelPosition = (val: number | null) => {
    if (val == null) return "insideTop" as const;
    return val >= 0 ? ("insideTop" as const) : ("insideBottom" as const);
  };
  const contribLabelVisible = (val: number | null) => {
    if (val == null || maxAbsContrib === 0) return false;
    return Math.abs(val) / maxAbsContrib >= LABEL_MIN_RATIO;
  };

  // Format a fractional value as a signed percentage string.
  const fmtPctSigned = (v: number | null, digits = 2): string =>
    v == null ? "—" : (v >= 0 ? "+" : "") + fmtNum(v * 100, digits) + "%";

  return {
    backgroundColor: "transparent",
    animation: false,
    grid: commonGrid({ left: 64, right: 64, bottom: 96 }),
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      backgroundColor: c.tooltipBg,
      borderColor: c.splitLineColor,
      textStyle: { color: c.textColor, fontSize: 11 },
      formatter: (params: unknown) => {
        const arr = (Array.isArray(params) ? params : [params]) as Array<{
          dataIndex?: number;
        }>;
        if (arr.length === 0) return "";
        const idx = arr[0].dataIndex ?? 0;
        const rv = returns[idx];
        const cv = contrib[idx];
        const sw = sharedWts[idx];
        const ba = benchmarkAmounts[idx];
        const ca = codeAmounts[idx];
        const er = etfRatios[idx];
        const ar = activeReturns[idx];
        const sign = cv == null ? "" : cv >= 0 ? "▲ " : "▼ ";
        const rsign = rv == null ? "" : rv >= 0 ? "▲ " : "▼ ";
        const share = er == null || er === 0 ? null : 1 / er;
        return `
          <div style="font-weight:600">${labels[idx]} <span style="opacity:0.6">(${codes[idx]})</span></div>
          <div style="margin-top:2px">${sign}Contribution (Ret×Wt): <b style="color:${cv == null ? c.textColor : cv >= 0 ? UP_COLOR : DOWN_COLOR}">${fmtPctSigned(cv)}</b></div>
          <div>${rsign}Raw Return: ${fmtPctSigned(rv)}</div>
          <div>Active vs subject: ${fmtPctSigned(ar)}</div>
          <div>Shared wt (in subject): ${sw == null ? "—" : fmtNum(sw, 4) + "%"}</div>
          <div>Benchmark ETF Trading Amt: ${ba == null ? "—" : fmtYi(ba, 2)}</div>
          <div>Code ETF Trading Amt: ${ca == null ? "—" : fmtYi(ca, 2)}</div>
          <div>ETF Amt Ratio (bench/code): ${er == null ? "—" : fmtNum(er, 4)}${share == null ? "" : ` · share ${fmtNum(share, 4)}`}</div>
        `;
      },
    },
    legend: commonLegend(themeMode, { itemWidth: 12, itemHeight: 7, data: ["Contribution", "Shared Wt"] }),
    xAxis: {
      type: "category",
      data: labels,
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: {
        color: c.textColor,
        fontSize: 8,
        interval: 0,
        rotate: 55,
        formatter: (v: string, i: number) => {
          const lbl = v.length > 6 ? v.slice(0, 5) + "…" : v;
          return sorted[i].is_broad_market === true
            ? `{light|${lbl}}`
            : lbl;
        },
        rich: {
          light: { color: SUBTITLE_COLOR, fontSize: 8 },
        },
      },
      splitLine: { show: false },
    },
    yAxis: [
      {
        type: "value",
        name: "Contribution",
        nameTextStyle: { color: c.textColor, fontSize: 9 },
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: {
          color: c.textColor,
          fontSize: 9,
          formatter: (v: number) => fmtPctSigned(v, 2),
        },
        splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.4 } },
      },
      {
        type: "value",
        name: "Shared Wt %",
        nameTextStyle: { color: c.textColor, fontSize: 9 },
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: {
          color: c.textColor,
          fontSize: 9,
          formatter: (v: number) => fmtNum(v, 1) + "%",
        },
        splitLine: { show: false },
      },
    ],
    series: [
      {
        name: "Contribution",
        type: "bar",
        yAxisIndex: 0,
        data: contrib.map((v, i) => {
          const visible = contribLabelVisible(v);
          const pos: "insideTop" | "insideBottom" = contribLabelPosition(v);
          const raw = returns[i];
          const rawStr =
            raw == null ? "" : `  [${raw >= 0 ? "▲" : "▼"}${fmtNum(raw * 100, 2)}%]`;
          const lblText =
            visible && v != null ? fmtPctSigned(v, 2) + rawStr : "";
          const broad = sorted[i].is_broad_market === true;
          return {
            value: v,
            benchmarkCode: codes[i],
            itemStyle: {
              color: returnColors[i],
              opacity: broad ? 0.4 : 1.0,
            },
            label: {
              show: visible,
              position: pos,
              distance: 2,
              color: broad ? SUBTITLE_COLOR : c.textColor,
              fontSize: 8,
              fontWeight: broad ? 400 : 600,
              formatter: () => lblText,
            },
          };
        }),
        barMaxWidth: 28,
        label: {
          show: false,
          color: c.textColor,
          fontSize: 8,
        },
      },
      {
        name: "Shared Wt",
        type: "bar",
        yAxisIndex: 1,
        data: sharedWts.map((v, i) => {
          const broad = sorted[i].is_broad_market === true;
          return {
            value: v,
            benchmarkCode: codes[i],
            itemStyle: {
              color: MUTED_PALETTE[5],
              opacity: broad ? 0.28 : 0.7,
            },
            label: {
              show: !(v == null || v < 1.5),
              position: "insideTop",
              distance: 2,
              color: broad ? SUBTITLE_COLOR : c.textColor,
              fontSize: 8,
              formatter: () =>
                v == null || v < 1.5 ? "" : fmtNum(v, 1) + "%",
            },
          };
        }),
        barMaxWidth: 28,
      },
    ],
  };
}
