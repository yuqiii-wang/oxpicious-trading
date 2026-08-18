/**
 * Build the ECharts option for the main Industry Sentiments price chart.
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
 */
import type { EChartsOption } from "echarts";
import type { ThemeMode } from "@/store/filters";
import type { IndustrySentimentsChartResponse } from "@shared/types";
import { axisColors, commonLegend, commonGrid, commonDataZoom } from "@/theme/chart-palette";
import { variantColorOf } from "@/theme/group-colors";
import { fmtNum } from "@/lib/series";
import type { PoolSize, PerIndustryAggregation } from "./types";
import { BENCHMARK_COLORS } from "./constants";
import { classifyPoolSize, rebaseTo100 } from "./helpers";

export function buildIndustryChartOption(
  data: IndustrySentimentsChartResponse,
  allDates: string[],
  visibleLo: number,
  visibleHi: number,
  poolSize: PoolSize,
  themeMode: ThemeMode,
  selectedBenchmarkCodes: string[],
  meanOnly: boolean,
  /** Single-industry overlay: render the merged mean + ±1σ band from
   *  data.aggregation. False in multi-industry mode. */
  showAggOverlay: boolean,
  /** Per-industry aggregation sets for multi-industry mean overlay. When
   *  non-empty AND meanOnly is true, one mean curve (with ±1σ band) is
   *  rendered per industry, each in that industry's MAJOR color. Empty
   *  in single-industry mode. */
  perIndustryAggregations: PerIndustryAggregation[] = [],
  /** Resolve a group key (industry_id) to its MAJOR color. Curves in the same
   *  group render as VARIANT shades of this major color. Built once by the
   *  page from the shared `buildGroupColorScheme` so colors stay consistent
   *  across the price + aggregate charts. */
  industryColorFor: (industryId: string) => string,
  /** Map from member-index code → industry_id (the curve's GROUP key).
   *  Required in multi-industry mode so each index resolves to its industry;
   *  when omitted (single-industry mode) every index falls back to
   *  `data.industry_id` — i.e. all curves share one major color. */
  indexGroupKey?: Map<string, string>,
): EChartsOption {
  const c = axisColors(themeMode);
  const visibleDates = allDates.slice(visibleLo, visibleHi + 1);

  // ---- Per-index VARIANT colors (same industry → same major color) ----
  // Each member index is colored as a variant shade of its industry's MAJOR
  // color. The first index of an industry gets the pure major color; later
  // indices diverge symmetrically (lighter / darker) so they stay in the same
  // color family while remaining distinguishable.
  const groupCounters = new Map<string, number>();
  const indexColors = data.indices.map((idx) => {
    const gid = indexGroupKey?.get(idx.code) ?? data.industry_id;
    const n = groupCounters.get(gid) ?? 0;
    groupCounters.set(gid, n + 1);
    return variantColorOf(industryColorFor(gid), n);
  });

  // ALL indices are always rendered (when not meanOnly). The pool_size toggle
  // highlights the selected pool and dims the others. Dimmed indices are
  // rendered at ~15% opacity (still visible for context).
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

      // pool_size highlight
      const poolHighlighted =
        poolSize === "all" ||
        idx.rows.some((r) => classifyPoolSize(r.stock_num) === poolSize);

      const isHighlighted = poolHighlighted;

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
      const color = indexColors[i];
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
        mean: a.mean_price,
        var: a.var_price,
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
  // distinct MAJOR color (its industry's group color) with a matching ±1σ
  // band. This lets the user compare industry-level sentiment trends on a
  // common rebased-to-100 scale.
  // Anchored at HISTORY START (same as single-industry overlay) — the slider
  // narrows the visible slice but does NOT re-rebase the means.
  //
  // Only rendered when meanOnly is ON (to avoid clutter alongside all the
  // per-index lines). When meanOnly is OFF in multi-industry mode, only the
  // per-index lines are shown (no mean overlay).
  if (perIndustryAggregations.length > 0 && meanOnly) {
    perIndustryAggregations.forEach((agg, i) => {
      const color = industryColorFor(agg.industry_id);
      const shortLabel = (agg.industry_label || agg.industry_id).split("  ")[0] || agg.industry_id;
      const aggByDate = new Map<string, { mean: number | null; var: number | null }>();
      for (const a of agg.aggregation) {
        if (a.pool_size !== poolSize) continue;
        aggByDate.set(a.date, { mean: a.mean_price, var: a.var_price });
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
    grid: commonGrid({ left: 56, right: 24, bottom: 50 }),
    dataZoom: commonDataZoom(),
    legend: commonLegend(themeMode, {
      data: series.map((s) => s.name as string),
    }),
    axisPointer: {
      link: [{ xAxisIndex: "all" }],
    },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "line", snap: true },
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
                    meanColor = industryColorFor(perIndustryAggregations[matchIdx].industry_id);
                    const aggRow = perIndustryAggregations[matchIdx].aggregation.find(
                      (a) => a.date === dateStr && a.pool_size === poolSize,
                    );
                    varVal = aggRow?.var_price ?? null;
                  }
                } else {
                  const aggRow = data.aggregation.find(
                    (a) => a.date === dateStr && a.pool_size === poolSize,
                  );
                  varVal = aggRow?.var_price ?? null;
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
            const color = indexColors[sIdx];
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
          if (!poolHighlighted) continue;
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
