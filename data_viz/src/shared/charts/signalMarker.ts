/**
 * signalMarker — THE shared buy/sell triangle marker (the trade-signal
 * look everywhere in the app): a green up-triangle for buy, a red
 * down-triangle for sell. One spec builds the OHLC chart's tradeSignals
 * markPoints, the trigger overlay's action points, the Options Bands
 * corridor exit markers and the Singleton strategy chart's decision
 * markers, so every buy/sell marker in the app reads identically.
 *
 * The down triangle uses an explicit path instead of a rotated built-in:
 * ECharts legend icons do not inherit symbolRotate, so a rotated
 * triangle would point UP in the legend — the path renders
 * down-pointing in the marker AND the legend.
 *
 * `signalEventScatterSeries` (below) is the layer built ON the specs:
 * THE global action-marker scatter for a signal-history event list —
 * buy/sell triangles + the no-action diamond, one series per class, the
 * selected-day emphasis — every chart that marks signal history draws
 * it through here (the vol-index and skew charts, the hedge-pair
 * curves) instead of hand-rolling the classes per builder.
 */
import type { SeriesOption } from "echarts";
import { DOWN_COLOR, NEUTRAL_FILL, UP_COLOR } from "@/theme/chart-palette";

/** Canonical marker box (px) at scale 1 — the OhlcChart markPoint look. */
const BASE_SIZE: [number, number] = [11, 10];
/** Canonical hug offset (px): buy sits below the bar, sell above. */
const BASE_HUG = 12;

/** Down-pointing triangle path in the BASE_SIZE box (base on top, apex
 *  at bottom) — legend-safe, see the module doc. */
const DOWN_TRIANGLE_PATH = "path://M0,0 L11,0 L5.5,10 Z";

/** One resolved marker's symbol geometry + fill — spread into a markPoint
 *  entry or a scatter series / per-point data item. */
export interface SignalMarkerSpec {
  symbol: string;
  symbolSize: [number, number];
  symbolOffset: [number, number];
  itemStyle: { color: string; shadowBlur?: number; shadowColor?: string };
}

/** The canonical buy/sell marker for one action ("buy" | "sell"; any
 *  other action string draws nothing upstream).
 *  - scale      grows/shrinks the whole marker (1 = canonical).
 *  - color      hue override that keeps the shape (the Singleton
 *               last-day-sell purple; the strategy chart's top-3
 *               shading rides per-point itemStyle instead).
 *  - placement  "hug" (default): offset away from the bar (buy below,
 *               sell above) — the OhlcChart markPoint placement;
 *               "onPoint": centered on the data point (strategy fills). */
export function signalMarkerSpec(
  action: string,
  opts?: {
    scale?: number;
    color?: string;
    placement?: "hug" | "onPoint";
  },
): SignalMarkerSpec {
  const buy = action === "buy";
  const k = opts?.scale ?? 1;
  const hug = (opts?.placement ?? "hug") === "hug";
  return {
    symbol: buy ? "triangle" : DOWN_TRIANGLE_PATH,
    symbolSize: [BASE_SIZE[0] * k, BASE_SIZE[1] * k],
    symbolOffset: hug ? [0, (buy ? BASE_HUG : -BASE_HUG) * k] : [0, 0],
    itemStyle: { color: opts?.color ?? (buy ? UP_COLOR : DOWN_COLOR) },
  };
}

/** Emphasis scale of the SELECTED signal day's marker (the clicked
 *  date — Recent Movements marker clicks). */
export const SIGNAL_MARKER_EMPHASIS_SCALE = 1.6;

/** markPoint name of the NO-ACTION (rejected) diamond — the emphasis
 *  effect skips it exactly like the dividend markers (a rejected day
 *  is documentation, never a decision to spotlight). */
export const REJECTION_MARK_NAME = "Rejection";

/** The NO-ACTION (rejected) marker — a GREY diamond centered on the
 *  day's bar: the condition fired but the owning strategy's gate
 *  explicitly declined it (is_rejection — options_wall's VRP-rejected
 *  put breaches). Draws INSTEAD of any buy/sell triangle, in the
 *  chart's neutral grey, so an inaction reads unmistakably different
 *  from an actionable signal. Same box as the triangles. */
export function rejectionMarkerSpec(
  opts?: { scale?: number },
): SignalMarkerSpec {
  const k = opts?.scale ?? 1;
  return {
    symbol: "diamond",
    symbolSize: [BASE_SIZE[0] * k, BASE_SIZE[0] * k],
    symbolOffset: [0, 0],
    itemStyle: { color: NEUTRAL_FILL },
  };
}

/** A bigger copy of one resolved marker with a soft glow in its own
 *  color — the selected-day emphasis. */
