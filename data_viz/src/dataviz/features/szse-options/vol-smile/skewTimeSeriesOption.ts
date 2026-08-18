import { useStore } from "@/store/filters";
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
import { makeSkewTooltipFormatter } from "./SkewTooltip";
import { expiryToYyyyMm } from "./expiryUtils";
import type { DailySkew, ExpiryGapsMap } from "./types";
import type { EChartsOption } from "echarts";

export function buildSkewTimeSeriesOption(
  dailySkew: DailySkew[],
  selectedDate: string,
  gapsMap: ExpiryGapsMap | null,
): EChartsOption {
  const themeMode = useStore.getState().themeMode;
  const c = axisColors(themeMode);
  const textColor = c.textColor;
  const splitColor = c.splitLineColor;

  if (dailySkew.length === 0) {
    return {
      backgroundColor: "transparent",
      title: {
        text: "Underlying Price & Smile Skewness Over Time  [No data]",
        left: "center",
        top: "center",
        textStyle: { color: textColor, fontSize: 11, fontWeight: 400 },
      },
    };
  }

  const spotData = dailySkew.map((d) => [d.date, d.S]);
  const skewData = dailySkew.map((d) =>
    d.skewPrice != null ? [d.date, d.skewPrice] : null,
  );

  const dates = dailySkew.map((d) => d.date);
  const selectedIdx = dates.indexOf(selectedDate);

  const allExpiries = new Map<string, string>();
  for (const d of dailySkew) {
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

  // Currently active expiry sets ON the clicked date (perExpiry is built from
  // rows with expiry_date >= date — strictly pre-expiry, mirroring the expiry
  // sets stored in analysis.options_stats_before_expiry). Each set's
  // expiryDate is its contracts' last active date: one shade per set, from
  // the clicked date till that expiry. Overlapping layers make near-term
  // regions darker and far regions lighter.
  const activeExpiryDates =
    selectedIdx >= 0
      ? Array.from(
          new Set(
            dailySkew[selectedIdx].perExpiry
              .map((pe) => pe.expiryDate)
              .filter((ed) => ed && ed >= selectedDate),
          ),
        ).sort()
      : [];

  // Stretch the x-axis past the data range so every shade reaches its
  // contract's true expiry (e.g. 3/6/9-month sets) instead of being clipped
  // at the last data date. Real data dates stay the axis prefix.
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

  // Per-expiry max/min skewPrice (for gap display)
  const expiryGapMap = new Map<string, { max: number; min: number }>();
  if (gapsMap) {
    for (const [, g] of gapsMap) {
      const yyyymm = expiryToYyyyMm(g.expiry_date);
      if (
        g.today_gap_from_max_before_expiry != null &&
        g.today_gap_from_min_before_expiry != null
      ) {
        const cur = expiryGapMap.get(yyyymm);
        const maxVal =
          g.today_gap_from_today_spot != null
            ? g.today_gap_from_today_spot + -g.today_gap_from_max_before_expiry
            : NaN;
        if (!cur) expiryGapMap.set(yyyymm, { max: maxVal, min: maxVal });
      }
    }
  }
  for (const d of dailySkew) {
    for (const pe of d.perExpiry) {
      if (pe.skewPrice == null || !Number.isFinite(pe.skewPrice)) continue;
      const cur = expiryGapMap.get(pe.expiry);
      if (!cur) {
        expiryGapMap.set(pe.expiry, { max: pe.skewPrice, min: pe.skewPrice });
      } else {
        if (pe.skewPrice > cur.max) cur.max = pe.skewPrice;
        if (pe.skewPrice < cur.min) cur.min = pe.skewPrice;
      }
    }
  }

  const perExpirySeries: EChartsOption["series"] = expiryList.map((exp, ei) => {
    const data: (number | null)[] = dailySkew.map((d) => {
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
  if (hasActiveExpiries) {
    const lastD = dailySkew[dailySkew.length - 1];
    const activeSets = dailySkew[selectedIdx].perExpiry
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
          const d = dailySkew[i];
          const sk =
            d.perExpiry.find((p) => p.expiry === pe.expiry)?.skewPrice ?? null;
          if (sk != null) {
            const lower = Math.min(d.S, sk);
            lastLower = lower;
            lastWidth = Math.abs(sk - d.S);
            lastSkew = sk;
            base.push(lower);
            diff.push(lastWidth);
          } else {
            base.push(d.S);
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
      const ySpot = endIdx < dates.length ? dailySkew[endIdx].S : lastD.S;
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
                  coord: [selectedDate, dailySkew[selectedIdx].S],
                },
              ],
              label: { show: false },
              z: 10,
            }
          : undefined,
    },
    {
      type: "line",
      name: "Skewness (OI-wtd)",
      showSymbol: false,
      smooth: false,
      connectNulls: false,
      lineStyle: { color: IV_BLUE, width: 2.5, type: "dashed", opacity: 0.95 },
      itemStyle: { color: IV_BLUE },
      data: skewData,
      z: 2,
      markPoint:
        selectedIdx >= 0 && dailySkew[selectedIdx].skewPrice != null
          ? {
              symbol: "circle",
              symbolSize: 9,
              itemStyle: { color: IV_BLUE, borderColor: "#fff", borderWidth: 1 },
              data: [
                {
                  name: "skew",
                  coord: [selectedDate, dailySkew[selectedIdx].skewPrice as number],
                },
              ],
              label: {
                show: true,
                formatter: `Skew Δ=${
                  dailySkew[selectedIdx].skewPct != null
                    ? (dailySkew[selectedIdx].skewPct >= 0 ? "+" : "") +
                      dailySkew[selectedIdx].skewPct.toFixed(2) +
                      "%"
                    : "—"
                }`,
                color: textColor,
                fontSize: 10,
                fontWeight: 600,
                position: "top",
                distance: 8,
              },
              z: 10,
            }
          : undefined,
    },
  ];

  const visibleLegendData = ["Underlying Spot", "Skewness (OI-wtd)"];

  return {
    backgroundColor: "transparent",
    animation: false,
    grid: commonGrid({ left: 56, right: 56, top: 36, bottom: 36 }),
    title: {
      text: "Underlying Price & Smile Skewness Over Time",
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
      formatter: makeSkewTooltipFormatter(dailySkew, gapsMap, allExpiries, expiryGapMap, expiryColorMap),
    },
    legend: commonLegend(themeMode, {
      top: 14,
      data: visibleLegendData,
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
    dataZoom: commonDataZoom({ xAxisIndex: 0 }, 0, 100),
    series,
  };
}
