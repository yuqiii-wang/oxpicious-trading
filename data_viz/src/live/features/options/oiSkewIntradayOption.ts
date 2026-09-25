/**
 * Intraday OI-skew chart builder — the Live "Options" tab's plot: the
 * dataviz Open Interests tab's "Underlying Price & OI-wtd Moneyness Skew
 * Over Time" layout with the x-axis switched from daily dates to 5-min
 * intraday times, on the SAME frozen full-day tick grid as the Market
 * Movements page (FULL_DAY_TICKS — every 5-min slot of both sessions,
 * nulls where no data yet, labels only at :00/:30). The axis is CONSTANT:
 * the chart always shows the whole trading day at fixed per-tick spacing,
 * whether the data covers one bar or the full session.
 *
 *   • Underlying spot curve (solid) — the day's 5-min closes
 *   • MEAN skew curve in price space (thick dashed, sentinel expiry
 *     9998-12-31) + the last-data-bar "Skew Δ" label
 *   • Per-expiry dashed curves — line WIDTH encodes the expiry's OI
 *     (peak over the day, sqrt-scaled) while the plotted value stays the
 *     ratio-based skew price
 *   • One shade band between spot and the MEAN skew (the daily chart
 *     shades per active expiry; intraday all expiries are active the
 *     whole day, so a single mean band keeps the canvas readable)
 */
import React from "react";
import {
  baseChartOption,
  commonTooltip,
  emptyChartOption,
} from "@/shared/charts/base-chart";
import type { ThemeMode } from "@/store/filters";
import {
  axisColors,
  commonGrid,
  commonLegend,
  expiryBlueColor,
  FUTURES_BLUE_NEAR,
  IV_BLUE,
} from "@/theme/chart-palette";
import { fmtNum, fmtCompact } from "@/lib/series";
import {
  renderReactElement,
  tooltipComponents,
} from "@/lib/react-tooltip-renderer";
import { FULL_DAY_TICKS } from "@/live/features/market-movements/marketMovementsOption";
import { LIVE_OPTIONS_MEAN_EXPIRY } from "@shared/types";
import type { LiveOptionsOiSkewResponse } from "@shared/types";
import type { EChartsOption } from "echarts";

/** Line-width range of the per-expiry OI-thickness encoding (same sqrt
 *  scaling as the daily chart's expiryOiWidths). */
const OI_WIDTH_MIN = 0.7;
const OI_WIDTH_MAX = 3.0;

/** "HH:MM:SS" tick (the Market Movements grid) → "HH:MM" (the keys the
 *  /api/live-options payload's time fields use). */
const hm = (tick: string): string => tick.slice(0, 5);

/** Shaped response data the option builder (and its tooltip) consumes. */
export interface OiSkewIntradayData {
  underlying: string;
  date: string;
  snapshotDate: string | null;
  /** Spot unit label — "yuan" for ETF targets, "points" for index targets. */
  unit: string;
  /** Sorted 5-min bar times (HH:MM). */
  times: string[];
  spotByTime: Map<string, number>;
  meanPriceByTime: Map<string, number>;
  meanPctByTime: Map<string, number>;
  /** Real expiry dates (the MEAN sentinel excluded), sorted. */
  expiries: string[];
  rowsByTimeExpiry: Map<
    string,
    { price: number | null; pct: number | null; oiTotal: number | null }
  >;
  oiWidthByExpiry: Map<string, number>;
}