export function emphasizeSignalMarker(
  spec: SignalMarkerSpec,
  scale: number = SIGNAL_MARKER_EMPHASIS_SCALE,
): SignalMarkerSpec {
  return {
    ...spec,
    symbolSize: [spec.symbolSize[0] * scale, spec.symbolSize[1] * scale],
    symbolOffset: [spec.symbolOffset[0] * scale, spec.symbolOffset[1] * scale],
    itemStyle: {
      ...spec.itemStyle,
      shadowBlur: 9,
      shadowColor: spec.itemStyle.color,
    },
  };
}

/** One trade-signal markPoint entry — the shared spec plus the name /
 *  coord fields the markPoint data carries (dividend entries share the
 *  host; their shape stays local to the option builder). */
export interface SignalMarkPointData extends SignalMarkerSpec {
  name: string;
  coord: [string, number];
}

// ---------------------------------------------------------------------------
// THE shared action-marker EVENT LAYER — signal history as scatter series
// ---------------------------------------------------------------------------

/** One signal-history event for the shared marker layer (a row of
 *  analysis_signals.history_signals as the API delivers it):
 *  actionable events draw the buy/sell triangles, a no-action record
 *  (is_rejection — the owning strategy's gate declined — or the
 *  literal "wait" action) draws the grey diamond instead. An inaction
 *  must never read as a trade. */
export interface SignalEventPoint {
  date: string;
  /** "buy" / "sell" — the event's recorded action. */
  action: string;
  /** TRUE = the gate-declined no-action record (the grey diamond). */
  is_rejection?: boolean;
}

/** Legend names of the three marker classes, in class order (buy /
 *  sell / no-action) — the series names every chart's marker layer
 *  shares, so the legend reads identically everywhere. */
export const SIGNAL_EVENT_SERIES_NAMES = [
  "Signal Buy",
  "Signal Sell",
  "Signal No-Action",
] as const;

export interface SignalEventSeriesOpts {
  /** Legend-name override (hedge-pairs' no-action word is "Wait").
   *  [buy, sell, noAction]. */
  names?: readonly [string, string, string];
  /** The chart's selected day — that day's marker(s) draw with
   *  `emphasizeSignalMarker` (grow + glow, the Recent Movements
   *  marker-click feedback), per point. */
  selectedDate?: string | null;
  /** Scatter z (the hedge-pair curves ride 6, the options charts 5). */
  z?: number;
}

/** THE global action-marker scatter layer for a signal-history event
 *  list on ONE value series: classifies the events into buy / sell /
 *  no-action via the shared specs and emits one scatter series per
 *  non-empty class (legend toggles classes individually). `yByDate`
 *  resolves each event's marker y (the host curve's value that day);
 *  events without a finite value that day skip (honest gaps). The
 *  selected day's points grow + glow (see `SignalEventSeriesOpts`).
 *  No border anywhere — the flat spec fill as Short-Term Tech renders
 *  it (the ringed variant was removed 2026-10-19, user-directed: the
 *  boundary read as noise on the dense option charts). */
export function signalEventScatterSeries(
  events: readonly SignalEventPoint[],
  yByDate: Map<string, number | null | undefined>,
  opts?: SignalEventSeriesOpts,
): SeriesOption[] {
  const names = opts?.names ?? SIGNAL_EVENT_SERIES_NAMES;
  const selected = opts?.selectedDate ?? null;
  const z = opts?.z ?? 5;
  const classes: Array<{
    key: "buy" | "sell" | "noAction";
    name: string;
    spec: SignalMarkerSpec;
  }> = [
    { key: "buy", name: names[0], spec: signalMarkerSpec("buy") },
    { key: "sell", name: names[1], spec: signalMarkerSpec("sell") },
    { key: "noAction", name: names[2], spec: rejectionMarkerSpec() },
  ];
  const out: SeriesOption[] = [];
  for (const cls of classes) {
    const data: Array<
      [string, number]
      | {
          value: [string, number];
          symbolSize: [number, number];
          symbolOffset: [number, number];
          itemStyle: SignalMarkerSpec["itemStyle"];
        }
    > = [];
    for (const ev of events) {
      const noAction = ev.is_rejection === true || ev.action === "wait";
      const match = cls.key === "noAction"
        ? noAction
        : !noAction && ev.action === cls.key;
      if (!match) continue;
      const y = yByDate.get(ev.date);
      if (y == null || !Number.isFinite(y)) continue;
      if (selected != null && ev.date === selected) {
        const em = emphasizeSignalMarker(cls.spec);
        data.push({
          value: [ev.date, y],
          symbolSize: em.symbolSize,
          symbolOffset: em.symbolOffset,
          itemStyle: em.itemStyle,
        });
      } else {
        data.push([ev.date, y]);
      }
    }
    if (data.length === 0) continue;
    out.push({
      type: "scatter" as const,
      name: cls.name,
      data,
      symbol: cls.spec.symbol,
      symbolSize: cls.spec.symbolSize,
      symbolOffset: cls.spec.symbolOffset,
      itemStyle: { color: cls.spec.itemStyle.color },
      z,
      tooltip: { show: false },
    });
  }
  return out;
}
