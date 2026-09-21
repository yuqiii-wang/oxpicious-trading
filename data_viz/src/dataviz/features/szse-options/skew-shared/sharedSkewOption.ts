/**
 * Unified skew-over-time chart builder — renders a SharedSkewSpec from
 * EITHER data source (oi_moneyness / greek_*) with the identical layout:
 *
 *   • Underlying spot curve (solid blue) + selected-date mark
 *   • Mean (aggregate) skew curve in price space (thick dashed blue)
 *   • Per-expiry blue-gradient dashed lines — line WIDTH encodes the
 *     expiry's absolute OI (peak daily calls+puts, sqrt-scaled) while the
 *     plotted value stays the ratio-based skew
 *   • Expiry shade bands from the selected date to each active expiry
 *     (band between spot and that expiry's skew curve)
 *   • Expiry dot marking each active expiry's closing boundary
 *
 * Generalized from the OI-wtd moneyness skew chart (oiMoneynessOption.ts).
 */
import {
  baseChartOption,
  commonTooltip,
  emptyChartOption,
} from "@/shared/charts/base-chart";
import type { ThemeMode } from "@/store/filters";
import {
  axisColors,
  commonDataZoom,
  commonGrid,
  commonLegend,
  expiryBlueColor,
  FUTURES_BLUE_NEAR,
  IV_BLUE,
} from "@/theme/chart-palette";
import { fmtNum } from "@/lib/series";
import { makeSharedSkewTooltipFormatter } from "./sharedSkewTooltip";
import type { SharedSkewSpec } from "./types";
import type { EChartsOption } from "echarts";

/** Expiry-set boundary dates active ON the selected date (deduped, sorted). */
export function activeExpiryDatesOf(
  spec: SharedSkewSpec,
  selectedDate: string,
): string[] {
  const dates = spec.points.map((d) => d.date);
  const selectedIdx = dates.indexOf(selectedDate);
  if (selectedIdx < 0) return [];
  return Array.from(
    new Set(
      spec.points[selectedIdx].perExpiry
        .map((pe) => pe.expiryDate)
        .filter((ed) => ed && ed >= selectedDate),
    ),
  ).sort();
}

/**
 * Category axis shared by BOTH skew panels: real data dates plus active
 * expiry dates past the last data date (so shade bands reach the true
 * expiry instead of being clipped). The convergence chart mirrors it, so a
 * shared dataZoom {start,end} percent covers the SAME dates on both charts
 * and cross-chart tooltip indices line up.
 */
export function sharedSkewAxisDates(
  spec: SharedSkewSpec,
  selectedDate: string,
): string[] {
  const dates = spec.points.map((d) => d.date);
  const lastDate = dates[dates.length - 1];
  return [
    ...dates,
    ...activeExpiryDatesOf(spec, selectedDate).filter((ed) => ed > lastDate),
  ];
}

/** Line-width range of the per-expiry OI-thickness encoding. */
export const OI_WIDTH_MIN = 0.7;
export const OI_WIDTH_MAX = 3.0;

/**
 * Per-expiry line width ∝ the expiry's ABSOLUTE open interest: the peak
 * daily total OI (calls + puts) over the curve, sqrt-scaled so perceived
 * thickness (area) grows with position size. The curves' VALUES stay the
 * ratio-based skew — thickness adds the size dimension. Expiries without
 * OI data (greek_* specs) are absent from the map; callers fall back to
 * their neutral width.
 */
export function expiryOiWidths(spec: SharedSkewSpec): Map<string, number> {
  const peak = new Map<string, number>();
  for (const p of spec.points) {
    for (const pe of p.perExpiry) {
      if (pe.oiTotal == null || !Number.isFinite(pe.oiTotal)) continue;
      const cur = peak.get(pe.expiry) ?? 0;
      if (pe.oiTotal > cur) peak.set(pe.expiry, pe.oiTotal);
    }
  }
  let max = 0;
  for (const v of peak.values()) if (v > max) max = v;
  const widths = new Map<string, number>();
  if (max <= 0) return widths;
  for (const [exp, v] of peak) {
    widths.set(
      exp,
      OI_WIDTH_MIN +
        (OI_WIDTH_MAX - OI_WIDTH_MIN) * Math.sqrt(Math.max(0, v) / max),
    );
  }
  return widths;
}

