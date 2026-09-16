/**
 * Refresh (filtered recompute) for the Opposite Industry Correlations page
 * — mirrors CorrelationChart: the Refresh button runs
 * `python -m analyze.analysis_composites --industry ... --benchmark ...`
 * (recompute + upsert) via the API, a 3s run-tag poll keeps the spinner in
 * step (and restores it on mount if a run started elsewhere), and
 * tryAutoRun triggers the same recompute ONCE per selection key when a
 * fresh selection has no materialized rows.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  fetchAnalysisRunStatus,
  runIndustryCorrOffsetsRefresh,
  INDUSTRY_CORR_OFFSET_RUN_TAG,
  invalidateCacheForPrefix,
} from "@/lib/api-client";

export function useOffsetCorrRefresh(
  selectedIds: string[],
  benchmark: string,
): {
  refreshing: boolean;
  refreshError: string | null;
  /** Bumped after a run finishes — the page's data effect re-fetches. */
  refreshTick: number;
  handleRefresh: () => Promise<void>;
  /** Auto-trigger the recompute once per key (a fresh selection with no
   *  materialized rows). */
  tryAutoRun: (key: string) => void;
} {
  const [refreshing, setRefreshing] = useState(false);
  const [refreshTick, setRefreshTick] = useState(0);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const autoRunKeyRef = useRef<string | null>(null);
  const selectedIdsRef = useRef(selectedIds);
  const benchmarkRef = useRef(benchmark);
  selectedIdsRef.current = selectedIds;
  benchmarkRef.current = benchmark;
  const wasRunningRef = useRef(false);

  // Poll the run tag while refreshing — also on mount once, so a run
  // started elsewhere restores the spinner.
  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      try {
        const st = await fetchAnalysisRunStatus([INDUSTRY_CORR_OFFSET_RUN_TAG]);
        if (cancelled) return;
        const running = Boolean(st[INDUSTRY_CORR_OFFSET_RUN_TAG]);
        if (wasRunningRef.current && !running) {
          invalidateCacheForPrefix("/api/analysis/industry-corr-offsets");
          setRefreshTick((n) => n + 1);
        }
        wasRunningRef.current = running;
        setRefreshing(running);
      } catch {
        /* status is best-effort */
      }
    };
    void poll();
    if (!refreshing) return;
    const t = setInterval(() => { void poll(); }, 3000);
    return () => { cancelled = true; clearInterval(t); };
  }, [refreshing]);

  const handleRefresh = useCallback(async () => {
    setRefreshError(null);
    setRefreshing(true);
    wasRunningRef.current = true;
    const result = await runIndustryCorrOffsetsRefresh(
      selectedIdsRef.current,
      benchmarkRef.current,
    );
    if (result.already_running) return;
    setRefreshing(false);
    wasRunningRef.current = false;
    if (!result.success) {
      setRefreshError(
        `Offset-corr recompute failed${result.stderr_tail ? `: ${result.stderr_tail.slice(-300)}` : ""}`,
      );
      return;
    }
    invalidateCacheForPrefix("/api/analysis/industry-corr-offsets");
    setRefreshTick((n) => n + 1);
  }, []);

  const tryAutoRun = useCallback(
    (key: string) => {
      if (autoRunKeyRef.current !== key) {
        autoRunKeyRef.current = key;
        void handleRefresh();
      }
    },
    [handleRefresh],
  );

  return { refreshing, refreshError, refreshTick, handleRefresh, tryAutoRun };
}
