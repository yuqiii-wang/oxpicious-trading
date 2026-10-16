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
  ATM_GRAY,
  axisColors,
  commonDataZoom,
  commonGrid,
  commonLegend,
  DOWN_COLOR,
  expiryBlueColor,
  FUTURES_BLUE_NEAR,
  IV_BLUE,
  NEUTRAL_FILL,
  TRIGGER_DATE_COLOR,
  TRIGGER_DATE_FILL,
  TRIGGER_STREAK_FILL,
  UP_COLOR,
} from "@/theme/chart-palette";
import { fmtNum } from "@/lib/series";
import {
  rejectionMarkerSpec,
  signalMarkerSpec,
} from "@/shared/charts/signalMarker";
import {
  CALL_ZONE_SERIES_NAME,
  PUT_ZONE_SERIES_NAME,
} from "../options-trend/bandData";
import { makeSharedSkewTooltipFormatter } from "./sharedSkewTooltip";
import type {
  SharedBandEvent,
  SharedCorridorPoint,
  SharedSkewHighlights,
  SharedSkewSignalEvent,
  SharedSkewSpec,
  SharedWallZone,
  SharedWallZones,
} from "./types";
import type { EChartsOption, SeriesOption } from "echarts";

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

/** Series names of the corridor-event marker classes (legend order). */
export const BAND_EVENT_SERIES_NAMES = [
  "Corridor Exit Low",
  "Corridor Exit High",
  "Corridor Re-enter",
] as const;

/** Series names of the SIGNAL-history marker classes (the options page's
 *  in-signal marker mode; legend order). */
export const SIGNAL_MARKER_SERIES_NAMES = [
  "Signal Buy",
  "Signal Sell",
  "Signal No-Action",
] as const;

/**
 * SIGNAL-history scatter markers on the spot curve (the options page's
 * IN-SIGNAL marker mode — analysis_signals.history_signals, the
 * options_wall family, fetched with signal_type=options_wall): the
 * actionable events draw THE shared buy/sell triangle (buy below the
 * line, sell above), and a REJECTED event (is_rejection — the
 * strategy gate's explicit no-action record, e.g. the VRP-declined
 * put-wall breach) draws the GREY DIAMOND on the line instead — an
 * inaction must never read as a trade. One scatter series per class
 * (only classes with >=1 event are emitted) so the legend toggles
 * classes individually.
 */
const SIGNAL_NO_ACTION_SPEC = rejectionMarkerSpec();

function signalEventSeries(
  events: readonly SharedSkewSignalEvent[],
  spotByDate: Map<string, number>,
): SeriesOption[] {
  const classes: Array<{
    key: "buy" | "sell" | "rej";
    name: string;
    symbol: string;
    symbolSize: [number, number];
    symbolOffset: [number, number];
    color: string;
  }> = [
    {
      key: "buy",
      name: SIGNAL_MARKER_SERIES_NAMES[0],
      symbol: SIGNAL_BUY_SPEC.symbol,
      symbolSize: SIGNAL_BUY_SPEC.symbolSize,
      symbolOffset: SIGNAL_BUY_SPEC.symbolOffset,
      color: SIGNAL_BUY_SPEC.itemStyle.color,
    },
    {
      key: "sell",
      name: SIGNAL_MARKER_SERIES_NAMES[1],
      symbol: SIGNAL_SELL_SPEC.symbol,
      symbolSize: SIGNAL_SELL_SPEC.symbolSize,
      symbolOffset: SIGNAL_SELL_SPEC.symbolOffset,
      color: SIGNAL_SELL_SPEC.itemStyle.color,
    },
    {
      key: "rej",
      name: SIGNAL_MARKER_SERIES_NAMES[2],
      symbol: SIGNAL_NO_ACTION_SPEC.symbol,
      symbolSize: SIGNAL_NO_ACTION_SPEC.symbolSize,
      symbolOffset: SIGNAL_NO_ACTION_SPEC.symbolOffset,
      color: SIGNAL_NO_ACTION_SPEC.itemStyle.color,
    },
  ];
  const out: SeriesOption[] = [];
  for (const cls of classes) {
    const data: Array<[string, number]> = [];
    for (const ev of events) {
      const rej = ev.is_rejection === true;
      const isBuy = !rej && ev.action === "buy";
      const isSell = !rej && ev.action === "sell";
      const match = cls.key === "rej" ? rej
        : cls.key === "buy" ? isBuy : isSell;
      if (!match) continue;
      const y = spotByDate.get(ev.date);
      if (y == null || !Number.isFinite(y)) continue;
      data.push([ev.date, y]);
    }
    if (data.length === 0) continue;
    out.push({
      type: "scatter" as const,
      name: cls.name,
      data,
      symbol: cls.symbol,
      symbolSize: cls.symbolSize,
      symbolOffset: cls.symbolOffset,
      itemStyle: { color: cls.color, borderColor: "#fff", borderWidth: 1 },
      z: 5,
      tooltip: { show: false },
    });
  }
  return out;
}

