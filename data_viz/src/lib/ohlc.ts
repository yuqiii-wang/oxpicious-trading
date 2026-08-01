/**
 * Shared OHLC bar series — single source of truth for every OHLC chart in
 * the app (ETF rebased OHLC %, options annual sentiment, index 5-min
 * intraday, stock/index daily baselines, …).
 *
 * Renders classic OHLC bars via an ECharts custom series (renderItem):
 *   • a vertical line from high to low
 *   • a left tick at the open price
 *   • a right tick at the close price
 * Colored green when close >= open, red otherwise — identical up/down
 * coloring to the former candlestick (matches `draw_candlestick` in
 * _plot_commons.py).
 *
 * Data order matches the previous candlestick convention:
 * `[open, close, low, high]` (low before high). Kept so existing data
 * preparation and tooltip destructuring (`const [o, cl, l, h] = value`)
 * continue to work unchanged.
 */
import type {
  CustomSeriesOption,
  CustomSeriesRenderItem,
  SeriesOption,
} from "echarts";
import { UP_COLOR, DOWN_COLOR } from "@/theme/chart-palette";
import { fmtNum, fmtPct } from "@/lib/series";

/** Per-series overrides accepted by `ohlcSeries`. */
export interface OhlcOverrides {
  name?: string;
  yAxisIndex?: number;
  z?: number;
}

// ----------------------------------------------------------------------------
//  Absolute vs Percentage display mode
//  Shared across every OHLC panel. Default is "percentage" — both the OHLC
//  bars and any close-derived MA lines are rebased to % change from the first
//  valid close so relative performance is directly comparable. "absolute"
//  shows raw prices. Series NOT derived from price (volume, PE, margin
//  scores, trading amount) are never rebased — callers keep them on their
//  own axes regardless of mode.
// ----------------------------------------------------------------------------
export type OhlcMode = "absolute" | "percentage";

/**
 * Rebase one or more price-derived arrays to % change from the first valid
 * value of the `close` array (falls back to the first available array when
 * `close` is absent). All arrays are scaled by the SAME base value so:
 *   • the relative shape of each OHLC candle is preserved (close-vs-open
 *     coloring is invariant under uniform scaling)
 *   • MA lines stay visually aligned with the candle closes
 *
 * In "absolute" mode the inputs are returned unchanged (no copy).
 *
 * @param arrays  Named arrays to rebase in lockstep. Conventionally includes
 *                `close` (used as the base source) plus `open`, `high`,
 *                `low`, and any MA arrays. Each value is `Array<number | null>`.
 * @param mode    "absolute" returns inputs unchanged; "percentage" rebases.
 * @returns `rebased` — same keys, rebased arrays. `baseIdx` — index of the
 *          base value in the close array (-1 when no valid base is found).
 */
export function rebasePriceArrays(
  arrays: Record<string, Array<number | null>>,
  mode: OhlcMode,
): { rebased: Record<string, Array<number | null>>; baseIdx: number } {
  if (mode === "absolute") {
    return { rebased: arrays, baseIdx: -1 };
  }
  const baseArr = arrays.close ?? Object.values(arrays)[0] ?? [];
  let baseIdx = -1;
  let baseVal: number | null = null;
  for (let i = 0; i < baseArr.length; i++) {
    const v = baseArr[i];
    if (v != null && Number.isFinite(v) && Math.abs(v) >= 1e-9) {
      baseIdx = i;
      baseVal = v;
      break;
    }
  }
  if (baseIdx < 0 || baseVal == null) {
    const rebased: Record<string, Array<number | null>> = {};
    for (const k of Object.keys(arrays)) {
      rebased[k] = arrays[k].map(() => null);
    }
    return { rebased, baseIdx: -1 };
  }
  const scale = (v: number | null) =>
    v != null && Number.isFinite(v) ? (v / baseVal! - 1) * 100 : null;
  const rebased: Record<string, Array<number | null>> = {};
  for (const k of Object.keys(arrays)) {
    rebased[k] = arrays[k].map(scale);
  }
  return { rebased, baseIdx };
}

/**
 * Format a price-derived value according to the display mode.
 * "percentage" → `fmtPct` (e.g. "+12.3%"); "absolute" → `fmtNum` (e.g. "12.3").
 * Use for OHLC components and MA line values. Non-price series (volume, PE,
 * trading amount) should use `fmtNum` directly regardless of mode.
 */
export function formatPriceValue(
  v: number | null | undefined,
  mode: OhlcMode,
  digits = 3,
): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return mode === "percentage" ? fmtPct(v, digits) : fmtNum(v, digits);
}

/**
 * Render a single OHLC bar. Returns undefined for gap/NaN rows so ECharts
 * draws nothing (matching the previous candlestick gap behavior).
 */
const ohlcRenderItem: CustomSeriesRenderItem = (params, api) => {
  const openVal = api.value(0);
  const closeVal = api.value(1);
  const lowVal = api.value(2);
  const highVal = api.value(3);
  if (
    typeof openVal !== "number" || !Number.isFinite(openVal) ||
    typeof closeVal !== "number" || !Number.isFinite(closeVal) ||
    typeof lowVal !== "number" || !Number.isFinite(lowVal) ||
    typeof highVal !== "number" || !Number.isFinite(highVal)
  ) {
    return undefined;
  }
  const idx = params.dataIndexInside;
  const high = api.coord([idx, highVal]);
  const low = api.coord([idx, lowVal]);
  const openPt = api.coord([idx, openVal]);
  const closePt = api.coord([idx, closeVal]);
  const x = high[0];
  // Width of one category band — ticks are ~30% of it on each side.
  let band = 6;
  if (api.size) {
    const s = api.size([1, 0]);
    band = Array.isArray(s) ? (s[0] as number) : (s as number);
  }
  const tickLen = Math.max(1, band * 0.3);
  const color = closeVal >= openVal ? UP_COLOR : DOWN_COLOR;
  return {
    type: "group",
    children: [
      // Vertical high-low line
      {
        type: "line",
        shape: { x1: x, y1: high[1], x2: x, y2: low[1] },
        style: { stroke: color, lineWidth: 1 },
      },
      // Open tick (left)
      {
        type: "line",
        shape: { x1: x - tickLen, y1: openPt[1], x2: x, y2: openPt[1] },
        style: { stroke: color, lineWidth: 1 },
      },
      // Close tick (right)
      {
        type: "line",
        shape: { x1: x, y1: closePt[1], x2: x + tickLen, y2: closePt[1] },
        style: { stroke: color, lineWidth: 1 },
      },
    ],
  };
};

/**
 * Build an OHLC bar series option using the shared style.
 *
 * @param data       Array of `[open, close, low, high]` tuples (low before
 *                   high — kept identical to the previous candlestick order).
 * @param overrides  Optional per-series fields (name, yAxisIndex, z).
 */
export function ohlcSeries(
  data: Array<Array<number | null>>,
  overrides: OhlcOverrides = {},
): SeriesOption {
  const opt: CustomSeriesOption = {
    type: "custom",
    data,
    // Declare all four dimensions as y-values so the y-axis scale includes
    // the full high/low range (custom series does not infer this itself).
    encode: { y: [0, 1, 2, 3] },
    renderItem: ohlcRenderItem,
    clip: true,
  };
  if (overrides.name != null) opt.name = overrides.name;
  if (overrides.yAxisIndex != null) opt.yAxisIndex = overrides.yAxisIndex;
  if (overrides.z != null) opt.z = overrides.z;
  return opt as SeriesOption;
}
