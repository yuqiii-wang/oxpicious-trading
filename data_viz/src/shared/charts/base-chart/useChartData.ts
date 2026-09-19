/**
 * Shared fetch lifecycle for self-fetching chart components — the
 * loading / error / data triple plus a refresh counter, generalizing the
 * pattern hand-rolled in CompositionPieChart, MarketTrendChart,
 * CodeTrendChart (monotonic loadSeq), and friends.
 *
 * Stale responses are discarded: each effect run owns a `cancelled` flag its
 * cleanup sets, so a slow earlier request can never overwrite the result of a
 * later one. The fetcher is read through a ref — inline arrow fetchers don't
 * re-trigger the effect on every render; only `deps` and `refresh()` do.
 */
import { useCallback, useEffect, useRef, useState } from "react";

export interface ChartDataState<T> {
  /** Last successful fetch result; null until the first success. */
  data: T | null;
  /** True from mount until the first (or current) fetch settles. */
  loading: boolean;
  /** Error message of the latest failed fetch; null when healthy. */
  error: string | null;
  /** Re-run the fetcher (e.g. after cache invalidation). */
  refresh: () => void;
}

/**
 * @param fetcher Called (with no arguments) whenever `deps` or the internal
 *                refresh counter change. Its identity is NOT a dependency.
 * @param deps    Values the fetch depends on (codes, dates, toggles).
 */
export function useChartData<T>(
  fetcher: () => Promise<T>,
  deps: readonly unknown[],
): ChartDataState<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshSeq, setRefreshSeq] = useState(0);

  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetcherRef
      .current()
      .then((result) => {
        if (cancelled) return;
        setData(result);
        setLoading(false);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : String(e));
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // The dependency list is caller-controlled via `deps`; the fetcher is
    // deliberately read through a ref so its identity never re-triggers.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, refreshSeq]);

  const refresh = useCallback(() => setRefreshSeq((s) => s + 1), []);

  return { data, loading, error, refresh };
}