function shapeResponse(
  resp: LiveOptionsOiSkewResponse,
  unit: string,
): OiSkewIntradayData {
  const times = Array.from(new Set(resp.bars.map((b) => b.time))).sort();
  const spotByTime = new Map<string, number>();
  for (const b of resp.bars) spotByTime.set(b.time, b.close);

  const meanPriceByTime = new Map<string, number>();
  const meanPctByTime = new Map<string, number>();
  const expiries = new Set<string>();
  const rowsByTimeExpiry = new Map<
    string,
    { price: number | null; pct: number | null; oiTotal: number | null }
  >();

  for (const p of resp.series) {
    if (p.expiry_date === LIVE_OPTIONS_MEAN_EXPIRY) {
      if (p.skew_price != null) meanPriceByTime.set(p.time, p.skew_price);
      if (p.skew_pct != null) meanPctByTime.set(p.time, p.skew_pct);
    } else {
      expiries.add(p.expiry_date);
      rowsByTimeExpiry.set(`${p.time}|${p.expiry_date}`, {
        price: p.skew_price,
        pct: p.skew_pct,
        oiTotal: p.oi_total,
      });
    }
  }

  // Per-expiry line width ∝ peak OI over the day (sqrt-scaled).
  const peak = new Map<string, number>();
  for (const [key, row] of rowsByTimeExpiry) {
    if (row.oiTotal == null || !Number.isFinite(row.oiTotal)) continue;
    const exp = key.split("|")[1] ?? "";
    if (row.oiTotal > (peak.get(exp) ?? 0)) peak.set(exp, row.oiTotal);
  }
  let maxOi = 0;
  for (const v of peak.values()) if (v > maxOi) maxOi = v;
  const oiWidthByExpiry = new Map<string, number>();
  for (const [exp, v] of peak) {
    oiWidthByExpiry.set(
      exp,
      maxOi > 0
        ? OI_WIDTH_MIN + (OI_WIDTH_MAX - OI_WIDTH_MIN) * Math.sqrt(Math.max(0, v) / maxOi)
        : 1,
    );
  }

  return {
    underlying: resp.underlying_code,
    date: resp.date,
    snapshotDate: resp.snapshot_date,
    unit,
    times,
    spotByTime,
    meanPriceByTime,
    meanPctByTime,
    expiries: Array.from(expiries).sort(),
    rowsByTimeExpiry,
    oiWidthByExpiry,
  };
}

function makeTooltipFormatter(
  data: OiSkewIntradayData,
  expiryColorMap: Map<string, string>,
): (params: unknown) => string {
  return (p: unknown): string => {
    const items = (Array.isArray(p) ? p : [p]) as Array<{
      axisValue?: string | number;
    }>;
    // Axis categories are the FULL_DAY_TICKS "HH:MM:SS" strings; the
    // payload lookups key on "HH:MM".
    const tick = String(items[0]?.axisValue ?? "");
    if (!tick) return "";
    const time = hm(tick);

    const makeHeader = (text: string) =>
      React.createElement(tooltipComponents.Header, null, text);
    const makeRow = (children: React.ReactNode, style?: React.CSSProperties) =>
      React.createElement(tooltipComponents.Row, { style }, children);

    const children: React.ReactNode[] = [makeHeader(`${data.date} · ${hm(tick)}`)];

    const spot = data.spotByTime.get(time);
    if (spot != null) {
      children.push(
        makeRow(["Spot: ", React.createElement(tooltipComponents.Bold, null, fmtNum(spot))]),
      );
    }
    const meanPct = data.meanPctByTime.get(time);
    const meanPrice = data.meanPriceByTime.get(time);
    if (meanPrice != null) {
      children.push(
        makeRow([
          "Skew (mean): ",
          React.createElement(tooltipComponents.Bold, null, fmtNum(meanPrice)),
          meanPct != null
            ? ` (${meanPct >= 0 ? "+" : ""}${fmtNum(meanPct, 2)}%)`
            : "",
        ]),
      );
    }

    if (data.expiries.length > 0) {
      children.push(
        makeRow(
          ["Per-expiry Δ% · OI"],
          { opacity: 0.7, marginTop: 2 },
        ),
      );
      for (const exp of data.expiries) {
        const row = data.rowsByTimeExpiry.get(`${time}|${exp}`);
        if (!row || row.price == null) continue;
        const pct =
          row.pct != null
            ? `${row.pct >= 0 ? "+" : ""}${fmtNum(row.pct, 2)}%`
            : "—";
        const oi =
          row.oiTotal != null ? ` · OI ${fmtCompact(row.oiTotal)}` : "";
        children.push(
          makeRow([
            React.createElement("span", { key: "d", style: { color: expiryColorMap.get(exp) ?? "#888" } }, "● "),
            `${exp.slice(0, 7)}: `,
            React.createElement(tooltipComponents.Bold, null, pct),
            oi,
          ]),
        );
      }
    }

    if (data.snapshotDate) {
      children.push(
        makeRow([`OI base: ${data.snapshotDate} daily snapshot + day volume`], {
          opacity: 0.6,
          marginTop: 2,
        }),
      );
    }
    return renderReactElement(React.createElement(React.Fragment, null, children));
  };
}

