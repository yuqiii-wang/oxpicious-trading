/**
 * Shared-skew overlay series — the optional layers buildSharedSkewOption
 * composes around the core skew curves, split out of sharedSkewOption.ts
 * (2026-10-20, >900-line split; re-exported from there for the existing
 * importers):
 *
 *   • bandEventSeries     — corridor-event scatter markers (options_bands v2)
 *   • corridorSeries      — the OI-mass corridor P10–P90 step shade
 *   • wallZoneSeries      — per-side wall/zone custom-series shades
 *   • highlightOverlay    — forecast trigger-day pinpoints + markArea bands
 */
import type { SeriesOption } from "echarts";
import { ATM_GRAY, DOWN_COLOR, NEUTRAL_FILL, TRIGGER_DATE_COLOR, TRIGGER_DATE_FILL, TRIGGER_STREAK_FILL, UP_COLOR } from "@/theme/chart-palette";
import { signalMarkerSpec } from "@/shared/charts/signalMarker";
import {
  CALL_ZONE_SERIES_NAME,
  PUT_ZONE_SERIES_NAME,
} from "../options-trend/bandData";
import type {
  SharedBandEvent,
  SharedCorridorPoint,
  SharedSkewHighlights,
  SharedWallZone,
  SharedWallZones,
} from "./types";

/** Series names of the corridor-event marker classes (legend order). */
export const BAND_EVENT_SERIES_NAMES = [
  "Corridor Exit Low",
  "Corridor Exit High",
        "Corridor Re-enter",
      ] as const;


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

export function bandEventSeries(
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
export function corridorSeries(
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
export function wallZoneSeries(
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
export function highlightOverlay(
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
