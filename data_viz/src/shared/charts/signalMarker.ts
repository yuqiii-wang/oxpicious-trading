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
 * The NEGATIVE-confidence REJECT tier (2026-11-16): a signal row whose
 * confidence is negative is advice AGAINST its action (a negative SELL
 * = "do not sell at this price", never "buy") and draws the
 * CROSS-TRIANGLE instead — the action-colored filled triangle with a
 * contrasting ✕ overlaid on top (`crossedMarkerSpecs`; the legend icon
 * is a single-color outline composite). Distinct from the grey
 * no-action diamond (`rejectionMarkerSpec` — the gate-declined wait
 * rows): a reject negates a DIRECTION, a wait declines the trigger.
 *
 * `signalEventScatterSeries` (below) is the layer built ON the specs:
 * THE global action-marker scatter for a signal-history event list —
 * buy/sell triangles + the no-action diamond + the no-buy/no-sell
 * cross-triangles, one series per class, the selected-day emphasis —
 * every chart that marks signal history draws it through here (the
 * vol-index and skew charts, the hedge-pair curves) instead of
 * hand-rolling the classes per builder.
 */
import type { SeriesOption } from "echarts";
import {
  DOWN_COLOR,
  NEUTRAL_FILL,
  REJECT_CROSS_COLOR,
  UP_COLOR,
} from "@/theme/chart-palette";

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

// ---------------------------------------------------------------------------
// The CROSS-TRIANGLE — the negative-confidence REJECT markers
// ---------------------------------------------------------------------------

/** The ✕ overlay glyph — two crossing bars in a 14×14 box (poking past
 * the base triangle's corners at the same anchor). */
const REJECT_CROSS_PATH =
  "path://M0.71,2.54 L2.54,0.71 L13.29,11.46 L11.46,13.29 Z " +
  "M11.46,0.71 L13.29,2.54 L2.54,13.29 L0.71,11.46 Z";

/** The LEGEND icon composites — ONE single-color path per action
 * (outline triangle ring + the ✕ bars, the action's own color — the
 * legend cannot composite two glyphs, so on-canvas points draw the
 * richer two-glyph overlay below while the legend shows this). */
const CROSSED_UP_COMPOSITE_PATH =
  "path://M0,10 L11,10 L5.5,0 Z M1.98,8.8 L5.5,2.4 L9.02,8.8 Z " +
  "M0.76,1.82 L2.32,0.26 L10.24,8.18 L8.68,9.74 Z " +
  "M8.68,0.26 L10.24,1.82 L2.32,9.74 L0.76,8.18 Z";
const CROSSED_DOWN_COMPOSITE_PATH =
  "path://M0,0 L11,0 L5.5,10 Z M1.98,1.2 L5.5,7.6 L9.02,1.2 Z " +
  "M0.76,8.18 L2.32,9.74 L10.24,1.82 L8.68,0.26 Z " +
  "M8.68,9.74 L10.24,8.18 L2.32,0.26 L0.76,1.82 Z";

/** The cross-triangle's resolved pair + legend icon for one action —
 * the NEGATIVE-confidence REJECT marker (2026-11-16): the action's own
 * filled triangle (the direction being negated) with the contrasting ✕
 * drawn ON TOP (two overlapped glyphs at the same anchor, the ✕ box
 * ~1.3× so it pokes past the triangle), plus the single-color
 * outline-composite the legend renders. Draw INSTEAD of the plain
 * triangle for a negative-confidence event — "do NOT buy/sell at this
 * price", never the inverse action. */
export interface CrossedMarkerSpecs {
  /** The action-colored filled triangle (the negated direction). */
  triangle: SignalMarkerSpec;
  /** The contrasting ✕ overlay (drawn after the triangle → on top). */
  cross: SignalMarkerSpec;
  /** The single-color outline composite — the LEGEND icon / series
   *   symbol (canvas points override per item with the two glyphs). */
  legend: SignalMarkerSpec;
}

