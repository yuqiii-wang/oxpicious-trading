/**
 * Shared-skew axis helpers — the category-axis construction and the
 * per-expiry OI line-width encoding, split out of sharedSkewOption.ts
 * (2026-10-20, >900-line split; re-exported from there for the existing
 * importers).
 */
import type { SharedSkewSpec } from "./types";

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
