/**
 * convergenceOption — options expiry-convergence ("contrarian") chart
 * builder. The options analogue of the futures basis-convergence plot
 * (features/futures/chartOption/convergenceChart.ts), calibrated the same
 * way: the ZERO LINE IS ALWAYS THE SPOT CURVE.
 *
 * One line per expiry-month group: the signed gap between that group's
 * skew-adjusted price and the underlying spot, in % of spot — exactly the
 * price-space rebase the main shared skew chart uses:
 *   oi_moneyness: skew_price = S × E[OI-wtd moneyness]
 *                 → gap% = (E[M] − 1) × 100 (the raw skewPct)
 *   iv_smile:     skew_price = S × (1 + rr25 × 0.5%)
 *                 → gap% = rr25 × 0.5 (the raw skewPct; 0 = on spot)
 *   greek_*:      skew_price = S × (1 + (skew − neutral) × 0.10)
 *                 → gap% = skewPct × GREEK_SKEW_PRICE_K
 * As a group ages toward expiry its gap visibly collapses into zero
 * (OI pinning for oi_moneyness, wing/directional rebalancing for
 * iv_smile / greek_*), making crowded one-sided positioning into expiry
 * obvious as a line that STAYS away from zero.
 *
 * Markers: a dot at each group's LAST observed gap — labelled "expiry"
 * for matured groups (the convergence endpoint) and "now" for active
 * ones (where each group stands now).
 *
 * Tooltip: gap % per expiry group.
 *
 * Colors/order follow the main shared skew chart (expiryBlueColor sorted
 * by |expiryDate − selectedDate|) so lines visually tie to their
 * per-expiry skew curves.
 */
import React from "react";
import { useStore } from "@/store/filters";
import { axisColors, expiryBlueColor } from "@/theme/chart-palette";
import {
  AXIS_POINTER_LINE,
  TOOLTIP_CARD_BG,
  TOOLTIP_CARD_BORDER,
  TOOLTIP_CARD_TEXT,
} from "@/theme/chart-palette";
import { renderReactElement, tooltipComponents } from "@/lib/react-tooltip-renderer";
import { GREEK_SKEW_PRICE_K } from "./types";
import type { SharedSkewSpec } from "./types";
import type { EChartsOption } from "echarts";

/** Title of the convergence chart for a skew mode. */
export function convergenceTitle(mode: SharedSkewSpec["mode"]): string {
  if (mode === "oi_moneyness") {
    return "OI Convergence (contrarian) — OI-weighted strike vs spot gap, %";
  }
  if (mode === "iv_smile") {
    return "Vol Skew Convergence (contrarian) — 25Δ RR skew price vs spot gap, %";
  }
  if (mode === "smile_slope") {
    return "Smile Slope Convergence (contrarian) — full-smile skew price vs spot gap, %";
  }
  const label = mode.slice("greek_".length);
  const cap = label.charAt(0).toUpperCase() + label.slice(1);
  return `${cap} Balance Convergence (contrarian) — skew price vs spot gap, %`;
}

