/**
 * Performance Attribution analysis page (ETF/Index subjects × Index benchmarks).
 *
 * Layout mirrors the data-viz ETF + Index pages:
 *   • Header — title + subtitle (active sector/industry label)
 *   • Controls — ETF | Index toggle + CodeSearchBar + RefreshButton
 *   • ThemeSelector — two-level cascade (L1 sector → L2 industry)
 *   • Stack of PerfAttrPanel cards — one per code on the current page.
 *     Each panel renders the Fluctuation Attribution chart for the latest
 *     date: grouped bars per benchmark showing benchmark_return (signed,
 *     green=positive, red=negative) on the left axis and code_sec_shared_weight
 *     (overlap %) on the right axis; tooltip includes benchmark/code ETF
 *     amounts (亿) and the bench/code amount ratio.
 *   • Pagination — page_size codes per page.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Box,
  CircularProgress,
  FormControlLabel,
  IconButton,
  Pagination,
  Slider,
  Stack,
  Switch,
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
//  Chart: Fluctuation Attribution
//  Vertical grouped bar chart — one pair of bars per benchmark:
//    Bar 1 (left  Y-axis): effective contribution = benchmark_return ×
//                           (code_sec_shared_weight / 100). Green if positive,
//                           red if negative. The raw return is shown in the
//                           label and tooltip for reference.
//    Bar 2 (right Y-axis): code_sec_shared_weight (% overlap with subject)
//
//  Label overlap mitigation:
//    • xAxis category labels rotated 55° and truncated to 6 chars.
//    • Bar value labels are placed INSIDE each bar (insideTop for non-negative,
//      insideBottom for negative) so they never collide with neighbours or
//      with the x-axis — no padding hack. Labels are hidden for tiny bars
//      where they wouldn't fit.
// ============================================================================
function buildFluctuationOption(
  data: PerfAttrAttributionResponse,
  themeMode: ThemeMode,
  showBroadMarket = true,
): EChartsOption {
  const c = axisColors(themeMode);
  // Sort benchmarks by effective contribution (discounted) rather than raw
  // return — more relevant after the discount is applied.
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

  // Optionally filter out broad market benchmarks (沪深300, 上证指数, etc.)
  // so sector/industry benchmarks stand out more clearly.
  if (!showBroadMarket) {
    sorted = sorted.filter((b) => b.is_broad_market !== true);
  }

  // Drop benchmarks with no shared weight or null contribution (i.e. either
  // benchmark_return or code_sec_shared_weight is null) — they carry no
  // meaningful attribution signal and would render as empty bars.
  sorted = sorted.filter(
    (b) =>
      b.code_sec_shared_weight != null &&
      b.benchmark_return != null,
  );

  const labels = sorted.map((b) => b.benchmark_name || b.benchmark_code);
  const returns = sorted.map((b) => b.benchmark_return);
  const sharedWts = sorted.map((b) => b.code_sec_shared_weight);
  const benchmarkAmounts = sorted.map((b) => b.benchmark_etf_amount);
  const codeAmounts = sorted.map((b) => b.code_etf_amount);
  const etfRatios = sorted.map((b) => b.etf_amount_ratio);
  const activeReturns = sorted.map((b) => b.active_return);
  const codes = sorted.map((b) => b.benchmark_code);

  // Discounted (effective) contribution: return × overlap_fraction
  const contrib = sorted.map((b, i) => {
    const r = returns[i];
    const w = sharedWts[i];
    if (r == null || w == null) return null as number | null;
    return r * (w / 100);
  });

  // Max absolute contribution — used to hide labels that can't fit.
  const maxAbsContrib = contrib.reduce(
    (m, v) => (v == null ? m : Math.max(m, Math.abs(v))),
    0,
  );
  const LABEL_MIN_RATIO = 0.08; // hide label if bar < 8% of max height

  // Per-bar color: green for rise, red for drop, neutral gray when null.
  // Color still follows RAW return (green/red tells the direction of the
  // underlying benchmark move), while height shows the discounted impact.
  const returnColors = returns.map((v) =>
    v == null ? c.axisLineColor : v >= 0 ? UP_COLOR : DOWN_COLOR,
  );

  // Signed-position helpers for bar labels. Positive contributions label
  // near the top edge, negative near the bottom edge, both INSIDE the bar.
  const contribLabelPosition = (val: number | null) => {
    if (val == null) return "insideTop" as const;
    return val >= 0 ? ("insideTop" as const) : ("insideBottom" as const);
  };
  const contribLabelVisible = (val: number | null) => {
    if (val == null || maxAbsContrib === 0) return false;
    return Math.abs(val) / maxAbsContrib >= LABEL_MIN_RATIO;
  };

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
          seriesName?: string;
          value?: number | null;
        }>;
        if (arr.length === 0) return "";
        const idx = arr[0].dataIndex ?? 0;
        const code = codes[idx];
        const rv = returns[idx];
        const cv = contrib[idx];
        const sw = sharedWts[idx];
        const ba = benchmarkAmounts[idx];
        const ca = codeAmounts[idx];
        const er = etfRatios[idx];
        const ar = activeReturns[idx];
        const sign = cv == null ? "" : cv >= 0 ? "▲ " : "▼ ";
        const rsign = rv == null ? "" : rv >= 0 ? "▲ " : "▼ ";
        // Subject's SHARE of the benchmark ETF market = 1 / ratio (only
        // meaningful when both amounts are non-null and ratio > 0).
        const share = er == null || er === 0 ? null : 1 / er;
        return `
          <div style="font-weight:600">${labels[idx]} <span style="opacity:0.6">(${code})</span></div>
          <div style="margin-top:2px">${sign}Contribution (Return×Wt): <b style="color:${cv == null ? c.textColor : cv >= 0 ? UP_COLOR : DOWN_COLOR}">${fmtNum(cv, 4)}</b></div>
          <div>${rsign}Raw Return: ${rv == null ? "—" : fmtNum(rv, 4)}</div>
          <div>Active vs subject: ${fmtNum(ar, 4)}</div>
          <div>Shared wt (in subject): ${sw == null ? "—" : fmtNum(sw, 4) + "%"}</div>
          <div>Benchmark ETF Trading Amt: ${ba == null ? "—" : fmtYi(ba, 2)}</div>
          <div>Code ETF Trading Amt: ${ca == null ? "—" : fmtYi(ca, 2)}</div>
          <div>ETF Trading Amt Ratio (bench/code): ${er == null ? "—" : fmtNum(er, 4)}${share == null ? "" : ` · share ${fmtNum(share, 4)}`}</div>
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
        // Broad market benchmarks get a lighter label color via rich text so
        // they visually recede behind sector/industry benchmarks.
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
          formatter: (v: number) => fmtNum(v, 2),
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
        // Per-item label: each bar carries its own label position + visibility
        // (series-level label.position doesn't accept per-item functions in
        // the ECharts TS bindings, and per-item overrides are the documented
        // escape hatch for directional bar charts).
        // Each bar also carries `benchmarkCode` so the click handler can
        // identify which benchmark was clicked without indexing back into the
        // sorted array.
        data: contrib.map((v, i) => {
          const visible = contribLabelVisible(v);
          const pos: "insideTop" | "insideBottom" = contribLabelPosition(v);
          const raw = returns[i];
          const rawStr =
            raw == null ? "" : `  [${raw >= 0 ? "▲" : "▼"}${fmtNum(raw, 2)}]`;
          const lblText =
            visible && v != null ? fmtNum(v, 2) + rawStr : "";
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
        // Series-level label is a fallback; data items override the key fields.
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
        label: { show: false, color: c.textColor, fontSize: 8 },
      },
    ],
  };
}

// ============================================================================
//  Chart: Subject vs Benchmark close-price comparison (two-line chart).
//  Shown when a user clicks a bar in the Fluctuation Attribution chart.
//  Uses the existing /api/analysis/perf-attr/chart endpoint which returns
//  the full date series of subject_close + benchmark_close (plus the four
//  corr_Nd rolling correlations) for one (code, benchmark_code) pair.
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
//    • active return / spread (subj − bench)
//    • corr_5d / corr_20d / corr_60d / corr_255d — the Pearson correlation
//      between the two close-price series over the trailing 5 / 20 / 60 /
//      255 trading days ending at the hovered date.
// ============================================================================
type ChartMode = "absolute" | "percentage";

function buildComparisonOption(
  data: PerfAttrChartResponse,
  themeMode: ThemeMode,
  mode: ChartMode = "absolute",
): EChartsOption {
  const c = axisColors(themeMode);
  const dates = data.rows.map((r) => r.date);
  const subjectCloses = data.rows.map((r) => r.subject_close);
  const benchmarkCloses = data.rows.map((r) => r.benchmark_close);
  const subjectReturns = data.rows.map((r) => r.subject_return);
  const benchmarkReturns = data.rows.map((r) => r.benchmark_return);
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
//  Chart: Amount Contribution (ETF-market turnover over time).
//  Renders two area lines comparing benchmark vs subject ETF turnover.
//  Two modes share the same chart geometry:
//    • mode="index"    — benchmark_etf_amount vs code_etf_amount
//                         (per-index aggregate ETF turnover from index_exts)
//    • mode="industry" — benchmark_industry_etf_amount vs code_industry_etf_amount
//                         (per-industry aggregate ETF turnover from etf_trading_amt)
//  Both plotted in 亿元 (yuan / 1e8) on a shared y-axis so the user can
//  visually compare the relative SIZE of the two ETF markets. The tooltip
//  also surfaces the bench/code ratio (computed on the fly for industry mode
//  since no GENERATED column exists for it). Shares the same date range
//  (slider-sliced `filteredChartData`) as the close-price comparison chart.
// ============================================================================
type AmountContributionMode = "index" | "industry";

function buildAmountContributionOption(
  data: PerfAttrChartResponse,
  themeMode: ThemeMode,
  mode: AmountContributionMode = "index",
): EChartsOption {
  const c = axisColors(themeMode);
  const dates = data.rows.map((r) => r.date);
  // Divide yuan by 1e8 → 亿元 for readable y-axis values.
  const isIndex = mode === "index";
  const benchmarkAmounts = data.rows.map((r) => {
    const v = isIndex ? r.benchmark_etf_amount : r.benchmark_industry_etf_amount;
    return v == null ? null : v / 1e8;
  });
  const codeAmounts = data.rows.map((r) => {
    const v = isIndex ? r.code_etf_amount : r.code_industry_etf_amount;
    return v == null ? null : v / 1e8;
  });
  const benchmarkEtfNums = data.rows.map((r) =>
    isIndex ? r.benchmark_etf_num : r.benchmark_industry_etf_num,
  );
  const codeEtfNums = data.rows.map((r) =>
    isIndex ? r.code_etf_num : r.code_industry_etf_num,
  );
  // For index mode, the ratio is the DB-generated etf_amount_ratio. For
  // industry mode, compute it on the fly (bench / code).
  const ratios = data.rows.map((r) => {
    if (isIndex) return r.etf_amount_ratio;
    const ba = r.benchmark_industry_etf_amount;
    const ca = r.code_industry_etf_amount;
    if (ba == null || ca == null || ca === 0) return null;
    return ba / ca;
  });

  const subjectName = data.name || data.code;
  const benchmarkName = data.benchmark_name || data.benchmark_code;
  // Industry ids are constant across dates — pull from the first non-null row.
  const benchmarkIndustryId = data.rows.find((r) => r.benchmark_industry_id)?.benchmark_industry_id ?? null;
  const codeIndustryId = data.rows.find((r) => r.code_industry_id)?.code_industry_id ?? null;
  const benchLabel = isIndex
    ? `${benchmarkName} ETF Amt`
    : `${benchmarkIndustryId ?? "—"} Industry Amt`;
  const codeLabel = isIndex
    ? `${subjectName} ETF Amt`
    : `${codeIndustryId ?? "—"} Industry Amt`;
  const yAxisName = isIndex ? "Index ETF Amt (亿元)" : "Industry ETF Amt (亿元)";

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
        const ba = benchmarkAmounts[idx];
        const ca = codeAmounts[idx];
        const er = ratios[idx];
        const benchNum = benchmarkEtfNums[idx];
        const codeNum = codeEtfNums[idx];
        // Subject's SHARE of the benchmark ETF market = 1 / ratio (only
        // meaningful when both amounts are non-null and ratio > 0).
        const share = er == null || er === 0 ? null : 1 / er;
        return `
          <div style="font-weight:600">${dates[idx]}</div>
          <div style="margin-top:2px">${benchLabel}: <b style="color:${MUTED_PALETTE[1]}">${ba == null ? "—" : fmtNum(ba, 2) + " 亿"}</b>${benchNum == null ? "" : ` <span style="opacity:0.6">(${benchNum} ETFs)</span>`}</div>
          <div>${codeLabel}: <b style="color:${MUTED_PALETTE[0]}">${ca == null ? "—" : fmtNum(ca, 2) + " 亿"}</b>${codeNum == null ? "" : ` <span style="opacity:0.6">(${codeNum} ETFs)</span>`}</div>
          <div style="margin-top:2px;opacity:0.85">Ratio (bench/code): ${er == null ? "—" : fmtNum(er, 4)}${share == null ? "" : ` · share ${fmtNum(share, 4)}`}</div>
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
        formatter: (v: number) => fmtNum(v, 1),
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
//  Panel — one card per code: fetches its attribution and renders the chart.
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

  // Selected benchmark for the two-curve comparison chart (set by clicking
  // a bar in the fluctuation chart).
  const [selectedBenchmark, setSelectedBenchmark] = useState<{
    code: string;
    name: string;
  } | null>(null);
  const [chartData, setChartData] = useState<PerfAttrChartResponse | null>(null);
  const [chartLoading, setChartLoading] = useState(false);
  const [chartError, setChartError] = useState<string | null>(null);

  // Date range slider state — two indices into the chart data rows array.
  // Mirrors the pattern used by EtfMarginPanel, IndexPanel, StockPanel.
  const [range, setRange] = useState<[number, number]>([0, 0]);

  // Toggle: show/hide broad market benchmark bars in the attribution chart.
  const [showBroadMarket, setShowBroadMarket] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchPerfAttrAttribution(code, secType)
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

  // Reset the comparison chart when the subject code or sec_type changes so a
  // stale benchmark selection from the previous subject doesn't persist.
  useEffect(() => {
    setSelectedBenchmark(null);
    setChartData(null);
    setChartError(null);
    setRange([0, 0]);
  }, [code, secType]);

  // Fetch the two-curve chart data whenever the user clicks a bar.
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
        // Reset slider to full range on new data.
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

  // Reset slider when chart data changes (e.g., different benchmark clicked).
  useEffect(() => {
    if (!chartData) return;
    const maxIdx = Math.max(0, chartData.rows.length - 1);
    setRange([0, maxIdx]);
  }, [chartData]);

  // Click handler — reads the `benchmarkCode` field embedded in each bar's
  // data object (see buildFluctuationOption) and resolves its display name
  // from the current attribution payload. Wrapped in useCallback so the
  // onEvents object identity stays stable across renders (avoids re-binding
  // the ECharts click listener on every parent re-render).
  const handleBarClick = useCallback(
    (params: unknown) => {
      const p = params as {
        data?: { benchmarkCode?: string };
        componentType?: string;
      };
      if (p.componentType !== "series") return;
      const bc = p.data?.benchmarkCode;
      if (!bc) return;
      const bench = data?.benchmarks.find((b) => b.benchmark_code === bc);
      setSelectedBenchmark({ code: bc, name: bench?.benchmark_name || bc });
    },
    [data],
  );

  // Slice chart rows to the selected date window.
  const filteredChartData = useMemo<PerfAttrChartResponse | null>(() => {
    if (!chartData) return null;
    const [lo, hi] = range;
    const rows = chartData.rows.slice(lo, hi + 1);
    return { ...chartData, rows };
  }, [chartData, range]);

  // Wire up cross-chart tooltip sync via echarts.connect() — the two
  // expanded charts (comparison + cumulative) share one group so hovering
  // either chart shows the tooltip on both simultaneously.
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
    <ChartCard title="Fluctuation Attribution" subtitle={subtitle}>
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
          <Stack direction="row" justifyContent="flex-end" alignItems="center" sx={{ mb: -0.5 }}>
            <FormControlLabel
              sx={{ mr: 1 }}
              control={
                <Switch
                  size="small"
                  checked={showBroadMarket}
                  onChange={(e) => setShowBroadMarket(e.target.checked)}
                />
              }
              label={
                <Typography variant="caption" color="text.secondary">
                  Broad market
                </Typography>
              }
            />
          </Stack>
          <EChart
            option={buildFluctuationOption(data, themeMode, showBroadMarket)}
            height={360}
            onEvents={{ click: handleBarClick }}
          />
        </>
      )}
      {!loading && !error && data && data.benchmarks.length === 0 && (
        <Box sx={{ display: "flex", justifyContent: "center", py: 3 }}>
          <Typography variant="body2" color="text.secondary">
            No benchmark data for {code}.
          </Typography>
        </Box>
      )}

      {/* Trading Amount Contribution charts — appears when a bar is clicked.
          Only the two amount charts (Index + Industry) are loaded; the
          close-price comparison chart is omitted to keep the panel focused
          on trading-amount analysis. */}
      {selectedBenchmark && (
        <Box sx={{ mt: 1.5, pt: 1.5, borderTop: 1, borderColor: "divider" }}>
          <Box
            sx={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              mb: 0.5,
              gap: 1,
            }}
          >
            <Box sx={{ flex: 1, minWidth: 0 }}>
              <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>
                Trading Amt Contribution
              </Typography>
              <Typography variant="caption" color="text.secondary">
                {code} · {data?.name || name || "—"} vs {selectedBenchmark.code} ·{" "}
                {selectedBenchmark.name}
              </Typography>
            </Box>
            <IconButton
              size="small"
              aria-label="close amount charts"
              onClick={() => setSelectedBenchmark(null)}
              sx={{ flexShrink: 0 }}
            >
              <Close fontSize="small" />
            </IconButton>
          </Box>
          {chartLoading && (
            <Box sx={{ display: "flex", justifyContent: "center", py: 2 }}>
              <CircularProgress size={20} />
            </Box>
          )}
          {chartError && (
            <Alert severity="error" sx={{ py: 0.5 }}>
              {chartError}
            </Alert>
          )}
          {!chartLoading && !chartError && filteredChartData && filteredChartData.rows.length > 0 && (
            <>
              <Box sx={{ mt: 1 }}>
                <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 0.25 }}>
                  Index Trading Amt contribution (benchmark vs subject index ETF turnover)
                </Typography>
                <EChart
                  option={buildAmountContributionOption(filteredChartData, themeMode, "index")}
                  height={170}
                  group={`perf-attr-${code}-${selectedBenchmark.code}`}
                />
                <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 1, mb: 0.25 }}>
                  Industry Trading Amt contribution (benchmark vs subject industry ETF turnover)
                </Typography>
                <EChart
                  option={buildAmountContributionOption(filteredChartData, themeMode, "industry")}
                  height={170}
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
          {!chartLoading && !chartError && filteredChartData && filteredChartData.rows.length === 0 && (
            <Box sx={{ display: "flex", justifyContent: "center", py: 2 }}>
              <Typography variant="body2" color="text.secondary">
                No data in the selected date range.
              </Typography>
            </Box>
          )}
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
    // sorted by avg_abs_active_return DESC).
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
            Click any bar to load two Trading Amount Contribution charts:
            one at the index level (per-index aggregate ETF turnover) and
            one at the industry level (per-industry aggregate ETF turnover),
            comparing benchmark vs subject ETF turnover over time.
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
