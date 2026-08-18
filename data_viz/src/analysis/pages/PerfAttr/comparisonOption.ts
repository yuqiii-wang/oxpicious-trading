/**
 * Build the ECharts option for the Subject vs Benchmark close-price
 * comparison (two-line chart). Uses the /api/analysis/perf-attr/chart
 * endpoint which returns the full date series of subject_close +
 * benchmark_close (plus the four corr_Nd rolling correlations) for one
 * (code, benchmark_code) pair.
 *
 * Two display modes (toggled in the panel header):
 *   • "absolute"  — raw close prices on dual y-axes (subject left, benchmark
 *                   right).
 *   • "percentage" — both curves rebased to 0% at the first date where BOTH
 *                   have non-null closes, then plotted on a single shared
 *                   y-axis.
 *
 * The tooltip on each hovered date shows:
 *   • subject close (or % change)  • benchmark close (or % change)
 *   • active return / spread (subj − bench) — computed client-side from
 *     close-price diffs (returns are no longer stored in the DB).
 *   • corr_5d / corr_20d / corr_60d / corr_255d — the Pearson correlation
 *     between the two close-price series over the trailing 5 / 20 / 60 /
 *     255 trading days ending at the hovered date.
 */
import type { EChartsOption } from "echarts";
import type { ThemeMode } from "@/store/filters";
import type { PerfAttrChartResponse } from "@shared/types";
import {
  UP_COLOR,
  DOWN_COLOR,
  MUTED_PALETTE,
  axisColors,
  commonDataZoom,
  commonLegend,
  commonGrid,
} from "@/theme/chart-palette";
import { fmtNum } from "@/lib/series";
import type { ChartMode } from "./types";

