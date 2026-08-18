import type { ExpiryDot, ExpiryDotDataItem } from "./types";

/** Build the scatter data payload for the expiry-dots series. */
export function buildExpiryDotsSeriesData(dots: ExpiryDot[]): ExpiryDotDataItem[] {
  return dots
    .filter((d) => d.value != null && Number.isFinite(d.value))
    .map((d) => ({ value: [d.dateIndex, d.value!], dot: d }));
}