export function buildSharedSkewOption(
  spec: SharedSkewSpec,
  mode: ThemeMode,
  selectedDate: string,
  dataZoomStart?: number,
  dataZoomEnd?: number,
): EChartsOption {
  const c = axisColors(mode);
  const textColor = c.textColor;
  const splitColor = c.splitLineColor;

  const { points, chartTitle, meanSeriesName } = spec;

  if (points.length === 0) {
    return emptyChartOption(mode, `${chartTitle}  [No data]`);
  }

  const spotData = points.map((d) => [d.date, d.spot]);
  const skewData = points.map((d) =>
    d.skewPrice != null ? [d.date, d.skewPrice] : null,
  );

  const dates = points.map((d) => d.date);
  const selectedIdx = dates.indexOf(selectedDate);

  const allExpiries = new Map<string, string>();
  for (const d of points) {
    for (const pe of d.perExpiry) {
      if (!allExpiries.has(pe.expiry) && pe.expiryDate) {
        allExpiries.set(pe.expiry, pe.expiryDate);
      }
    }
  }

  const expiryEntries = Array.from(allExpiries.entries());
  expiryEntries.sort((a, b) => {
    const da = Math.abs(a[1].localeCompare(selectedDate));
    const db = Math.abs(b[1].localeCompare(selectedDate));
    return da - db;
  });
  const expiryList = expiryEntries.map(([exp]) => exp);
  const nExpiries = expiryList.length;

  // Currently active expiry sets ON the selected date (each set's
  // expiryDate is its boundary): one shade per set, from the selected
  // date till that expiry. Overlapping layers make near-term regions
  // darker and far regions lighter.
  const activeExpiryDates = activeExpiryDatesOf(spec, selectedDate);

  // Stretch the x-axis past the data range so every shade reaches its
  // contract's true expiry (e.g. 3/6/9-month sets) instead of being
  // clipped at the last data date. Real data dates stay the axis prefix.
  // Shared with the convergence chart (see sharedSkewAxisDates).
  const axisDates = sharedSkewAxisDates(spec, selectedDate);

  // Expiry dates within the range but not on the plotted category axis
  // (non-trading days) clamp to the nearest earlier plotted date.
  const expiryX = (ed: string): string => {
    if (axisDates.includes(ed)) return ed;
    let lo = dates[0];
    for (const dt of dates) if (dt < ed) lo = dt;
    return lo;
  };

  const hasActiveExpiries = selectedIdx >= 0 && activeExpiryDates.length > 0;

  const expiryColorMap = new Map<string, string>();
  expiryList.forEach((exp, ei) => {
    expiryColorMap.set(exp, expiryBlueColor(ei, nExpiries));
  });

  // Thickness ∝ absolute OI (peak daily total per expiry); ratio values
  // unchanged. Greek specs carry no OI → neutral width 1.
  const oiWidths = expiryOiWidths(spec);

  const perExpirySeries: EChartsOption["series"] = expiryList.map((exp, ei) => {
    const data: (number | null)[] = points.map((d) => {
      const pe = d.perExpiry.find((p) => p.expiry === exp);
      if (!pe || pe.skewPrice == null) return null;
      return pe.skewPrice;
    });
    const color = expiryBlueColor(ei, nExpiries);
    return {
      type: "line" as const,
      name: `Skew ${exp}`,
      showSymbol: false,
      smooth: false,
      connectNulls: false,
      lineStyle: {
        color,
        width: oiWidths.get(exp) ?? 1,
        type: "dashed" as const,
        opacity: 0.45,
      },
      itemStyle: { color },
      data,
      z: 1,
      tooltip: { show: false },
    };
  });

  // Layered light-blue shades centered about the spot curve: each active
  // expiry shades the band between the spot curve and that expiry's own
  // skewness curve — skew can sit above OR below spot, so the band is
  // anchored at the lower edge min(spot, skew) with width |skew − spot|
  // (always non-negative; ECharts stacks positive/negative values in
  // separate sign groups, so a signed width would break the band toward 0
  // and expand the y-axis). Rendered as stacked pairs (base = lower edge,
  // width ≥ 0) with invisible lines and a translucent area fill (no smooth
  // — smooth breaks stacked band boundaries); beyond the last data date the
  // band carries the last edges flat to the true expiry, so the y-axis
  // extent never changes. Each expiry also gets a dot at its expiry,
  // centered on the band (spot ↔ skew midpoint).
  const SHADE_COLOR = "rgba(31, 119, 180, 0.12)";
  const expiryShadeSeries: EChartsOption["series"] = [];
  if (hasActiveExpiries) {
    const lastD = points[points.length - 1];
    const activeSets = points[selectedIdx].perExpiry
      .filter((pe) => pe.expiryDate && pe.expiryDate >= selectedDate)
      .sort((a, b) => a.expiryDate.localeCompare(b.expiryDate));
    activeSets.forEach((pe, k) => {
      const xEnd = expiryX(pe.expiryDate);
      const endIdx = Math.max(axisDates.indexOf(xEnd), selectedIdx);
      const base: (number | null)[] = [];
      const diff: (number | null)[] = [];
      let lastLower: number | null = null;
      let lastWidth: number | null = null;
      let lastSkew: number | null = null;
      for (let i = 0; i < axisDates.length; i++) {
        if (i < selectedIdx || i > endIdx) {
          base.push(null);
          diff.push(null);
          continue;
        }
        if (i < dates.length) {
          const d = points[i];
          const sk =
            d.perExpiry.find((p) => p.expiry === pe.expiry)?.skewPrice ?? null;
          if (sk != null) {
            const lower = Math.min(d.spot, sk);
            lastLower = lower;
            lastWidth = Math.abs(sk - d.spot);
            lastSkew = sk;
            base.push(lower);
            diff.push(lastWidth);
          } else {
            base.push(d.spot);
            diff.push(null);
          }
        } else {
          // Appended future expiry dates: carry the last band edges flat.
          base.push(lastLower);
          diff.push(lastWidth);
        }
      }
      if (lastSkew == null) return; // no skew values in range → nothing to bound
      const stackId = `exp-shade-${k}`;
      const ySpot = endIdx < dates.length ? points[endIdx].spot : lastD.spot;

      expiryShadeSeries.push(
        {
          type: "line" as const,
          name: `shade-base ${pe.expiry}`,
          data: base,
          stack: stackId,
          showSymbol: false,
          smooth: false,
          lineStyle: { opacity: 0 },
          silent: true,
          tooltip: { show: false },
          z: 0,
        },
        {
          type: "line" as const,
          name: `shade ${pe.expiry}`,
          data: diff,
          stack: stackId,
          showSymbol: false,
          smooth: false,
          lineStyle: { opacity: 0 },
          areaStyle: { color: SHADE_COLOR },
          silent: true,
          tooltip: { show: false },
          z: 0,
        },
        // Expiry dot at the expiry: centered in the band (spot ↔ skew
        // midpoint) — marks where this expiry's shade closes without the
        // visual weight of a full-height vertical line. Drawn as a
        // single-point scatter series (markLine/markPoint coord pairs
        // proved unreliable on the stacked shade series).
        {
          type: "scatter" as const,
          name: `expiry-dot ${pe.expiry}`,
          data: [[xEnd, (ySpot + lastSkew) / 2]],
          symbolSize: 7,
          itemStyle: { color: IV_BLUE, borderColor: "#fff", borderWidth: 1 },
          silent: true,
          tooltip: { show: false },
          z: 4,
        },
      );
    });
  }

  const series: EChartsOption["series"] = [
    ...perExpirySeries,
    ...expiryShadeSeries,
    {
      type: "line",
      name: "Underlying Spot",
      showSymbol: false,
      smooth: false,
      lineStyle: { color: FUTURES_BLUE_NEAR, width: 1.5, opacity: 0.9 },
      itemStyle: { color: FUTURES_BLUE_NEAR },
      data: spotData,
      z: 3,
      markPoint:
        selectedIdx >= 0
          ? {
              symbol: "circle",
              symbolSize: 9,
              itemStyle: { color: FUTURES_BLUE_NEAR, borderColor: "#fff", borderWidth: 1 },
              data: [
                {
                  name: "spot",
                  coord: [selectedDate, points[selectedIdx].spot],
                },
              ],
              label: { show: false },
              z: 10,
            }
          : undefined,
    },
    {
      type: "line",
      name: meanSeriesName,
      showSymbol: false,
      smooth: false,
      connectNulls: false,
      lineStyle: { color: IV_BLUE, width: 2.5, type: "dashed" as const, opacity: 0.95 },
      itemStyle: { color: IV_BLUE },
      data: skewData,
      z: 2,
      markPoint: (() => {
        // Evolving array: TS infers the union of pushed item literals.
        const data = [];
        if (selectedIdx >= 0 && points[selectedIdx].skewPrice != null) {
          data.push({
            name: "skew",
            coord: [selectedDate, points[selectedIdx].skewPrice as number],
            symbol: "circle",
            symbolSize: 9,
            itemStyle: { color: IV_BLUE, borderColor: "#fff", borderWidth: 1 },
            label: {
              show: true,
              formatter: `Skew Δ=${
                points[selectedIdx].skewPct != null
                  ? (points[selectedIdx].skewPct as number) >= 0
                    ? "+" + (points[selectedIdx].skewPct as number).toFixed(2) + "%"
                    : (points[selectedIdx].skewPct as number).toFixed(2) + "%"
                  : "—"
              }`,
              color: textColor,
              fontSize: 10,
              fontWeight: 600,
              position: "top",
              distance: 8,
            },
          });
        }
        return data.length > 0 ? { data, z: 10 } : undefined;
      })(),
    },
  ];

  return baseChartOption(mode, {
    grid: commonGrid({ left: 56, right: 56, top: 36, bottom: 36 }),
    title: {
      text: chartTitle,
      left: "left",
      textStyle: { color: textColor, fontSize: 11, fontWeight: 600 },
    },
    tooltip: commonTooltip(mode, {
      axisPointer: {
        type: "cross",
        snap: true,
        lineStyle: { color: textColor, type: "dashed", opacity: 0.5 },
        label: {
          color: textColor,
          fontSize: 9,
          backgroundColor: c.tooltipBg,
          borderColor: c.splitLineColor,
          borderWidth: 1,
          padding: [3, 5],
          formatter: (params: unknown) => {
            const v = (params as { value: string | number }).value;
            return String(v);
          },
        },
      },
      formatter: makeSharedSkewTooltipFormatter(
        points,
        expiryColorMap,
      ),
    }),
    // Legend centered: the top-right corner is reserved for the overlay
    // "Neutral Skew/Moneyness Days" toggle (absolute, top: 0, right: 8) —
    // a right-aligned legend would sit underneath it and the texts overlap.
    legend: commonLegend(mode, {
      top: 14,
      left: "center",
      right: "auto",
      data: ["Underlying Spot", meanSeriesName],
    }),
    xAxis: {
      type: "category",
      data: axisDates,
      name: "Date",
      nameLocation: "middle",
      nameGap: 24,
      nameTextStyle: { color: textColor, fontSize: 9 },
      axisLine: { lineStyle: { color: textColor } },
      axisLabel: { color: textColor, fontSize: 9 },
      splitLine: { show: false },
      boundaryGap: false,
    },
    yAxis: {
      type: "value",
      scale: true,
      name: "Price (yuan)",
      nameLocation: "middle",
      nameGap: 40,
      nameTextStyle: { color: textColor, fontSize: 9 },
      axisLine: { lineStyle: { color: textColor } },
      axisLabel: {
        color: textColor,
        fontSize: 9,
        formatter: (v: number) => fmtNum(v),
      },
      splitLine: {
        lineStyle: { color: splitColor, type: "dashed", opacity: 0.4 },
      },
    },
    dataZoom: commonDataZoom(
      { xAxisIndex: 0 },
      dataZoomStart ?? 0,
      dataZoomEnd ?? 100,
    ),
    series,
  });
}
