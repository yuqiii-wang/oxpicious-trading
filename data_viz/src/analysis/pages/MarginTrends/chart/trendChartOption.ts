/**
 * chart/trendChartOption — the main composite Margin Trends option builder.
 *
 * Two synced grids:
 *   TOP    — margin series (Balance | Buy, in 亿), one line per security;
 *            muted for unselected, highlighted for selected.
 *   BOTTOM — the volatility-amplification view: DAILY RETURN bars per
 *            selected security, colored up/down by sign and SATURATED by
 *            the margin-buy share of turnover (buyShare per-code rank
 *            tercile: drained ≈ 25% opacity → hyped = full). A thin dashed
 *            trailing-20d return-vol line rides a right % axis.
 *
 * Bar colors are the GLOBAL shared CSS red/green scheme (UP_COLOR /
 * DOWN_COLOR → --chart-up / --chart-down in theme/colors.css).
 *
 * Study grounding: see chart/derive.ts header.
 */
import type { EChartsOption } from "echarts";
import type {
  MarginIndustrySeriesResponse,
  MarginSecurity,
} from "@shared/types";
import type { ThemeMode } from "@/store/filters";
import { baseChartOption } from "@/shared/charts/base-chart";
import {
  GROUP_MAJOR_COLORS,
  MARKER_EDGE_GRAY,
  MUTED_PALETTE,
  TRIGGER_DATE_COLOR,
  UP_COLOR,
  DOWN_COLOR,
  axisColors,
} from "@/theme/chart-palette";
import { fmtNum } from "@/lib/series";
import type { MarginAnalytics, PivotResult } from "./types";
import { MEMBERS_SUFFIX, RET_SUFFIX, VOL_SUFFIX } from "./types";
import type { MarginAttribution, MarginSeries } from "../constants";
import { buildTrendTooltip } from "./trendTooltip";

const MUTED_GRAY = MUTED_PALETTE[7] ?? "#999999";

/** Return-bar opacity by buyShare tercile: 0 drained / 1 mid / 2 hyped,
 *  -1 unknown (null share day or no tercile history). */
const TERCILE_OPACITY = [0.25, 0.6, 1.0] as const;
const UNKNOWN_OPACITY = 0.35;

/** All inputs of the composite option, grouped (decoupled from fetch/state). */
export interface TrendChartArgs {
  seriesData: MarginIndustrySeriesResponse;
  pivot: PivotResult;
  analytics: MarginAnalytics;
  displaySecurities: MarginSecurity[];
  selectedCodes: string[];
  attribution: MarginAttribution;
  series: MarginSeries;
  themeMode: ThemeMode;
  isSingleItemMode: boolean;
  selectedItemCode: string | null | undefined;
  /** Industry attribution: the pivot carries the NORMALIZED curve (SUM of
   *  the per-side member averages) — switches the y-axis unit label. The
   *  analytics are raw-based regardless. */
  normalized?: boolean;
}