export function buildSkewConvergenceOption(
  spec: SharedSkewSpec,
  selectedDate: string,
  dataZoomStart?: number,
  dataZoomEnd?: number,
): EChartsOption | null {
  const { points, mode } = spec;
  if (points.length === 0) return null;

  const themeMode = useStore.getState().themeMode;
  const c = axisColors(themeMode);
  const textColor = c.textColor;
  const splitColor = c.splitLineColor;

  const dates = points.map((d) => d.date);
  const anchorDate =
    selectedDate && dates.includes(selectedDate)
      ? selectedDate
      : dates[dates.length - 1];
  const lastDate = dates[dates.length - 1];

  // Expiry groups with their boundary date, ordered like the main skew
  // chart (nearest expiry to the selected date first) for matching colors.
  const expiryByGroup = new Map<string, string>();
  for (const d of points) {
    for (const pe of d.perExpiry) {
      if (!expiryByGroup.has(pe.expiry) && pe.expiryDate) {
        expiryByGroup.set(pe.expiry, pe.expiryDate);
      }
    }
  }
  const expiryList = Array.from(expiryByGroup.entries())
    .sort(
      (a, b) =>
        Math.abs(a[1].localeCompare(anchorDate)) -
        Math.abs(b[1].localeCompare(anchorDate)),
    )
    .map(([exp]) => exp);
  if (expiryList.length === 0) return null;

  const fmtPct = (v: number) => `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;

  // Spot-at-zero calibration: convert the spec's per-expiry skewPct into
  // the skew-price-vs-spot gap in % of spot (see header). Only greek_*
  // modes need the price-space rebase; oi_moneyness / iv_smile skewPct is
  // already the % gap vs spot.
  const gapPctOfSpot = (skewPct: number): number =>
    mode.startsWith("greek_") ? skewPct * GREEK_SKEW_PRICE_K : skewPct;

  const mkSeries = (exp: string, ei: number) => {
    const color = expiryBlueColor(ei, expiryList.length);
    const expiryDate = expiryByGroup.get(exp) ?? "";

    const values: (number | null)[] = [];
    let lastIdx = -1;
    let lastVal: number | null = null;
    points.forEach((d, i) => {
      const pe = d.perExpiry.find((p) => p.expiry === exp);
      const raw = pe?.skewPct;
      if (raw != null && Number.isFinite(raw)) {
        const v = gapPctOfSpot(raw);
        values.push(v);
        lastIdx = i;
        lastVal = v;
      } else {
        values.push(null);
      }
    });
    if (lastIdx < 0 || lastVal == null) return null;

    // Convergence marker at the group's last observed gap: matured groups
    // (boundary <= last data date) label the expiry endpoint; active ones
    // label the current gap.
    const matured = !!expiryDate && expiryDate <= lastDate;
    const stage = matured ? "expiry" : "now";

    return {
      name: exp,
      type: "line" as const,
      showSymbol: false,
      connectNulls: false,
      sampling: "lttb" as const,
      data: values,
      itemStyle: { color },
      lineStyle: { width: 1.2, color, opacity: 0.75 },
      z: matured ? 5 : 10,
      markPoint:
        lastVal != null
          ? {
              symbol: "circle",
              symbolSize: 7,
              itemStyle: {
                color,
                borderColor: "#fff",
                borderWidth: 1,
                opacity: matured ? 0.75 : 1,
              },
              label: {
                show: true,
                position: "top" as const,
                distance: 5,
                fontSize: 10,
                color: "#666",
                formatter: `{${stage}|${fmtPct(lastVal)}}`,
                rich: {
                  expiry: { color: "#999", fontSize: 10 },
                  now: { color, fontSize: 10, fontWeight: 600 },
                },
              },
              data: [
                {
                  coord: [dates[lastIdx], lastVal] as [string, number],
                },
              ],
            }
          : undefined,
    };
  };

  const series = expiryList
    .map((exp, ei) => mkSeries(exp, ei))
    .filter((s): s is NonNullable<ReturnType<typeof mkSeries>> => s != null);
  if (series.length === 0) return null;

  return {
    backgroundColor: "transparent",
    animation: false,
    grid: { left: 60, right: 24, top: 24, bottom: 48 },
    tooltip: {
      trigger: "axis",
      axisPointer: {
        type: "line",
        lineStyle: { color: AXIS_POINTER_LINE, type: "dashed" },
      },
      confine: true,
      backgroundColor: TOOLTIP_CARD_BG,
      borderColor: TOOLTIP_CARD_BORDER,
      borderWidth: 1,
      textStyle: { color: TOOLTIP_CARD_TEXT, fontSize: 11 },
      formatter: (params: unknown) => {
        const arr = (Array.isArray(params) ? params : [params]) as Array<{
          dataIndex?: number;
          axisValue?: string;
          seriesName?: string;
          value?: number | null;
          color?: string;
        }>;
        if (arr.length === 0) return "";
        const dateStr = arr[0].axisValue ?? dates[arr[0].dataIndex ?? 0] ?? "";
        const filtered = arr.filter((p) => {
          const v = Array.isArray(p.value) ? p.value[1] : p.value;
          return v != null && Number.isFinite(v as number);
        });
        const children: React.ReactNode[] = [];
        children.push(
          React.createElement(tooltipComponents.Header, null, dateStr),
        );
        for (const p of filtered) {
          const v = (Array.isArray(p.value) ? p.value[1] : p.value) as number;
          children.push(
            React.createElement(tooltipComponents.Row, null, [
              React.createElement(
                "span",
                { style: { color: p.color ?? "" } },
                "●",
              ),
              ` ${p.seriesName ?? ""}: `,
              React.createElement(
                tooltipComponents.Bold,
                null,
                fmtPct(v),
              ),
            ]),
          );
        }
        return renderReactElement(
          React.createElement(React.Fragment, null, children),
        );
      },
    },
    xAxis: {
      type: "category",
      data: dates,
      axisLine: { lineStyle: { color: splitColor } },
      axisLabel: { fontSize: 11, color: textColor },
      splitLine: { show: false },
      boundaryGap: false,
    },
    yAxis: {
      type: "value",
      name: "Skew price vs spot gap (%)",
      nameTextStyle: { fontSize: 10, color: textColor },
      axisLine: { lineStyle: { color: splitColor } },
      axisLabel: {
        fontSize: 11,
        color: textColor,
        formatter: (v: number) => `${v >= 0 ? "+" : ""}${v}`,
      },
      splitLine: { lineStyle: { color: splitColor, type: "dashed", opacity: 0.4 } },
    },
    // The zero line (last series, silent + excluded from the tooltip) is
    // the convergence target — the skew-adjusted price MEETING SPOT.
    series: [
      ...series,
      {
        name: "__zero_line__",
        type: "line" as const,
        data: dates.map(() => 0),
        showSymbol: false,
        silent: true,
        lineStyle: { width: 1.5, color: "#999", type: "solid", opacity: 0.9 },
        z: 8,
        tooltip: { show: false },
      },
    ],
    dataZoom: [
      {
        type: "inside",
        start: dataZoomStart ?? 0,
        end: dataZoomEnd ?? 100,
        zoomLock: false,
      },
      {
        type: "slider",
        height: 18,
        bottom: 6,
        start: dataZoomStart ?? 0,
        end: dataZoomEnd ?? 100,
      },
    ],
  } as EChartsOption;
}
