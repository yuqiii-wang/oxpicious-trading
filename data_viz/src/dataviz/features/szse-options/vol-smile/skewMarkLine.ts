import { axisColors } from "@/theme/chart-palette";
import { fmtNum } from "@/lib/series";
import { computeSmileSkewness } from "@/lib/options-stats";
import type { OptionsRow } from "@shared/types";

/**
 * Shared skewness vertical-line series for ALL options smile plots.
 *
 * Renders a solid, even-width line at the OI-weighted mean moneyness (the
 * smile's skewness), with an inline label and — when hovered — a tooltip
 * listing the per-expiry OI-weighted 3rd-moment skewness (CALL / PUT /
 * overall). Negative = downside (puts) richer; positive = upside (calls).
 *
 * Centralized here so the snapshot smile and any future smile-style chart
 * share identical skew styling + tooltip behaviour.
 */
export function buildSkewLineSeries(
  valid: OptionsRow[],
  S: number,
  themeMode: "light" | "dark",
  yMax: number,
  priceScale: number,
): Record<string, unknown> | null {
  const c = axisColors(themeMode);
  const textColor = c.textColor;
  const splitColor = c.splitLineColor;

  const totalOi = valid.reduce((s, r) => s + Math.max(1, r.open_interest), 0);
  if (totalOi <= 0) return null;

  const weightedMeanMoneyness =
    valid.reduce((s, r) => {
      const oi = Math.max(1, r.open_interest);
      const mn = r.strike_price / priceScale / S;
      return s + oi * mn;
    }, 0) / totalOi;

  const skewDx = weightedMeanMoneyness - 1.0;
  const skewColor =
    skewDx < -1e-4
      ? "rgba(220, 50, 50, 0.9)"
      : skewDx > 1e-4
        ? "rgba(50, 140, 220, 0.9)"
        : "rgba(128,128,128,0.85)";
  const skewLabel = skewDx >= 0 ? `+${skewDx.toFixed(3)}` : skewDx.toFixed(3);

  const skewInfo = computeSmileSkewness(valid).map((s) => ({
    expiry: s.expiry,
    callSkew: s.callSkew,
    putSkew: s.putSkew,
    overallSkew: s.overallSkew,
  }));

  const lines = [`<b>IV Smile Skewness</b>`, `OI-wtd Δ moneyness: ${skewLabel}`];
  if (skewInfo.length > 0) {
    lines.push(`<div style="opacity:0.7;margin-top:2px">Per-expiry (3rd moment)</div>`);
    for (const s of skewInfo) {
      const fmt = (v: number | null) =>
        v != null && Number.isFinite(v) ? fmtNum(v, 3) : "—";
      lines.push(
        `<div style="padding-left:8px">${s.expiry}: ` +
          `C <b>${fmt(s.callSkew)}</b> · P <b>${fmt(s.putSkew)}</b> · ` +
          `all <b>${fmt(s.overallSkew)}</b></div>`,
      );
    }
  }
  lines.push(
    `<div style="opacity:0.6;margin-top:2px">Neg = downside IV richer (puts) · Pos = upside (calls)</div>`,
  );

  return {
    type: "line",
    name: "Skewness",
    showSymbol: false,
    data: [
      [weightedMeanMoneyness, 0],
      [weightedMeanMoneyness, yMax],
    ],
    lineStyle: { color: skewColor, type: "solid", width: 1.5, opacity: 0.85 },
    silent: false,
    z: 1,
    emphasis: { lineStyle: { width: 2.5, opacity: 1 } },
    tooltip: {
      show: true,
      backgroundColor: c.tooltipBg,
      borderColor: splitColor,
      textStyle: { color: textColor, fontSize: 11 },
      formatter: () => lines.join("<br/>"),
    },
    label: {
      show: true,
      formatter: `Skew Δ=${skewLabel}`,
      color: textColor,
      fontSize: 10,
      fontWeight: 600,
      position: "bottom",
      distance: 4,
    },
  };
}