/**
 * Corridor-event scatter markers on the spot curve (analysis.options_bands
 * v2): fresh crosses of the OI-mass corridor vs the prior session's
 * corridor. EXIT_LOW below the price line (green up-triangle — the
 * validated snap-back-up side: close <= P10 = 90% up next 5 sessions,
 * mean +4.5%), EXIT_HIGH above it (red down-triangle — 89% down at the
 * P90 edge), REENTER on the line (neutral diamond). One scatter series
 * per class (only classes with >=1 event are emitted), so the legend can
 * also toggle classes individually. The two exit classes draw THE shared
 * buy/sell triangle (signalMarker.ts) — the app-wide trade-signal look.
 *
 * AMPLIFIED markers (ev.sig): events whose side was crossed while the
 * exchange had just extended the strike grid the same way (>= 2 new
 * strikes in the trailing 40 sessions — e.g. 000300 2025-08-28: EXIT_HIGH
 * with 6 fresh higher strikes) override per-point symbolSize + a colored
 * glow — same class/legend entry, visually "more significant".
 */
const EXIT_LOW_SPEC = signalMarkerSpec("buy");
const EXIT_HIGH_SPEC = signalMarkerSpec("sell");
const SIGNAL_BUY_SPEC = signalMarkerSpec("buy");
const SIGNAL_SELL_SPEC = signalMarkerSpec("sell");

function bandEventSeries(
  events: readonly SharedBandEvent[],
  spotByDate: Map<string, number>,
): SeriesOption[] {
  const classes: Array<{
    kind: "EXIT_LOW" | "EXIT_HIGH" | "REENTER";
    name: string;
    symbol: string;
    symbolSize: [number, number];
    symbolOffset: [number, number];
    color: string;
  }> = [
    {
      kind: "EXIT_LOW",
      name: "Corridor Exit Low",
      symbol: EXIT_LOW_SPEC.symbol,
      symbolSize: EXIT_LOW_SPEC.symbolSize,
      symbolOffset: EXIT_LOW_SPEC.symbolOffset,
      color: EXIT_LOW_SPEC.itemStyle.color,
    },
    {
      kind: "EXIT_HIGH",
      name: "Corridor Exit High",
      symbol: EXIT_HIGH_SPEC.symbol,
      symbolSize: EXIT_HIGH_SPEC.symbolSize,
      symbolOffset: EXIT_HIGH_SPEC.symbolOffset,
      color: EXIT_HIGH_SPEC.itemStyle.color,
    },
    {
      kind: "REENTER",
      name: "Corridor Re-enter",
      symbol: "diamond",
      symbolSize: [9, 9],
      symbolOffset: [0, 0],
      color: ATM_GRAY,
    },
  ];
  const out: SeriesOption[] = [];
  for (const cls of classes) {
    const data: Array<[string, number] | {
      value: [string, number];
      symbolSize: [number, number];
      itemStyle: { shadowBlur: number; shadowColor: string };
    }> = [];
    for (const ev of events) {
      if (ev.kind !== cls.kind) continue;
      const y = spotByDate.get(ev.date);
      if (y == null || !Number.isFinite(y)) continue;
      // per-point override: bigger + glowing (series-level style stays
      // the default for non-amplified events)
      data.push(
        ev.sig
          ? {
              value: [ev.date, y],
              symbolSize: [cls.symbolSize[0] * 1.7, cls.symbolSize[1] * 1.7],
              itemStyle: {
                shadowBlur: 9,
                shadowColor: cls.color,
              },
            }
          : [ev.date, y],
      );
    }
    if (data.length === 0) continue;
    out.push({
      type: "scatter" as const,
      name: cls.name,
      data,
      symbol: cls.symbol,
      symbolSize: cls.symbolSize,
      symbolOffset: cls.symbolOffset,
      itemStyle: { color: cls.color, borderColor: "#fff", borderWidth: 1 },
      z: 5,
      tooltip: { show: false },
    });
  }
  return out;
}

