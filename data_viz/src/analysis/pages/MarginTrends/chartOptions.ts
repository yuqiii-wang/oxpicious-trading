/**
 * chartOptions — ECharts option builders for Margin Trends.
 *
 * Two plot builders:
 *   1. buildTrendChartOption — margin trends (top) + close price (bottom)
 *      with synced axisPointer, trend episode shade overlays, and rich
 *      tooltips showing margin/close values + active trend episodes.
 *   2. buildCorrChartOption — pairwise correlation curves with ±1 Y range.
 */
import React from "react";
import type { EChartsOption } from "echarts";
import type {
  MarginIndustrySeriesResponse,
  MarginIndustryCorrelationResponse,
  MarginTrendsShadeResponse,
  MarginSecurity,
} from "@shared/types";
import type { ThemeMode } from "@/store/filters";
import {
  GROUP_MAJOR_COLORS,
  MUTED_PALETTE,
  UP_COLOR,
  DOWN_COLOR,
  axisColors,
  commonGrid,
} from "@/theme/chart-palette";
import { fmtNum } from "@/lib/series";
import { renderReactElement, tooltipComponents } from "@/lib/react-tooltip-renderer";
import type { MarginAttribution, MarginSeries } from "./constants";

const MUTED_GRAY = MUTED_PALETTE[7] ?? "#999999";

// ---- Types for the pivot data structure ----
interface PivotResult {
  dates: string[];
  codeMargin: Map<string, Map<string, number | null>>;
  codeClose: Map<string, Map<string, number | null>>;
}

/** Pivot raw series rows into date→value maps per code. */
export function pivotSeriesData(
  seriesData: MarginIndustrySeriesResponse,
  series: MarginSeries,
  targetCodes: Set<string> | null,
): PivotResult {
  const dateSet = new Set<string>();
  const codeMargin = new Map<string, Map<string, number | null>>();
  const codeClose = new Map<string, Map<string, number | null>>();
  for (const r of seriesData.rows) {
    if (targetCodes && !targetCodes.has(r.code)) continue;
    dateSet.add(r.date);
    const v = series === "balance" ? r.balance : r.buy;
    if (!codeMargin.has(r.code)) codeMargin.set(r.code, new Map());
    codeMargin.get(r.code)!.set(r.date, v);
    if (!codeClose.has(r.code)) codeClose.set(r.code, new Map());
    codeClose.get(r.code)!.set(r.date, r.close);
  }
  const dates = Array.from(dateSet).sort();
  return { dates, codeMargin, codeClose };
}

// ---- Trend episode helpers ----
interface TrendEpisode {
  start: string;
  end: string;
  isUp: boolean;
}

function buildTrendMarkArea(
  epList: TrendEpisode[],
  opacity: number,
) {
  return {
    silent: true,
    itemStyle: { borderWidth: 0 },
    data: epList.map((ep) => [
      {
        xAxis: ep.start,
        itemStyle: { color: ep.isUp ? UP_COLOR : DOWN_COLOR, opacity },
      },
      { xAxis: ep.end },
    ]),
  };
}

