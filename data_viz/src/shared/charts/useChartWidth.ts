/**
 * useChartWidth — measured content width of a container element, kept
 * current with a ResizeObserver. Used by the time-slider chart families to
 * feed the width-aware date-tick granularity (dateTicks.pickDateTickGranularity):
 * one observer per page, the measured width flows down to every panel.
 */
import { useEffect, useState } from "react";
import type { RefObject } from "react";

export function useChartWidth(ref: RefObject<HTMLElement | null>): number | undefined {
  const [width, setWidth] = useState<number | undefined>(undefined);

  useEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width;
      if (w != null && w > 0) setWidth(Math.round(w));
    });
    ro.observe(el);
    setWidth(Math.round(el.clientWidth));
    return () => ro.disconnect();
  }, [ref]);

  return width;
}
