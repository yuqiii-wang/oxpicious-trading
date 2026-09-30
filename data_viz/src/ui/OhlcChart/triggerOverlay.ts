/**
 * triggerOverlay — the payload builder for OhlcChart's forecast
 * trigger-day overlay (the imperative incremental update). Extracted
 * verbatim from the component's triggerOverlay useMemo; pure apart from
 * the two date → tooltip-context refs it refreshes for the axis tooltip
 * formatter.
 */
import {
  TRIGGER_DATE_FILL,
  TRIGGER_STREAK_FILL,
  UP_COLOR,
  DOWN_COLOR,
} from "@/theme/chart-palette";
import type { CodeTrendStreakInfo } from "@/shared/charts/CodeTrendTooltip";
import type {
  ForecastActionByDateRef,
  HighlightSpan,
  OhlcForecastAction,
  StreakInfoByDateRef,
  TriggerActionPoint,
  TriggerAnchor,
} from "./types";
import { TRIGGER_RAIL_Y } from "./types";

/** One markArea rect pair of the overlay (xAxis range + fill color). */
type OverlayMarkAreaItem = [
  { xAxis: string; itemStyle: object },
  { xAxis: string },
];

export interface TriggerOverlay {
  /** Rail circles: [date, TRIGGER_RAIL_Y] per highlighted signal day. */
  points: Array<[string, number]>;
  /** Buy/sell triangles per highlighted day (when the bucket registered
   *  a signal action). */
  actionPoints: TriggerActionPoint[];
  /** Union-merged light (forward window) + dark (streak period) spans. */
  markArea: { silent: boolean; data: OverlayMarkAreaItem[] } | undefined;
}

export interface TriggerOverlayArgs {
  /** The anchor geometry stashed by the latest base option build. */
  anchor: TriggerAnchor | null;
  highlightDates: string[];
  highlightSpans: HighlightSpan[];
  highlightHorizonDays: number;
  highlightAction: OhlcForecastAction | null;
  streakInfoByDateRef: StreakInfoByDateRef;
  forecastActionByDateRef: ForecastActionByDateRef;
}

export function buildTriggerOverlay({
  anchor,
  highlightDates,
  highlightSpans,
  highlightHorizonDays,
  highlightAction,
  streakInfoByDateRef,
  forecastActionByDateRef,
}: TriggerOverlayArgs): TriggerOverlay {
  const empty = {
    points: [] as Array<[string, number]>,
    actionPoints: [] as TriggerActionPoint[],
    markArea: undefined,
  };
  if (!anchor || (highlightDates.length === 0 && highlightSpans.length === 0)) {
    streakInfoByDateRef.current = new Map();
    forecastActionByDateRef.current = new Map();
    return empty;
  }
  const rowIdx = new Map(anchor.dates.map((d, i) => [d, i]));
  const lastRow = anchor.dates.length - 1;
  // Per-date streak context for the axis tooltip: every date inside a
  // qualifying-run span maps to that run's day count + full [start,
  // end] (the span dates, not the clipped ones). Runs of one bucket
  // are disjoint, so first span wins on the (impossible) overlap.
  const streakInfo = new Map<string, CodeTrendStreakInfo>();
  for (const s of highlightSpans) {
    let a = rowIdx.get(s.start);
    const b = rowIdx.get(s.end);
    if (b === undefined) continue;
    if (a === undefined) {
      // start predates the chart — clip to the first visible row
      a = 0;
    }
    for (let i = a; i <= b; i++) {
      const d = anchor.dates[i];
      if (d != null && !streakInfo.has(d)) {
        streakInfo.set(d, { days: s.days ?? null, start: s.start, end: s.end });
      }
    }
  }
  streakInfoByDateRef.current = streakInfo;
  const points: Array<[string, number]> = [];
  // Action triangles: the bucket's registered buy/sell per mid day —
  // anchored at the rebased low (buy, below the bar) / high (sell,
  // above the bar) with the close as fallback, exactly like the
  // tradeSignals markPoints. Per-item symbol overrides on the scatter
  // host; the tooltip labels come from forecastActionByDateRef.
  const actionPoints: TriggerActionPoint[] = [];
  const forecastActionInfo = new Map<string, OhlcForecastAction>();
  const act =
    highlightAction != null &&
    (highlightAction.action === "buy" || highlightAction.action === "sell")
      ? highlightAction
      : null;
  // Light spans: mid day through its forward forecast window.
  const fwd: Array<[number, number]> = [];
  for (const d of new Set(highlightDates)) {
    const i = rowIdx.get(d);
    if (i === undefined) continue; // signal day not on the chart
    points.push([d, TRIGGER_RAIL_Y]);
    if (act) {
      const bar = act.action === "buy" ? anchor.low[i] : anchor.high[i];
      const y = bar != null && Number.isFinite(bar)
        ? bar
        : anchor.close[i];
      if (y != null && Number.isFinite(y)) {
        const buy = act.action === "buy";
        actionPoints.push({
          value: [d, y],
          symbol: "triangle",
          symbolRotate: buy ? 0 : 180,
          symbolSize: [11, 10],
          // Up-triangle below the bar for buys, down-triangle above
          // for sells (symbolOffset px: +y down, -y up).
          symbolOffset: buy ? [0, 12] : [0, -12],
          itemStyle: { color: buy ? UP_COLOR : DOWN_COLOR },
        });
        forecastActionInfo.set(d, act);
      }
    }
    const H = Math.max(1, highlightHorizonDays);
    fwd.push([i, Math.min(i + H, lastRow)]);
  }
  forecastActionByDateRef.current = forecastActionInfo;
  // Dark spans: each signal's qualifying streak period, clipped to
  // the visible rows (the run may extend beyond the chart window).
  const dark: Array<[number, number]> = [];
  for (const s of highlightSpans) {
    let a = rowIdx.get(s.start);
    const b = rowIdx.get(s.end);
    if (b === undefined) continue;
    if (a === undefined) {
      // start predates the chart — clip to the first visible row
      a = 0;
    }
    dark.push([a, b]);
  }
  const merge = (spans: Array<[number, number]>): Array<[number, number]> => {
    const sorted = [...spans].sort((x, y) => x[0] - y[0] || x[1] - y[1]);
    const merged: Array<[number, number]> = [];
    for (const s of sorted) {
      const last = merged[merged.length - 1];
      if (last && s[0] <= last[1] + 1) {
        if (s[1] > last[1]) last[1] = s[1];
      } else {
        merged.push([s[0], s[1]]);
      }
    }
    return merged;
  };
  const mergedFwd = merge(fwd);
  const mergedDark = merge(dark);
  if (mergedFwd.length === 0 && mergedDark.length === 0 && actionPoints.length === 0) {
    return empty;
  }
  const markArea = {
    silent: true,
    data: [
      ...mergedFwd.map(
        ([a, b]) =>
          [
            { xAxis: anchor.dates[a], itemStyle: { color: TRIGGER_DATE_FILL } },
            { xAxis: anchor.dates[b] },
          ] as OverlayMarkAreaItem,
      ),
      ...mergedDark.map(
        ([a, b]) =>
          [
            { xAxis: anchor.dates[a], itemStyle: { color: TRIGGER_STREAK_FILL } },
            { xAxis: anchor.dates[b] },
          ] as OverlayMarkAreaItem,
      ),
    ],
  };
  return { points, actionPoints, markArea };
}
