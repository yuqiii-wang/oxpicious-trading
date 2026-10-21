/**
 * useMaSpreadData — the MaSpreadPanel's entire data layer: the three
 * table-aligned chart blocks (loaded in PARALLEL on mount / code switch /
 * refresh), the composed response the option builders consume, and the
 * on-demand metric groups (fetch-through ensureMetric(s)).
 *
 * Chart blocks: pairs is the REQUIRED block (the default Price/MA5 chart,
 * the MA chips, and the shared date axis all come from it — its landing
 * ends `loading`); ema (the EMA detail table → EMA chip grid) and shared
 * (RSI + OHLC pivot + amt metrics → tooltip) are best-effort overlays
 * that merge into the composed response whenever they land: a failed
 * block just leaves its feature absent instead of failing the panel.
 *
 * On-demand groups (MovAveSpreadMetric → ExtrasState key, see
 * METRIC_STATE_KEY): the default chart load carries just the 18 pair
 * series + the shared tooltip metrics. The Amt/MA pairs, OHLC extrema,
 * streak rows, px-vol states, RSI ranks, and rebound audit load lazily —
 * picking a section's button fetches its group, and while ANY group is in
 * flight every control is disabled and the plot shows the shared spinner
 * (extrasLoading). The Momentum group is the exception: it AUTO-LOADS
 * with the blocks (the adaptive chips' lookback labels need it); a
 * failure leaves it unloaded — clicking a term retries through the
 * normal path.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  fetchMovAveSpreadChartPairs,
  fetchMovAveSpreadChartEma,
  fetchMovAveSpreadChartShared,
  fetchMovAveSpreadExtras,
  invalidateCacheForPrefix,
} from "@/lib/api-client";
import type {
  MovAveSpreadChartPairsResponse,
  MovAveSpreadChartResponse,
  MovAveSpreadMetric,
  MovAveSpreadPairSeries,
  MovAveSpreadSharedMetricsRow,
} from "@shared/types";
import type { MaSpreadSecType } from "@shared/types";
import type { ExtrasState } from "../types";

/**
 * The on-demand metric groups (MovAveSpreadMetric) → the ExtrasState key
 * they populate. Each group feeds one control section and is fetched the
 * first time one of its buttons is picked:
 *   amt → the 5 Amt/MA chips · ohlc + rfaudit → the Roof/Floor
 *   Trendlines row (extrema + the rebound audit) · rfest → the row's
 *   Est-levels toggle · streaks → the High/Low
 *   Streaks row · pxvol → the Px-Vol States row ·
 *   rsiranks → the RSI Trends section's pct row.
 */
const METRIC_STATE_KEY: Record<MovAveSpreadMetric, "amtPairs" | "ohlc" | "rfEstimates" | "rfRebounds" | "highLowStreaks" | "priceVsAmt" | "ohlcGaps" | "rsiRanks" | "momentum"> = {
  amt: "amtPairs",
  ohlc: "ohlc",
  rfest: "rfEstimates",
  rfaudit: "rfRebounds",
  streaks: "highLowStreaks",
  pxvol: "priceVsAmt",
  gaps: "ohlcGaps",
  rsiranks: "rsiRanks",
  momentum: "momentum",
};

/** Called the moment the REQUIRED pairs block lands (loading flips off) —
 *  the panel resets its pair/anchor selections there. */
export type OnPairsLanded = () => void;

