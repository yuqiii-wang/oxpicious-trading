/**
 * chart/hypedDrainedOption — the companion "hyped vs drained" option
 * builder: ONE security's volatility-amplification split as a clean
 * single-axis HORIZONTAL grouped bar chart — REGIME GROUPS × margin
 * tercile bars.
 *
 * y groups = the canonical market regimes (stats.market_regimes: calm /
 *     hot / panic / quiet, shown top→bottom in label-dim order; the
 *     group label carries that regime's accent color + the within-regime
 *     amplification ×ratio);
 * bars   = mean |daily return| % for the code's Drained (low buyShare
 *     rank tercile, faint) vs Hyped (high tercile, saturated) days WITHIN
 *     that regime — the Mid tercile is dropped (no contrast);
 * bar-end label = mean |ret| % + up-hit rate ("↑ 48%" — share of days
 *     closing up).
 *
 * Why the regime split (study_margin_regime_amplification +
 * probe_margin_axis_noise, 8 study indices 2020-2026): the pooled
 * hyped/drained contrast (×1.18, t=+8.0) mixes a clean non-stress
 * amplification (calm ×1.10 t=+3.9, quiet ×1.11 t=+1.9 — non-stress
 * pooled ×1.19 t=+8.4) with a genuine INVERSION in panic (×0.66,
 * t=−5.0: margin-hyped panic days are calmer than drained ones) and a
 * null in hot (×0.93). The regimes must stay the GROUPING, not the bar
 * axis — mean |ret| per regime alone would just re-plot the vol_z-based
 * regime definition.
 */
import type { EChartsOption } from "echarts";
import React from "react";
import type { MarketRegime } from "@shared/types";
import type { ThemeMode } from "@/store/filters";
import { baseChartOption, commonTooltip } from "@/shared/charts/base-chart";
import {
  ALL_REGIMES,
  REGIME_ACCENT_COLORS,
  REGIME_LABELS,
} from "@/shared/charts/regimeBands";
import { MUTED_INLINE_COLOR, PALETTE_HI, UP_COLOR, axisColors } from "@/theme/chart-palette";
import { fmtNum } from "@/lib/series";
import { renderReactElement, tooltipComponents } from "@/lib/react-tooltip-renderer";
import { fmtShare } from "./types";
import { regimeAmplificationRatio } from "./derive";
import type { CodeDerived } from "./types";

const DRAINED = "Drained" as const;
const HYPED = "Hyped" as const;

export interface HypedDrainedArgs {
  code: string;
  label: string;
  derived: CodeDerived;
  themeMode: ThemeMode;
}