/**
 * OI-mass corridor shade (analysis.options_bands v2): the bounded region
 * between the OI-CDF P10 and P90 strikes, shaded as the same
 * stacked-pair pattern the expiry shades use (base = corridor low,
 * width = high - low >= 0) — translucent NEUTRAL gray so it reads
 * distinctly from the blue expiry shades and the green/red event
 * markers. STEP rendering: the bounds are daily levels, and steps
 * avoid steep diagonal fill artifacts when the corridor jumps
 * between expiry mixes day-to-day. Dates missing from the category
 * axis are skipped.
 */
function corridorSeries(
  corridor: readonly SharedCorridorPoint[],
  axisDates: string[],
): SeriesOption[] {
  const byDate = new Map<string, SharedCorridorPoint>();
  for (const z of corridor) byDate.set(z.date, z);

  const base: (number | null)[] = [];
  const diff: (number | null)[] = [];
  let count = 0;
  for (const d of axisDates) {
    const z = byDate.get(d);
    if (z && z.low != null && z.high != null
        && Number.isFinite(z.low) && Number.isFinite(z.high)) {
      base.push(z.low);
      diff.push(Math.max(0, z.high - z.low));
      count++;
    } else {
      base.push(null);
      diff.push(null);
    }
  }
  if (count === 0) return [];
  return [
    {
      type: "line",
      name: "corridor-base",
      data: base,
      stack: "oi-corridor",
      showSymbol: false,
      smooth: false,
      step: "end",
      connectNulls: true,
      lineStyle: { opacity: 0 },
      silent: true,
      tooltip: { show: false },
      z: 0,
    },
    {
      type: "line",
      name: "OI Corridor",
      data: diff,
      stack: "oi-corridor",
      showSymbol: false,
      smooth: false,
      step: "end",
      connectNulls: true,
      lineStyle: { opacity: 0 },
      areaStyle: { color: NEUTRAL_FILL, opacity: 0.16 },
      silent: true,
      tooltip: { show: false },
      z: 0,
    },
  ];
}

/**
 * Wall/zone light shades (analysis.options_walls, wall_type='zone') — the
 * same per-side wall overlay the Options Trend OI Bands chart draws, in a
 * lighter key: one chain-wide dominant zone per (date, side) — CALL green
 * above / PUT red below — rendered as a translucent low→high band with
 * low/high edge rails and the OI-weighted center strike line (dashed when
 * BREACHED, same breach language as Options Trend). Fill opacity runs at
 * roughly half the Options Trend values because this chart already layers
 * the blue expiry shades and the gray OI corridor behind it. Steps per
 * date (the walls are daily levels); dates without a wall are skipped.
 * Details (mass share / state) live in the tooltip via wallZonesByDate —
 * the custom series itself stays tooltip-silent.
 */
const WALL_ZONE_FILL_OPACITY: Record<string, number> = {
  BREACHED: 0.04,
  ERODED: 0.08,
  ACTIVE: 0.13,
};

