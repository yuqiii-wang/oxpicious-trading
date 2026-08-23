/**
 * Unified skew-over-time chart builder — renders a SharedSkewSpec from
 * EITHER data source (oi_moneyness / iv_smile) with the identical layout:
 *
 *   • Underlying spot curve (solid blue) + selected-date mark
 *   • Mean (aggregate) skew curve in price space (thick dashed blue)
 *   • Per-expiry thin dashed blue-gradient lines
 *   • Expiry shade bands from the selected date to each active expiry
 *     (band between spot and that expiry's skew curve)
 *   • Vertical expiry closing lines + neutral-cross-count markPoints
 *
 * Generalized from the OI-wtd moneyness skew chart (oiMoneynessOption.ts).
 */
import { useStore } from "@/store/filters";
import {
  axisColors,
  commonDataZoom,
  commonGrid,
  commonLegend,
  expiryBlueColor,
  FUTURES_BLUE_NEAR,
  FUTURES_EXPIRY_DOT_BORDER,
  IV_BLUE,
} from "@/theme/chart-palette";
import { fmtNum } from "@/lib/series";
import { makeSharedSkewTooltipFormatter } from "./sharedSkewTooltip";
import type { SharedSkewSpec } from "./types";
import type { EChartsOption } from "echarts";

export function buildSharedSkewOption(
  spec: SharedSkewSpec,
  selectedDate: string,
  showCrossCounts: boolean = true,
  dataZoomStart?: number,
  dataZoomEnd?: number,
): EChartsOption {
  const themeMode = useStore.getState().themeMode;
  const c = axisColors(themeMode);
  const textColor = c.textColor;
  const splitColor = c.splitLineColor;

  const { points, chartTitle, meanSeriesName } = spec;

  if (points.length === 0) {
    return {
      backgroundColor: "transparent",
      title: {
        text: `${chartTitle}  [No data]`,
        left: "center",
        top: "center",
        textStyle: { color: textColor, fontSize: 11, fontWeight: 400 },
      },
    };
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
  const activeExpiryDates =
    selectedIdx >= 0
      ? Array.from(
          new Set(
            points[selectedIdx].perExpiry
              .map((pe) => pe.expiryDate)
              .filter((ed) => ed && ed >= selectedDate),
          ),
        ).sort()
      : [];

  // Stretch the x-axis past the data range so every shade reaches its
  // contract's true expiry (e.g. 3/6/9-month sets) instead of being
  // clipped at the last data date. Real data dates stay the axis prefix.
  const lastDate = dates[dates.length - 1];
  const futureExpiryDates = activeExpiryDates.filter((ed) => ed > lastDate);
  const axisDates = [...dates, ...futureExpiryDates];

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
      lineStyle: { color, width: 1, type: "dashed" as const, opacity: 0.45 },
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
  // extent never changes. Each expiry also gets a vertical closing line at
  // its expiry, bounded by the band levels (spot ↔ skew).
  const SHADE_COLOR = "rgba(31, 119, 180, 0.12)";
  const expiryShadeSeries: EChartsOption["series"] = [];
  const crossCountMarkPoints: {
    name: string;
    coord: [string, number];
    value: number;
    itemStyle: { color: string; borderColor: string; borderWidth: number };
    label: Record<string, unknown>;
    symbol: string;
    symbolSize: number;
    tooltip: { show: boolean };
  }[] = [];
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

      // Cross count markPoint at spot price level on the expiry date.
      // Marker shape follows the futures expiry-date dots (see
      // features/futures/chartOption/priceChart.ts): circle with a white
      // ring + two-line label with a white halo — but tinted with THIS
      // expiry's blue-gradient contract color (expiryColorMap) so each
      // mark visually ties to its own per-expiry skew line.
      if (showCrossCounts && pe.countSkewnessCurveCrossedSpot != null && pe.countSkewnessCurveCrossedSpot > 0) {
        const expColor = expiryColorMap.get(pe.expiry) ?? IV_BLUE;
        crossCountMarkPoints.push({
          name: `cross-count-${pe.expiry}`,
          coord: [xEnd, ySpot],
          value: pe.countSkewnessCurveCrossedSpot,
          itemStyle: {
            color: expColor,
            borderColor: FUTURES_EXPIRY_DOT_BORDER,
            borderWidth: 2,
          },
          label: {
            show: true,
            formatter: `${pe.expiryDate}\nCrossed ×${pe.countSkewnessCurveCrossedSpot}`,
            color: expColor,
            fontSize: 10,
            fontWeight: 600,
            lineHeight: 13,
            position: "top",
            distance: 6,
            textBorderColor: FUTURES_EXPIRY_DOT_BORDER,
            textBorderWidth: 2,
          },
          symbol: "circle",
          symbolSize: 10,
          tooltip: { show: false },
        });
      }

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
        // Vertical closing segment at the expiry: bounded by the spot level
        // and this expiry's skewness level (band height at the boundary).
        // Drawn as a 2-point line series (same category twice) — markLine
        // coord pairs proved unreliable on the stacked shade series.
        {
          type: "line" as const,
          name: `expiry-line ${pe.expiry}`,
          data: [
            [xEnd, ySpot],
            [xEnd, lastSkew],
          ],
          showSymbol: false,
          smooth: false,
          lineStyle: { color: IV_BLUE, width: 2, opacity: 1 },
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
        data.push(...crossCountMarkPoints);
        return data.length > 0 ? { data, z: 10 } : undefined;
      })(),
    },
  ];

  return {
    backgroundColor: "transparent",
    animation: false,
    grid: commonGrid({ left: 56, right: 56, top: 36, bottom: 36 }),
    title: {
      text: chartTitle,
      left: "left",
      textStyle: { color: textColor, fontSize: 11, fontWeight: 600 },
    },
    tooltip: {
      trigger: "axis",
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
      backgroundColor: c.tooltipBg,
      borderColor: splitColor,
      textStyle: { color: textColor, fontSize: 11 },
      formatter: makeSharedSkewTooltipFormatter(
        points,
        expiryColorMap,
      ),
    },
    // Legend centered: the top-right corner is reserved for the overlay
    // "Neutral Skew/Moneyness Days" toggle (absolute, top: 0, right: 8) —
    // a right-aligned legend would sit underneath it and the texts overlap.
    legend: commonLegend(themeMode, {
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
  };
}
