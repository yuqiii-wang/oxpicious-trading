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
 *   greek_*:      skew_price = S × (1 + (skew − neutral) × 0.10)
 *                 → gap% = skewPct × GREEK_SKEW_PRICE_K
 * As a group ages toward expiry its gap visibly collapses into zero
 * (OI pinning for oi_moneyness, wing/directional rebalancing for
 * greek_*), making crowded one-sided positioning into expiry
 * obvious as a line that STAYS away from zero.
 *
 * Markers: a dot at each group's LAST observed gap — labelled "expiry"
 * for matured groups (the convergence endpoint) and "now" for active
 * ones (where each group stands now).
 *
 * Tooltip: gap % per expiry group; hovering a MATURED group's expiry
 * date (the dot's column) also shows that group's realized "% of OI
 * expiring OTM (worthless)" per call/put side — calls with strike ≥
 * settle, puts with strike ≤ settle — so a far-from-zero terminal gap
 * can be read as actual unexercised OI, not just a blended average.
 *
 * Colors/order/thickness follow the main shared skew chart (expiryBlueColor
 * sorted by |expiryDate − selectedDate|; line width ∝ the expiry's
 * absolute OI) so lines visually tie to their per-expiry skew curves.
 *
 * No time slider of its own: the x-axis mirrors the main skew chart's
 * (sharedSkewAxisDates, incl. the future-expiry tail) and the window is
 * set from the panel's shared zoom state — the main chart's slider drives
 * this chart. The inside dataZoom exists only to receive that window
 * (setOption), with all user interaction disabled.
 */
import React from "react";
import {
  baseChartOption,
  commonTooltip,
} from "@/shared/charts/base-chart";
import type { ThemeMode } from "@/store/filters";
import { axisColors, expiryBlueColor } from "@/theme/chart-palette";
import {
  AXIS_POINTER_LINE,
  TOOLTIP_CARD_BG,
  TOOLTIP_CARD_BORDER,
  TOOLTIP_CARD_TEXT,
} from "@/theme/chart-palette";
import { renderReactElement, tooltipComponents } from "@/lib/react-tooltip-renderer";
import { fmtCompact } from "@/lib/series";
import { GREEK_SKEW_PRICE_K } from "./types";
import { expiryOiWidths, sharedSkewAxisDates } from "./sharedSkewOption";
import type { OtmOiShare } from "../vol-smile/types";
import type { SharedSkewSpec } from "./types";
import type { EChartsOption } from "echarts";

/** Title of the convergence chart for a skew mode. */
export function convergenceTitle(mode: SharedSkewSpec["mode"]): string {
  if (mode === "oi_moneyness") {
    return "OI Convergence (contrarian) — OI-weighted strike vs spot gap, %";
  }
  const label = mode.slice("greek_".length);
  const cap = label.charAt(0).toUpperCase() + label.slice(1);
  return `${cap} Balance Convergence (contrarian) — skew price vs spot gap, %`;
}

export function buildSkewConvergenceOption(
  spec: SharedSkewSpec,
  mode: ThemeMode,
  selectedDate: string,
  dataZoomStart?: number,
  dataZoomEnd?: number,
): EChartsOption | null {
  // `mode` is the THEME here; the spec's data-source mode is `skewMode`.
  const { points, mode: skewMode } = spec;
  if (points.length === 0) return null;

  const c = axisColors(mode);
  const textColor = c.textColor;
  const splitColor = c.splitLineColor;

  const dates = points.map((d) => d.date);
  // Same axis as the main skew chart (data dates + future-expiry tail) so
  // the shared zoom percent covers the same dates on both charts.
  const axisDates = sharedSkewAxisDates(spec, selectedDate);
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
  const fmtShare = (v: number | null): string =>
    v == null ? "n/a" : `${Math.round(v * 100)}%`;

  // Expiry-dot payloads for the tooltip: per expiry group, its last observed
  // index (the dot's date), maturity, and the OTM/worthless OI shares at
  // that final observation (oi_moneyness only; null for greek_* specs).
  const expiryMeta = new Map<
    string,
    { lastIdx: number; matured: boolean; otm: OtmOiShare | null }
  >();

  // Line thickness ∝ the expiry's ABSOLUTE open interest (same encoding
  // as the main skew chart); the plotted gap% stays the ratio signal.
  const oiWidths = expiryOiWidths(spec);

  // Spot-at-zero calibration: convert the spec's per-expiry skewPct into
  // the skew-price-vs-spot gap in % of spot (see header). Only greek_*
  // modes need the price-space rebase; oi_moneyness / iv_smile skewPct is
  // already the % gap vs spot.
  const gapPctOfSpot = (skewPct: number): number =>
    skewMode.startsWith("greek_") ? skewPct * GREEK_SKEW_PRICE_K : skewPct;

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
    expiryMeta.set(exp, {
      lastIdx,
      matured,
      otm: points[lastIdx].perExpiry.find((p) => p.expiry === exp)?.otmShare ?? null,
    });

    return {
      name: exp,
      type: "line" as const,
      showSymbol: false,
      connectNulls: false,
      sampling: "lttb" as const,
      data: values,
      itemStyle: { color },
      lineStyle: { width: oiWidths.get(exp) ?? 1.2, color, opacity: 0.75 },
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

  // No legend in the original layout — the per-expiry series are read off
  // the expiry dots/labels; omit the kit's default legend fragment.
  return baseChartOption(mode, {
    legend: null,
    // No bottom slider anymore — the plot can use the full card height.
    grid: { left: 60, right: 24, top: 24, bottom: 24 },
    tooltip: commonTooltip(mode, {
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
        const dateStr =
          arr[0].axisValue ?? axisDates[arr[0].dataIndex ?? 0] ?? "";
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
          // The group's absolute OI at the hovered date (oi_moneyness only)
          const hoveredOi = (() => {
            const di = p.dataIndex ?? -1;
            const point = di >= 0 && di < points.length ? points[di] : undefined;
            return point?.perExpiry.find((pe) => pe.expiry === p.seriesName)?.oiTotal;
          })();
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
              ...(hoveredOi != null
                ? [` · OI ${fmtCompact(hoveredOi)}`]
                : []),
            ]),
          );
          // Hovering a matured group's expiry dot (its last observed date):
          // realized % of OI that expired worthless, per side.
          const meta = expiryMeta.get(p.seriesName ?? "");
          if (
            meta != null &&
            meta.matured &&
            meta.lastIdx === (p.dataIndex ?? -1) &&
            meta.otm != null
          ) {
            children.push(
              React.createElement(
                tooltipComponents.Row,
                { style: { fontSize: 10, color: "#999", marginLeft: 12 } },
                `↳ OTM at expiry (worthless): calls ${fmtShare(meta.otm.callShare)} · puts ${fmtShare(meta.otm.putShare)} · all ${fmtShare(meta.otm.allShare)} of OI`,
              ),
            );
          }
        }
        return renderReactElement(
          React.createElement(React.Fragment, null, children),
        );
      },
    }),
    xAxis: {
      type: "category",
      data: axisDates,
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
    // Slider-free window: driven by the main skew chart's time slider via
    // the panel's shared zoom state. The inside component receives the
    // window through setOption only — wheel zoom / drag pan are disabled
    // so this chart can never drift out of sync.
    dataZoom: [
      {
        type: "inside",
        xAxisIndex: 0,
        start: dataZoomStart ?? 0,
        end: dataZoomEnd ?? 100,
        filterMode: "none",
        zoomOnMouseWheel: false,
        moveOnMouseWheel: false,
        moveOnMouseMove: false,
      },
    ],
  });
}
