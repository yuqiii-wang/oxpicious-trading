/**
 * Build the ECharts option for the Industry ETF Price line chart (1st plot in
 * "ETF Contribution" mode).
 *
 * Shows ALL ETFs tracking member indices of the selected industries as
 * separate lines. Each ETF is rebased to 100 at its OWN first available date
 * using CASCADING REBASING:
 *   • The earliest ETF starts at 100.
 *   • Each subsequent ETF starts at the MEAN of all already-active ETFs'
 *     rebased values on the new ETF's first date. This makes later-listed
 *     ETFs "blend in" with existing lines rather than jumping to 100 and
 *     being far off from the pack.
 *
 * Formula:
 *   rebased[t] = (close[t] / close[first]) × base_mean
 *   where base_mean = 100 for the first ETF, or the cross-sectional mean
 *   of already-rebased ETFs on this ETF's first date.
 *
 * A vertical markLine indicates the currently selected date. The chart is
 * clickable — onCanvasClick fires with the x-axis category index.
 *
 * An optional trading-amount bar overlay can be toggled on (right Y-axis)
 * showing each ETF's daily turnover — but with many ETFs this is visually
 * noisy, so it defaults to OFF.
 */
import type { EChartsOption } from "echarts";
import type { ThemeMode } from "@/store/filters";
import type { IndustryEtfPriceSeriesResponse } from "@shared/types";
import {
  UP_COLOR,
  axisColors,
  commonLegend,
  commonGrid,
} from "@/theme/chart-palette";
import { buildItemGroupColors } from "@/theme/group-colors";
import { fmtNum } from "@/lib/series";
import React from "react";
import { renderReactElement, tooltipComponents } from "@/lib/react-tooltip-renderer";

/** Which layer is prominent. The other layer is rendered lowkey (low opacity)
 *  rather than hidden, so the user always sees both — only the emphasis
 *  flips. Driven by the in-plot merged toggle. */
export type EtfPriceViewMode = "price" | "amt";

/** Which moving average of the per-date total ETF trading amount to draw as
 *  a line on the trading-amt (right) axis. */
export type EtfPriceMaMode = "ma5" | "ma20";

/** One ETF's rebased series prepared for plotting. */
interface EtfSeries {
  etf_code: string;
  etf_name: string;
  industry_id: string;
  industry_label: string;
  /** Rebasing base: 100 for first ETF, or cross-sectional mean for later ones. */
  base_mean: number;
  /** Rebased values aligned to the global dates array. NULL where no data. */
  values: Array<number | null>;
  /** Raw closes aligned to the global dates array (for tooltip). */
  rawCloses: Array<number | null>;
  /** Raw trading amounts aligned to the global dates array (for the
   *  trading-amt bar overlay). NULL where no liquidity data. */
  rawAmts: Array<number | null>;
  firstDate: string;
}

/** Format a fractional value as a signed percentage string. */
function fmtPctSigned(v: number | null, digits = 2): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return (v >= 0 ? "+" : "") + fmtNum(v * 100, digits) + "%";
}

/** Format a yuan amount as 亿元 (100M yuan) — for the trading-amount axis
 *  labels and tooltip. */
function fmtAmtYi(v: number | null, digits = 2): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return fmtNum(v / 1e8, digits) + "亿";
}

/**
 * Compute the cascading rebased series for all ETFs.
 *
 * Algorithm:
 *   1. Collect the global sorted date axis (union of all ETF dates).
 *   2. Sort ETFs by their first available date.
 *   3. For each ETF (in first-date order):
 *      a. Find its first non-null close.
 *      b. If no prior ETF has data on this date → base_mean = 100.
 *         Else → base_mean = MEAN of all already-processed ETFs' rebased
 *         values on this ETF's first date.
 *      c. rebased[t] = (close[t] / close_first) × base_mean.
 */