export function crossedMarkerSpecs(
  action: string,
  opts?: { scale?: number; placement?: "hug" | "onPoint" },
): CrossedMarkerSpecs {
  const buy = action === "buy";
  const k = opts?.scale ?? 1;
  const hug = (opts?.placement ?? "hug") === "hug";
  const offset: [number, number] = hug
    ? [0, (buy ? BASE_HUG : -BASE_HUG) * k]
    : [0, 0];
  return {
    triangle: {
      symbol: buy ? "triangle" : DOWN_TRIANGLE_PATH,
      symbolSize: [BASE_SIZE[0] * k, BASE_SIZE[1] * k],
      symbolOffset: offset,
      itemStyle: { color: buy ? UP_COLOR : DOWN_COLOR },
    },
    cross: {
      symbol: REJECT_CROSS_PATH,
      symbolSize: [BASE_SIZE[0] * 1.3 * k, BASE_SIZE[0] * 1.3 * k],
      symbolOffset: offset,
      itemStyle: { color: REJECT_CROSS_COLOR },
    },
    legend: {
      symbol: buy ? CROSSED_UP_COMPOSITE_PATH : CROSSED_DOWN_COMPOSITE_PATH,
      symbolSize: [BASE_SIZE[0] * k, BASE_SIZE[1] * k],
      symbolOffset: offset,
      itemStyle: { color: buy ? UP_COLOR : DOWN_COLOR },
    },
  };
}

/** markPoint names of the two REJECT classes (the OhlcChart emphasis
 * effect's name checks; a reject negates a direction — it IS a decision
 * to spotlight, unlike the wait diamond). */
export const NO_BUY_MARK_NAME = "No-Buy";
export const NO_SELL_MARK_NAME = "No-Sell";

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
 *  literal "wait" action) draws the grey diamond instead, and a
 *  NEGATIVE-confidence event (the REJECT tier) draws the cross-triangle
 *  of its action — the direction negated, never the inverse action. An
 *  inaction must never read as a trade. */
export interface SignalEventPoint {
  date: string;
  /** "buy" / "sell" — the event's recorded action. */
  action: string;
  /** TRUE = the gate-declined no-action record (the grey diamond). */
  is_rejection?: boolean;
  /** The event's confidence in PERCENT — NEGATIVE = the REJECT tier
   *  (the cross-triangle of the event's own action: do NOT buy/sell at
   *  this price). */
  confidence?: number;
  /** Optional identity of the owning event row (the portfolio history
   *  panel's row key) — the selected-EVENT emphasis matches it before
   *  the date (a clicked portfolio row highlights its own markers even
   *  when another event shares the day). */
  key?: string;
}

/** The five marker classes, in class order (buy / sell / no-action /
 * no-buy / no-sell). */
export type SignalEventClass =
  | "buy"
  | "sell"
  | "noAction"
  | "noBuy"
  | "noSell";

/** Classify one event into its marker class — the single decision
 * every marker surface (the scatter layer, the OhlcChart markPoints)
 * shares: wait / is_rejection → the grey no-action diamond; a
 * buy/sell event with NEGATIVE confidence → the cross-triangle of its
 * own action (REJECT the action at this price); otherwise the plain
 * action triangle. Unknown action strings classify to null (nothing
 * draws). */
export function signalEventClass(ev: SignalEventPoint): SignalEventClass | null {
  if (ev.is_rejection === true || ev.action === "wait") return "noAction";
  const negative = (ev.confidence ?? 0) < 0;
  if (ev.action === "buy") return negative ? "noBuy" : "buy";
  if (ev.action === "sell") return negative ? "noSell" : "sell";
  return null;
}

/** Legend names of the five marker classes, in class order (buy /
 * sell / no-action / no-buy / no-sell) — the series names every
 * chart's marker layer shares, so the legend reads identically
 * everywhere. */
export const SIGNAL_EVENT_SERIES_NAMES = [
  "Signal Buy",
  "Signal Sell",
  "Signal No-Action",
  "Signal No-Buy",
  "Signal No-Sell",
] as const;

