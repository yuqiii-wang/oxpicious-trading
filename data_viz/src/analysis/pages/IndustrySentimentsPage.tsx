/**
 * Industry Sentiments analysis page.
 *
 * Plots each industry's member INDEX VALUES directly, rebased to 100 at the
 * start of the displayed (zoom) window. Rebased-to-100 makes member indices
 * comparable regardless of absolute price level — e.g. CSI 500 (~5500pts)
 * and SSE 50 (~2600pts) plot on a common scale, so a +10% move on either
 * looks equally large. The LINE rebasing is computed CLIENT-SIDE from raw
 * daily closes.
 *
 * ADDITIONALLY overlays the server-precomputed MEAN and ±1σ VARIANCE band
 * across member indices for the user-selected pool_size slice
 * (small <51 stocks / mid <301 / large / all). The mean/var are anchored at
 * the START OF ALL HISTORY (per-index first available close, fixed server-side).
 * When the client-side slider narrows, the lines re-rebase to the slider's
 * window-start but the mean/var overlay STAYS anchored at history start —
 * they are aligned only at full slider range.
 *
 * BROAD-MARKET indices (BROAD_CSI, BROAD_SSE, BROAD_SZSE, BROAD_STAR) are
 * classified as industries under the FIN sector and are aggregated IDENTICALLY.
 *
 * COMPOSITION-ONLY: the API only returns indices that have at least one
 * stats.sec_composition snapshot. Indices WITHOUT composition data are never
 * loaded — every member index plotted here has a known stock_num.
 *
 * Data source (queried directly by the API):
 *   stats.index_basic_stats.close   (raw daily index closes)
 *   JOIN stats.sec_classification   (type='index') for industry membership
 *   stats.sec_composition           (stock_num → pool_size classification)
 *   analysis.industry_sentiments    (precomputed mean/var per pool_size slice)
 *
 * Per industry (one plot):
 *   • Lines  = one per member index (filtered by pool_size toggle), rebased
 *              to 100 at the start of the visible (zoom) window.
 *   • Overlay = mean line (dashed, thicker) + ±1σ variance band (shaded)
 *              from the precomputed aggregation for the selected pool_size.
 *   • Slider = bottom, controls the visible range [startIdx, endIdx] in the
 *              shared date axis. Rebase point recomputes for the LINES only.
 *   • Toggle = All / Small / Mid / Large pool-size filter (filters both
 *              lines and overlay).
 *   • Tooltip = per-index actual close (raw value) + rebased % + stock_num.
 */
import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Autocomplete,
  Box,
  Chip,
  CircularProgress,
  Checkbox,
  Collapse,
  IconButton,
  Slider,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import { ArrowBack, ExpandLess, ExpandMore } from "@mui/icons-material";
import { useNavigate } from "react-router-dom";
import ChartCard from "@/components/ChartCard";
import EChart from "@/components/EChart";
import RefreshButton from "@/components/RefreshButton";
import ThemeSelector from "@/components/ThemeSelector";
import { useStore } from "@/store/filters";
import { fmtNum } from "@/lib/series";
import {
  MUTED_PALETTE,
  axisColors,
  commonLegend,
  commonGrid,
} from "@/theme/chart-palette";
import {
  fetchIndustrySentimentsThemes,
  fetchIndustrySentimentsChart,
  fetchIndustryCorrelations,
  invalidateCacheForPrefix,
} from "@/lib/api-client";
import type {
  IndustrySentimentsAggRow,
  IndustrySentimentsChartResponse,
  IndustrySentimentsIndex,
  IndustryCorrelationsResponse,
  SectorNode,
} from "../../../shared/types";
import type { ThemeMode } from "@/store/filters";
import type { EChartsOption } from "echarts";

type PoolSize = "all" | "small" | "mid" | "large";

/** Classify a stock_num into a pool_size bucket. NULL → null (no bucket).
 *  small <51, mid 51-180, large >180. */
function classifyPoolSize(stockNum: number | null): PoolSize | null {
  if (stockNum == null) return null;
  if (stockNum < 51) return "small";
  if (stockNum <= 180) return "mid";
  return "large";
}

/** Stable color per benchmark code — used for the line, the dropdown
 *  checkbox indicator, and the tooltip ━ marker so each benchmark is
 *  visually consistent across the UI. */
const BENCHMARK_COLORS: Record<string, string> = {
  "000300": "#ff6b35", // 沪深300 — orange
  "000016": "#1565c0", // 上证50 — blue
  "000852": "#6a1b9a", // 中证1000 — purple
  "000688": "#00897b", // 科创50 — teal
};

/** Distinct colors for per-industry mean curves in multi-industry "Mean only"
 *  mode. ColorBrewer Set1 — high contrast and colorblind-friendly. Each
 *  industry's mean line + ±1σ band reuses the same color so the user can
 *  visually pair a mean curve with its dispersion band. */
const MEAN_PALETTE = [
  "#e41a1c", // red
  "#377eb8", // blue
  "#4daf4a", // green
  "#984ea3", // purple
  "#ff7f00", // orange
  "#a65628", // brown
  "#f781bf", // pink
  "#999999", // grey
];

// ----------------------------------------------------------------------------
//  Rebase helper — rebase an index's close series to 100 at the first
//  non-null close within the visible window [lo, hi].
// ----------------------------------------------------------------------------
function rebaseTo100(
  closes: Array<number | null>,
  lo: number,
  hi: number,
): Array<number | null> {
  const n = closes.length;
  const start = Math.max(0, Math.min(lo, n - 1));
  const end = Math.max(0, Math.min(hi, n - 1));
  let rebasePoint: number | null = null;
  for (let i = start; i <= end; i++) {
    const v = closes[i];
    if (v != null && Number.isFinite(v) && Math.abs(v) > 1e-9) {
      rebasePoint = v;
      break;
    }
  }
  if (rebasePoint == null) return closes.map(() => null);
  return closes.map((v) =>
    v == null || !Number.isFinite(v) ? null : (v / rebasePoint) * 100,
  );
}

// ----------------------------------------------------------------------------
//  Build the chart option — one line per member index (filtered by pool_size)
//  rebased to 100, PLUS mean line + ±1σ variance band overlay.
// ----------------------------------------------------------------------------
/** One industry's precomputed aggregation set, used to render a per-industry
 *  mean curve in multi-industry "Mean only" mode. */
interface PerIndustryAggregation {
  industry_id: string;
  industry_label: string;
  aggregation: IndustrySentimentsAggRow[];
}