function buildWallZoneRenderItem(
  points: (SharedWallZone | null)[],
  color: string,
  emphasis: "emph" | "dim" | "none" = "none",
) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return (params: any, api: any) => {
    const p = points[params.dataIndex as number];
    if (!p) return null;
    const coordC = api.coord([params.dataIndex, p.center]) as number[];
    const yLow = (api.coord([params.dataIndex, p.low]) as number[])[1];
    const yHigh = (api.coord([params.dataIndex, p.high]) as number[])[1];
    const cx = coordC[0];
    const cy = coordC[1];
    if (!Number.isFinite(cx) || !Number.isFinite(yLow) || !Number.isFinite(yHigh)) return null;
    const step = api.size ? (api.size([1, 0]) as number[]) : null;
    const half = step && step[0] > 0 ? Math.max(step[0] / 2 - 1, 2) : 4;
    const breached = p.state === "BREACHED";
    const lineOpacity = breached ? 0.3 : 0.5;
    const lineDash = breached ? [3, 3] : undefined;
    // the config-click emphasis (the Options Signals panel's row
    // clicks): the selected config's own wall side draws BOLD (fill +
    // line widths amplified, a soft glow), the opposite side dims to a
    // whisper — the breach bar's wall stands out of the layer
    const emphK = emphasis === "emph" ? 2.2 : emphasis === "dim" ? 0.3 : 1;
    const fillOpacity = Math.min(
      (p.state ? WALL_ZONE_FILL_OPACITY[p.state]
        : WALL_ZONE_FILL_OPACITY.ACTIVE) * emphK,
      0.85,
    );
    const glow = emphasis === "emph"
      ? { shadowBlur: 6, shadowColor: color }
      : {};
    return {
      type: "group" as const,
      children: [
        {
          type: "rect" as const,
          shape: {
            x: cx - half,
            y: yHigh,
            width: half * 2,
            height: Math.max(yLow - yHigh, 1),
          },
          style: {
            fill: color,
            opacity: fillOpacity,
            ...glow,
          },
        },
        {
          type: "line" as const,
          shape: { x1: cx - half, y1: yHigh, x2: cx + half, y2: yHigh },
          style: { stroke: color, lineWidth: 1 * emphK, opacity: lineOpacity * 0.55 * emphK, lineDash, ...glow },
        },
        {
          type: "line" as const,
          shape: { x1: cx - half, y1: yLow, x2: cx + half, y2: yLow },
          style: { stroke: color, lineWidth: 1 * emphK, opacity: lineOpacity * 0.55 * emphK, lineDash, ...glow },
        },
        {
          type: "line" as const,
          shape: { x1: cx - half, y1: cy, x2: cx + half, y2: cy },
          style: { stroke: color, lineWidth: 1.4 * emphK, opacity: lineOpacity * emphK, lineDash, ...glow },
        },
      ],
    };
  };
}

/** Wall/zone custom series per side (only sides with >=1 wall emitted). */
function wallZoneSeries(
  wallZones: SharedWallZones,
  axisDates: string[],
  emphasize?: "call" | "put" | null,
): SeriesOption[] {
  const sides: Array<{ key: "call" | "put"; name: string; color: string; byDate: Map<string, SharedWallZone> }> = [
    { key: "call", name: CALL_ZONE_SERIES_NAME, color: UP_COLOR, byDate: wallZones.call },
    { key: "put", name: PUT_ZONE_SERIES_NAME, color: DOWN_COLOR, byDate: wallZones.put },
  ];
  const out: SeriesOption[] = [];
  for (const side of sides) {
    if (side.byDate.size === 0) continue;
    const points: (SharedWallZone | null)[] = axisDates.map(
      (d) => side.byDate.get(d) ?? null,
    );
    if (!points.some(Boolean)) continue;
    const emphasis = emphasize == null
      ? "none"
      : emphasize === side.key ? "emph" : "dim";
    out.push({
      type: "custom",
      name: side.name,
      // series-level color only drives the legend swatch — the custom
      // renderItem styles its own shapes
      color: side.color,
      renderItem: buildWallZoneRenderItem(points, side.color, emphasis),
      data: points.map((p, xi) => (p ? { value: [xi, p.center] } : null)),
      encode: { x: 0, y: 1 },
      clip: true,
      progressive: 0,
      silent: true,
      tooltip: { show: false },
      z: 1,
    });
  }
  return out;
}

/**
 * Forecast trigger-day highlight overlay (the Options Bands signal
 * page's forecast row clicks — the same visual language the Recent
 * Movements trend chart's overlay uses): a purple pinpoint scatter on
 * the spot curve at each trigger day, plus full-height markArea bands
 * on the spot series — DARK purple over each merged signal's qualifying
 * streak period, LIGHT purple over each trigger day's forward forecast
 * window (horizonDays axis rows, clamped to the axis tail; overlapping
 * windows of one class union-merge so overlapping bands shade
 * uniformly). Dates missing from the category axis are skipped.
 */
