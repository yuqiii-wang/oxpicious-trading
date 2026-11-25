/**
 * useDateTickGranularity — zoom-aware date-tick granularity for the
 * time-slider chart families (macro / debt). Picks the densest tick
 * granularity (month → quarter → year) that fits the measured chart width
 * for the VISIBLE dataZoom window, and re-picks when the user zooms the
 * time slider: a zoomed-in window re-densifies (year → quarter → month), a
 * zoomed-out one re-coarsens, so the labels never overlap.
 *
 * Returns the granularity for the option builder plus a stable
 * `{ datazoom }` onEvents map for BaseChart — the rebuild only happens when
 * the granularity actually crosses a rung, not on every slider drag (the
 * EChart wrapper preserves the live zoom window across those rebuilds).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  pickDateTickGranularity,
  type DateTickGranularity,
  type ZoomWindowPct,
} from "./dateTicks";

export interface DateTickGranularityHandle {
  granularity: DateTickGranularity;
  /** Pass to BaseChart's onEvents (stable identity). */
  onEvents: Record<string, (params: unknown) => void>;
}

export function useDateTickGranularity(
  dates: string[],
  chartWidth: number | undefined,
): DateTickGranularityHandle {
  // Initial pick happens when the panel first renders — by then the page's
  // width measurement has landed (panels mount after their data fetch).
  const [granularity, setGranularity] = useState<DateTickGranularity>(() =>
    pickDateTickGranularity(dates, chartWidth),
  );
  // Latest inputs for the stable datazoom handler (no re-bind on drag).
  const datesRef = useRef(dates);
  datesRef.current = dates;
  const widthRef = useRef(chartWidth);
  widthRef.current = chartWidth;
  // Last seen zoom window — re-picks after a data/width change keep the
  // granularity consistent with the preserved zoom window.
  const zoomRef = useRef<ZoomWindowPct | undefined>(undefined);

  useEffect(() => {
    setGranularity(pickDateTickGranularity(dates, chartWidth, zoomRef.current));
  }, [dates, chartWidth]);

  const handleDataZoom = useCallback((params: unknown) => {
    // The event carries the new window either flat or in a batch (the
    // option pairs an inside + slider dataZoom; one changes at a time).
    const p = params as {
      start?: number;
      end?: number;
      batch?: Array<{ start?: number; end?: number }>;
    };
    const src = Array.isArray(p.batch) && p.batch.length > 0 ? p.batch[0] : p;
    const s = typeof src.start === "number" ? src.start : undefined;
    const e = typeof src.end === "number" ? src.end : undefined;
    if (s == null || e == null) return;
    zoomRef.current = { start: s, end: e };
    setGranularity((prev) => {
      const next = pickDateTickGranularity(
        datesRef.current,
        widthRef.current,
        zoomRef.current,
      );
      return next === prev ? prev : next;
    });
  }, []);

  const onEvents = useMemo(
    () => ({ datazoom: handleDataZoom }),
    [handleDataZoom],
  );

  return { granularity, onEvents };
}