function buildIndustryChartOption(
  data: IndustrySentimentsChartResponse,
  allDates: string[],
  visibleLo: number,
  visibleHi: number,
  poolSize: PoolSize,
  themeMode: ThemeMode,
  selectedBenchmarkCodes: string[],
  meanOnly: boolean,
  hideHK: boolean,
  /** Single-industry overlay: render the merged mean + ±1σ band from
   *  data.aggregation. False in multi-industry mode. */
  showAggOverlay: boolean,
  /** Per-industry aggregation sets for multi-industry mean overlay. When
   *  non-empty AND meanOnly is true, one mean curve (with ±1σ band) is
   *  rendered per industry, each in a distinct MEAN_PALETTE color. Empty
   *  in single-industry mode. */
  perIndustryAggregations: PerIndustryAggregation[] = [],
): EChartsOption {
  const c = axisColors(themeMode);
  const visibleDates = allDates.slice(visibleLo, visibleHi + 1);

  // ALL indices are always rendered (when not meanOnly). Two independent
  // dim/highlight layers compose:
  //   1. pool_size toggle — highlights selected pool, dims others
  //   2. hideHK — when ON, dims indices with exchange='HK'
  // An index is HIGHLIGHTED only if ALL active layers agree it should be.
  // Dimmed indices are rendered at ~15% opacity (still visible for context).
  //
  // NOTE: the API only returns compositioned indices (every member has a
  // stock_num), so there is no longer a "no composition" case to handle here.
  const rawClosesPerIdx: Array<Array<number | null>> = [];
  const stockNumPerIdx: Array<number | null> = [];
  const series: Array<Record<string, unknown>> = [];

  if (!meanOnly) {
    for (let i = 0; i < data.indices.length; i++) {
      const idx = data.indices[i];
      const latestStockNum = idx.rows.reduce<number | null>(
        (acc, r) => (r.stock_num != null ? r.stock_num : acc),
        null,
      );

      // Layer 1: pool_size highlight
      const poolHighlighted =
        poolSize === "all" ||
        idx.rows.some((r) => classifyPoolSize(r.stock_num) === poolSize);
      // Layer 2: HK (hideHK dims indices with exchange='HK')
      const hkHidden = hideHK && idx.exchange === "HK";

      const isHighlighted = poolHighlighted && !hkHidden;

      const closeByDate = new Map<string, number | null>();
      for (const r of idx.rows) {
        closeByDate.set(r.date, r.close);
      }
      const closesAligned: Array<number | null> = allDates.map(
        (d) => closeByDate.get(d) ?? null,
      );
      stockNumPerIdx.push(latestStockNum);
      const rebased = rebaseTo100(closesAligned, visibleLo, visibleHi);
      rawClosesPerIdx.push(closesAligned.slice(visibleLo, visibleHi + 1));
      const visibleData = rebased.slice(visibleLo, visibleHi + 1);
      const color = MUTED_PALETTE[i % MUTED_PALETTE.length];
      series.push({
        name: idx.name || idx.code,
        type: "line",
        smooth: false,
        showSymbol: false,
        data: visibleData,
        lineStyle: isHighlighted
          ? { width: 1.6, color, opacity: 1 }
          : { width: 0.8, color, opacity: 0.15 },
        itemStyle: { color, opacity: isHighlighted ? 1 : 0.15 },
        z: isHighlighted ? 3 : 2,
      });
    }
  }

  // ---- Benchmark lines (one per selected benchmark) — rebased to 100 ----
  // Each selected benchmark is rebased to 100 at the visible window start
  // (same as member indices) and rendered as a thick colored line.
  const visibleBenchmarks = data.benchmarks.filter((b) =>
    selectedBenchmarkCodes.includes(b.code),
  );
  for (const bm of visibleBenchmarks) {
    const bmCloseByDate = new Map<string, number | null>();
    for (const r of bm.rows) {
      bmCloseByDate.set(r.date, r.close);
    }
    // Extend allDates with benchmark dates (benchmark may have dates outside
    // the union of member indices — e.g. when the industry has few indices).
    const bmClosesAligned: Array<number | null> = allDates.map(
      (d) => bmCloseByDate.get(d) ?? null,
    );
    const bmRebased = rebaseTo100(bmClosesAligned, visibleLo, visibleHi);
    const bmVisibleData = bmRebased.slice(visibleLo, visibleHi + 1);
    const color = BENCHMARK_COLORS[bm.code] ?? "#ff6b35";
    series.push({
      name: bm.name,
      type: "line",
      smooth: false,
      showSymbol: false,
      data: bmVisibleData,
      lineStyle: { width: 2.5, color },
      itemStyle: { color },
      z: 4,
    });
  }

  // ---- Mean + ±1σ variance band overlay (server-precomputed) ----
  // Filter aggregation rows to the selected pool_size, build aligned arrays
  // over allDates, then slice to visible range. mean/var are anchored at
  // HISTORY START (fixed server-side) — they do NOT re-rebase when the slider
  // narrows. This is the documented tradeoff.
  //
  // SKIPPED entirely when showAggOverlay is false (multi-industry merge):
  // aggregation is per-industry and cannot be combined across industries.
  if (showAggOverlay) {
    const aggByDate = new Map<string, { mean: number | null; var: number | null; count: number | null }>();
    for (const a of data.aggregation) {
      if (a.pool_size !== poolSize) continue;
      aggByDate.set(a.date, {
        mean: a.mean_rebased,
        var: a.var_rebased,
        count: a.index_count,
      });
    }
    const meanAligned: Array<number | null> = allDates.map(
      (d) => aggByDate.get(d)?.mean ?? null,
    );
    const varAligned: Array<number | null> = allDates.map(
      (d) => aggByDate.get(d)?.var ?? null,
    );
    // ±1σ band: mean ± sqrt(var). Null when mean or var is null.
    const upperBand: Array<number | null> = meanAligned.map((m, i) => {
      const v = varAligned[i];
      if (m == null || v == null || v < 0) return null;
      return m + Math.sqrt(v);
    });
    const lowerBand: Array<number | null> = meanAligned.map((m, i) => {
      const v = varAligned[i];
      if (m == null || v == null || v < 0) return null;
      return m - Math.sqrt(v);
    });
    const meanVisible = meanAligned.slice(visibleLo, visibleHi + 1);
    const upperVisible = upperBand.slice(visibleLo, visibleHi + 1);
    const lowerVisible = lowerBand.slice(visibleLo, visibleHi + 1);

    // Mean line (dashed, thicker, dark gray).
    series.push({
      name: `mean (${poolSize})`,
      type: "line",
      smooth: false,
      showSymbol: false,
      data: meanVisible,
      lineStyle: { width: 2, color: "#444", type: "dashed" },
      itemStyle: { color: "#444" },
      z: 5,
    });
    // ±1σ band as stacked area (upper band visible, lower band transparent).
    // Render as a translucent filled band via two stacked area series.
    series.push({
      name: "+1σ",
      type: "line",
      smooth: false,
      showSymbol: false,
      data: upperVisible,
      lineStyle: { width: 0, opacity: 0 },
      areaStyle: { color: "rgba(100,100,100,0.12)", opacity: 0.5 },
      stack: "sigmaBand",
      z: 1,
    });
    series.push({
      name: "-1σ",
      type: "line",
      smooth: false,
      showSymbol: false,
      data: lowerVisible,
      lineStyle: { width: 0, opacity: 0 },
      // Fill BELOW the lower band with transparent — the band between upper and
      // lower is rendered by stacking the difference. ECharts stack semantics
      // require careful handling; the simplest correct approach is to render
      // the band as a single series with custom data via renderItem. For
      // simplicity here, we render the upper band as a shaded area DOWN to
      // a baseline of 0, and the lower band as a shaded area down to 0; the
      // overlap region (lower→upper) is darker. Acceptable visual proxy for
      // ±1σ dispersion.
      areaStyle: { color: "rgba(100,100,100,0.06)", opacity: 0.5 },
      stack: "sigmaBand",
      z: 1,
    });
  }

  // ---- Per-industry mean curves (multi-industry "Mean only" mode) ----
  // When multiple industries are merged AND meanOnly is ON, render ONE mean
  // curve PER industry (each filtered by the selected pool_size), each in a
  // distinct MEAN_PALETTE color with a matching ±1σ band. This lets the user
  // compare industry-level sentiment trends on a common rebased-to-100 scale.
  // Anchored at HISTORY START (same as single-industry overlay) — the slider
  // narrows the visible slice but does NOT re-rebase the means.
  //
  // Only rendered when meanOnly is ON (to avoid clutter alongside all the
  // per-index lines). When meanOnly is OFF in multi-industry mode, only the
  // per-index lines are shown (no mean overlay).
  if (perIndustryAggregations.length > 0 && meanOnly) {
    perIndustryAggregations.forEach((agg, i) => {
      const color = MEAN_PALETTE[i % MEAN_PALETTE.length];
      const shortLabel = (agg.industry_label || agg.industry_id).split("  ")[0] || agg.industry_id;
      const aggByDate = new Map<string, { mean: number | null; var: number | null }>();
      for (const a of agg.aggregation) {
        if (a.pool_size !== poolSize) continue;
        aggByDate.set(a.date, { mean: a.mean_rebased, var: a.var_rebased });
      }
      const meanAligned: Array<number | null> = allDates.map(
        (d) => aggByDate.get(d)?.mean ?? null,
      );
      const varAligned: Array<number | null> = allDates.map(
        (d) => aggByDate.get(d)?.var ?? null,
      );
      const meanVisible = meanAligned.slice(visibleLo, visibleHi + 1);

      // Mean line (solid, thick, industry-colored).
      series.push({
        name: `mean · ${shortLabel}`,
        type: "line",
        smooth: false,
        showSymbol: false,
        data: meanVisible,
        lineStyle: { width: 2.5, color },
        itemStyle: { color },
        z: 5,
      });

      // ±1σ band for this industry (translucent, same color). Rendered as
      // upper + lower area series stacked together; the fill between them
      // conveys dispersion. Lighter opacity than the mean line so overlaps
      // remain readable when industries' bands overlap.
      const upperBand: Array<number | null> = meanAligned.map((m, j) => {
        const v = varAligned[j];
        if (m == null || v == null || v < 0) return null;
        return m + Math.sqrt(v);
      });
      const lowerBand: Array<number | null> = meanAligned.map((m, j) => {
        const v = varAligned[j];
        if (m == null || v == null || v < 0) return null;
        return m - Math.sqrt(v);
      });
      series.push({
        name: `+1σ · ${shortLabel}`,
        type: "line",
        smooth: false,
        showSymbol: false,
        data: upperBand.slice(visibleLo, visibleHi + 1),
        lineStyle: { width: 0, opacity: 0 },
        areaStyle: { color, opacity: 0.12 },
        stack: `sigmaBand_${agg.industry_id}`,
        z: 1,
      });
      series.push({
        name: `-1σ · ${shortLabel}`,
        type: "line",
        smooth: false,
        showSymbol: false,
        data: lowerBand.slice(visibleLo, visibleHi + 1),
        lineStyle: { width: 0, opacity: 0 },
        areaStyle: { color, opacity: 0.06 },
        stack: `sigmaBand_${agg.industry_id}`,
        z: 1,
      });
    });
  }

  return {
    backgroundColor: "transparent",
    animation: false,
    grid: commonGrid({ left: 56, right: 24, bottom: 32 }),
    legend: commonLegend(themeMode, {
      data: series.map((s) => s.name as string),
    }),
    tooltip: {
      trigger: "axis",
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
        const idx0 = arr[0].dataIndex ?? 0;
        const dateStr = visibleDates[idx0] ?? "";
        if (!dateStr) return "";
        // Build per-index rows: name, actual close, rebased %, stock_num.
        const rowsHtml = arr
          .map((p) => {
            const sIdx = data.indices.findIndex(
              (idx) => (idx.name || idx.code) === p.seriesName,
            );
            if (sIdx < 0) {
              // Benchmark row(s) — show actual close + rebased %.
              const bm = data.benchmarks.find((b) => b.name === p.seriesName);
              if (bm) {
                const v = p.value;
                const fmtV = (x: number | null | undefined) => {
                  if (x == null || !Number.isFinite(x)) return "—";
                  const pct = x - 100;
                  return (pct >= 0 ? "+" : "") + fmtNum(pct, 2) + "%";
                };
                // Look up the benchmark's raw close on this date.
                const bmCloseByDate = new Map<string, number | null>();
                for (const r of bm.rows) bmCloseByDate.set(r.date, r.close);
                const rawClose = bmCloseByDate.get(dateStr) ?? null;
                const fmtRaw = (v: number | null | undefined) =>
                  v == null || !Number.isFinite(v) ? "—" : fmtNum(v, 2);
                const color = BENCHMARK_COLORS[bm.code] ?? "#ff6b35";
                return `<div style="display:flex;justify-content:space-between;gap:12px">
                  <span style="color:${color}">━</span>
                  <span style="flex:1;font-weight:600">${p.seriesName ?? ""}</span>
                  <span style="opacity:0.7">${fmtRaw(rawClose)}</span>
                  <b>${fmtV(v)}</b>
                </div>`;
              }
              // Mean / ±1σ rows — show value directly.
              const v = p.value;
              const fmtV = (x: number | null | undefined) => {
                if (x == null || !Number.isFinite(x)) return "—";
                const pct = x - 100;
                return (pct >= 0 ? "+" : "") + fmtNum(pct, 2) + "%";
              };
              if (p.seriesName?.startsWith("mean")) {
                // Mean row. Two cases:
                //   • single-industry: name = "mean (all)" → look up var from
                //     data.aggregation (the merged set).
                //   • multi-industry:  name = "mean · <shortLabel>" → look up
                //     var from the matching perIndustryAggregations entry.
                const idx0Local = p.dataIndex ?? 0;
                let varVal: number | null = null;
                let meanColor = "#444";
                if (perIndustryAggregations.length > 0) {
                  // Parse the shortLabel from "mean · <shortLabel>".
                  const shortLabel = p.seriesName.includes(" · ")
                    ? p.seriesName.split(" · ").slice(1).join(" · ")
                    : "";
                  const matchIdx = perIndustryAggregations.findIndex(
                    (a) => (a.industry_label || a.industry_id).split("  ")[0] === shortLabel
                      || a.industry_id === shortLabel,
                  );
                  if (matchIdx >= 0) {
                    meanColor = MEAN_PALETTE[matchIdx % MEAN_PALETTE.length];
                    const aggRow = perIndustryAggregations[matchIdx].aggregation.find(
                      (a) => a.date === dateStr && a.pool_size === poolSize,
                    );
                    varVal = aggRow?.var_rebased ?? null;
                  }
                } else {
                  const aggRow = data.aggregation.find(
                    (a) => a.date === dateStr && a.pool_size === poolSize,
                  );
                  varVal = aggRow?.var_rebased ?? null;
                }
                const fmtVar = (x: number | null | undefined) => {
                  if (x == null || !Number.isFinite(x)) return "—";
                  return fmtNum(x, 2);
                };
                return `<div style="display:flex;justify-content:space-between;gap:12px">
                  <span style="color:${meanColor}">┄</span>
                  <span style="flex:1;font-weight:600">${p.seriesName ?? ""}</span>
                  <span style="opacity:0.7">var ${fmtVar(varVal)}</span>
                  <b>${fmtV(v)}</b>
                </div>`;
              }
              return ""; // ±1σ band — skip in tooltip
            }
            const raw = rawClosesPerIdx[sIdx][p.dataIndex ?? 0];
            const rebased = p.value;
            const sn = stockNumPerIdx[sIdx];
            const fmtRaw = (v: number | null | undefined) =>
              v == null || !Number.isFinite(v) ? "—" : fmtNum(v, 2);
            const fmtRebased = (v: number | null | undefined) => {
              if (v == null || !Number.isFinite(v)) return "—";
              const pct = v - 100;
              return (pct >= 0 ? "+" : "") + fmtNum(pct, 2) + "%";
            };
            const color = MUTED_PALETTE[sIdx % MUTED_PALETTE.length];
            const stockNumStr = sn == null ? "" : ` · ${sn} stocks`;
            return `<div style="display:flex;justify-content:space-between;gap:12px">
              <span style="color:${color}">●</span>
              <span style="flex:1">${p.seriesName ?? ""}<span style="opacity:0.5;font-size:0.9em">${stockNumStr}</span></span>
              <span style="opacity:0.7">${fmtRaw(raw)}</span>
              <b>${fmtRebased(rebased)}</b>
            </div>`;
          })
          .join("");

        // ---- One-date stats summary (header block) ----
        // Aggregate across all HIGHLIGHTED indices that have data on this date.
        const fmtRawOuter = (v: number | null | undefined) =>
          v == null || !Number.isFinite(v) ? "—" : fmtNum(v, 2);
        const highlightedRebased: Array<{ name: string; rebased: number; raw: number | null }> = [];
        for (let i = 0; i < data.indices.length; i++) {
          const idx = data.indices[i];
          const poolHighlighted =
            poolSize === "all" ||
            idx.rows.some((r) => classifyPoolSize(r.stock_num) === poolSize);
          const hkHidden = hideHK && idx.exchange === "HK";
          if (!poolHighlighted || hkHidden) continue;
          const raw = rawClosesPerIdx[i]?.[idx0] ?? null;
          const rebasedVal = series[i] && Array.isArray((series[i] as Record<string, unknown>).data)
            ? ((series[i] as Record<string, unknown>).data as Array<number | null>)[idx0] ?? null
            : null;
          if (rebasedVal != null && Number.isFinite(rebasedVal)) {
            highlightedRebased.push({ name: idx.name || idx.code, rebased: rebasedVal, raw });
          }
        }

        const fmtPct = (x: number) => {
          const pct = x - 100;
          return (pct >= 0 ? "+" : "") + fmtNum(pct, 2) + "%";
        };

        let statsHtml = "";
        if (highlightedRebased.length > 0) {
          const vals = highlightedRebased.map((h) => h.rebased);
          const mean = vals.reduce((a, b) => a + b, 0) / vals.length;
          const variance = vals.length > 1
            ? vals.reduce((a, b) => a + (b - mean) ** 2, 0) / (vals.length - 1)
            : 0;
          const maxItem = highlightedRebased.reduce((a, b) => (b.rebased > a.rebased ? b : a));
          const minItem = highlightedRebased.reduce((a, b) => (b.rebased < a.rebased ? b : a));
          const sd = Math.sqrt(variance);
          statsHtml = `<div style="margin-top:4px;padding:4px 6px;border:1px solid ${c.splitLineColor};border-radius:3px;font-size:0.95em">
            <div style="display:flex;justify-content:space-between;gap:12px">
              <span style="opacity:0.7">highlighted</span><b>${highlightedRebased.length} indices</b>
            </div>
            <div style="display:flex;justify-content:space-between;gap:12px">
              <span style="opacity:0.7">mean</span><b>${fmtPct(mean)}</b>
            </div>
            <div style="display:flex;justify-content:space-between;gap:12px">
              <span style="opacity:0.7">var / σ</span><b>${fmtNum(variance, 2)} / ${fmtNum(sd, 2)}</b>
            </div>
            <div style="display:flex;justify-content:space-between;gap:12px">
              <span style="opacity:0.7">max</span><b>${fmtPct(maxItem.rebased)} (${maxItem.name})</b>
            </div>
            <div style="display:flex;justify-content:space-between;gap:12px">
              <span style="opacity:0.7">min</span><b>${fmtPct(minItem.rebased)} (${minItem.name})</b>
            </div>
          </div>`;
        }

        // Benchmark close + rebased % for this date (one row per visible
        // selected benchmark).
        let bmHtml = "";
        for (const bm of visibleBenchmarks) {
          const bmRow = bm.rows.find((r) => r.date === dateStr);
          if (!bmRow || bmRow.close == null) continue;
          const bmSeries = series.find(
            (s) => (s as Record<string, unknown>).name === bm.name,
          ) as { data?: Array<number | null> } | undefined;
          const bmRebased = bmSeries?.data?.[idx0] ?? null;
          const fmtBmPct = (v: number | null) => {
            if (v == null || !Number.isFinite(v)) return "—";
            return fmtPct(v);
          };
          const color = BENCHMARK_COLORS[bm.code] ?? "#ff6b35";
          bmHtml += `<div style="display:flex;justify-content:space-between;gap:12px;margin-top:2px">
            <span style="color:${color}">━</span>
            <span style="flex:1;opacity:0.7">${bm.name}</span>
            <span style="opacity:0.7">${fmtRawOuter(bmRow.close)}</span>
            <b style="color:${color}">${fmtBmPct(bmRebased)}</b>
          </div>`;
        }

        return `<div style="font-weight:600">${dateStr}</div>
                <div style="margin-top:2px;opacity:0.7">Rebased to 100 at window start · actual close shown</div>
                ${statsHtml}
                ${bmHtml}
                <div style="margin-top:4px">${rowsHtml}</div>`;
      },
    },
    xAxis: {
      type: "category",
      data: visibleDates,
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
      name: "Rebased (start = 100)",
      nameTextStyle: { color: c.textColor, fontSize: 9 },
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: {
        color: c.textColor,
        fontSize: 9,
        formatter: (v: number) => fmtNum(v, 1),
      },
      splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.4 } },
    },
    series,
  };
}