export function buildTrendChartOption(args: TrendChartArgs): EChartsOption | null {
  const {
    seriesData, pivot, analytics, displaySecurities, selectedCodes,
    attribution, series, themeMode, isSingleItemMode, selectedItemCode,
    normalized,
  } = args;
  if (!seriesData || seriesData.rows.length === 0 || displaySecurities.length === 0) {
    return null;
  }

  const { dates, codeMargin } = pivot;
  const effectiveSelectedSet = isSingleItemMode && selectedItemCode
    ? new Set([selectedItemCode])
    : new Set(selectedCodes);
  const selectedList = displaySecurities.filter((s) => effectiveSelectedSet.has(s.code));

  const c = axisColors(themeMode);
  const seriesLabel = series === "balance" ? "RZ Balance" : "RZ Buy";
  const unit =
    attribution === "index" ? "100M yuan (w-avg)"
    : attribution === "industry"
      ? (normalized ? "100M yuan (member-avg stock+ETF)" : "100M yuan (SUM stock+ETF)")
      : "100M yuan";

  const codeToLabel = new Map<string, string>();
  for (const sec of displaySecurities) {
    codeToLabel.set(sec.code, sec.label || sec.code);
  }
  const colorOf = (code: string): string => {
    const idx = displaySecurities.findIndex((s) => s.code === code);
    return GROUP_MAJOR_COLORS[(idx < 0 ? 0 : idx) % GROUP_MAJOR_COLORS.length];
  };

  // ---- TOP grid: margin lines --------------------------------------------
  const marginSeries = displaySecurities.map((sec) => {
    const dm = codeMargin.get(sec.code);
    const data = dates.map((d) => {
      const v = dm?.get(d) ?? null;
      return v != null && Number.isFinite(v) ? v : null;
    });
    const isSelected = effectiveSelectedSet.has(sec.code);
    const color = colorOf(sec.code);
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
    };
  });

  // ---- Member-change dots (industry rows carry n_members) -----------------
  // One dot on the margin line per date whose n_members differs from the
  // previous row of that code — the population behind the industry's
  // averages shifted, so the curve moved for composition reasons, not
  // margin reasons. Rendered in BOTH raw and normalized modes (whenever
  // the rows carry member counts). Dates where the line itself is null
  // are skipped (nothing to anchor a dot to).
  const hasMemberData = seriesData.rows.some((r) => r.n_members != null);
  const memberChangeData = new Map<string, Array<[string, number]>>();
  if (hasMemberData) {
    const prevByCode = new Map<string, number | null>();
    for (const r of seriesData.rows) {
      const n = r.n_members ?? null;
      const prev = prevByCode.get(r.code);
      if (prev != null && n != null && n !== prev) {
        const v = codeMargin.get(r.code)?.get(r.date) ?? null;
        if (v != null) {
          let arr = memberChangeData.get(r.code);
          if (!arr) {
            arr = [];
            memberChangeData.set(r.code, arr);
          }
          arr.push([r.date, v]);
        }
      }
      prevByCode.set(r.code, n);
    }
  }
  const memberChangeSeries = displaySecurities
    .map((sec) => ({ sec, data: memberChangeData.get(sec.code) ?? [] }))
    .filter((m) => m.data.length > 0)
    .map(({ sec, data }) => ({
      name: `${sec.label || sec.code}${MEMBERS_SUFFIX}`,
      type: "scatter" as const,
      xAxisIndex: 0,
      yAxisIndex: 0,
      data,
      // Pinpoint style (the signal-trigger convention): the marker must
      // read against ANY line color — line-colored dots are invisible on
      // the line itself.
      symbolSize: 7,
      itemStyle: {
        color: TRIGGER_DATE_COLOR,
        borderColor: MARKER_EDGE_GRAY,
        borderWidth: 1,
      },
      z: 6,
      emphasis: { focus: "series" as const },
    }));

  // ---- BOTTOM grid: daily return bars, saturation = margin intensity ----
  // Bar color: sign → global CSS red/green (UP_COLOR/DOWN_COLOR); opacity:
  // buyShare rank tercile of the code (drained → hyped). The visual claim
  // rendered: margin-heavy days carry bigger, one-sided swings.
  const retBarSeries = selectedList.map((sec) => {
    const color = colorOf(sec.code);
    const derived = analytics.byCode.get(sec.code);
    const data = dates.map((d) => {
      const p = derived?.points.get(d);
      const r = p?.ret;
      if (r == null || !Number.isFinite(r)) return null;
      const base = r >= 0 ? UP_COLOR : DOWN_COLOR;
      const opacity =
        p != null && p.tercile >= 0
          ? TERCILE_OPACITY[p.tercile]
          : UNKNOWN_OPACITY;
      return { value: r, itemStyle: { color: base, opacity } };
    });
    return {
      name: `${sec.label || sec.code}${RET_SUFFIX}`,
      type: "bar" as const,
      xAxisIndex: 1,
      yAxisIndex: 1,
      data,
      barWidth: "60%",
      itemStyle: { color },
      emphasis: { focus: "series" as const },
      z: 3,
    };
  });

  // ---- BOTTOM grid: trailing-20d return vol (right % axis) --------------
  // Axis order: [0]=margin (grid 0), [1]=ret (grid 1), [2]=vol (grid 1,
  // right). Bind by computed index, never a literal.
  const volSeries = selectedList.map((sec) => {
    const color = colorOf(sec.code);
    const derived = analytics.byCode.get(sec.code);
    const data = dates.map((d) => derived?.points.get(d)?.vol20 ?? null);
    return {
      name: `${sec.label || sec.code}${VOL_SUFFIX}`,
      type: "line" as const,
      xAxisIndex: 1,
      yAxisIndex: 2,
      smooth: true,
      showSymbol: false,
      connectNulls: false,
      data,
      lineStyle: { width: 1.0, color, type: "dashed" as const, opacity: 0.8 },
      itemStyle: { color },
      emphasis: { focus: "series" as const },
      z: 4,
    };
  }).filter((s) => s.data.some((v) => v != null));
  const hasVol = volSeries.length > 0;

  // date×code member count behind the averages (rows carry n_members) —
  // the tooltip appends "· N members (Δ ±k)" to margin rows.
  const membersByCodeDate = hasMemberData
    ? new Map(
        seriesData.rows.map((r) => [`${r.code}|${r.date}`, r.n_members ?? null]),
      )
    : undefined;

  const tooltip = buildTrendTooltip({
    dates,
    analytics,
    codeToLabel,
    effectiveSelectedSet,
    unit,
    themeMode,
    membersByCodeDate,
  });

  return baseChartOption(themeMode, {
    grid: [
      { left: 64, right: 24, top: 32, height: "38%" },
      { left: 64, right: hasVol ? 56 : 24, top: "54%", bottom: 28 },
    ],
    axisPointer: {
      link: [{ xAxisIndex: "all" }],
    },
    legend: {
      data: [
        ...selectedList.map((s) => s.label || s.code),
        ...retBarSeries.map((s) => s.name),
        ...volSeries.map((s) => s.name),
      ],
      textStyle: { color: c.textColor, fontSize: 10 },
      top: 0,
      type: "scroll",
    },
    tooltip,
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
        // 1 decimal: Buy-mode values are ~0.01-0.2 亿 — 0 decimals would
        // round every tick to "0".
        axisLabel: { color: c.textColor, fontSize: 9, formatter: (v: number) => fmtNum(v, 1) },
        splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.4 } },
      },
      {
        type: "value",
        gridIndex: 1,
        name: "Daily Ret (%)",
        nameTextStyle: { color: c.textColor, fontSize: 9 },
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: { color: c.textColor, fontSize: 9, formatter: (v: number) => fmtNum(v, 0) },
        splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.4 } },
      },
      ...(hasVol
        ? [{
            type: "value" as const,
            gridIndex: 1,
            position: "right" as const,
            scale: true,
            name: "20d Vol (%)",
            nameTextStyle: { color: c.textColor, fontSize: 9 },
            axisLine: { lineStyle: { color: c.axisLineColor } },
            axisLabel: { color: c.textColor, fontSize: 9, formatter: (v: number) => `${v.toFixed(1)}%` },
            splitLine: { show: false },
          }]
        : []),
    ],
    series: [...marginSeries, ...memberChangeSeries, ...retBarSeries, ...volSeries],
  });
}
