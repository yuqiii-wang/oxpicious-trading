/**
 * Performance Attribution analysis page (Index subjects × Index benchmarks).
 *
 * Layout mirrors the data-viz ETF + Index pages:
 *   • Header — title + subtitle (active sector/industry label)
 *   • Controls — Index toggle + CodeSearchBar + RefreshButton
 *   • ThemeSelector — two-level cascade (L1 sector → L2 industry)
 *   • Stack of PerfAttrPanel cards — one per code on the current page.
 *     Each panel renders:
 *       1. Fluctuation Attribution chart (top) — grouped bars per benchmark
 *          showing shared-weight contribution (= fractional benchmark return ×
 *          composition overlap) and overlap %. Click a bar to select that
 *          benchmark. An All/Sector toggle shows/hides broad-market benchmarks.
 *       2. %/Abs toggle for the time-series charts (shown after a bar is clicked).
 *       3. Index Trading Amt Contribution (benchmark vs subject ETF turnover)
 *       4. Close Price History Trend (subject vs benchmark) with rolling
 *          close correlations (5/20/60/255d) in the tooltip.
 *     Returns are NOT stored in the DB — benchmark_return and subject_return
 *     are computed on-the-fly in the attribution SQL via LATERAL joins to
 *     stats.index_basic_stats (fractional returns, scale-invariant).
 *   • Pagination — page_size codes per page.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Box,
  CircularProgress,
  IconButton,
  Pagination,
  Popover,
  Slider,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import { ArrowBack, Close } from "@mui/icons-material";
import { useNavigate } from "react-router-dom";
import * as echarts from "echarts";
import ChartCard from "@/components/ChartCard";
import CodeSearchBar, { findCodeInThemes } from "@/components/CodeSearchBar";
import EChart from "@/components/EChart";
import RefreshButton from "@/components/RefreshButton";
import ThemeSelector from "@/components/ThemeSelector";
import { useStore } from "@/store/filters";
import { fmtNum, fmtYi } from "@/lib/series";
import {
  UP_COLOR,
  DOWN_COLOR,
  MUTED_PALETTE,
  SUBTITLE_COLOR,
  axisColors,
  commonLegend,
  commonGrid,
} from "@/theme/chart-palette";
import {
  fetchPerfAttrAttribution,
  fetchPerfAttrChart,
  fetchPerfAttrCodes,
  fetchPerfAttrThemes,
  invalidateCacheForPrefix,
} from "@/lib/api-client";
import type {
  PerfAttrAttributionResponse,
  PerfAttrChartResponse,
  PerfAttrCodesResponse,
  PerfAttrSecType,
  SectorNode,
} from "../../../shared/types";
import type { ThemeMode } from "@/store/filters";
import type { EChartsOption } from "echarts";

const PAGE_SIZE = 2;

// ----------------------------------------------------------------------------
//  Broad market benchmark detection is sourced from the DB via the
//  PerfAttrBenchmarkRow.is_broad_market field (computed in the API by JOINing
//  stats.sec_index_tags). The former hardcoded BROAD_MARKET_BENCHMARKS list
//  has been removed — broad-market status now follows build_classification.py.
// ----------------------------------------------------------------------------

// ============================================================================
//  Chart: Subject vs Benchmark close-price comparison (two-line chart).
//  Uses the /api/analysis/perf-attr/chart endpoint which returns the full
//  date series of subject_close + benchmark_close (plus the four corr_Nd
//  rolling correlations) for one (code, benchmark_code) pair.
//
//  Two display modes (toggled in the panel header):
//    • "absolute"  — raw close prices on dual y-axes (subject left, benchmark
//                    right).  Useful when the two series live on very
//                    different scales (e.g. ETF ≈5 yuan vs index ≈3000 pts).
//    • "percentage" — both curves rebased to 0% at the first date where BOTH
//                    have non-null closes, then plotted on a single shared
//                    y-axis.  This aligns the two starting points to the
//                    same horizontal baseline so relative performance is
//                    directly comparable.
//
//  The tooltip on each hovered date shows:
//    • subject close (or % change)  • benchmark close (or % change)
//    • active return / spread (subj − bench) — computed client-side from
//      close-price diffs (returns are no longer stored in the DB).
//    • corr_5d / corr_20d / corr_60d / corr_255d — the Pearson correlation
//      between the two close-price series over the trailing 5 / 20 / 60 /
//      255 trading days ending at the hovered date.
// ============================================================================
type ChartMode = "absolute" | "percentage";

// ============================================================================
//  Chart: Fluctuation Attribution (recovered).
//  Vertical grouped bar chart — one pair of bars per benchmark:
//    Bar 1 (left  Y-axis): shared-weight contribution = benchmark_return ×
//                          (code_sec_shared_weight / 100). Both values are
//                          FRACTIONAL (benchmark_return is a fractional daily
//                          return, e.g. 0.0125 = +1.25%; shared_weight is in
//                          %, e.g. 57.166). The product is a scale-invariant
//                          return attribution: "the benchmark's % return scaled
//                          by how much the subject overlaps via composition."
//                          Green if benchmark rose, red if dropped.
//    Bar 2 (right Y-axis): code_sec_shared_weight (% overlap with subject).
//
//  Computed on-the-fly from PerfAttrAttributionResponse (latest date per
//  benchmark). Returns are NOT stored in the DB — they're computed in the
//  attribution SQL via LATERAL joins to stats.index_basic_stats.
//
//  Clicking a bar selects that benchmark for the time-series charts below.
// ============================================================================
function buildFluctuationOption(
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
  const benchmarkAmounts = sorted.map((b) => b.benchmark_etf_amount);
  const codeAmounts = sorted.map((b) => b.code_etf_amount);
  const etfRatios = sorted.map((b) => b.etf_amount_ratio);
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

function buildComparisonOption(
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
    grid: commonGrid({ left: 56, right: 56, bottom: 32 }),
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

// ============================================================================
//  Chart: Index Trading Amt Contribution (ETF-market turnover over time).
//  Renders two area lines comparing benchmark vs subject INDEX-LEVEL ETF
//  turnover (per-index aggregate from stats.index_exts, precomputed by
//  build_index_exts.py = Σ etf_liquidity_margin.amount_wan×1e4 across ALL
//  ETFs tracking each index via stats.sec_classification.parent_index_code).
//
//  A display-mode toggle (chartMode) applies to both expanded plots (this
//  one and the close-price comparison below):
//    • "absolute"   — raw 亿元 values on a shared y-axis (default). Best for
//                     comparing the relative SIZE of the two ETF markets.
//    • "percentage" — both curves rebased to 0% at the first date where both
//                     have non-null, non-zero amounts. Best for comparing
//                     relative GROWTH in turnover over time. The tooltip still
//                     surfaces the raw 亿元 value in parentheses.
//  The tooltip surfaces the bench/code liquidity ratio (DB-GENERATED
//  etf_amount_ratio_benchmark_to_code), the subject's share (1/ratio), AND
//  the 5-day moving average of the ratio (etf_amount_ratio_benchmark_to_code_ma5,
//  precomputed by analyze_sec_alloc_perf_attribution.py) as a dedicated line
//  — the former standalone MA5 chart has been consolidated into this tooltip.
//  Shares the same date range (slider-sliced `filteredChartData`) as the
//  close-price plot.
// ============================================================================
function buildAmountContributionOption(
  data: PerfAttrChartResponse,
  themeMode: ThemeMode,
  chartMode: ChartMode = "absolute",
): EChartsOption {
  const c = axisColors(themeMode);
  const dates = data.rows.map((r) => r.date);
  // Divide yuan by 1e8 → 亿元 for readable y-axis values.
  const benchmarkAmountsRaw = data.rows.map((r) =>
    r.benchmark_etf_amount == null ? null : r.benchmark_etf_amount / 1e8,
  );
  const codeAmountsRaw = data.rows.map((r) =>
    r.code_etf_amount == null ? null : r.code_etf_amount / 1e8,
  );
  // Watermark condition: no ETFs linked to either the benchmark or the code
  // (subject) index. Both linked_etfs arrays are empty → the "Index Trading
  // Amt contribution" concept is meaningless for this pair.
  const noEtfLinked =
    data.benchmark_linked_etfs.length === 0 && data.code_linked_etfs.length === 0;
  const benchmarkEtfNums = data.rows.map((r) => r.benchmark_etf_num);
  const codeEtfNums = data.rows.map((r) => r.code_etf_num);
  // DB-generated liquidity ratio (benchmark_etf_amount / code_etf_amount).
  const ratios = data.rows.map((r) => r.etf_amount_ratio);
  // 5-day moving average of the ratio (precomputed by the analyze script).
  const ratioMa5s = data.rows.map((r) => r.etf_amount_ratio_ma5);

  const subjectName = data.name || data.code;
  const benchmarkName = data.benchmark_name || data.benchmark_code;
  const benchLabel = `${benchmarkName} ETF Amt`;
  const codeLabel = `${subjectName} ETF Amt`;

  // ---- Percentage mode: rebase each curve independently to 0% at the first
  //      date where it has a non-null, non-zero amount. This ensures that a
  //      benchmark with no tracking ETF (all-null benchmark_etf_amount) does
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
    grid: commonGrid({ left: 56, right: 56, bottom: 32 }),
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

// ============================================================================
//  Panel — one card per code: benchmark selector + two time-series charts.
// ============================================================================
interface PanelProps {
  code: string;
  name: string;
  secType: PerfAttrSecType;
  themeMode: ThemeMode;
}

function PerfAttrPanel({ code, name, secType, themeMode }: PanelProps) {
  const [data, setData] = useState<PerfAttrAttributionResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Selected benchmark for the two time-series charts. Auto-selected when
  // attribution data loads (000300 if available, else first benchmark).
  const [selectedBenchmark, setSelectedBenchmark] = useState<{
    code: string;
    name: string;
  } | null>(null);
  const [chartData, setChartData] = useState<PerfAttrChartResponse | null>(null);
  const [chartLoading, setChartLoading] = useState(false);
  const [chartError, setChartError] = useState<string | null>(null);

  // Date range slider state — two indices into the chart data rows array.
  const [range, setRange] = useState<[number, number]>([0, 0]);

  // Comparison chart display mode: "percentage" rebases both curves to 0% at
  // the first common date (best for relative-performance trend comparison);
  // "absolute" shows raw close prices on dual y-axes.
  const [chartMode, setChartMode] = useState<ChartMode>("percentage");

  // Broad-market benchmark visibility in the Fluctuation Attribution chart.
  // When FALSE, broad-market benchmarks (沪深300, 上证指数, etc.) are hidden so
  // sector/industry benchmarks stand out.
  const [showBroadMarket, setShowBroadMarket] = useState(true);

  // Popover anchor for the "Linked ETFs" button in the Index Trading Amt
  // contribution chart header. Null when the popover is closed.
  const [etfPopoverAnchor, setEtfPopoverAnchor] = useState<HTMLElement | null>(null);

  // Click handler for the Fluctuation Attribution chart — uses dataIndex
  // from the click params to look up the benchmark code from a ref to the
  // sorted benchmarks array. Ref-based so the chart-level binding (done once
  // via onReady) always reads the latest data without re-binding.
  const dataRef = useRef(data);
  useEffect(() => { dataRef.current = data; }, [data]);
  const showBroadMarketRef = useRef(showBroadMarket);
  useEffect(() => { showBroadMarketRef.current = showBroadMarket; }, [showBroadMarket]);

  const handleFluctuationReady = useCallback((chart: echarts.ECharts) => {
    // Use zr-level (canvas) click to avoid ECharts series-level event
    // binding quirks. Convert pixel → x-axis category index → benchmark code.
    chart.getZr().on("click", (params: { offsetX?: number; offsetY?: number }) => {
      const x = params.offsetX;
      const y = params.offsetY;
      if (x == null || y == null) return;
      // Only fire inside the plot grid.
      if (!chart.containPixel("grid", [x, y])) return;
      const idx = chart.convertFromPixel({ xAxisIndex: 0 }, x);
      const dataIdx = Math.round(idx);
      if (dataIdx < 0) return;
      const d = dataRef.current;
      if (!d) return;
      // Re-derive the sorted+filtered benchmarks the same way
      // buildFluctuationOption does, to map index → benchmark_code.
      let sorted = [...d.benchmarks].sort((a, b) => {
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
      if (!showBroadMarketRef.current) {
        sorted = sorted.filter((b) => b.is_broad_market !== true);
      }
      sorted = sorted.filter(
        (b) => b.code_sec_shared_weight != null && b.benchmark_return != null,
      );
      const bench = sorted[dataIdx];
      if (bench) {
        setSelectedBenchmark({
          code: bench.benchmark_code,
          name: bench.benchmark_name || bench.benchmark_code,
        });
      }
    });
  }, []);

  // Memoized Fluctuation Attribution chart option — recomputes only when the
  // attribution data, theme, or broad-market toggle changes. Returns null
  // when data hasn't loaded yet (the chart is only rendered when data is
  // non-null, but useMemo runs on every render regardless).
  const fluctuationOption = useMemo(
    () => (data ? buildFluctuationOption(data, themeMode, showBroadMarket) : null),
    [data, themeMode, showBroadMarket],
  );

  // Fetch attribution data (benchmark list for the selector) on mount.
  // NOTE: no auto-select — the expanded time-series charts are shown ONLY
  // after the user clicks a bar in the Fluctuation Attribution chart.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchPerfAttrAttribution(code, secType, null)
      .then((d) => {
        if (cancelled) return;
        setData(d);
        setLoading(false);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setError(e.message);
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [code, secType]);

  // Reset when the subject code or sec_type changes.
  useEffect(() => {
    setSelectedBenchmark(null);
    setChartData(null);
    setChartError(null);
    setRange([0, 0]);
  }, [code, secType]);

  // Fetch the time-series chart data whenever the selected benchmark changes.
  useEffect(() => {
    if (!selectedBenchmark) {
      setChartData(null);
      setChartError(null);
      setRange([0, 0]);
      return;
    }
    let cancelled = false;
    setChartLoading(true);
    setChartError(null);
    fetchPerfAttrChart(code, selectedBenchmark.code, secType)
      .then((d) => {
        if (cancelled) return;
        setChartData(d);
        const maxIdx = Math.max(0, d.rows.length - 1);
        setRange([0, maxIdx]);
        setChartLoading(false);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setChartError(e.message);
        setChartLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedBenchmark, code, secType]);

  // Slice chart rows to the selected date window.
  const filteredChartData = useMemo<PerfAttrChartResponse | null>(() => {
    if (!chartData) return null;
    const [lo, hi] = range;
    const rows = chartData.rows.slice(lo, hi + 1);
    return { ...chartData, rows };
  }, [chartData, range]);

  // Wire up cross-chart tooltip sync via echarts.connect() — the two
  // charts share one group so hovering either shows the tooltip on both.
  const chartGroup = selectedBenchmark
    ? `perf-attr-${code}-${selectedBenchmark.code}`
    : null;
  const connectedGroupsRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    if (!chartGroup) return;
    if (connectedGroupsRef.current.has(chartGroup)) return;
    echarts.connect(chartGroup);
    connectedGroupsRef.current.add(chartGroup);
  }, [chartGroup]);

  const maxIdx = chartData ? Math.max(0, chartData.rows.length - 1) : 0;

  const subtitle = data
    ? `${data.code} · ${data.name || name || "—"} · ${data.latest_date || "—"}`
    : `${code} · ${name || "—"}`;

  return (
    <ChartCard title="Perf Attribution" subtitle={subtitle}>
      {loading && (
        <Box sx={{ display: "flex", justifyContent: "center", py: 3 }}>
          <CircularProgress size={20} />
        </Box>
      )}
      {error && (
        <Alert severity="error" sx={{ py: 0.5 }}>
          {error}
        </Alert>
      )}
      {!loading && !error && data && data.benchmarks.length > 0 && (
        <>
          {/* Fluctuation Attribution chart — shared-weight contribution per
              benchmark for the latest date. Bar 1 (left axis) = benchmark
              fractional return × (shared_weight/100); Bar 2 (right axis) =
              shared_weight %. Click a bar to select that benchmark for the
              time-series charts below. */}
          <Box sx={{ mb: 1 }}>
            <Box
              sx={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                mb: 0.25,
                gap: 1,
              }}
            >
              <Typography variant="caption" color="text.secondary" sx={{ fontSize: "0.7rem" }}>
                Fluctuation Attribution (contribution = return × overlap) · {data.latest_date}
              </Typography>
              <ToggleButtonGroup
                size="small"
                value={showBroadMarket ? "all" : "sector"}
                exclusive
                onChange={(_, v) => { if (v) setShowBroadMarket(v === "all"); }}
                sx={{ flexShrink: 0 }}
              >
                <ToggleButton
                  value="all"
                  sx={{ py: 0, px: 0.75, fontSize: "0.6rem", lineHeight: 1.2 }}
                >
                  All
                </ToggleButton>
                <ToggleButton
                  value="sector"
                  sx={{ py: 0, px: 0.75, fontSize: "0.6rem", lineHeight: 1.2 }}
                >
                  Sector
                </ToggleButton>
              </ToggleButtonGroup>
            </Box>
            <EChart
              option={fluctuationOption ?? {}}
              height={300}
              onReady={handleFluctuationReady}
            />
          </Box>

          {/* Expanded time-series charts — shown ONLY after the user clicks a
              bar in the Fluctuation Attribution chart above. No dropdown; the
              benchmark is selected exclusively via bar click.
              1. Index Trading Amt contribution (benchmark vs subject index ETF
                 turnover) — tooltip surfaces the bench/code liquidity ratio,
                 its 5-day MA, and the subject's share (1/ratio).
              2. Close price history trend (subject vs benchmark) — tooltip
                 surfaces the rolling 5/20/60/255-day close-price correlations.
              The %/Abs toggle applies to both charts: "percentage" rebases
              both curves to 0% at the first common date; "absolute" shows raw
              values (亿元 / close price). */}
          {!selectedBenchmark && (
            <Box sx={{ display: "flex", justifyContent: "center", py: 2 }}>
              <Typography variant="body2" color="text.secondary" sx={{ fontStyle: "italic" }}>
                Click a bar above to expand the time-series charts for that benchmark.
              </Typography>
            </Box>
          )}
          {selectedBenchmark && chartLoading && (
            <Box sx={{ display: "flex", justifyContent: "center", py: 2 }}>
              <CircularProgress size={20} />
            </Box>
          )}
          {selectedBenchmark && chartError && (
            <Alert severity="error" sx={{ py: 0.5 }}>
              {chartError}
            </Alert>
          )}
          {selectedBenchmark && !chartLoading && !chartError && filteredChartData && filteredChartData.rows.length > 0 && (
            <>
              <Box sx={{ mt: 1 }}>
                {/* Expanded charts header: selected benchmark label + %/Abs toggle */}
                <Box
                  sx={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    mb: 0.25,
                    gap: 1,
                  }}
                >
                  <Typography variant="caption" color="text.secondary" sx={{ fontSize: "0.7rem" }}>
                    Selected: <b>{selectedBenchmark.name}</b> ({selectedBenchmark.code})
                  </Typography>
                  <ToggleButtonGroup
                    size="small"
                    value={chartMode}
                    exclusive
                    onChange={(_, v) => {
                      if (v) setChartMode(v as ChartMode);
                    }}
                    sx={{ flexShrink: 0 }}
                  >
                    <ToggleButton
                      value="percentage"
                      sx={{ py: 0, px: 0.75, fontSize: "0.65rem", lineHeight: 1.2 }}
                    >
                      %
                    </ToggleButton>
                    <ToggleButton
                      value="absolute"
                      sx={{ py: 0, px: 0.75, fontSize: "0.65rem", lineHeight: 1.2 }}
                    >
                      Abs
                    </ToggleButton>
                  </ToggleButtonGroup>
                </Box>
                <Box sx={{ display: "flex", alignItems: "center", gap: 0.5, mb: 0.25 }}>
                  <Typography variant="caption" color="text.secondary">
                    Index Trading Amt contribution (benchmark vs subject index ETF turnover)
                  </Typography>
                  {/* Linked ETFs button — opens a popover listing the ETFs
                      tracking the benchmark and subject indices. */}
                  <Typography
                    component="span"
                    variant="caption"
                    onClick={(e) => setEtfPopoverAnchor(e.currentTarget)}
                    sx={{
                      cursor: "pointer",
                      color: "primary.main",
                      fontSize: "0.65rem",
                      textDecoration: "underline",
                      ml: 0.5,
                    }}
                  >
                    Linked ETFs
                  </Typography>
                  <Popover
                    open={etfPopoverAnchor != null}
                    anchorEl={etfPopoverAnchor}
                    onClose={() => setEtfPopoverAnchor(null)}
                    anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
                    transformOrigin={{ vertical: "top", horizontal: "left" }}
                    PaperProps={{ sx: { maxWidth: 360, p: 1.25 } }}
                  >
                    {filteredChartData && (
                      <Box sx={{ fontSize: "0.75rem" }}>
                        <Typography variant="caption" sx={{ fontWeight: 600, display: "block", mb: 0.5 }}>
                          Benchmark: {filteredChartData.benchmark_name || filteredChartData.benchmark_code}
                        </Typography>
                        {filteredChartData.benchmark_linked_etfs.length > 0 ? (
                          <Box component="ul" sx={{ m: 0, pl: 1.5, mb: 1 }}>
                            {filteredChartData.benchmark_linked_etfs.map((etf) => (
                              <li key={etf.code}>
                                {etf.name} <span style={{ opacity: 0.5 }}>({etf.code})</span>
                              </li>
                            ))}
                          </Box>
                        ) : (
                          <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 1, fontStyle: "italic" }}>
                            No ETF tracks this benchmark index.
                          </Typography>
                        )}
                        <Typography variant="caption" sx={{ fontWeight: 600, display: "block", mb: 0.5 }}>
                          Subject: {filteredChartData.name || filteredChartData.code}
                        </Typography>
                        {filteredChartData.code_linked_etfs.length > 0 ? (
                          <Box component="ul" sx={{ m: 0, pl: 1.5 }}>
                            {filteredChartData.code_linked_etfs.map((etf) => (
                              <li key={etf.code}>
                                {etf.name} <span style={{ opacity: 0.5 }}>({etf.code})</span>
                              </li>
                            ))}
                          </Box>
                        ) : (
                          <Typography variant="caption" color="text.secondary" sx={{ display: "block", fontStyle: "italic" }}>
                            No ETF tracks this subject index.
                          </Typography>
                        )}
                      </Box>
                    )}
                  </Popover>
                </Box>
                <EChart
                  option={buildAmountContributionOption(filteredChartData, themeMode, chartMode)}
                  height={170}
                  group={`perf-attr-${code}-${selectedBenchmark.code}`}
                />
                <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 1, mb: 0.25 }}>
                  Close price history trend (subject vs benchmark)
                </Typography>
                <EChart
                  option={buildComparisonOption(filteredChartData, themeMode, chartMode)}
                  height={200}
                  group={`perf-attr-${code}-${selectedBenchmark.code}`}
                />
              </Box>
              {maxIdx > 0 && (
                <Box sx={{ px: 1, mt: 0.5 }}>
                  <Slider
                    value={range}
                    onChange={(_, v) => setRange(v as [number, number])}
                    min={0}
                    max={maxIdx}
                    size="small"
                    valueLabelDisplay="auto"
                    valueLabelFormat={(idx) => chartData?.rows[idx]?.date ?? ""}
                    sx={{ mt: 0.5, "& .MuiSlider-valueLabel": { fontSize: "0.7rem" } }}
                  />
                  <Stack direction="row" justifyContent="space-between" sx={{ mt: -0.5 }}>
                    <Typography variant="caption" color="text.secondary" sx={{ fontSize: "0.65rem" }}>
                      {chartData?.rows[range[0]]?.date ?? "—"}
                    </Typography>
                    <Typography variant="caption" color="text.secondary" sx={{ fontSize: "0.65rem" }}>
                      {chartData?.rows[range[1]]?.date ?? "—"}
                    </Typography>
                  </Stack>
                </Box>
              )}
            </>
          )}
          {selectedBenchmark && !chartLoading && !chartError && filteredChartData && filteredChartData.rows.length === 0 && (
            <Box sx={{ display: "flex", justifyContent: "center", py: 2 }}>
              <Typography variant="body2" color="text.secondary">
                No data in the selected date range.
              </Typography>
            </Box>
          )}
        </>
      )}
      {!loading && !error && data && data.benchmarks.length === 0 && (
        <Box sx={{ display: "flex", justifyContent: "center", py: 3 }}>
          <Typography variant="body2" color="text.secondary">
            No benchmark data for {code}.
          </Typography>
        </Box>
      )}
    </ChartCard>
  );
}