// ============================================================================
//  Plot card — multi-line chart + pool-size toggle + benchmark dropdown +
//  mean-only toggle + date-range slider.
// ============================================================================
interface PlotProps {
  data: IndustrySentimentsChartResponse;
  themeMode: ThemeMode;
  /** When true, the data is a merge of multiple industries. The single
   *  mean/var overlay is hidden; instead, when meanOnly is ON, one mean
   *  curve per industry is rendered (each in a distinct color). */
  multiIndustry: boolean;
  /** Number of source industries in the merge (1 when single-select). Used
   *  only for the subtitle when multiIndustry is true. */
  numIndustries: number;
  /** Per-industry chart responses (the un-merged source data). Used to build
   *  per-industry aggregation sets for the multi-industry mean overlay. Empty
   *  in single-industry mode (the merged `data.aggregation` is used instead). */
  chartDataList: IndustrySentimentsChartResponse[];
  /** Selected industry IDs (passed through from the page so the Correlation
   *  button can fetch pairwise correlation rows from the API). Empty in
   *  single-industry mode. */
  selectedIndustryIds: string[];
}

function IndustrySentimentsPlot({
  data,
  themeMode,
  multiIndustry,
  numIndustries,
  chartDataList,
  selectedIndustryIds,
}: PlotProps) {
  const [range, setRange] = useState<[number, number]>([0, 0]);
  const [poolSize, setPoolSize] = useState<PoolSize>("all");
  const [selectedBenchmarks, setSelectedBenchmarks] = useState<string[]>([]);
  const [meanOnly, setMeanOnly] = useState(false);
  const [hideHK, setHideHK] = useState(false);

  // ---- Correlation expandable section ----
  // The Correlation button is only enabled when 2+ industries are selected.
  // On click, the parent ChartCard expands vertically to reveal a second
  // chart showing pairwise rolling correlations (one line per industry pair).
  // The chart shows the SELECTED INDUSTRIES' MEAN values being correlated
  // (input data), and the tooltip on hover shows the correlation value(s)
  // at the hovered date.
  const [showCorrelation, setShowCorrelation] = useState(false);
  const correlationEnabled = selectedIndustryIds.length >= 2;

  // Single-industry overlay: render the merged mean + ±1σ band from
  // data.aggregation. Only in single-industry mode.
  const showAggOverlay = !multiIndustry && data.aggregation.length > 0;

  // Per-industry aggregation sets for multi-industry mean overlay. Built from
  // chartDataList — each industry's aggregation array is passed through
  // verbatim (filtered by pool_size inside buildIndustryChartOption).
  // Industries with no aggregation rows (analysis not yet run) are dropped.
  const perIndustryAggregations: PerIndustryAggregation[] = useMemo(() => {
    if (!multiIndustry) return [];
    return chartDataList
      .filter((d) => d.aggregation.length > 0)
      .map((d) => ({
        industry_id: d.industry_id,
        industry_label: d.industry_label,
        aggregation: d.aggregation,
      }));
  }, [multiIndustry, chartDataList]);

  // Whether the "Mean only" toggle should be enabled. In single-industry
  // mode it needs data.aggregation; in multi-industry mode it needs at least
  // one industry with aggregation rows.
  const meanToggleEnabled = multiIndustry
    ? perIndustryAggregations.length > 0
    : showAggOverlay;

  // Build the unified date axis — sorted union of all member indices' dates
  // PLUS benchmark dates (so benchmark lines span the full chart width even
  // when member indices have fewer dates).
  const allDates = useMemo(() => {
    const set = new Set<string>();
    for (const idx of data.indices) for (const r of idx.rows) set.add(r.date);
    for (const bm of data.benchmarks) for (const r of bm.rows) set.add(r.date);
    return Array.from(set).sort();
  }, [data]);

  useEffect(() => {
    setRange([0, Math.max(0, allDates.length - 1)]);
  }, [allDates]);

  const maxIdx = Math.max(0, allDates.length - 1);
  // Count indices in each pool bucket + HK (for toggle labels). The API only
  // returns compositioned indices, so every member has a stock_num — there is
  // no "null composition" bucket anymore.
  const poolCounts = useMemo(() => {
    const counts = { all: data.indices.length, small: 0, mid: 0, large: 0, hk: 0 };
    for (const idx of data.indices) {
      if (idx.exchange === "HK") counts.hk++;
      const ps = idx.rows.reduce<PoolSize | null>(
        (acc, r) => (r.stock_num != null ? classifyPoolSize(r.stock_num) : acc),
        null,
      );
      if (ps) counts[ps]++;
    }
    return counts;
  }, [data.indices]);

  const visibleCount = useMemo(() => {
    if (poolSize === "all") return data.indices.length;
    return data.indices.filter((idx) =>
      idx.rows.some((r) => classifyPoolSize(r.stock_num) === poolSize),
    ).length;
  }, [data.indices, poolSize]);

  return (
    <ChartCard
      title={data.industry_label || data.industry_id}
      subtitle={
        multiIndustry
          ? `${data.indices.length} member indices across ${numIndustries} industries — ${visibleCount} highlighted (${poolSize} pool)`
          : `${data.industry_label || data.industry_id} — ${visibleCount} of ${data.indices.length} member indices highlighted (${poolSize} pool)`
      }
    >
      <Box sx={{ display: "flex", justifyContent: "space-between", gap: 2, mb: 1, flexWrap: "wrap" }}>
        <ToggleButtonGroup
          value={poolSize}
          exclusive
          size="small"
          onChange={(_, v: PoolSize | null) => v && setPoolSize(v)}
        >
          <ToggleButton value="all">All ({poolCounts.all})</ToggleButton>
          <ToggleButton value="small">Small &lt;51 ({poolCounts.small})</ToggleButton>
          <ToggleButton value="mid">Mid 51-180 ({poolCounts.mid})</ToggleButton>
          <ToggleButton value="large">Large &gt;180 ({poolCounts.large})</ToggleButton>
        </ToggleButtonGroup>
        {/* Benchmark dropdown (multi-select with checkboxes) + standalone
            ToggleButtons (NOT inside ToggleButtonGroup) to avoid the group
            intercepting clicks and getting stuck on double-click. Each
            button independently toggles its own boolean state. */}
        <Box sx={{ display: "flex", gap: 1, alignItems: "center", flexWrap: "wrap" }}>
          {data.benchmarks.length > 0 && (
            <Autocomplete
              multiple
              size="small"
              disableCloseOnSelect
              limitTags={3}
              options={data.benchmarks}
              getOptionLabel={(b) => b.name}
              isOptionEqualToValue={(a, b) => a.code === b.code}
              value={data.benchmarks.filter((b) =>
                selectedBenchmarks.includes(b.code),
              )}
              onChange={(_, newValue) =>
                setSelectedBenchmarks(newValue.map((b) => b.code))
              }
              renderOption={(props, option, { selected }) => (
                <li {...props} style={{ display: "flex", alignItems: "center", gap: 4 }}>
                  <Checkbox size="small" checked={selected} sx={{ p: 0.5 }} />
                  <span style={{ color: BENCHMARK_COLORS[option.code] ?? "#ff6b35", fontWeight: 700 }}>━</span>
                  <span>{option.name}</span>
                </li>
              )}
              renderTags={(value, getTagProps) =>
                value.map((option, index) => {
                  const { key, ...tagProps } = getTagProps({ index });
                  return (
                    <Chip
                      key={key}
                      size="small"
                      label={option.name.replace(" (benchmark)", "")}
                      {...tagProps}
                      sx={{
                        height: 22,
                        borderColor: BENCHMARK_COLORS[option.code] ?? "#ff6b35",
                        "& .MuiChip-label": { fontSize: "0.7rem", px: 0.5 },
                      }}
                    />
                  );
                })
              }
              renderInput={(params) => (
                <TextField
                  {...params}
                  size="small"
                  label="Benchmarks"
                  placeholder={selectedBenchmarks.length === 0 ? "Tick to show" : ""}
                  sx={{ minWidth: 170, "& .MuiOutlinedInput-root": { py: 0.25 } }}
                />
              )}
              sx={{ minWidth: 170, maxWidth: 280 }}
            />
          )}
          <ToggleButton
            size="small"
            value="meanOnly"
            selected={meanOnly}
            onClick={() => setMeanOnly((v) => !v)}
            disabled={!meanToggleEnabled}
            sx={!meanToggleEnabled ? { opacity: 0.4 } : {}}
          >
            Mean only{multiIndustry && meanToggleEnabled ? ` (${perIndustryAggregations.length})` : ""}
          </ToggleButton>
          <ToggleButton
            size="small"
            value="hideHK"
            selected={hideHK}
            onClick={() => setHideHK((v) => !v)}
          >
            Hide HK ({poolCounts.hk})
          </ToggleButton>
          <ToggleButton
            size="small"
            value="correlation"
            selected={showCorrelation}
            onClick={() => setShowCorrelation((v) => !v)}
            disabled={!correlationEnabled}
            sx={!correlationEnabled ? { opacity: 0.4 } : {}}
            title={
              correlationEnabled
                ? "Expand this card to show pairwise rolling correlations between selected industries"
                : "Select 2+ industries to enable"
            }
          >
            Correlation
            {correlationEnabled ? ` (${selectedIndustryIds.length})` : ""}
            {showCorrelation ? <ExpandLess fontSize="small" /> : <ExpandMore fontSize="small" />}
          </ToggleButton>
        </Box>
      </Box>
      {data.indices.length === 0 || allDates.length === 0 ? (
        <Box sx={{ display: "flex", justifyContent: "center", py: 2 }}>
          <Typography variant="body2" color="text.secondary">
            No member indices with close data for {data.industry_id}.
          </Typography>
        </Box>
      ) : (
        <>
          <EChart
            option={buildIndustryChartOption(
              data,
              allDates,
              range[0],
              range[1],
              poolSize,
              themeMode,
              selectedBenchmarks,
              meanOnly,
              hideHK,
              showAggOverlay,
              perIndustryAggregations,
            )}
            height={460}
          />
          {maxIdx > 0 && (
            <Box sx={{ px: 1, mt: 0.5 }}>
              <Slider
                value={range}
                onChange={(_, v) => setRange(v as [number, number])}
                min={0}
                max={maxIdx}
                size="small"
                valueLabelDisplay="auto"
                valueLabelFormat={(idx) => allDates[idx] ?? ""}
                sx={{ mt: 0.5, "& .MuiSlider-valueLabel": { fontSize: "0.7rem" } }}
              />
              <Stack direction="row" justifyContent="space-between" sx={{ mt: -0.5 }}>
                <Typography variant="caption" color="text.secondary" sx={{ fontSize: "0.65rem" }}>
                  {allDates[range[0]] ?? "—"}
                </Typography>
                <Typography variant="caption" color="text.secondary" sx={{ fontSize: "0.65rem" }}>
                  {allDates[range[1]] ?? "—"}
                </Typography>
              </Stack>
            </Box>
          )}
        </>
      )}
      <Collapse in={showCorrelation && correlationEnabled} timeout="auto" unmountOnExit>
        <Box sx={{ mt: 2, pt: 1, borderTop: 1, borderColor: "divider" }}>
          <CorrelationChart
            industryIds={selectedIndustryIds}
            poolSize={poolSize}
            themeMode={themeMode}
          />
        </Box>
      </Collapse>
    </ChartCard>
  );
}

