/**
 * chartOptions — ECharts option builders for Margin Trends.
 *
 * One plot builder:
 *   buildTrendChartOption — margin trends (top) + close price (bottom)
 *      with synced axisPointer, trend episode shade overlays, per-episode
 *      rz_buy_vs_trading_amt_ratio segments (Buy mode, right % axis), and
 *      rich tooltips showing margin/close values + active trend episodes.
 */
import React from "react";
import type { EChartsOption } from "echarts";
import type {
  MarginIndustrySeriesResponse,
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
} from "@/theme/chart-palette";
import { fmtNum } from "@/lib/series";
import { renderReactElement, tooltipComponents } from "@/lib/react-tooltip-renderer";
import type { MarginAttribution, MarginSeries } from "./constants";

const MUTED_GRAY = MUTED_PALETTE[7] ?? "#999999";

/** Series-name suffix for the RZ buy / turnover ratio (买入占比) overlay
 *  series rendered on the Buy (融资买入额) plot. */
const RATIO_SUFFIX = " · 买入占比";

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
  /** rz_buy_vs_trading_amt_ratio (fraction) over the episode window. */
  ratio: number | null;
}

function buildTrendMarkArea(
  epList: TrendEpisode[],
  opacity: number,
) {
  return {
    silent: true,
    itemStyle: { borderWidth: 0 },
    data: epList.map((ep): [
      { xAxis: string; itemStyle: { color: string; opacity: number } },
      { xAxis: string },
    ] => [
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
        ratio: ep.rz_buy_vs_trading_amt_ratio,
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

  // ---- Top grid: RZ buy / turnover ratio segments (Buy mode only) ----
  // One dashed horizontal segment per trend episode, drawn at the
  // episode's rz_buy_vs_trading_amt_ratio level (Σ rz_buy / Σ trading_amount
  // over the window) on a dedicated right-side % axis. Selected securities
  // only (matches the trend shade overlay convention).
  const isBuySeries = series === "buy";
  const ratioSeries = (isBuySeries
    ? displaySecurities
        .filter((sec) => effectiveSelectedSet.has(sec.code))
        .map((sec) => {
          const idx = displaySecurities.indexOf(sec);
          const color = GROUP_MAJOR_COLORS[idx % GROUP_MAJOR_COLORS.length];
          const eps = trendsByCode.get(sec.code) ?? [];
          const data = dates.map((d) => {
            const ep = eps.find((e) => d >= e.start && d <= e.end);
            const v = ep?.ratio;
            return v != null && Number.isFinite(v) ? v * 100 : null;
          });
          return {
            name: `${sec.label || sec.code}${RATIO_SUFFIX}`,
            type: "line" as const,
            xAxisIndex: 0,
            yAxisIndex: 2,
            smooth: false,
            showSymbol: false,
            connectNulls: false,
            data,
            lineStyle: { width: 1.4, color, type: "dashed" as const, opacity: 0.9 },
            itemStyle: { color },
            emphasis: { focus: "series" as const },
            z: 4,
          };
        })
    : []
  ).filter((s) => s.data.some((v) => v != null));
  const hasRatio = ratioSeries.length > 0;

  return {
    backgroundColor: "transparent",
    animation: false,
    grid: [
      { left: 64, right: hasRatio ? 56 : 24, top: 32, height: "46%" },
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
          .filter((p) => p.seriesName && !p.seriesName.endsWith(CLOSE_SUFFIX) && !p.seriesName.endsWith(RATIO_SUFFIX))
          .filter((p) => p.value != null && Number.isFinite(p.value as number))
          .sort((a, b) => (b.value as number) - (a.value as number))
          .slice(0, 8);
        const ratioRows = arr
          .filter((p) => p.seriesName && p.seriesName.endsWith(RATIO_SUFFIX))
          .filter((p) => p.value != null && Number.isFinite(p.value as number))
          .sort((a, b) => (b.value as number) - (a.value as number));
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
        if (hasMargin && ratioRows.length > 0) {
          children.push(React.createElement("div", {
            style: { borderTop: `1px solid ${c.splitLineColor}`, margin: "3px 0" },
          }));
        }
        for (const p of ratioRows) {
          const v = p.value as number;
          const label = (p.seriesName ?? "").replace(RATIO_SUFFIX, "");
          children.push(React.createElement(tooltipComponents.Row, null, [
            React.createElement("span", { style: { color: p.color ?? "" } }, "●"),
            ` ${label} 买入占比: `,
            React.createElement(tooltipComponents.Bold, null, `${fmtNum(v, 2)}%`),
          ]));
        }
        if ((hasMargin || ratioRows.length > 0) && hasClose) {
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
        const trendRows: Array<{ label: string; isUp: boolean; start: string; end: string; ratio: number | null }> = [];
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
                ratio: ep.ratio,
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
          const ratioPct = t.ratio != null ? ` · ${(t.ratio * 100).toFixed(2)}%` : "";
          children.push(React.createElement(tooltipComponents.Row, null, [
            React.createElement("span", { style: { color } }, arrow),
            ` ${t.label}: `,
            React.createElement(tooltipComponents.Bold, null, dirLabel),
            ratioPct,
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
      ...(hasRatio
        ? [{
            type: "value" as const,
            gridIndex: 0,
            position: "right" as const,
            scale: true,
            name: "买入占比 (%)",
            nameTextStyle: { color: c.textColor, fontSize: 9 },
            axisLine: { lineStyle: { color: c.axisLineColor } },
            axisLabel: { color: c.textColor, fontSize: 9, formatter: (v: number) => `${fmtNum(v, 0)}%` },
            splitLine: { show: false },
          }]
        : []),
    ],
    series: [...marginSeries, ...closeSeries, ...ratioSeries],
  };
}