export function useMaSpreadData(
  code: string,
  secType: MaSpreadSecType,
  onPairsLanded: OnPairsLanded,
) {
  const [pairsBlock, setPairsBlock] = useState<MovAveSpreadChartPairsResponse | null>(null);
  const [emaPairs, setEmaPairs] = useState<MovAveSpreadPairSeries[] | null>(null);
  const [sharedRows, setSharedRows] = useState<MovAveSpreadSharedMetricsRow[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // The composed response the option builders consume — identical shape to
  // the monolithic GET /chart payload (9 MA pairs first, then the EMA pairs
  // once its block lands; shared defaults to [] until its block lands).
  const chartData = useMemo<MovAveSpreadChartResponse | null>(
    () =>
      pairsBlock
        ? {
            code: pairsBlock.code,
            name: pairsBlock.name,
            pairs: [...pairsBlock.pairs, ...(emaPairs ?? [])],
            shared: sharedRows ?? [],
          }
        : null,
    [pairsBlock, emaPairs, sharedRows],
  );

  // Bumped by the per-security AnalysisRunButton after a rebuild run —
  // retriggers the chart fetch (the cache entry is invalidated first in
  // the completion handler).
  const [refreshKey, setRefreshKey] = useState(0);

  const [extras, setExtras] = useState<ExtrasState>({});
  const [loadingMetrics, setLoadingMetrics] = useState<MovAveSpreadMetric[]>([]);
  const [metricError, setMetricError] = useState<string | null>(null);
  const extrasLoading = loadingMetrics.length > 0;

  // Keep the landing callback fresh without re-running the fetch effect
  // (the effect's deps stay [code, secType, refreshKey]).
  const onPairsLandedRef = useRef(onPairsLanded);
  useEffect(() => {
    onPairsLandedRef.current = onPairsLanded;
  });

  // Refetch after a per-security analysis rebuild (AnalysisRunButton):
  // drop the cached chart + extras responses, then bump the refresh key.
  // The chart prefix covers all three block URLs (pairs/ema/shared) plus
  // the legacy monolithic chart endpoint.
  const handleAnalysisRunCompleted = useCallback(() => {
    invalidateCacheForPrefix("/api/analysis/mov-ave-spread/chart");
    invalidateCacheForPrefix("/api/analysis/mov-ave-spread/extras");
    setRefreshKey((k) => k + 1);
  }, []);

  // Fetch the not-yet-loaded subset of metric groups in ONE /extras call;
  // resolves after they land so callers chain their button's state change
  // onto it.
  const ensureMetrics = useCallback(
    (metrics: MovAveSpreadMetric[]): Promise<void> => {
      const missing = metrics.filter((m) => extras[METRIC_STATE_KEY[m]] == null);
      if (missing.length === 0) return Promise.resolve();
      setLoadingMetrics((prev) => [
        ...prev,
        ...missing.filter((m) => !prev.includes(m)),
      ]);
      setMetricError(null);
      return fetchMovAveSpreadExtras(code, secType, missing)
        .then((d) => {
          setExtras((prev) => {
            const next: ExtrasState = { ...prev };
            const src = d as unknown as Record<string, unknown>;
            const dst = next as unknown as Record<string, unknown>;
            for (const m of missing) {
              dst[METRIC_STATE_KEY[m]] = src[METRIC_STATE_KEY[m]];
            }
            return next;
          });
        })
        .catch((e: Error) => {
          setMetricError(e.message);
        })
        .finally(() => {
          setLoadingMetrics((prev) => prev.filter((m) => !missing.includes(m)));
        });
    },
    [extras, code, secType],
  );

  const ensureMetric = useCallback(
    (metric: MovAveSpreadMetric): Promise<void> => ensureMetrics([metric]),
    [ensureMetrics],
  );

  // Fetch the chart blocks on mount and whenever the code/sec_type changes.
  // All three blocks fire AT ONCE (parallel-then-render): the panel renders
  // as soon as the REQUIRED pairs block lands, while the EMA chips and the
  // tooltip metrics appear when their blocks settle. The on-demand metric
  // groups reset with it — a new code starts from the blocks-only load.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setPairsBlock(null);
    setEmaPairs(null);
    setSharedRows(null);
    setExtras({});
    setLoadingMetrics([]);
    setMetricError(null);
    fetchMovAveSpreadChartPairs(code, secType)
      .then((d) => {
        if (cancelled) return;
        setPairsBlock(d);
        setLoading(false);
        onPairsLandedRef.current();
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setError(e.message);
        setLoading(false);
      });
    fetchMovAveSpreadChartEma(code, secType)
      .then((d) => {
        if (!cancelled) setEmaPairs(d.pairs);
      })
      .catch((e) => {
        console.warn(`[ma-spread] ema block failed for ${code} — EMA chips hidden:`, e);
      });
    fetchMovAveSpreadChartShared(code, secType)
      .then((d) => {
        if (!cancelled) setSharedRows(d.shared);
      })
      .catch((e) => {
        console.warn(`[ma-spread] shared block failed for ${code} — tooltip metrics hidden:`, e);
      });
    // The Momentum section's group auto-loads with the blocks (NOT
    // pick-gated like the other groups): the adaptive short/mid/long
    // chips spell their CURRENT effective lookbacks (latest day), known
    // only once the rows land. Same loading/freeze machinery as
    // ensureMetric; a failure leaves the group unloaded — clicking a
    // term retries through the normal path.
    setLoadingMetrics((prev) =>
      prev.includes("momentum") ? prev : [...prev, "momentum"],
    );
    fetchMovAveSpreadExtras(code, secType, ["momentum"])
      .then((d) => {
        if (!cancelled) setExtras((prev) => ({ ...prev, momentum: d.momentum }));
      })
      .catch((e: Error) => {
        if (!cancelled) setMetricError(e.message);
      })
      .finally(() => {
        if (!cancelled)
          setLoadingMetrics((prev) => prev.filter((m) => m !== "momentum"));
      });
    return () => {
      cancelled = true;
    };
  }, [code, secType, refreshKey]);

  return {
    chartData,
    loading,
    error,
    extras,
    loadingMetrics,
    extrasLoading,
    metricError,
    ensureMetric,
    ensureMetrics,
    handleAnalysisRunCompleted,
  };
}