function highlightOverlay(
  highlights: SharedSkewHighlights,
  axisDates: string[],
  spotByDate: Map<string, number>,
): { pinpoints: SeriesOption[]; markArea: object | undefined } {
  const idxOf = new Map<string, number>();
  axisDates.forEach((d, i) => idxOf.set(d, i));
  const last = axisDates.length - 1;

  const pinData: Array<[string, number]> = [];
  const fwd: Array<[number, number]> = [];
  for (const d of highlights.dates) {
    const y = spotByDate.get(d);
    const i = idxOf.get(d);
    if (y != null && Number.isFinite(y)) pinData.push([d, y]);
    if (i != null) {
      fwd.push([i, Math.min(i + Math.max(0, highlights.horizonDays), last)]);
    }
  }
  const dark: Array<[number, number]> = [];
  for (const s of highlights.spans) {
    const a = idxOf.get(s.start);
    const b = idxOf.get(s.end);
    if (a != null && b != null) dark.push([Math.min(a, b), Math.max(a, b)]);
  }

  const merge = (spans: Array<[number, number]>): Array<[number, number]> => {
    const sorted = [...spans].sort((x, y) => x[0] - y[0] || x[1] - y[1]);
    const merged: Array<[number, number]> = [];
    for (const s of sorted) {
      const tail = merged[merged.length - 1];
      if (tail && s[0] <= tail[1] + 1) {
        if (s[1] > tail[1]) tail[1] = s[1];
      } else {
        merged.push([s[0], s[1]]);
      }
    }
    return merged;
  };
  const mergedFwd = merge(fwd);
  const mergedDark = merge(dark);

  const markArea =
    mergedFwd.length + mergedDark.length > 0
      ? {
          silent: true,
          data: [
            ...mergedFwd.map(
              ([a, b]) => [
                { xAxis: axisDates[a], itemStyle: { color: TRIGGER_DATE_FILL } },
                { xAxis: axisDates[b] },
              ],
            ),
            ...mergedDark.map(
              ([a, b]) => [
                { xAxis: axisDates[a], itemStyle: { color: TRIGGER_STREAK_FILL } },
                { xAxis: axisDates[b] },
              ],
            ),
          ],
        }
      : undefined;
  const pinpoints: SeriesOption[] =
    pinData.length > 0
      ? [{
          type: "scatter" as const,
          name: "Trigger days",
          data: pinData,
          symbol: "circle",
          symbolSize: 9,
          itemStyle: {
            color: TRIGGER_DATE_COLOR,
            borderColor: "#fff",
            borderWidth: 1,
          },
          z: 6,
          tooltip: { show: false },
        }]
      : [];
  return { pinpoints, markArea };
}