export interface SignalEventSeriesOpts {
  /** Legend-name override (hedge-pairs' no-action word is "Wait").
   *  [buy, sell, noAction, noBuy, noSell]. */
  names?: readonly [string, string, string, string, string];
  /** The chart's selected day — that day's marker(s) draw with
   *  `emphasizeSignalMarker` (grow + glow, the Recent Movements
   *  marker-click feedback), per point. */
  selectedDate?: string | null;
  /** The selected EVENT's identity — when set it wins over
   *  ``selectedDate`` for the points carrying a matching ``key`` (the
   *  hedge-pairs page's clicked portfolio row), the date match still
   *  applying to keyless points. */
  selectedKey?: string | null;
  /** Scatter z (the hedge-pair curves ride 6, the options charts 5). */
  z?: number;
  /** Axis indices the scatter series bind to (both default 0 — the
   *  multi-axis charts whose host curve is NOT on axis 0 pass theirs
   *  here; the crowdedness curve chart's markers ride its OFFSET
   *  crowdedness axis). */
  axisIndices?: { xAxisIndex?: number; yAxisIndex?: number };
}

/** THE global action-marker scatter layer for a signal-history event
 *  list on ONE value series: classifies the events into buy / sell /
 *  no-action / no-buy / no-sell (`signalEventClass`) via the shared
 *  specs and emits one scatter series per non-empty class (legend
 *  toggles classes individually). The REJECT classes' points draw the
 *  TWO-GLYPH overlay (the action-colored filled triangle + the
 *  contrasting ✕, per-item symbol overrides) while the series symbol
 *  stays the legend composite. `yByDate`
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
  const selectedKey = opts?.selectedKey ?? null;
  const z = opts?.z ?? 5;
  const crossed = {
    buy: crossedMarkerSpecs("buy"),
    sell: crossedMarkerSpecs("sell"),
  };
  const classes: Array<{
    key: SignalEventClass;
    name: string;
    spec: SignalMarkerSpec;
  }> = [
    { key: "buy", name: names[0], spec: signalMarkerSpec("buy") },
    { key: "sell", name: names[1], spec: signalMarkerSpec("sell") },
    { key: "noAction", name: names[2], spec: rejectionMarkerSpec() },
    // the REJECT classes: the series-level symbol is the legend
    // composite; the points draw the two-glyph overlay
    { key: "noBuy", name: names[3], spec: crossed.buy.legend },
    { key: "noSell", name: names[4], spec: crossed.sell.legend },
  ];
  const out: SeriesOption[] = [];
  for (const cls of classes) {
    const data: Array<
      [string, number]
      | {
          value: [string, number];
          symbol?: string;
          symbolSize: [number, number];
          symbolOffset: [number, number];
          itemStyle: SignalMarkerSpec["itemStyle"];
        }
    > = [];
    for (const ev of events) {
      if (signalEventClass(ev) !== cls.key) continue;
      const y = yByDate.get(ev.date);
      if (y == null || !Number.isFinite(y)) continue;
      const isSel = (selected != null && ev.date === selected)
        || (selectedKey != null && ev.key != null
          && ev.key === selectedKey);
      if (cls.key === "noBuy" || cls.key === "noSell") {
        // the two-glyph overlay: filled triangle FIRST, the ✕ ON TOP
        // (same anchor — the cross pokes past the triangle's corners)
        const glyphs = cls.key === "noBuy" ? crossed.buy : crossed.sell;
        for (const glyph of [glyphs.triangle, glyphs.cross]) {
          const spec = isSel ? emphasizeSignalMarker(glyph) : glyph;
          data.push({
            value: [ev.date, y],
            symbol: spec.symbol,
            symbolSize: spec.symbolSize,
            symbolOffset: spec.symbolOffset,
            itemStyle: spec.itemStyle,
          });
        }
        continue;
      }
      if (isSel) {
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
      xAxisIndex: opts?.axisIndices?.xAxisIndex,
      yAxisIndex: opts?.axisIndices?.yAxisIndex,
      tooltip: { show: false },
    });
  }
  return out;
}
