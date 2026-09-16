/**
 * Chart ↔ date-strip sync for the Market Sentiments by AI and News page
 * (the DataViz AI page's idiom). The strip has NO slider of its own — its
 * VISIBLE date window IS the benchmark chart's visible window (same start,
 * end, and length): BenchmarkPriceChart reports its visible [start, end]
 * via onVisibleRangeChange and the strip pins its axis to exactly that
 * range, so dragging the benchmark chart's slider re-windows the dots in
 * lockstep. Sync runs BOTH ways: clicking a dot sends focusDateRequest,
 * the chart's dataZoom window jumps to the event date, and the strip
 * re-windows via the report above.
 */
import { useCallback, useEffect, useState } from "react";

interface ChartRange {
  start: string;
  end: string;
}

export function useChartStripSync(
  benchmarkCode: string | null,
  mode: "market" | "industry",
): {
  chartRange: ChartRange | null;
  /** The FULL row span from onLoaded — bridges the moment before the first
   *  range report lands (null for an empty load). */
  chartSpan: ChartRange | null;
  /** Pending focus jump for the benchmark chart (set by strip-dot picks). */
  focusReq: { date: string; seq: number } | null;
  setFocusReq: React.Dispatch<React.SetStateAction<{ date: string; seq: number } | null>>;
  handleChartRange: (r: ChartRange | null) => void;
  handleChartLoaded: (d: { rows: Array<{ date: string }> } | null) => void;
} {
  const [chartRange, setChartRange] = useState<ChartRange | null>(null);
  const [chartSpan, setChartSpan] = useState<ChartRange | null>(null);
  const [focusReq, setFocusReq] = useState<{ date: string; seq: number } | null>(null);
  const handleChartRange = useCallback(
    (r: ChartRange | null) => setChartRange(r),
    [],
  );
  const handleChartLoaded = useCallback(
    (d: { rows: Array<{ date: string }> } | null) => {
      const rows = d?.rows ?? [];
      setChartSpan(rows.length > 0
        ? { start: rows[0].date, end: rows[rows.length - 1].date }
        : null);
    },
    [],
  );
  // A benchmark switch re-anchors the chart — drop the stale pins/request
  // so the strip falls back to the calendar until the new chart reports.
  useEffect(() => {
    setChartRange(null);
    setChartSpan(null);
    setFocusReq(null);
  }, [benchmarkCode]);
  // A mode switch re-anchors the strip too: in market mode there is no
  // benchmark chart to report a visible window or take focus jumps, so the
  // strip falls back to its own calendar range + time slider.
  useEffect(() => {
    setChartRange(null);
    setChartSpan(null);
    setFocusReq(null);
  }, [mode]);

  return { chartRange, chartSpan, focusReq, setFocusReq, handleChartRange, handleChartLoaded };
}
