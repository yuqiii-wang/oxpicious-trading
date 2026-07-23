/**
 * Shared candlestick series style — single source of truth for every
 * candlestick chart in the app (ETF rebased OHLC %, options annual
 * sentiment, index 5-min intraday, …).
 *
 * Extracted from EtfMarginPanel so all candlesticks render identically and
 * any future styling change propagates from one place.
 */
import type { SeriesOption } from "echarts";
import { UP_COLOR, DOWN_COLOR } from "@/theme/chart-palette";

/**
 * Candlestick itemStyle — green up bodies/borders, red down bodies/borders.
 * Matches `draw_candlestick` in _plot_commons.py.
 */
export const CANDLESTICK_ITEM_STYLE = {
  color: UP_COLOR,
  color0: DOWN_COLOR,
  borderColor: UP_COLOR,
  borderColor0: DOWN_COLOR,
} as const;

/** Per-series overrides accepted by `candlestickSeries`. */
export interface CandlestickOverrides {
  name?: string;
  yAxisIndex?: number;
  z?: number;
  barWidth?: string | number;
}

/**
 * Build a candlestick series option using the shared style.
 *
 * @param data       Array of `[open, close, low, high]` tuples (ECharts
 *                   candlestick data order — NOTE: low before high).
 * @param overrides  Optional per-series fields (name, yAxisIndex, z, …).
 */
export function candlestickSeries(
  data: Array<Array<number | null>>,
  overrides: CandlestickOverrides = {},
): SeriesOption {
  return {
    type: "candlestick",
    data,
    itemStyle: { ...CANDLESTICK_ITEM_STYLE },
    ...overrides,
  } as SeriesOption;
}