// ============================================================================
//  Correlation chart — expandable section below the main multi-line chart.
//  Renders one line per industry pair, showing the rolling Pearson
//  correlation of the two industries' mean_rebased series over the
//  user-selected window (5d / 20d / 60d / 255d). Hover shows the
//  correlation value(s) at the hovered date.
//
//  Disabled (button hidden) when fewer than 2 industries are selected —
//  there are no pairs to correlate. The button lives in the parent plot's
//  toolbar; this component is only rendered inside the Collapse when open.
// ============================================================================
type CorrWindow = "5d" | "20d" | "60d" | "255d";

const CORR_WINDOWS: CorrWindow[] = ["5d", "20d", "60d", "255d"];

interface CorrelationChartProps {
  industryIds: string[];
  poolSize: PoolSize;
  themeMode: ThemeMode;
}

function CorrelationChart({
  industryIds,
  poolSize,
  themeMode,
}: CorrelationChartProps) {
  const [data, setData] = useState<IndustryCorrelationsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [window, setWindow] = useState<CorrWindow>("60d");

  // Stable key for the fetch effect — refetch when industry set or pool changes.
  const idsKey = industryIds.slice().sort().join(",");
  useEffect(() => {
    if (industryIds.length < 2) {
      setData(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchIndustryCorrelations(industryIds, poolSize)
      .then((resp) => {
        if (cancelled) return;
        setData(resp);
        setLoading(false);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setError(e.message);
        setLoading(false);
      });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [idsKey, poolSize]);

  // Build the chart option — one line per industry pair, plotting the
  // rolling correlation for the user-selected window over time. Tooltip
  // shows all 4 windows' values at the hovered date (richer than just the
  // selected window — lets the user compare short vs long-term co-movement
  // at a glance without toggling windows).
  const option = useMemo<EChartsOption | null>(() => {
    if (!data || data.correlations.length === 0) return null;
    const c = axisColors(themeMode);

    // Group rows by pair key (industry_id, benchmark_industry_id). Each
    // pair becomes one series. Pairs are sorted lexicographically for
    // stable color assignment.
    const pairKeys = new Set<string>();
    const byPair = new Map<string, typeof data.correlations>();
    for (const row of data.correlations) {
      const key = `${row.industry_id}\u0000${row.benchmark_industry_id}`;
      pairKeys.add(key);
      let arr = byPair.get(key);
      if (!arr) {
        arr = [];
        byPair.set(key, arr);
      }
      arr.push(row);
    }
    const sortedPairs = Array.from(pairKeys).sort();
    // Sorted union of all pair dates — X axis.
    const allDatesSet = new Set<string>();
    for (const row of data.correlations) allDatesSet.add(row.date);
    const allDates = Array.from(allDatesSet).sort();

    // Selected window column → numeric value.
    const windowCol: Record<CorrWindow, "corr_5d" | "corr_20d" | "corr_60d" | "corr_255d"> = {
      "5d": "corr_5d",
      "20d": "corr_20d",
      "60d": "corr_60d",
      "255d": "corr_255d",
    };

    const series: Array<Record<string, unknown>> = sortedPairs.map((key, i) => {
      const rows = byPair.get(key)!;
      const byDate = new Map<string, typeof rows[number]>();
      for (const r of rows) byDate.set(r.date, r);
      const pair = rows[0];
      const labelA = pair.industry_label || pair.industry_id;
      const labelB = pair.benchmark_industry_label || pair.benchmark_industry_id;
      const shortA = labelA.split("  ")[0] || pair.industry_id;
      const shortB = labelB.split("  ")[0] || pair.benchmark_industry_id;
      const name = `${shortA} ↔ ${shortB}`;
      const color = MUTED_PALETTE[i % MUTED_PALETTE.length];
      const aligned = allDates.map(
        (d) => byDate.get(d)?.[windowCol[window]] ?? null,
      );
      return {
        name,
        type: "line",
        smooth: false,
        showSymbol: false,
        connectNulls: false,
        data: aligned,
        lineStyle: { width: 1.6, color },
        itemStyle: { color },
        z: 3,
      };
    });

    return {
      backgroundColor: "transparent",
      animation: false,
      grid: commonGrid({ left: 56, right: 24, bottom: 32 }),
      legend: commonLegend(themeMode, {
        data: series.map((s) => s.name as string),
      }),
      tooltip: {
        trigger: "axis",
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
          const idx0 = arr[0].dataIndex ?? 0;
          const dateStr = allDates[idx0] ?? "";
          if (!dateStr) return "";
          // For each pair (in series order), look up all 4 window values
          // at this date. Display the selected window's value as the main
          // number; the other 3 as small muted chips for context.
          const rowsHtml = arr
            .map((p) => {
              const key = sortedPairs.find((k) => {
                const rows = byPair.get(k);
                if (!rows || rows.length === 0) return false;
                const r0 = rows[0];
                const shortA = (r0.industry_label || r0.industry_id).split("  ")[0] || r0.industry_id;
                const shortB = (r0.benchmark_industry_label || r0.benchmark_industry_id).split("  ")[0] || r0.benchmark_industry_id;
                return `${shortA} ↔ ${shortB}` === p.seriesName;
              });
              if (!key) return "";
              const rows = byPair.get(key)!;
              const r = rows.find((x) => x.date === dateStr);
              if (!r) return "";
              const pairIdx = sortedPairs.indexOf(key);
              const color = MUTED_PALETTE[pairIdx % MUTED_PALETTE.length];
              const fmtV = (v: number | null | undefined) => {
                if (v == null || !Number.isFinite(v)) return "—";
                return (v >= 0 ? "+" : "") + fmtNum(v, 3);
              };
              const chip = (w: CorrWindow, v: number | null) => {
                const isSel = w === window;
                const cls = isSel ? "font-weight:700" : "opacity:0.55;font-size:0.85em";
                return `<span style="${cls}">${w}:${v == null || !Number.isFinite(v) ? "—" : fmtV(v)}</span>`;
              };
              return `<div style="display:flex;justify-content:space-between;gap:8px;align-items:baseline">
                <span style="color:${color}">●</span>
                <span style="flex:1">${p.seriesName ?? ""}</span>
                <span style="display:flex;gap:6px;align-items:baseline">
                  ${chip("5d", r.corr_5d)} ${chip("20d", r.corr_20d)} ${chip("60d", r.corr_60d)} ${chip("255d", r.corr_255d)}
                </span>
              </div>`;
            })
            .join("");
          return `<div style="font-weight:600">${dateStr}</div>
                  <div style="margin-top:2px;opacity:0.7">Pairwise rolling Pearson correlation of mean_rebased series</div>
                  <div style="margin-top:4px">
                    <div style="display:flex;justify-content:space-between;gap:8px;opacity:0.55;font-size:0.85em">
                      <span>window:</span>
                      <span><b>${window}</b> highlighted · others shown for context</span>
                    </div>
                  </div>
                  <div style="margin-top:4px">${rowsHtml}</div>`;
        },
      },
      xAxis: {
        type: "category",
        data: allDates,
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
        min: -1,
        max: 1,
        name: "Correlation",
        nameTextStyle: { color: c.textColor, fontSize: 9 },
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: {
          color: c.textColor,
          fontSize: 9,
          formatter: (v: number) => fmtNum(v, 2),
        },
        splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.4 } },
      },
      series,
    };
  }, [data, themeMode, window]);

  const numPairs = data
    ? new Set(data.correlations.map((r) => `${r.industry_id}|${r.benchmark_industry_id}`)).size
    : 0;

  return (
    <Box>
      <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center", mb: 1, flexWrap: "wrap", gap: 1 }}>
        <Typography variant="body2" sx={{ fontWeight: 600 }}>
          Pairwise Correlation of Industry Mean Sentiments
          {data ? ` — ${numPairs} pair${numPairs === 1 ? "" : "s"} · ${data.correlations.length.toLocaleString()} rows · pool=${poolSize}` : ""}
        </Typography>
        <ToggleButtonGroup
          value={window}
          exclusive
          size="small"
          onChange={(_, v: CorrWindow | null) => v && setWindow(v)}
        >
          {CORR_WINDOWS.map((w) => (
            <ToggleButton key={w} value={w}>{w}</ToggleButton>
          ))}
        </ToggleButtonGroup>
      </Box>
      {loading && (
        <Box sx={{ display: "flex", justifyContent: "center", py: 3 }}>
          <CircularProgress size={24} />
        </Box>
      )}
      {error && (
        <Alert severity="error" sx={{ py: 0.5 }}>Failed to load correlations: {error}</Alert>
      )}
      {!loading && !error && option && (
        <EChart option={option} height={360} />
      )}
      {!loading && !error && !option && (
        <Box sx={{ display: "flex", justifyContent: "center", py: 3 }}>
          <Typography variant="body2" color="text.secondary">
            No correlation data available for the selected industries. Run{" "}
            <code>analyze_industry_correlations.py</code> to populate.
          </Typography>
        </Box>
      )}
    </Box>
  );
}