function computeCascadingRebased(
  data: IndustryEtfPriceSeriesResponse,
  globalDates: string[],
): EtfSeries[] {
  // Map each date to its index in the global array for O(1) lookup.
  const dateIdx = new Map<string, number>();
  globalDates.forEach((d, i) => dateIdx.set(d, i));

  // Sort ETFs by first available date (earliest first).
  const sortedEtfs = [...data.etfs].sort((a, b) => {
    const af = a.rows.find((r) => r.close != null)?.date ?? "";
    const bf = b.rows.find((r) => r.close != null)?.date ?? "";
    return af.localeCompare(bf);
  });

  const result: EtfSeries[] = [];

  for (const etf of sortedEtfs) {
    // Build rawCloses + rawAmts aligned to global dates.
    const rawCloses: Array<number | null> = new Array(globalDates.length).fill(null);
    const rawAmts: Array<number | null> = new Array(globalDates.length).fill(null);
    for (const r of etf.rows) {
      const idx = dateIdx.get(r.date);
      if (idx != null) {
        rawCloses[idx] = r.close;
        rawAmts[idx] = r.trading_amount;
      }
    }

    // Find first non-null close.
    let firstIdx = -1;
    let firstClose: number | null = null;
    for (let i = 0; i < rawCloses.length; i++) {
      if (rawCloses[i] != null && rawCloses[i] !== 0) {
        firstIdx = i;
        firstClose = rawCloses[i];
        break;
      }
    }
    if (firstIdx < 0 || firstClose == null) {
      // No data — skip this ETF.
      result.push({
        etf_code: etf.etf_code,
        etf_name: etf.etf_name,
        industry_id: etf.industry_id,
        industry_label: etf.industry_label,
        base_mean: 100,
        values: new Array(globalDates.length).fill(null),
        rawCloses,
        rawAmts,
        firstDate: "",
      });
      continue;
    }

    const firstDate = globalDates[firstIdx];

    // Compute base_mean: cross-sectional mean of already-processed ETFs'
    // rebased values on this ETF's first date.
    let baseMean = 100;
    if (result.length > 0) {
      const activeValues: number[] = [];
      for (const prev of result) {
        const v = prev.values[firstIdx];
        if (v != null && Number.isFinite(v)) activeValues.push(v);
      }
      if (activeValues.length > 0) {
        baseMean = activeValues.reduce((s, v) => s + v, 0) / activeValues.length;
      }
    }

    // Rebased: (close / close_first) × base_mean.
    const values = rawCloses.map((c) =>
      c != null && firstClose != null && firstClose !== 0
        ? (c / firstClose) * baseMean
        : null,
    );

    result.push({
      etf_code: etf.etf_code,
      etf_name: etf.etf_name,
      industry_id: etf.industry_id,
      industry_label: etf.industry_label,
      base_mean: baseMean,
      values,
      rawCloses,
      rawAmts,
      firstDate,
    });
  }

  return result;
}

