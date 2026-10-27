import type { ExpiryDot, ExpiryDotDataItem } from "./types";

/** Expiry calendar date as known on `dateStr`: the row's days_to_expiry
 *  (true days remaining on that date) added to it. */
export function expiryDateFromDte(dateStr: string, dte: number): string {
  const d = new Date(dateStr + "T00:00:00");
  d.setDate(d.getDate() + Math.round(dte));
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

/** Build the scatter data payload for the expiry-dots series. Each dot sits
 *  at its contract's expiry anchor; dots near the axis end flip their label
 *  to the left so the text stays on the canvas. */
export function buildExpiryDotsSeriesData(
  dots: ExpiryDot[],
  totalDates?: number,
): ExpiryDotDataItem[] {
  const rightEdgeIdx =
    totalDates != null && totalDates > 0
      ? totalDates - 1 - Math.max(1, Math.round(totalDates * 0.02))
      : null;
  return dots
    .filter((d) => d.value != null && Number.isFinite(d.value))
    .map((d) => ({
      value: [d.dateIndex, d.value!],
      dot: d,
      ...(rightEdgeIdx != null && d.dateIndex > rightEdgeIdx
        ? { label: { position: "left" as const } }
        : {}),
    }));
}