export function buildSharedSkewOption(
  spec: SharedSkewSpec,
  mode: ThemeMode,
  selectedDate: string,
  dataZoomStart?: number,
  dataZoomEnd?: number,
  bandEvents?: readonly SharedBandEvent[],
  corridor?: readonly SharedCorridorPoint[],
  wallZones?: SharedWallZones,
  highlights?: SharedSkewHighlights,
  signalEvents?: readonly SharedSkewSignalEvent[],
  emphasizedWallSide?: "call" | "put" | null,
): EChartsOption {
  const c = axisColors(mode);
  const textColor = c.textColor;
  const splitColor = c.splitLineColor;

  const { points, chartTitle, meanSeriesName } = spec;

  if (points.length === 0) {
    return emptyChartOption(mode, `${chartTitle}  [No data]`);
  }

  // null spot → null value → line gap (connectNulls is false by default):
  // dates whose underlying close is missing (loader 0 = "no spot") are
  // skipped, never pinned to the 0 axis.
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
          if (sk != null && d.spot != null) {
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
      );
      // Expiry dot at the expiry: centered in the band (spot ↔ skew
      // midpoint) — marks where this expiry's shade closes without the
      // visual weight of a full-height vertical line. Drawn as a
      // single-point scatter series (markLine/markPoint coord pairs
      // proved unreliable on the stacked shade series). Skipped when the
      // boundary date has no spot (loader 0 = missing).
      if (ySpot != null) {
        expiryShadeSeries.push({
          type: "scatter" as const,
          name: `expiry-dot ${pe.expiry}`,
          data: [[xEnd, (ySpot + lastSkew) / 2]],
          symbolSize: 7,
          itemStyle: { color: IV_BLUE, borderColor: "#fff", borderWidth: 1 },
          silent: true,
          tooltip: { show: false },
          z: 4,
        });
      }
    });
  }

  // Band-event markers (options_bands): joined to the spot curve by
  // date; events on dates missing from the axis/spot are skipped.
  const spotByDate = new Map<string, number>();
  for (const d of points) {
    if (d.spot != null && Number.isFinite(d.spot)) {
      spotByDate.set(d.date, d.spot);
    }
  }
  // the MARKER layer's two modes (the options page's toggle): in-signal
  // (signalEvents — the signal history) replaces the corridor-event
  // markers; both never draw together
  const markerSeries = signalEvents
    ? signalEventSeries(signalEvents, spotByDate)
    : bandEvents
      ? bandEventSeries(bandEvents, spotByDate)
      : [];
  const zoneSeries =
    corridor && axisDates.length > 0 ? corridorSeries(corridor, axisDates) : [];
  const wallSeries = wallZones
    ? wallZoneSeries(wallZones, axisDates, emphasizedWallSide ?? null)
    : [];
  // Forecast trigger-day overlay (Options Bands signal page row clicks):
  // pinpoints scatter + the spot series' full-height markArea bands.
  const hl = highlights
    ? highlightOverlay(highlights, axisDates, spotByDate)
    : { pinpoints: [] as SeriesOption[], markArea: undefined };
  const bandEventsByDate = new Map<string, SharedBandEvent[]>();
  if (bandEvents) {
    for (const ev of bandEvents) {
      if (!spotByDate.has(ev.date)) continue;
      const list = bandEventsByDate.get(ev.date);
      if (list) list.push(ev);
      else bandEventsByDate.set(ev.date, [ev]);
    }
  }

  const series: EChartsOption["series"] = [
    ...zoneSeries,
    ...wallSeries,
    ...perExpirySeries,
    ...expiryShadeSeries,
    ...markerSeries,
    ...hl.pinpoints,
    {
      type: "line",
      name: "Underlying Spot",
      showSymbol: false,
      smooth: false,
      lineStyle: { color: FUTURES_BLUE_NEAR, width: 1.5, opacity: 0.9 },
      itemStyle: { color: FUTURES_BLUE_NEAR },
      data: spotData,
      z: 3,
      // Forecast trigger-day bands (light forward windows + dark streak
      // periods) ride the spot series' markArea — full-height, silent.
      markArea: hl.markArea,
      markPoint:
        selectedIdx >= 0 && points[selectedIdx].spot != null
          ? {
              symbol: "circle",
              symbolSize: 9,
              itemStyle: { color: FUTURES_BLUE_NEAR, borderColor: "#fff", borderWidth: 1 },
              data: [
                {
                  name: "spot",
                  coord: [selectedDate, points[selectedIdx].spot as number],
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
        bandEventsByDate.size > 0 ? bandEventsByDate : undefined,
        wallZones,
      ),
    }),
    // Legend centered: the top-right corner is reserved for the overlay
    // band-events Show/Hide toggle (absolute, top: 0, right: 8) — a
    // right-aligned legend would sit underneath it and the texts overlap.
    // Band zone shades + event marker classes join the legend when shown
    // (clickable per-class toggles for free).
    legend: commonLegend(mode, {
      top: 14,
      left: "center",
      right: "auto",
      data: [
        "Underlying Spot",
        meanSeriesName,
        ...zoneSeries
          .filter((s) => (s as { name?: string }).name !== "corridor-base")
          .map((s) => (s as { name: string }).name),
        ...wallSeries.map((s) => (s as { name: string }).name),
        ...markerSeries.map(
          (s) => (s as { name: string }).name,
        ),
      ],
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
      name: `Price (${spec.priceUnit ?? "yuan"})`,
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