export function buildIndustryEtfPriceOption(
  data: IndustryEtfPriceSeriesResponse,
  themeMode: ThemeMode,
  selectedDate: string | null,
  range?: [number, number],
  mode: EtfPriceViewMode = "price",
  maMode: EtfPriceMaMode = "ma5",
): EChartsOption {
  const c = axisColors(themeMode);

  // ---- Build global date axis (union of all ETF dates, sorted) ----
  const dateSet = new Set<string>();
  for (const etf of data.etfs) {
    for (const r of etf.rows) dateSet.add(r.date);
  }
  const allDates = Array.from(dateSet).sort();
  const totalN = allDates.length;

  // ---- Compute cascading rebased series ----
  const seriesData = computeCascadingRebased(data, allDates);

  // ---- Per-ETF group colors (same industry → same major color) ----
  // ETFs tracking member indices of the SAME industry share a major color;
  // individual ETFs within an industry render as variant shades of that
  // major. Built from `seriesData` (sorted by ETF first-date) so the color
  // array aligns 1:1 with the ETF loops below.
  const { scheme: etfGroupScheme, colors: etfColors } = buildItemGroupColors(
    seriesData,
    (s) => s.industry_id,
  );

  // ---- Per-industry per-date TOTAL ETF trading amount + its MA ----
  // Computed PER INDUSTRY (sum across that industry's ETFs only), so when the
  // user selects multiple industries the chart shows one MA curve per
  // industry rather than a single cross-industry aggregate. Computed over the
  // FULL global date axis (not the visible slice) so the MA lines stay stable
  // as the slider narrows. The MA mirrors the pandas rolling(w,
  // min_periods=1).mean() semantics: a trailing window of w trading days,
  // partial windows allowed, null days skipped.
  //
  // Industry order = first appearance in seriesData (which is sorted by ETF
  // first-date). Each industry's MA line uses that industry's MAJOR group
  // color, so it visually pairs with that industry's ETF price lines.
  const industryOrder: string[] = [];
  const industryLabelById = new Map<string, string>();
  for (const s of seriesData) {
    if (!industryLabelById.has(s.industry_id)) {
      industryOrder.push(s.industry_id);
      industryLabelById.set(s.industry_id, s.industry_label || s.industry_id);
    }
  }
  const maWindow = maMode === "ma20" ? 20 : 5;
  const maName = maMode === "ma20" ? "MA20" : "MA5";
  // Per-industry arrays indexed by [industryIndex][dateIndex].
  const industryTotals: Array<Array<number | null>> = industryOrder.map(() =>
    new Array(totalN).fill(null),
  );
  for (let di = 0; di < totalN; di++) {
    for (let ii = 0; ii < industryOrder.length; ii++) {
      let sum = 0;
      let any = false;
      for (const s of seriesData) {
        if (s.industry_id !== industryOrder[ii]) continue;
        const a = s.rawAmts[di];
        if (a != null) { sum += a; any = true; }
      }
      industryTotals[ii][di] = any ? sum : null;
    }
  }
  // Per-industry MA line (trailing window over that industry's total).
  const industryMaLines: Array<Array<number | null>> = industryOrder.map((_, ii) => {
    const line: Array<number | null> = new Array(totalN).fill(null);
    const totals = industryTotals[ii];
    for (let i = 0; i < totalN; i++) {
      let sum = 0;
      let cnt = 0;
      const lo = Math.max(0, i - maWindow + 1);
      for (let j = lo; j <= i; j++) {
        const v = totals[j];
        if (v != null) { sum += v; cnt++; }
      }
      line[i] = cnt > 0 ? sum / cnt : null;
    }
    return line;
  });
  // Per-industry MA color: each industry's MA line uses that industry's
  // MAJOR group color (matching its ETF price lines).
  const industryMaColors: string[] = industryOrder.map((id) =>
    etfGroupScheme.majorColor(id),
  );
  // Also keep the cross-industry grand total for the tooltip header + the
  // per-ETF % share denominator.
  const totalAmts: Array<number | null> = new Array(totalN).fill(null);
  for (let i = 0; i < totalN; i++) {
    let sum = 0;
    let any = false;
    for (const s of seriesData) {
      const a = s.rawAmts[i];
      if (a != null) { sum += a; any = true; }
    }
    totalAmts[i] = any ? sum : null;
  }

  // ---- Apply visible range (slider) ----
  const startIdx = range ? Math.max(0, Math.min(range[0], totalN - 1)) : 0;
  const endIdx = range ? Math.max(startIdx, Math.min(range[1], totalN - 1)) : totalN - 1;
  const dates = allDates.slice(startIdx, endIdx + 1);
  const n = dates.length;

  // ---- Find the selected date index for markLine ----
  let selectedIdx = n - 1;
  if (selectedDate) {
    const found = dates.indexOf(selectedDate);
    if (found >= 0) selectedIdx = found;
  }

  // ---- Layer emphasis (mode) ----
  // Both layers are ALWAYS rendered; the non-prominent one is drawn lowkey
  // (low opacity / thin) rather than hidden, so the user can always see both
  // and only the emphasis flips.
  const priceProminent = mode === "price";
  const priceLineOpacity = priceProminent ? 1.0 : 0.18;
  const priceLineWidthMain = priceProminent ? 1.8 : 0.7;
  const priceLineWidthOther = priceProminent ? 1.0 : 0.5;
  const barOpacity = priceProminent ? 0.12 : 0.55;
  const maLineOpacity = priceProminent ? 0.30 : 0.9;
  const maLineWidth = priceProminent ? 1.0 : 2.0;

  // ---- Build series array ----
  const echartsSeries: EChartsOption["series"] = [];

  // ---- Trading-amount bar overlay (rendered first so lines draw on top) ----
  // ONE stacked bar per date on yAxis 1 (right axis). Each ETF contributes
  // its own trading_amount as a segment; the segments stack to the date's
  // TOTAL ETF trading amount, so each segment = its proportional share of
  // the total. Segment colors match each ETF's line color. Opacity follows
  // `mode` (lowkey when price is prominent).
  for (let i = 0; i < seriesData.length; i++) {
    const s = seriesData[i];
    const slicedAmts = s.rawAmts.slice(startIdx, endIdx + 1);
    const color = etfColors[i];
    echartsSeries.push({
      name: `${s.etf_name} (amt)`,
      type: "bar",
      yAxisIndex: 1,
      stack: "etfAmtTotal",
      data: slicedAmts,
      barCategoryGap: "20%",
      itemStyle: { color, opacity: barOpacity },
      z: priceProminent ? 1 : 8,
      tooltip: { show: false },
    } as Record<string, unknown>);
  }

  // ---- Per-industry MA lines of each industry's total trading amount ----
  // One line per industry on the trading-amt (right) axis, each in a distinct
  // color. When the user selects N industries, N MA curves are drawn.
  for (let ii = 0; ii < industryOrder.length; ii++) {
    const indLabel = industryLabelById.get(industryOrder[ii]) ?? industryOrder[ii];
    const indColor = industryMaColors[ii];
    echartsSeries.push({
      name: `${indLabel} ${maName}`,
      type: "line",
      yAxisIndex: 1,
      data: industryMaLines[ii].slice(startIdx, endIdx + 1),
      showSymbol: false,
      symbol: "circle",
      symbolSize: 4,
      smooth: true,
      lineStyle: { color: indColor, width: maLineWidth, opacity: maLineOpacity },
      itemStyle: { color: indColor, opacity: maLineOpacity },
      z: priceProminent ? 6 : 10,
      tooltip: { show: false },
    } as Record<string, unknown>);
  }

  for (let i = 0; i < seriesData.length; i++) {
    const s = seriesData[i];
    const slicedValues = s.values.slice(startIdx, endIdx + 1);
    const color = etfColors[i];
    const lw = i === 0 ? priceLineWidthMain : priceLineWidthOther;

    echartsSeries.push({
      name: s.etf_name,
      type: "line",
      data: slicedValues,
      showSymbol: false,
      symbol: "circle",
      symbolSize: 5,
      lineStyle: { color, width: lw, opacity: priceLineOpacity },
      itemStyle: { color, opacity: priceLineOpacity },
      z: priceProminent ? 20 - i : 3,
    });
  }

  // ---- Build a markLine on the first PRICE line series (selected date) ----
  // Find the first line series named after an ETF (not the "(amt)" bar or a
  // per-industry "<label> MA5/MA20" line).
  const maSuffix = ` ${maName}`;
  const firstPriceSeriesIdx = echartsSeries.findIndex(
    (s) => {
      const ss = s as Record<string, unknown>;
      if (ss.type !== "line") return false;
      const nm = String(ss.name ?? "");
      if (nm.endsWith("(amt)")) return false;
      if (nm.endsWith(maSuffix)) return false;
      return true;
    },
  );
  if (firstPriceSeriesIdx >= 0) {
    const first = echartsSeries[firstPriceSeriesIdx] as Record<string, unknown>;
    first.markLine = {
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
    };
  }

  // ---- Legend data ----
  const legendData = [
    ...seriesData.map((s) => s.etf_name),
    ...industryOrder.map((id, ii) =>
      `${industryLabelById.get(id) ?? id} ${maName}`,
    ),
  ];

  // ---- X-axis: year-month ticks at a 3-month interval ----
  // Show "YYYY-MM" once per displayed month, on the first trading day of
  // that month. Displayed months = every 3rd distinct month counting from
  // the start of the visible range (so a series starting in Feb shows
  // Feb / May / Aug / Nov, not quarter-aligned Jan/Apr/Jul/Oct). Same
  // scheme as benchmarkPriceOption.ts.
  const displayMonths = new Set<string>();
  {
    const orderedMonths: string[] = [];
    const seen = new Set<string>();
    for (const d of dates) {
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
    for (const d of dates) {
      const ym = d.slice(0, 7);
      if (ym !== prev) {
        firstDateOfMonth.add(d);
        prev = ym;
      }
    }
  }

  return {
    backgroundColor: "transparent",
    animation: false,
    grid: commonGrid({ left: 56, right: 56, bottom: 48, top: 32 }),
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
          value?: number | null;
        }>;
        if (arr.length === 0) return "";
        const idx = arr[0].dataIndex ?? 0;
        const dt = dates[idx] ?? "—";
        const children: React.ReactNode[] = [];
        children.push(
          React.createElement(tooltipComponents.Header, null, dt)
        );
        for (let ii = 0; ii < industryOrder.length; ii++) {
          const indLabel = industryLabelById.get(industryOrder[ii]) ?? industryOrder[ii];
          const indTotal = industryTotals[ii][startIdx + idx];
          const indMa = industryMaLines[ii][startIdx + idx];
          const indColor = industryMaColors[ii];
          const rowChildren: React.ReactNode[] = [
            React.createElement("span", { style: { opacity: 0.85 } }, indLabel),
            ": ",
            React.createElement(tooltipComponents.Bold, null, fmtAmtYi(indTotal))
          ];
          if (indMa != null) {
            rowChildren.push(
              ` · ${maName}: `,
              React.createElement(tooltipComponents.Bold, { style: { color: indColor } }, fmtAmtYi(indMa))
            );
          }
          children.push(
            React.createElement("div", { style: { marginTop: 2 } }, ...rowChildren)
          );
        }
        const total = totalAmts[startIdx + idx] ?? 0;
        children.push(
          React.createElement("div", { style: { marginTop: 2, opacity: 0.7 } },
            "All industries: ",
            React.createElement(tooltipComponents.Bold, null, fmtAmtYi(total))
          )
        );
        for (const p of arr) {
          const sIdx = seriesData.findIndex((s) => s.etf_name === p.seriesName);
          if (sIdx < 0) continue;
          const s = seriesData[sIdx];
          const raw = s.rawCloses[startIdx + idx];
          const rebased = p.value;
          if (rebased == null) {
            children.push(
              React.createElement("div", { style: { opacity: 0.5 } }, `${s.etf_name}: —`)
            );
          } else {
            const prevRaw = idx > 0 ? s.rawCloses[startIdx + idx - 1] : null;
            const change = raw != null && prevRaw != null && prevRaw !== 0
              ? (raw - prevRaw) / prevRaw
              : null;
            const inner: React.ReactNode[] = [
              `${s.etf_name}: `,
              React.createElement(tooltipComponents.Bold, null, fmtNum(rebased, 1))
            ];
            if (change != null) {
              inner.push(
                " ",
                React.createElement("span", { style: { opacity: 0.7 } }, fmtPctSigned(change))
              );
            }
            inner.push(
              " ",
              React.createElement("span", { style: { opacity: 0.5 } }, `(px: ${fmtNum(raw, 3)})`)
            );
            const rawAmt = s.rawAmts[startIdx + idx];
            inner.push(
              " ",
              React.createElement("span", { style: { opacity: 0.6 } },
                `· ${fmtAmtYi(rawAmt)}`,
                total > 0 && rawAmt != null
                  ? ` (${fmtNum((rawAmt as number) / total * 100, 1)}%)`
                  : "",
              )
            );
            children.push(React.createElement("div", null, ...inner));
          }
        }
        return renderReactElement(React.createElement(React.Fragment, null, ...children));
      },
    },
    legend: commonLegend(themeMode, {
      itemWidth: 10,
      itemHeight: 6,
      data: legendData,
    }),
    xAxis: {
      type: "category",
      data: dates,
      boundaryGap: true,
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: {
        color: c.textColor,
        fontSize: 9,
        // Show one "YYYY-MM" tick every 3 months, on the first trading day
        // of each displayed month (full date still shown in the tooltip).
        interval: (_idx: number, value: string) =>
          displayMonths.has(value.slice(0, 7)) && firstDateOfMonth.has(value),
        formatter: (v: string) => v.slice(0, 7), // YYYY-MM
      },
      splitLine: { show: false },
    },
    yAxis: [
      {
        type: "value",
        scale: true,
        name: "Rebased (cascading)",
        nameTextStyle: { color: c.textColor, fontSize: 9 },
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: {
          color: c.textColor,
          fontSize: 9,
          formatter: (v: number) => fmtNum(v, 0),
        },
        splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.4 } },
      },
      // Right axis: ETF trading amount (yuan) — always present (bars + MA line
      // are always rendered; only their emphasis changes with `mode`).
      {
        type: "value" as const,
        scale: true,
        name: "Amt (亿)",
        nameTextStyle: { color: c.textColor, fontSize: 9 },
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: {
          color: c.textColor,
          fontSize: 9,
          formatter: (v: number) => fmtNum(v / 1e8, 1),
        },
        splitLine: { show: false },
      },
    ],
    series: echartsSeries,
  };
}