// ============================================================================
//  Page
// ============================================================================
export default function PerfAttrPage() {
  const navigate = useNavigate();
  const themeMode = useStore((s) => s.themeMode);
  // Local sector/industry state (independent from the global ETF/index filters
  // — perf-attr has its own themes tree scoped to sec_type, so a shared global
  // sector_id would not map cleanly between ETF and Index themes).
  const [sectorId, setSectorId] = useState<string | null>(null);
  const [industrySlug, setIndustrySlug] = useState<string | null>(null);
  const [exchange, setExchange] = useState<string | null>(null);

  const [secType, setSecType] = useState<PerfAttrSecType>("index");
  const [sectors, setSectors] = useState<SectorNode[]>([]);
  const [codesData, setCodesData] = useState<PerfAttrCodesResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [searchCode, setSearchCode] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  // ---- Reset selection when secType changes -------------------------------
  // Wipes themes + codes + sector/industry so the user never sees stale
  // data from the other sec_type while the new sec_type's data is loading.
  useEffect(() => {
    setSectors([]);
    setCodesData(null);
    setError(null);
    setSectorId(null);
    setIndustrySlug(null);
    setExchange(null);
    setSearchCode(null);
    setPage(1);
  }, [secType]);

  // ---- Load themes + codes whenever secType changes or refresh is bumped --
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.all([fetchPerfAttrThemes(secType), fetchPerfAttrCodes(secType)])
      .then(([t, c]) => {
        if (cancelled) return;
        setSectors(t);
        setCodesData(c);
        // Default to BROAD sector if available, else first sector (highest count).
        if (t.length > 0 && sectorId == null) {
          const broad = t.find((s) => s.sector_id === "BROAD");
          setSectorId(broad ? broad.sector_id : t[0].sector_id);
        }
        setLoading(false);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setError(e.message);
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [secType, refreshKey]);

  // Reset to page 1 whenever sector, industry, or exchange changes.
  useEffect(() => {
    setPage(1);
  }, [sectorId, industrySlug, exchange]);

  const handleRefresh = () => {
    // All perf-attr endpoints share the "/api/analysis/perf-attr/" prefix.
    invalidateCacheForPrefix("/api/analysis/perf-attr/");
    setRefreshKey((k) => k + 1);
  };

  // Resolve a searched code against the themes tree.
  const handleSearch = (code: string) => {
    const found = findCodeInThemes(sectors, code);
    if (!found) {
      setError(`Code not found in ${secType.toUpperCase()} perf-attr data: ${code}`);
      setSearchCode(null);
      return;
    }
    setError(null);
    setSectorId(found.sectorId);
    setIndustrySlug(found.industrySlug);
    setSearchCode(code);
    setPage(1);
  };

  const handleClearSearch = () => {
    setSearchCode(null);
  };

  const handleSectorChange = (id: string | null) => {
    setSearchCode(null);
    setSectorId(id);
  };
  const handleIndustryChange = (slug: string | null) => {
    setSearchCode(null);
    setIndustrySlug(slug);
  };
  const handleExchangeChange = (ex: string | null) => {
    setSearchCode(null);
    setExchange(ex);
  };

  // ---- Filter codes by sector/industry/exchange or by exact code search ----
  const { pageCodes, totalCodes } = useMemo(() => {
    const all = codesData?.codes ?? [];
    if (searchCode) {
      // Exact-code search: bypass sector/industry/exchange filter, find the one match.
      const norm = searchCode.toUpperCase().replace(/\.(SS|SZ|SH|BJ|HK)$/i, "");
      const match = all.find(
        (c) => c.code.toUpperCase().replace(/\.(SS|SZ|SH|BJ|HK)$/i, "") === norm,
      );
      return { pageCodes: match ? [match] : [], totalCodes: match ? 1 : 0 };
    }
    // Build the set of codes that belong to the selected sector/industry in
    // the themes tree, then preserve the order from `all` (which is already
    // sorted by n_dates DESC NULLS LAST, code by the codes endpoint).
    const wantedSet = new Set<string>();
    for (const s of sectors) {
      if (sectorId && s.sector_id !== sectorId) continue;
      for (const ind of s.industries) {
        if (industrySlug && ind.industry_slug !== industrySlug) continue;
        for (const item of ind.items) {
          // Exchange filter: match by code suffix (.SS, .SZ, .BJ).
          // Indices have bare codes (no suffix) — they won't match a specific
          // exchange filter, which is correct (indices are cross-market).
          if (exchange) {
            const suffix = `.${exchange}`;
            if (!item.code.toUpperCase().endsWith(suffix)) continue;
          }
          wantedSet.add(item.code);
        }
      }
    }
    const wanted = all.filter((c) => wantedSet.has(c.code));
    return { pageCodes: wanted, totalCodes: wanted.length };
  }, [codesData, sectors, sectorId, industrySlug, exchange, searchCode]);

  const totalPages = Math.max(1, Math.ceil(totalCodes / PAGE_SIZE));
  const safePage = Math.min(page, totalPages);
  const visibleCodes = pageCodes.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE);

  const activeSector = sectors.find((s) => s.sector_id === sectorId);
  const activeIndustry = activeSector?.industries.find(
    (i) => i.industry_slug === industrySlug,
  );
  const headerLabel = activeIndustry
    ? `${activeSector?.sector_label ?? ""} / ${activeIndustry.industry_label}`
    : activeSector
      ? `${activeSector.sector_label} (All)`
      : "Select a sector";

  return (
    <Box>
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          gap: 2,
          flexWrap: "wrap",
        }}
      >
        <Box>
          <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
            <IconButton
              onClick={() => navigate("/analysis/commons")}
              size="small"
              aria-label="back to commons"
            >
              <ArrowBack />
            </IconButton>
            <Typography variant="h5" sx={{ fontWeight: 700 }}>
              Sec Allocation Perf Attribution
            </Typography>
          </Box>
          <Typography variant="body2" color="text.secondary">
            {headerLabel} — daily fluctuation decomposition vs all index benchmarks.
            Green bars = benchmark rose, red = dropped. Shared Wt % = overlap of the
            subject's holdings with each benchmark. Broad-market indices
            (沪深300, 中证A500, 中证500, 中证1000, 中证2000, 上证50, 上证指数,
            深证成指, 创业板指, 科创50, 科创综指, 科技先锋, 北证50,
            国债指数, 企债指数) are shown in a lighter color.
            Click any bar to load two charts: an index-level ETF turnover
            (Trading Amt Contribution — tooltip surfaces the bench/code liquidity
            ratio + 5-day MA) and a close-price history trend (subject vs
            benchmark) with a percentage/absolute mode toggle. Both share the
            same date range slider and synced tooltips.
          </Typography>
        </Box>
        <Box sx={{ display: "flex", alignItems: "center", gap: 1, flexWrap: "wrap" }}>
          <ToggleButtonGroup
            value={secType}
            exclusive
            size="small"
            onChange={(_, v) => {
              if (v) setSecType(v as PerfAttrSecType);
            }}
          >
            <ToggleButton value="etf">ETF</ToggleButton>
            <ToggleButton value="index">Index</ToggleButton>
          </ToggleButtonGroup>
          <CodeSearchBar
            activeCode={searchCode}
            onSearch={handleSearch}
            onClear={handleClearSearch}
            placeholder={`${secType === "etf" ? "ETF" : "Index"} code (e.g. ${secType === "etf" ? "510050" : "000300"})`}
          />
          <RefreshButton
            onClick={handleRefresh}
            loading={loading}
            label="Refresh"
            tooltip="Refresh perf-attr themes + codes + attribution (bypass cache)"
          />
        </Box>
      </Box>

      <ThemeSelector
        sectors={sectors}
        sectorId={sectorId}
        industrySlug={industrySlug}
        exchange={exchange}
        onSectorChange={handleSectorChange}
        onIndustryChange={handleIndustryChange}
        onExchangeChange={handleExchangeChange}
      />

      {loading && (
        <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
          <CircularProgress size={32} />
        </Box>
      )}
      {error && (
        <Alert severity="error" variant="filled" sx={{ mb: 2 }}>
          Failed to load perf-attr data: {error}
        </Alert>
      )}
      {!loading && !error && (
        <>
          {visibleCodes.length === 0 ? (
            <Alert severity="warning">
              {searchCode
                ? `No data available for code: ${searchCode}`
                : `No ${secType.toUpperCase()} perf-attr data in this sector/industry.`}
            </Alert>
          ) : (
            <>
              <Typography variant="caption" color="text.secondary">
                {searchCode
                  ? `Search result for ${searchCode}`
                  : `${visibleCodes.length} of ${totalCodes} ${secType.toUpperCase()}s on this page · page ${safePage}/${totalPages}`}
              </Typography>
              <Stack spacing={1.5} sx={{ mt: 0.5 }}>
                {visibleCodes.map((c) => (
                  <PerfAttrPanel
                    key={c.code}
                    code={c.code}
                    name={c.name}
                    secType={secType}
                    themeMode={themeMode}
                  />
                ))}
              </Stack>
              {!searchCode && totalPages > 1 && (
                <Box sx={{ display: "flex", justifyContent: "center", pt: 2, pb: 1 }}>
                  <Pagination
                    count={totalPages}
                    page={safePage}
                    onChange={(_e, v) => setPage(v)}
                    color="primary"
                    showFirstButton
                    showLastButton
                  />
                </Box>
              )}
            </>
          )}
        </>
      )}
    </Box>
  );
}