// ---- 1st plot: margin trends + close price ----
export function buildTrendChartOption(
  seriesData: MarginIndustrySeriesResponse,
  seriesPivot: PivotResult,
  displaySecurities: MarginSecurity[],
  selectedCodes: string[],
  attribution: MarginAttribution,
  series: MarginSeries,
  themeMode: ThemeMode,
  trendsData: MarginTrendsShadeResponse | null,
  isSingleItemMode: boolean,
  selectedItemCode: string | null | undefined,
): EChartsOption | null {
  if (!seriesData || seriesData.rows.length === 0 || displaySecurities.length === 0) return null;

  const { dates, codeMargin, codeClose } = seriesPivot;
  const effectiveSelectedSet = isSingleItemMode && selectedItemCode
    ? new Set([selectedItemCode])
    : new Set(selectedCodes);

  const c = axisColors(themeMode);
  const seriesLabel = series === "balance" ? "融资余额" : "融资买入额";
  const unit = attribution === "index" ? "亿 (weighted-avg)" : "亿";
  const trendShadeOpacity = themeMode === "dark" ? 0.18 : 0.20;

  // Build code → trend episodes map.
  const trendsByCode = new Map<string, TrendEpisode[]>();
  if (trendsData) {
    for (const ep of trendsData.episodes) {
      if (!trendsByCode.has(ep.code)) trendsByCode.set(ep.code, []);
      trendsByCode.get(ep.code)!.push({
        start: ep.start_date,
        end: ep.end_date,
        isUp: ep.is_trend_up_not_down,
      });
    }
  }

  // code → label map for tooltip.
  const codeToLabel = new Map<string, string>();
  for (const sec of displaySecurities) {
    codeToLabel.set(sec.code, sec.label || sec.code);
  }

  // ---- Top grid: margin series ----
  const marginSeries = displaySecurities.map((sec, idx) => {
    const dm = codeMargin.get(sec.code);
    const data = dates.map((d) => {
      const v = dm?.get(d) ?? null;
      return v != null && Number.isFinite(v) ? v / 1e8 : null;
    });
    const isSelected = effectiveSelectedSet.has(sec.code);
    const color = GROUP_MAJOR_COLORS[idx % GROUP_MAJOR_COLORS.length];
    const epList = isSelected ? trendsByCode.get(sec.code) : undefined;
    const markArea = epList && epList.length > 0
      ? buildTrendMarkArea(epList, trendShadeOpacity)
      : undefined;
    return {
      name: sec.label || sec.code,
      type: "line" as const,
      xAxisIndex: 0,
      yAxisIndex: 0,
      smooth: false,
      showSymbol: false,
      connectNulls: false,
      data,
      lineStyle: {
        width: isSelected ? 2.0 : 0.8,
        color: isSelected ? color : MUTED_GRAY,
        opacity: isSelected ? 1.0 : 0.25,
      },
      itemStyle: { color },
      emphasis: { focus: "series" as const },
      z: isSelected ? 5 : 1,
      ...(markArea ? { markArea } : {}),
    };
  });

  // ---- Bottom grid: close price series (selected only) ----
  const CLOSE_SUFFIX = " · Close";
  const closeSeries = displaySecurities
    .filter((sec) => effectiveSelectedSet.has(sec.code))
    .map((sec) => {
      const idx = displaySecurities.indexOf(sec);
      const dc = codeClose.get(sec.code);
      const data = dates.map((d) => {
        const v = dc?.get(d) ?? null;
        return v != null && Number.isFinite(v) ? v : null;
      });
      const color = GROUP_MAJOR_COLORS[idx % GROUP_MAJOR_COLORS.length];
      const epList = trendsByCode.get(sec.code);
      const markArea = epList && epList.length > 0
        ? buildTrendMarkArea(epList, trendShadeOpacity)
        : undefined;
      return {
        name: `${sec.label || sec.code}${CLOSE_SUFFIX}`,
        type: "line" as const,
        xAxisIndex: 1,
        yAxisIndex: 1,
        smooth: false,
        showSymbol: false,
        connectNulls: false,
        data,
        lineStyle: { width: 1.6, color },
        itemStyle: { color },
        z: 3,
        ...(markArea ? { markArea } : {}),
      };
    });

  return {
    backgroundColor: "transparent",
    animation: false,
    grid: [
      { left: 64, right: 24, top: 32, height: "46%" },
      { left: 64, right: 24, top: "58%", bottom: 28 },
    ],
    axisPointer: {
      link: [{ xAxisIndex: "all" }],
    },
    legend: {
      data: displaySecurities
        .filter((s) => effectiveSelectedSet.has(s.code))
        .map((s) => s.label || s.code),
      textStyle: { color: c.textColor, fontSize: 10 },
      top: 0,
      type: "scroll",
    },
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
          color?: string;
        }>;
        if (arr.length === 0) return "";
        const idx = arr[0].dataIndex ?? 0;
        const dateStr = dates[idx] ?? "";
        const marginRows = arr
          .filter((p) => p.seriesName && !p.seriesName.endsWith(CLOSE_SUFFIX))
          .filter((p) => p.value != null && Number.isFinite(p.value as number))
          .sort((a, b) => (b.value as number) - (a.value as number))
          .slice(0, 8);
        const closeRows = arr
          .filter((p) => p.seriesName && p.seriesName.endsWith(CLOSE_SUFFIX))
          .filter((p) => p.value != null && Number.isFinite(p.value as number))
          .sort((a, b) => (b.value as number) - (a.value as number));
        const hasMargin = marginRows.length > 0;
        const hasClose = closeRows.length > 0;
        const children: React.ReactNode[] = [];
        children.push(React.createElement(tooltipComponents.Header, null, dateStr));
        for (const p of marginRows) {
          const v = p.value as number;
          children.push(React.createElement(tooltipComponents.Row, null, [
            React.createElement("span", { style: { color: p.color ?? "" } }, "●"),
            ` ${p.seriesName}: `,
            React.createElement(tooltipComponents.Bold, null, fmtNum(v, 2)),
            ` ${unit}`,
          ]));
        }
        if (hasMargin && hasClose) {
          children.push(React.createElement("div", {
            style: { borderTop: `1px solid ${c.splitLineColor}`, margin: "3px 0" },
          }));
        }
        for (const p of closeRows) {
          const v = p.value as number;
          const label = (p.seriesName ?? "").replace(CLOSE_SUFFIX, "");
          children.push(React.createElement(tooltipComponents.Row, null, [
            React.createElement("span", { style: { color: p.color ?? "" } }, "●"),
            ` ${label}: `,
            React.createElement(tooltipComponents.Bold, null, fmtNum(v, 2)),
          ]));
        }
        const trendRows: Array<{ label: string; isUp: boolean; start: string; end: string }> = [];
        for (const code of selectedCodes) {
          const eps = trendsByCode.get(code);
          if (!eps) continue;
          for (const ep of eps) {
            if (dateStr >= ep.start && dateStr <= ep.end) {
              trendRows.push({
                label: codeToLabel.get(code) ?? code,
                isUp: ep.isUp,
                start: ep.start,
                end: ep.end,
              });
            }
          }
        }
        const hasTrend = trendRows.length > 0;
        if (hasTrend && (hasMargin || hasClose)) {
          children.push(React.createElement("div", {
            style: { borderTop: `1px solid ${c.splitLineColor}`, margin: "3px 0" },
          }));
        }
        if (hasTrend) {
          children.push(React.createElement("div", {
            style: { opacity: 0.8, fontSize: 10 },
          }, "Margin Trend"));
        }
        for (const t of trendRows) {
          const arrow = t.isUp ? "▲" : "▼";
          const dirLabel = t.isUp ? "UP" : "DOWN";
          const color = t.isUp ? UP_COLOR : DOWN_COLOR;
          children.push(React.createElement(tooltipComponents.Row, null, [
            React.createElement("span", { style: { color } }, arrow),
            ` ${t.label}: `,
            React.createElement(tooltipComponents.Bold, null, dirLabel),
            " ",
            React.createElement("span", { style: { opacity: 0.7 } }, `(${t.start} → ${t.end})`),
          ]));
        }
        return renderReactElement(React.createElement(React.Fragment, null, children));
      },
    },
    xAxis: [
      {
        type: "category",
        data: dates,
        gridIndex: 0,
        axisLine: { show: false },
        axisLabel: { show: false },
        axisTick: { show: false },
        splitLine: { show: false },
      },
      {
        type: "category",
        data: dates,
        gridIndex: 1,
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: { color: c.textColor, fontSize: 9, formatter: (v: string) => v.slice(0, 7) },
        splitLine: { show: false },
      },
    ],
    yAxis: [
      {
        type: "value",
        gridIndex: 0,
        name: `${seriesLabel} (${unit})`,
        nameTextStyle: { color: c.textColor, fontSize: 9 },
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: { color: c.textColor, fontSize: 9, formatter: (v: number) => fmtNum(v, 0) },
        splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.4 } },
      },
      {
        type: "value",
        gridIndex: 1,
        scale: true,
        name: "Close",
        nameTextStyle: { color: c.textColor, fontSize: 9 },
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: { color: c.textColor, fontSize: 9, formatter: (v: number) => fmtNum(v, 0) },
        splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.4 } },
      },
    ],
    series: [...marginSeries, ...closeSeries],
  };
}