export function buildOiSkewIntradayOption(
  resp: LiveOptionsOiSkewResponse | null,
  mode: ThemeMode,
  unit: string,
): EChartsOption {
  const c = axisColors(mode);
  const textColor = c.textColor;

  const data = resp ? shapeResponse(resp, unit) : null;
  if (!data || data.times.length === 0) {
    return emptyChartOption(mode, "OI-weighted Moneyness Skew · Intraday  [No data]");
  }

  const expiryColorMap = new Map<string, string>();
  data.expiries.forEach((exp, ei) => {
    expiryColorMap.set(exp, expiryBlueColor(ei, data.expiries.length));
  });

  // Frozen x-axis: the Market Movements full-day tick grid. Every series
  // maps onto it with null at slots without data (future ticks during the
  // live session, or bars the stream hasn't produced) — the chart always
  // shows the WHOLE trading day at constant 5-min spacing.
  const times = FULL_DAY_TICKS;
  const byTick = (t: string): string => hm(t);

  // Per-expiry dashed curves (value = skew price; width ∝ OI).
  const perExpirySeries: EChartsOption["series"] = data.expiries.map((exp) => {
    const color = expiryColorMap.get(exp) ?? IV_BLUE;
    return {
      type: "line" as const,
      name: `Skew ${exp.slice(0, 7)}`,
      showSymbol: false,
      smooth: false,
      connectNulls: false,
      lineStyle: {
        color,
        width: data.oiWidthByExpiry.get(exp) ?? 1,
        type: "dashed" as const,
        opacity: 0.45,
      },
      itemStyle: { color },
      data: times.map((t) => {
        const row = data.rowsByTimeExpiry.get(`${byTick(t)}|${exp}`);
        return row && row.price != null ? row.price : null;
      }),
      z: 1,
      tooltip: { show: false },
    };
  });

  // Single shade band between spot and the MEAN skew (stacked base+width
  // pair, same trick as the daily chart — width always ≥ 0).
  const shadeBase: (number | null)[] = [];
  const shadeWidth: (number | null)[] = [];
  for (const t of times) {
    const key = byTick(t);
    const spot = data.spotByTime.get(key);
    const mean = data.meanPriceByTime.get(key);
    if (spot != null && mean != null) {
      shadeBase.push(Math.min(spot, mean));
      shadeWidth.push(Math.abs(mean - spot));
    } else {
      shadeBase.push(null);
      shadeWidth.push(null);
    }
  }
  const shadeSeries: EChartsOption["series"] = [
    {
      type: "line" as const,
      name: "shade-base",
      data: shadeBase,
      stack: "mean-shade",
      showSymbol: false,
      smooth: false,
      lineStyle: { opacity: 0 },
      silent: true,
      tooltip: { show: false },
      z: 0,
    },
    {
      type: "line" as const,
      name: "shade",
      data: shadeWidth,
      stack: "mean-shade",
      showSymbol: false,
      smooth: false,
      lineStyle: { opacity: 0 },
      areaStyle: { color: "rgba(31, 119, 180, 0.10)" },
      silent: true,
      tooltip: { show: false },
      z: 0,
    },
  ];

  const spotData = times.map((t) => data.spotByTime.get(byTick(t)) ?? null);
  const meanData = times.map((t) => data.meanPriceByTime.get(byTick(t)) ?? null);

  // Last grid tick carrying data — the "Skew Δ" label anchors there (the
  // latest bar of the live session, not the 15:30 axis end).
  let lastDataIdx = -1;
  for (let i = times.length - 1; i >= 0; i--) {
    if (spotData[i] != null || meanData[i] != null) {
      lastDataIdx = i;
      break;
    }
  }
  const lastTick = lastDataIdx >= 0 ? times[lastDataIdx] ?? "" : "";
  const lastMeanPct =
    lastTick !== "" ? data.meanPctByTime.get(byTick(lastTick)) ?? null : null;
  const lastMeanPrice =
    lastTick !== "" ? data.meanPriceByTime.get(byTick(lastTick)) ?? null : null;

  const series: EChartsOption["series"] = [
    ...shadeSeries,
    ...perExpirySeries,
    {
      type: "line",
      name: "Underlying Spot",
      showSymbol: false,
      smooth: false,
      lineStyle: { color: FUTURES_BLUE_NEAR, width: 1.5, opacity: 0.9 },
      itemStyle: { color: FUTURES_BLUE_NEAR },
      data: spotData,
      z: 3,
    },
    {
      type: "line",
      name: "Skew (OI-wtd mean)",
      showSymbol: false,
      smooth: false,
      connectNulls: false,
      lineStyle: { color: IV_BLUE, width: 2.5, type: "dashed" as const, opacity: 0.95 },
      itemStyle: { color: IV_BLUE },
      data: meanData,
      z: 2,
      markPoint:
        lastTick !== "" && lastMeanPct != null && lastMeanPrice != null
          ? {
              symbol: "circle",
              symbolSize: 9,
              itemStyle: { color: IV_BLUE, borderColor: "#fff", borderWidth: 1 },
              label: {
                show: true,
                formatter: `Skew Δ=${lastMeanPct >= 0 ? "+" : ""}${lastMeanPct.toFixed(2)}%`,
                color: textColor,
                fontSize: 10,
                fontWeight: 600,
                position: "top",
                distance: 8,
              },
              data: [{ name: "skew", coord: [lastTick, lastMeanPrice] }],
              z: 10,
            }
          : undefined,
    },
  ];

  return baseChartOption(mode, {
    grid: commonGrid({ left: 56, right: 56, top: 36, bottom: 36 }),
    tooltip: commonTooltip(mode, {
      formatter: makeTooltipFormatter(data, expiryColorMap),
    }),
    legend: commonLegend(mode, {
      top: 0,
      left: "center",
      data: ["Underlying Spot", "Skew (OI-wtd mean)"],
    }),
    xAxis: {
      type: "category",
      data: times,
      name: "Time",
      nameLocation: "middle",
      nameGap: 24,
      nameTextStyle: { color: textColor, fontSize: 9 },
      axisLine: { lineStyle: { color: textColor } },
      axisLabel: {
        color: textColor,
        fontSize: 10,
        // A label every 30 min (at :00 and :30 minutes) — the Market
        // Movements convention on the same frozen grid. The grid is one
        // continuous 09:30–15:30 timeline (real lunch slots), so the
        // cadence flows straight through noon: 11:30, 12:00, 12:30, 13:00.
        interval: (_idx: number, val: string) => {
          const mm = val.slice(3, 5);
          return mm === "00" || mm === "30";
        },
      },
      splitLine: { show: false },
    },
    yAxis: {
      type: "value",
      scale: true,
      name: `Price (${data.unit})`,
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
        lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.4 },
      },
    },
    series,
  });
}
