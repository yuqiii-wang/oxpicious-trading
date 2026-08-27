/**
 * Custom hook for managing singleton strategy data loading.
 *
 * Handles:
 *  - Loading backtest + risk results from DB
 */
import { useCallback, useEffect, useState } from "react";
import {
  fetchSingletonBacktest,
  fetchSingletonRisks,
  invalidateCacheForPrefix,
  type CheckExistingResult,
  type StrategySelection,
} from "@/lib/api-client";
import type {
  MaSpreadSecType,
  StrategyBacktestResponse,
  StrategyRiskResponse,
} from "@shared/types";
import type { SelectedPeriod } from "../singletonStrategyChartOption";

export interface StrategyDataState {
  backtest: StrategyBacktestResponse | null;
  risks: StrategyRiskResponse | null;
  loading: boolean;
  error: string | null;
  selectedPeriod: SelectedPeriod | null;
  displayBacktest: StrategyBacktestResponse | null;
  backtestRef: React.MutableRefObject<StrategyBacktestResponse | null>;
  setSelectedPeriod: (p: SelectedPeriod | null) => void;
}

export interface StrategyDataControls {
  reset: () => void;
  /** Invalidate strategy caches + refetch everything for (code, secType)
   *   — used after a REMOTE process (process-id-tag poll) finishes. */
  reload: (code: string, secType: MaSpreadSecType) => void;
}

export function useStrategyData(
  searchCode: string | null,
  secType: MaSpreadSecType,
  selection: StrategySelection,
  faultTolerance: number,
): StrategyDataState & StrategyDataControls {
  const [backtest, setBacktest] = useState<StrategyBacktestResponse | null>(null);
  const [risks, setRisks] = useState<StrategyRiskResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedPeriod, setSelectedPeriod] = useState<SelectedPeriod | null>(null);

  const backtestRef = useState<React.MutableRefObject<StrategyBacktestResponse | null>>(
    { current: null },
  )[0];

  const displayBacktest = backtest;

  useEffect(() => { backtestRef.current = displayBacktest; }, [displayBacktest]);

  const loadFromDb = useCallback((code: string, secType: MaSpreadSecType) => {
    invalidateCacheForPrefix("/api/strategy/singleton/backtest");
    invalidateCacheForPrefix("/api/strategy/singleton/risks");
    setLoading(true);
    setError(null);
    Promise.all([
      fetchSingletonBacktest(code, secType, selection, faultTolerance),
      fetchSingletonRisks(code, secType, selection, faultTolerance),
    ])
      .then(([bt, rk]) => {
        setBacktest(bt);
        setRisks(rk);
      })
      .catch((e) => {
        setError(String(e instanceof Error ? e.message : e));
      })
      .finally(() => setLoading(false));
  }, [selection, faultTolerance]);

  const reset = useCallback(() => {
    setBacktest(null);
    setRisks(null);
    setError(null);
    setSelectedPeriod(null);
  }, []);

  useEffect(() => {
    if (!searchCode) {
      reset();
      return;
    }
    setSelectedPeriod(null);
    loadFromDb(searchCode, secType);
  }, [searchCode, secType, loadFromDb, reset]);

  return {
    backtest,
    risks,
    loading,
    error,
    selectedPeriod,
    displayBacktest,
    backtestRef,
    setSelectedPeriod,
    reset,
    reload: loadFromDb,
  };
}