// ---- 2nd plot: pairwise correlation ----
export function buildCorrChartOption(
  corrData: MarginIndustryCorrelationResponse,
  displaySecurities: MarginSecurity[],
  series: MarginSeries,
  themeMode: ThemeMode,
): EChartsOption | null {
  if (corrData.rows.length === 0 || corrData.pairs.length === 0) return null;

  const c = axisColors(themeMode);
  const dateSet = new Set<string>();
  const pairMap = new Map<string, Map<string, number | null>>();
  const labelOf = new Map<string, string>();
  for (const s of displaySecurities) labelOf.set(s.code, s.label || s.code);
  for (const r of corrData.rows) {
    dateSet.add(r.date);
    const key = `${r.security_code}|${r.benchmark_code}`;
    if (!pairMap.has(key)) pairMap.set(key, new Map());
    pairMap.get(key)!.set(r.date, r.corr);
  }
  const dates = Array.from(dateSet).sort();

  const echartsSeries = corrData.pairs.map((pair, idx) => {
    const key = `${pair.security_code}|${pair.benchmark_code}`;
    const dm = pairMap.get(key);
    const data = dates.map((d) => dm?.get(d) ?? null);
    const aLabel = labelOf.get(pair.security_code) ?? pair.security_code;
    const bLabel = labelOf.get(pair.benchmark_code) ?? pair.benchmark_code;
    const color = GROUP_MAJOR_COLORS[idx % GROUP_MAJOR_COLORS.length];
    return {
      name: `${aLabel} vs ${bLabel}`,
      type: "line" as const,
      smooth: false,
      showSymbol: false,
      connectNulls: false,
      data,
      lineStyle: { width: 1.6, color },
      itemStyle: { color },
      z: 3,
    };
  });

  return {
    backgroundColor: "transparent",
    animation: false,
    grid: commonGrid({ left: 56, right: 24, bottom: 32 }),
    legend: {
      data: echartsSeries.map((s) => s.name),
      textStyle: { color: c.textColor, fontSize: 10 },
      top: 0,
      type: "scroll",
    },
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
          color?: string;
        }>;
        if (arr.length === 0) return "";
        const idx = arr[0].dataIndex ?? 0;
        const dateStr = dates[idx] ?? "";
        const children: React.ReactNode[] = [];
        children.push(React.createElement(tooltipComponents.Header, null, dateStr));
        for (const p of arr) {
          if (p.value == null || !Number.isFinite(p.value as number)) continue;
          const v = p.value as number;
          const valStr = (v >= 0 ? "+" : "") + fmtNum(v, 3);
          children.push(React.createElement(tooltipComponents.Row, null, [
            React.createElement("span", { style: { color: p.color ?? "" } }, "●"),
            ` ${p.seriesName ?? ""}: `,
            React.createElement(tooltipComponents.Bold, null, valStr),
          ]));
        }
        return renderReactElement(React.createElement(React.Fragment, null, children));
      },
    },
    xAxis: {
      type: "category",
      data: dates,
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: { color: c.textColor, fontSize: 9, formatter: (v: string) => v.slice(0, 7) },
      splitLine: { show: false },
    },
    yAxis: {
      type: "value",
      min: -1,
      max: 1,
      name: "Correlation",
      nameTextStyle: { color: c.textColor, fontSize: 9 },
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: { color: c.textColor, fontSize: 9, formatter: (v: number) => fmtNum(v, 2) },
      splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.4 } },
    },
    series: echartsSeries,
  };
}
