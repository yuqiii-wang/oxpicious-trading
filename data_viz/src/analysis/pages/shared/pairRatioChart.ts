/**
 * Pair hedge-ratio chart kit — shared by the Composites page's
 * PairRatioSection and the signals Hedge Pairs page (moved 2026-10-17
 * from Composites/OppositeIndustryCorrelations/pairRatioChartOption.ts +
 * the PAIR_RATIO_* constants — one consumer became two).
 *
 * The charted metrics are the minimum-variance hedge ratio
 * h* = −Cov(A,B)/Var(A) per trailing window (20/60/255d) from
 * analysis.pair_pct_change_ratios, in a selectable mode: RAW (industry
 * aggregate pct changes) or MARKET OFFSET (each side minus the market
 * pct). See the sibling services for the table semantics.
 */
import React from "react";
import type { EChartsOption } from "echarts";
import { baseChartOption } from "@/shared/charts/base-chart";
import type { ThemeMode } from "@/store/filters";
import {
  axisColors,
  commonGrid,
  commonLegend,
  windowBlueColor,
} from "@/theme/chart-palette";
import { fmtNum } from "@/lib/series";
import { renderReactElement, tooltipComponents } from "@/lib/react-tooltip-renderer";
import type { PairPctRatioRow } from "@shared/types";

// ---- Pair pct-change ratios (analysis.pair_pct_change_ratios) -----------
/** The charted rolling windows of the hedge ratio (the DB's 5d window is
 *  not surfaced — too noisy to read). */
export const PAIR_RATIO_WINDOWS = ["ma20d", "ma60d", "ma255d"] as const;
export type PairRatioWindow = (typeof PAIR_RATIO_WINDOWS)[number];

/** The two hedge modes: RAW (industry aggregate pct changes) and
 *  MARKET-OFFSET (each industry's pct minus the benchmark market
 *  index's pct — the market factor cancelled; where the negative
 *  opposite-moves correlation and the buy-to-hedge answer live). */
export const PAIR_RATIO_MODES = ["raw", "offset"] as const;
export type PairRatioMode = (typeof PAIR_RATIO_MODES)[number];

export const PAIR_RATIO_MODE_COLS: Record<PairRatioMode, Record<PairRatioWindow, string>> = {
  raw: {
    ma20d: "hedge_raw_ma20d",
    ma60d: "hedge_raw_ma60d",
    ma255d: "hedge_raw_ma255d",
  },
  offset: {
    ma20d: "hedge_offset_ma20d",
    ma60d: "hedge_offset_ma60d",
    ma255d: "hedge_offset_ma255d",
  },
};

export const PAIR_RATIO_MODE_LABELS: Record<PairRatioMode, string> = {
  raw: "Raw",
  offset: "Market offset",
};

/** window → series label. */
export const PAIR_RATIO_LABELS: Record<PairRatioWindow, string> = {
  ma20d: "20d",
  ma60d: "60d",
  ma255d: "255d",
};

/**
 * Chart option for the pair hedge-ratio panel — the minimum-variance
 * hedge ratio h* = -Cov(A,B)/Var(A) per trailing window (20/60/255d)
 * for the selected industry pair, in the selected mode (raw or
 * market-offset), with an axis tooltip listing all three windows and a
 * dashed zero line (h* = 0: no hedge; h* > 0 = buy the subject to hedge
 * the exposure industry). The three windows share one hue at graded
 * darkness (windowBlueColor: dark = 20d, light = 255d) — the futures
 * dark-near/light-far ramp convention.
 */
export function buildPairRatioChartOption(
  rows: PairPctRatioRow[],
  mode: ThemeMode,
  hedgeMode: PairRatioMode,
): EChartsOption | null {
  if (rows.length === 0) return null;
  const c = axisColors(mode);
  const dates = rows.map((r) => r.date);
  const cols = PAIR_RATIO_MODE_COLS[hedgeMode];

  const series = PAIR_RATIO_WINDOWS.map((w, i) => {
    // One blue family at graded darkness (dark = 20d … light = 255d), the
    // futures expiry-ramp convention — the three windows are horizons of ONE
    // metric, not unrelated series.
    const color = windowBlueColor(i, PAIR_RATIO_WINDOWS.length);
    return {
      name: PAIR_RATIO_LABELS[w],
      type: "line" as const,
      smooth: false,
      showSymbol: false,
      connectNulls: false,
      data: rows.map((r) => {
        const v = (r as unknown as Record<string, number | null>)[cols[w]];
        return v == null || !Number.isFinite(v) ? null : v;
      }),
      lineStyle: { width: 1.6, color },
      itemStyle: { color },
      z: 3,
      // Hedge parity (h* = 0) on the first series only — one dashed
      // reference line, not three.
      markLine:
        i === 0
          ? {
              silent: true,
              symbol: "none",
              lineStyle: { color: c.splitLineColor, type: "dashed" },
              label: {
                formatter: "0",
                color: c.textColor,
                fontSize: 9,
              },
              data: [{ yAxis: 0 }],
            }
          : undefined,
    };
  });

  // Same tooltip style as the sibling correlation chart: a plain themed
  // card WITHOUT an axisPointer (page-level interaction consistency).
  return baseChartOption(mode, {
    grid: commonGrid({ left: 56, right: 24, bottom: 32 }),
    legend: commonLegend(mode, {
      data: series.map((s) => s.name),
    }),
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
        const idx0 = arr[0].dataIndex ?? 0;
        const row = rows[idx0];
        if (!row) return "";
        const children: React.ReactNode[] = [
          React.createElement(tooltipComponents.Header, null, row.date),
          React.createElement(
            "div",
            { style: { marginTop: 2, opacity: 0.7 } },
            `hedge ratio (${PAIR_RATIO_MODE_LABELS[hedgeMode]}), units of subject per unit benchmark, stride-20 grid`,
          ),
        ];
        const rowChildren: React.ReactNode[] = [];
        PAIR_RATIO_WINDOWS.forEach((w, i) => {
          const v = (row as unknown as Record<string, number | null>)[cols[w]];
          const color = windowBlueColor(i, PAIR_RATIO_WINDOWS.length);
          rowChildren.push(
            React.createElement(
              "div",
              {
                style: {
                  display: "flex",
                  justifyContent: "space-between",
                  gap: 8,
                },
                key: w,
              },
              React.createElement("span", { style: { color } }, "●"),
              React.createElement("span", { style: { flex: 1 } }, PAIR_RATIO_LABELS[w]),
              React.createElement(
                "span",
                null,
                v == null || !Number.isFinite(v)
                  ? "—"
                  : fmtNum(v, 3),
              ),
            ),
          );
        });
        children.push(
          React.createElement("div", { style: { marginTop: 4 } }, rowChildren),
        );
        return renderReactElement(
          React.createElement(React.Fragment, null, children),
        );
      },
    },
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
      name: "hedge h*",
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
  });
}