export function buildHypedDrainedOption(args: HypedDrainedArgs): EChartsOption | null {
  const { label, derived, themeMode } = args;
  const bins = derived.bins;
  const regimeBins = derived.regimeBins;
  if (!bins || !regimeBins) return null;

  const c = axisColors(themeMode);

  // y category order: ECharts puts data[0] at the BOTTOM — reverse the
  // canonical order so the display reads calm → hot → panic → quiet
  // top→bottom.
  const yAxisData = [...ALL_REGIMES].reverse() as MarketRegime[];

  // Series-level itemStyle (NOT per-datum): the legend markers inherit the
  // series color/opacity — per-datum styling leaves the series colorless
  // and the legend falls back to the default palette (the mismatch bug).
  const seriesStyle = (k: 0 | 1) => ({
    color: UP_COLOR,
    opacity: k === 0 ? 0.45 : 1.0,
    borderRadius: [0, 2, 2, 0],
  });
  const mkData = (k: 0 | 1): Array<number | null> =>
    yAxisData.map((reg) => regimeBins[reg][k].meanAbsRet);

  const mkLabel = (k: 0 | 1) => ({
    show: true,
    position: "right" as const,
    distance: 6,
    formatter: (p: unknown) => {
      const pt = p as { dataIndex?: number; value?: number | null };
      const reg = yAxisData[pt.dataIndex ?? 0];
      const bin = regimeBins[reg][k];
      const hit = bin.hitRate;
      return `{v|${pt.value != null ? `${fmtNum(pt.value, 2)}%` : "—"}}{h|  ${
        hit != null ? `↑ ${fmtNum(hit * 100, 0)}%` : "↑ —"
      }}`;
    },
    rich: {
      v: { fontSize: 10, fontWeight: 600, color: c.textColor },
      h: { fontSize: 9, color: PALETTE_HI },
    },
  });

  const cut = derived.cutpoints;
  const ratioOf = (reg: MarketRegime): number | null =>
    regimeAmplificationRatio(regimeBins[reg]);

  return baseChartOption(themeMode, {
    legend: {
      icon: "roundRect",
      itemWidth: 10,
      itemHeight: 8,
      itemGap: 12,
      top: 0,
      right: 0,
      textStyle: { color: c.textColor, fontSize: 10 },
      data: [DRAINED, HYPED],
    },
    grid: { left: 88, right: 64, top: 28, bottom: 26 },
    // No ECharts title component: the chart label rides in BaseChart
    // children (HypedVsDrainedChart), matching the main chart's
    // convention. The bar-end labels carry the values, so the value axis
    // needs no name.
    title: null,
    tooltip: commonTooltip(themeMode, {
      trigger: "item",
      axisPointer: undefined,
      formatter: (p: unknown) => {
        const pt = p as {
          seriesName?: string;
          dataIndex?: number;
        };
        const reg = yAxisData[pt.dataIndex ?? 0];
        const k: 0 | 1 = pt.seriesName === HYPED ? 1 : 0;
        const bin = regimeBins[reg][k];
        // buyShare range covered by this side of the pair (cutpoints)
        const range = cut
          ? k === 0
            ? `buy-share 0–${fmtShare(cut[0])}`
            : `buy-share ${fmtShare(cut[1])}+`
          : null;
        const ratio = ratioOf(reg);
        const children: React.ReactNode[] = [
          React.createElement(
            tooltipComponents.Header,
            null,
            `${REGIME_LABELS[reg]} · ${label}`,
          ),
        ];
        if (range) {
          children.push(
            React.createElement(
              "div",
              { style: { opacity: 0.8, fontSize: 10 } },
              `${k === 0 ? DRAINED : HYPED} tercile — ${range}`,
            ),
          );
        }
        children.push(
          React.createElement(tooltipComponents.Row, null, [
            `${k === 0 ? DRAINED : HYPED} mean |ret|: `,
            React.createElement(
              tooltipComponents.Bold,
              null,
              bin.meanAbsRet != null ? `${fmtNum(bin.meanAbsRet, 3)}%` : "—",
            ),
          ]),
          React.createElement(tooltipComponents.Row, null, [
            "Up-hit: ",
            bin.hitRate != null ? `${fmtNum(bin.hitRate * 100, 1)}%` : "—",
          ]),
          React.createElement(tooltipComponents.Row, null, [
            "Mean ret: ",
            bin.meanRet != null
              ? `${bin.meanRet >= 0 ? "+" : ""}${fmtNum(bin.meanRet, 3)}%`
              : "—",
          ]),
          React.createElement(tooltipComponents.Row, null, [`Days: ${bin.n}`]),
          React.createElement(tooltipComponents.Row, null, [
            "Amplification (Hyped/Drained): ",
            ratio != null ? `×${ratio.toFixed(2)}` : "—",
          ]),
        );
        return renderReactElement(React.createElement(React.Fragment, null, children));
      },
    }),
    xAxis: {
      type: "value",
      min: 0,
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: {
        color: c.textColor,
        fontSize: 9,
        formatter: (v: number) => `${fmtNum(v, 1)}%`,
      },
      splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.4 } },
    },
    yAxis: {
      type: "category",
      data: yAxisData,
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisTick: { show: false },
      // group label: regime name in its accent color + the within-regime
      // amplification ratio (the study's per-regime number, made literal)
      axisLabel: {
        fontSize: 10,
        margin: 10,
        formatter: (v: string) => {
          const ratio = ratioOf(v as MarketRegime);
          return `{${v}|${REGIME_LABELS[v as MarketRegime]}}{r|  ${
            ratio != null ? `×${ratio.toFixed(2)}` : "×—"
          }}`;
        },
        rich: {
          calm: { fontSize: 10, color: REGIME_ACCENT_COLORS.calm },
          hot: { fontSize: 10, color: REGIME_ACCENT_COLORS.hot },
          panic: { fontSize: 10, color: REGIME_ACCENT_COLORS.panic },
          quiet: { fontSize: 10, color: REGIME_ACCENT_COLORS.quiet },
          r: { fontSize: 9, color: MUTED_INLINE_COLOR },
        },
      },
    },
    series: [
      {
        name: DRAINED,
        type: "bar",
        data: mkData(0),
        itemStyle: seriesStyle(0),
        barWidth: "32%",
        barGap: "25%",
        label: mkLabel(0),
      },
      {
        name: HYPED,
        type: "bar",
        data: mkData(1),
        itemStyle: seriesStyle(1),
        barWidth: "32%",
        label: mkLabel(1),
      },
    ],
  });
}
