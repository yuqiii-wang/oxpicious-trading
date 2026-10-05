/**
 * The shared full-height shade-overlay carrier series — one rect-marked
 * legend series per overlay whose markArea carries the precomputed
 * rects. Every shade button group renders through this (Px-Vol States,
 * Market Regimes, Top-10% OHLC Days, the High/Low Streak sides, and the
 * Roof/Floor extreme↔2nd-extreme zones), so a legend click toggles its
 * whole overlay the same way everywhere.
 */
import type { SeriesOption } from "echarts";
import type { ShadeOverlay } from "./types";
import type { StreakMarkAreaDatum } from "@/shared/charts/streakBands";

/** The two markArea datum shapes the overlays carry: date-range rects
 *  (ShadeOverlay) and price-band rects (the streak / extreme zones). */
export type OverlayMarkAreaData = ShadeOverlay["data"] | StreakMarkAreaDatum[];

export interface ShadeOverlaySeriesSpec {
  /** Legend entry name — toggling it hides the overlay's markArea. */
  name: string;
  data: OverlayMarkAreaData;
  /** Legend marker + rect fill color. */
  accent: string;
  /** Series z-order. */
  z: number;
  /** Optional darker outline (the streak sides draw one). */
  borderColor?: string;
}

export function shadeOverlaySeries(spec: ShadeOverlaySeriesSpec): SeriesOption {
  return {
    type: "scatter",
    name: spec.name,
    data: [null],
    symbol: "rect",
    symbolSize: [10, 8],
    itemStyle: {
      color: spec.accent,
      opacity: 0.45,
      ...(spec.borderColor != null ? { borderColor: spec.borderColor } : {}),
    },
    z: spec.z,
    markArea: {
      silent: true as const,
      itemStyle: { borderWidth: 0 },
      data: spec.data,
    },
  };
}