export function buildComparisonOption(
  data: PerfAttrChartResponse,
  themeMode: ThemeMode,
  mode: ChartMode = "absolute",
): EChartsOption {
  const c = axisColors(themeMode);
  const dates = data.rows.map((r) => r.date);
  const subjectCloses = data.rows.map((r) => r.subject_close);
  const benchmarkCloses = data.rows.map((r) => r.benchmark_close);
  // Compute daily returns client-side from close-price diffs (returns are
  // no longer stored in the DB — subject_return / benchmark_return columns
  // were removed). First row per series has no prior close → null.
  const subjectReturns = subjectCloses.map((v, i) => {
    if (i === 0 || v == null) return null;
    const prev = subjectCloses[i - 1];
    return prev == null ? null : v - prev;
  });
  const benchmarkReturns = benchmarkCloses.map((v, i) => {
    if (i === 0 || v == null) return null;
    const prev = benchmarkCloses[i - 1];
    return prev == null ? null : v - prev;
  });
  const corr5d = data.rows.map((r) => r.corr_5d);
  const corr20d = data.rows.map((r) => r.corr_20d);
  const corr60d = data.rows.map((r) => r.corr_60d);
  const corr255d = data.rows.map((r) => r.corr_255d);
  const subjectName = data.name || data.code;
  const benchmarkName = data.benchmark_name || data.benchmark_code;

  // Color a correlation value: strong positive → green, strong negative → red,
  // near-zero → muted. Returns an inline CSS color string.
  const corrColor = (v: number | null): string => {
    if (v == null) return c.textColor;
    if (v >= 0.5) return UP_COLOR;
    if (v <= -0.5) return DOWN_COLOR;
    return c.textColor;
  };

  // ---- Percentage mode: rebase both curves to 0% at the first date where
  //      both have non-null, non-zero closes.  This aligns the two starting
  //      points to the same horizontal baseline (0%) so relative performance
  //      is directly comparable on a shared y-axis. ----
  let subjectValues: (number | null)[] = subjectCloses;
  let benchmarkValues: (number | null)[] = benchmarkCloses;
  let baseDate: string | null = null;

  if (mode === "percentage") {
    let baseIdx = -1;
    let subjectBase: number | null = null;
    let benchmarkBase: number | null = null;
    for (let i = 0; i < subjectCloses.length; i++) {
      const s = subjectCloses[i];
      const b = benchmarkCloses[i];
      if (s != null && s !== 0 && b != null && b !== 0) {
        baseIdx = i;
        subjectBase = s;
        benchmarkBase = b;
        break;
      }
    }
    if (baseIdx >= 0 && subjectBase != null && benchmarkBase != null) {
      baseDate = dates[baseIdx];
      subjectValues = subjectCloses.map((v, i) => {
        if (i < baseIdx || v == null) return null;
        return (v / subjectBase! - 1) * 100;
      });
      benchmarkValues = benchmarkCloses.map((v, i) => {
        if (i < baseIdx || v == null) return null;
        return (v / benchmarkBase! - 1) * 100;
      });
    } else {
      subjectValues = subjectCloses.map(() => null);
      benchmarkValues = benchmarkCloses.map(() => null);
    }
  }

  // Helper: format a percentage value with sign.
  const fmtPct = (v: number | null): string =>
    v == null ? "—" : (v >= 0 ? "+" : "") + fmtNum(v, 2) + "%";

  // Y-axis configuration depends on mode.
  //  - percentage: single shared y-axis (both curves in % units)
  //  - absolute:   dual y-axes (subject left, benchmark right — different scales)
  const useSingleAxis = mode === "percentage";
  const yAxis: EChartsOption["yAxis"] = useSingleAxis
    ? [{
        type: "value",
        name: baseDate ? `% change (base: ${baseDate})` : "% change",
        nameTextStyle: { color: c.textColor, fontSize: 9 },
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: {
          color: c.textColor,
          fontSize: 9,
          formatter: (v: number) => (v >= 0 ? "+" : "") + fmtNum(v, 1) + "%",
        },
        splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.4 } },
      }]
    : [
        {
          type: "value",
          name: `${subjectName} Close`,
          nameTextStyle: { color: MUTED_PALETTE[0], fontSize: 9 },
          axisLine: { lineStyle: { color: MUTED_PALETTE[0] } },
          axisLabel: {
            color: MUTED_PALETTE[0],
            fontSize: 9,
            formatter: (v: number) => fmtNum(v, 3),
          },
          splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.4 } },
        },
        {
          type: "value",
          name: `${benchmarkName} Close`,
          nameTextStyle: { color: MUTED_PALETTE[1], fontSize: 9 },
          axisLine: { lineStyle: { color: MUTED_PALETTE[1] } },
          axisLabel: {
            color: MUTED_PALETTE[1],
            fontSize: 9,
            formatter: (v: number) => fmtNum(v, 3),
          },
          splitLine: { show: false },
        },
      ];

  return {
    backgroundColor: "transparent",
    animation: false,
    grid: commonGrid({ left: 56, right: 56, bottom: 50 }),
    dataZoom: commonDataZoom(),
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
        const sc = subjectCloses[idx];
        const bc = benchmarkCloses[idx];
        const sr = subjectReturns[idx];
        const br = benchmarkReturns[idx];
        const ar = sr == null || br == null ? null : sr - br;
        const c5 = corr5d[idx];
        const c20 = corr20d[idx];
        const c60 = corr60d[idx];
        const c255 = corr255d[idx];

        // Header + close-price/correlation block is common to both modes.
        // Only the middle section (value lines) differs.
        let valueLines: string;
        if (mode === "percentage") {
          const sp = subjectValues[idx];
          const bp = benchmarkValues[idx];
          const spread = sp == null || bp == null ? null : sp - bp;
          valueLines = `
            <div>${subjectName}: <b style="color:${MUTED_PALETTE[0]}">${fmtPct(sp)}</b> <span style="opacity:0.5">(close ${sc == null ? "—" : fmtNum(sc, 3)})</span></div>
            <div>${benchmarkName}: <b style="color:${MUTED_PALETTE[1]}">${fmtPct(bp)}</b> <span style="opacity:0.5">(close ${bc == null ? "—" : fmtNum(bc, 3)})</span></div>
            <div style="margin-top:2px">Spread (subj − bench): <b>${fmtPct(spread)}</b></div>
          `;
        } else {
          valueLines = `
            <div>${subjectName} (${data.code}) close: <b style="color:${MUTED_PALETTE[0]}">${sc == null ? "—" : fmtNum(sc, 3)}</b></div>
            <div>${benchmarkName} (${data.benchmark_code}) close: <b style="color:${MUTED_PALETTE[1]}">${bc == null ? "—" : fmtNum(bc, 3)}</b></div>
            <div style="margin-top:2px">Active (subj − bench): <b>${ar == null ? "—" : fmtNum(ar, 4)}</b></div>
          `;
        }

        return `
          <div style="font-weight:600">${dates[idx]}</div>
          ${valueLines}
          <div style="margin-top:2px;opacity:0.85">Rolling close correlation:</div>
          <div style="opacity:0.85">  5d: <b style="color:${corrColor(c5)}">${c5 == null ? "—" : fmtNum(c5, 4)}</b>  ·  20d: <b style="color:${corrColor(c20)}">${c20 == null ? "—" : fmtNum(c20, 4)}</b></div>
          <div style="opacity:0.85">  60d: <b style="color:${corrColor(c60)}">${c60 == null ? "—" : fmtNum(c60, 4)}</b>  ·  255d: <b style="color:${corrColor(c255)}">${c255 == null ? "—" : fmtNum(c255, 4)}</b></div>
        `;
      },
    },
    legend: commonLegend(themeMode, {
      itemWidth: 12,
      itemHeight: 7,
      data: [subjectName, benchmarkName],
      formatter: (name: string) => (name.length > 8 ? name.slice(0, 7) + "…" : name),
    }),
    xAxis: {
      type: "category",
      data: dates,
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: {
        color: c.textColor,
        fontSize: 9,
        // Show ~6 ticks to avoid label crowding on long histories.
        formatter: (v: string) => v.slice(0, 7),
      },
      splitLine: { show: false },
    },
    yAxis,
    series: [
      {
        name: subjectName,
        type: "line",
        yAxisIndex: 0,
        showSymbol: false,
        smooth: false,
        data: subjectValues,
        lineStyle: { width: 1.5, color: MUTED_PALETTE[0] },
        itemStyle: { color: MUTED_PALETTE[0] },
      },
      {
        name: benchmarkName,
        type: "line",
        yAxisIndex: useSingleAxis ? 0 : 1,
        showSymbol: false,
        smooth: false,
        data: benchmarkValues,
        lineStyle: { width: 1.5, color: MUTED_PALETTE[1] },
        itemStyle: { color: MUTED_PALETTE[1] },
      },
    ],
  };
}
