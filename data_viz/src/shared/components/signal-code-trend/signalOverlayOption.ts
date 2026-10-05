/**
 * signalOverlayOption — the pure option builder of the Recent
 * Movements code-trend's CURVE overlay panel (SignalOverlayPanel).
 *
 *  buildSignalCurveOption — the CONTINUOUS depth-response view: the
 *  fitted line mu(s) = mu_center + slope * (s - s_center) per regime
 *  (analysis_signals.signal_curves, latest snapshot), drawn over
 *  s = Phi^-1(1-q) with vertical bucket guides at the 1/2/3/5/10% tail
 *  positions and each cell's own operating point (s_center, mu(s_center))
 *  as a dot. (The retired THRESHOLD bars view — the active RSI bars
 *  overlay — was removed 2026-10-05; the curves subsume it.)
 */
import type { EChartsOption } from "echarts";
import type { ThemeMode } from "@/store/filters";
import { axisColors, commonGrid } from "@/theme/chart-palette";
import { baseChartOption, commonTooltip } from "@/shared/charts/base-chart";
import {
  REGIME_ACCENT_COLORS,
  REGIME_LABELS,
} from "@/shared/charts/regimeBands";
import type { MarketRegime } from "@shared/types";
import type { SignalCurve } from "@/lib/api-client";

/** The Gaussian depth score s = Phi^-1(1 - q) at the standard tail
 * buckets (deterministic math constants — the curve's x axis). */
export const TAIL_S_BY_PCT: Array<{ pct: number; s: number }> = [
  { pct: 10, s: 1.2816 },
  { pct: 5, s: 1.6449 },
  { pct: 3, s: 1.8808 },
  { pct: 2, s: 2.0537 },
  { pct: 1, s: 2.3263 },
];

/** The curve panel's x window (s of the 10% tail .. a little past the
 * 1% bar). */
const S_MIN = 1.24;
const S_MAX = 2.45;
const S_STEP = 0.02;


/** mu(s) — the curve evaluated at s (fraction, sign-aligned). */
export function curveMu(curve: SignalCurve, s: number): number {
  return curve.mu_center + curve.slope * (s - curve.s_center);
}

// ---------------------------------------------------------------------------
//  CURVE view
// ---------------------------------------------------------------------------

/** Display label per signal TAG (signal_type — the indicator family
 *  behind the curves). Only mov_rsi exists today; new families add an
 *  entry here and surface in the panel's tag selector automatically
 *  (unknown types fall back to the raw code). */
export const SIGNAL_TYPE_LABELS: Record<string, string> = {
  mov_rsi: "RSI",
};

export function buildSignalCurveOption(
  curves: SignalCurve[],
  signalType: string,
  rsiWindow: number,
  side: "bottom" | "top",
  mode: ThemeMode,
): EChartsOption {
  const c = axisColors(mode);
  const cell = curves.filter(
    (cv) =>
      cv.signal_type === signalType &&
      cv.rsi_window === rsiWindow &&
      cv.side === side,
  );
  const sGrid: number[] = [];
  for (let s = S_MIN; s <= S_MAX + 1e-9; s += S_STEP) {
    sGrid.push(Number(s.toFixed(3)));
  }
  const guides = TAIL_S_BY_PCT.map(({ pct, s }) => ({
    xAxis: s,
    lineStyle: { color: c.splitLineColor, type: "dashed" as const, width: 1 },
    label: {
      formatter: `${pct}%`,
      position: "insideTopRight" as const,
      fontSize: 9,
      color: c.textColor,
    },
  }));
  const series = cell.map((cv) => ({
    name: REGIME_LABELS[cv.regime_state as MarketRegime] ?? cv.regime_state,
    type: "line" as const,
    data: sGrid.map((s) => [s, Number((curveMu(cv, s) * 100).toFixed(4))]),
    showSymbol: false,
    lineStyle: { width: 1.6, color: REGIME_ACCENT_COLORS[cv.regime_state as MarketRegime] },
    itemStyle: { color: REGIME_ACCENT_COLORS[cv.regime_state as MarketRegime] },
    // the cell's own operating point: mu at its empirical depth center
    markPoint: {
      symbol: "circle",
      symbolSize: 7,
      label: { show: false },
      data: [
        {
          coord: [
            cv.s_center,
            Number((curveMu(cv, cv.s_center) * 100).toFixed(4)),
          ],
          itemStyle: {
            color: REGIME_ACCENT_COLORS[cv.regime_state as MarketRegime],
          },
        },
      ],
    },
    markLine: { silent: true, symbol: "none", data: guides },
  }));
  return baseChartOption(mode, {
    grid: commonGrid({ bottom: 20 }),
    tooltip: commonTooltip(mode, {
      formatter: (params) => {
        const p = Array.isArray(params) ? params[0] : params;
        if (!p || !Array.isArray(p.data)) return "";
        const [s, muPct] = p.data as [number, number];
        const nearest = TAIL_S_BY_PCT.reduce((a, b) =>
          Math.abs(b.s - s) < Math.abs(a.s - s) ? b : a,
        );
        return (
          `<b>${p.seriesName}</b><br/>` +
          `depth s = ${s.toFixed(2)} (≈ ${nearest.pct}% tail)<br/>` +
          `expected rebound ${muPct >= 0 ? "+" : ""}${muPct.toFixed(2)}%`
        );
      },
    }),
    xAxis: {
      type: "value",
      min: S_MIN,
      max: S_MAX,
      name: "depth s",
      nameTextStyle: { color: c.textColor, fontSize: 9 },
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: { color: c.textColor, fontSize: 9 },
    },
    yAxis: {
      type: "value",
      name: side === "bottom" ? "expected rebound %" : "sell-side μ %",
      nameTextStyle: { color: c.textColor, fontSize: 9 },
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: {
        color: c.textColor,
        fontSize: 9,
        formatter: (v: number) => `${v.toFixed(1)}%`,
      },
      splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.4 } },
    },
    series,
  });
}