// ============================================================================
//  Page
// ============================================================================
export default function IndustrySentimentsPage() {
  const navigate = useNavigate();
  const themeMode = useStore((s) => s.themeMode);

  const [sectors, setSectors] = useState<SectorNode[]>([]);
  const [sectorId, setSectorId] = useState<string | null>(null);
  // Multi-select: list of selected industry slugs. Persists across sector
  // switches so the user can pick industries from multiple sectors.
  const [selectedIndustrySlugs, setSelectedIndustrySlugs] = useState<string[]>([]);
  const [exchange, setExchange] = useState<string | null>(null);

  const [chartDataList, setChartDataList] = useState<IndustrySentimentsChartResponse[]>([]);
  const [loading, setLoading] = useState(false);
  const [chartLoading, setChartLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [chartError, setChartError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  const slugToIndustryId = useMemo(() => {
    const m = new Map<string, string>();
    for (const s of sectors) {
      for (const ind of s.industries) {
        m.set(ind.industry_slug, ind.industry_id);
      }
    }
    return m;
  }, [sectors]);

  // Map selected slugs → industry IDs (dropping any slug that no longer maps,
  // e.g. if the taxonomy was refreshed and the industry disappeared).
  const selectedIndustryIds = useMemo(
    () =>
      selectedIndustrySlugs
        .map((slug) => slugToIndustryId.get(slug))
        .filter((id): id is string => Boolean(id)),
    [selectedIndustrySlugs, slugToIndustryId],
  );

  // Snapshot of which industry_id each slug maps to (for the merged-chart
  // industry-label lookup). Kept in sync with selectedIndustrySlugs so the
  // chart can prefix each index with its source industry.
  const slugToIndustryLabel = useMemo(() => {
    const m = new Map<string, string>();
    for (const s of sectors) {
      for (const ind of s.industries) {
        m.set(ind.industry_slug, ind.industry_label);
      }
    }
    return m;
  }, [sectors]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchIndustrySentimentsThemes()
      .then((t) => {
        if (cancelled) return;
        setSectors(t);
        if (t.length > 0 && sectorId == null) {
          setSectorId(t[0].sector_id);
          // Seed the multi-select with the first industry of the first sector
          // so the page shows data immediately on first load.
          const firstSlug = t[0].industries[0]?.industry_slug ?? null;
          setSelectedIndustrySlugs(firstSlug ? [firstSlug] : []);
        }
        setLoading(false);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setError(e.message);
        setLoading(false);
      });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshKey]);

  // Fetch ALL selected industries' chart data in parallel. The dependency is
  // the joined ID string so the effect fires once per selection change.
  const selectedIdsKey = selectedIndustryIds.join(",");
  useEffect(() => {
    if (selectedIndustryIds.length === 0) {
      setChartDataList([]);
      return;
    }
    let cancelled = false;
    setChartLoading(true);
    setChartError(null);
    Promise.all(
      selectedIndustryIds.map((id) => fetchIndustrySentimentsChart(id)),
    )
      .then((results) => {
        if (cancelled) return;
        // Re-order results to match the selectedIndustryIds order (Promise.all
        // preserves order, but be defensive in case of any re-ordering).
        setChartDataList(results);
        setChartLoading(false);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setChartError(e.message);
        setChartLoading(false);
      });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedIdsKey, refreshKey]);

  // Merge multiple industries' chart data into a single
  // IndustrySentimentsChartResponse. When only one industry is selected, the
  // merge is a passthrough (preserves the mean/var overlay). When multiple
  // are selected:
  //   • indices are concatenated (de-duplicated by code — an index may carry
  //     multiple industry tags and would otherwise appear once per tag).
  //   • Each index's name is prefixed with "[industry_short] " so the legend
  //     and tooltip identify which industry it came from.
  //   • aggregation is DROPPED (per-industry mean/var cannot be combined
  //     across industries). The chart hides the mean/var overlay accordingly.
  //   • benchmarks come from the first response (they're identical across
  //     industries — same hardcoded broad-market list).
  //   • industry_label lists all source industries joined by " + ".
  const mergedChartData = useMemo<IndustrySentimentsChartResponse | null>(() => {
    if (chartDataList.length === 0) return null;
    if (chartDataList.length === 1) return chartDataList[0];

    const multi = chartDataList.length > 1;
    const seenCodes = new Set<string>();
    const mergedIndices: IndustrySentimentsIndex[] = [];
    for (const d of chartDataList) {
      // Resolve the short industry label for the prefix. Match by industry_id
      // (the chart response carries industry_id, not slug).
      const slugEntry = Array.from(slugToIndustryId.entries()).find(
        ([, id]) => id === d.industry_id,
      );
      const fullLabel = slugEntry
        ? (slugToIndustryLabel.get(slugEntry[0]) ?? d.industry_label)
        : d.industry_label;
      const shortLabel = (fullLabel || d.industry_id).split("  ")[0] || d.industry_id;
      for (const idx of d.indices) {
        if (seenCodes.has(idx.code)) continue;
        seenCodes.add(idx.code);
        mergedIndices.push(
          multi
            ? { ...idx, name: `[${shortLabel}] ${idx.name}` }
            : idx,
        );
      }
    }
    return {
      industry_id: chartDataList.map((d) => d.industry_id).join(","),
      industry_label: chartDataList
        .map((d) => {
          const slugEntry = Array.from(slugToIndustryId.entries()).find(
            ([, id]) => id === d.industry_id,
          );
          return slugEntry
            ? (slugToIndustryLabel.get(slugEntry[0]) ?? d.industry_label)
            : d.industry_label;
        })
        .join(" + "),
      indices: mergedIndices,
      aggregation: [], // dropped — see comment above
      benchmarks: chartDataList[0]?.benchmarks ?? [],
    };
  }, [chartDataList, slugToIndustryId, slugToIndustryLabel]);

  const multiIndustry = chartDataList.length > 1;

  const handleRefresh = () => {
    invalidateCacheForPrefix("/api/analysis/industry-sentiments/");
    setRefreshKey((k) => k + 1);
  };

  // Sector change only updates the row-2 browsing context — it does NOT clear
  // the multi-select selection (industries picked from other sectors persist).
  const handleSectorChange = (id: string | null) => {
    setSectorId(id);
  };
  const handleMultiIndustryChange = (slugs: string[]) => {
    setSelectedIndustrySlugs(slugs);
  };
  // Kept for ThemeSelector's single-select prop signature (no-op in multi mode).
  const handleIndustryChange = (_slug: string | null) => {
    /* no-op — multi-select mode uses handleMultiIndustryChange */
  };
  const handleExchangeChange = (ex: string | null) => {
    setExchange(ex);
  };

  // Header label: when 0 selected → "Select industries"; when 1 → the
  // industry's full sector/industry path; when >1 → "N industries selected".
  const headerLabel =
    selectedIndustrySlugs.length === 0
      ? "Select industries"
      : selectedIndustrySlugs.length === 1
        ? (() => {
            const slug = selectedIndustrySlugs[0];
            const sector = sectors.find((s) =>
              s.industries.some((i) => i.industry_slug === slug),
            );
            const ind = sector?.industries.find((i) => i.industry_slug === slug);
            return ind
              ? `${sector?.sector_label ?? ""} / ${ind.industry_label}`
              : "1 industry selected";
          })()
        : `${selectedIndustrySlugs.length} industries selected`;

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
              Industry Sentiments
            </Typography>
          </Box>
          <Typography variant="body2" color="text.secondary">
            {headerLabel} — each member index's daily close (actual value shown in
            tooltip), rebased to 100 at the start of the visible (zoom) window.
            <strong> Multi-select:</strong> tick multiple industry chips (across
            sectors — switch the active sector to browse, picked industries
            persist) to merge their member indices into one plot. Toggle pool
            size to filter by member count (small &lt;51, mid 51-180, large
            &gt;180). The dashed mean line and ±1σ band are precomputed
            server-side (anchored at history start — aligned with lines only at
            full slider range). In single-industry mode the mean/var overlay is
            always shown; in multi-industry mode, toggle <strong>Mean only</strong>
            to hide the per-index lines and render one mean curve PER industry
            (each in a distinct color with its own ±1σ band) for cross-industry
            comparison. Only indices WITH composition data are shown; indices
            without any composition snapshot are excluded entirely. Broad-market
            indices (BROAD_CSI/SSE/SZSE/STAR) appear under the FIN sector.
          </Typography>
        </Box>
        <RefreshButton
          onClick={handleRefresh}
          loading={loading}
          label="Refresh"
          tooltip="Refresh industry-sentiments data (bypass cache)"
        />
      </Box>

      <ThemeSelector
        sectors={sectors}
        sectorId={sectorId}
        industrySlug={selectedIndustrySlugs[0] ?? null}
        exchange={exchange}
        onSectorChange={handleSectorChange}
        onIndustryChange={handleIndustryChange}
        onExchangeChange={handleExchangeChange}
        multiSelect
        selectedIndustrySlugs={selectedIndustrySlugs}
        onMultiIndustryChange={handleMultiIndustryChange}
      />

      {loading && (
        <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
          <CircularProgress size={32} />
        </Box>
      )}
      {error && (
        <Alert severity="error" variant="filled" sx={{ mb: 2 }}>
          Failed to load industry-sentiments data: {error}
        </Alert>
      )}
      {!loading && !error && selectedIndustryIds.length === 0 && (
        <Alert severity="warning">Select one or more industries to see the member indices.</Alert>
      )}
      {!loading && !error && selectedIndustryIds.length > 0 && (
        <>
          {chartLoading && (
            <Box sx={{ display: "flex", justifyContent: "center", py: 3 }}>
              <CircularProgress size={28} />
            </Box>
          )}
          {chartError && (
            <Alert severity="error" sx={{ py: 0.5 }}>{chartError}</Alert>
          )}
          {!chartLoading && !chartError && mergedChartData && (
            <IndustrySentimentsPlot
              data={mergedChartData}
              themeMode={themeMode}
              multiIndustry={multiIndustry}
              numIndustries={chartDataList.length}
              chartDataList={chartDataList}
              selectedIndustryIds={selectedIndustryIds}
            />
          )}
        </>
      )}
    </Box>
  );
}
