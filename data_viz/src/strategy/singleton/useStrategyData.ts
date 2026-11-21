/**
 * Custom hook for managing singleton strategy data loading.
 *
 * Handles:
 *  - Loading backtest + risk results from DB
 *  - Run-history list + the selected historical run (seq_id); a null
 *    selection means the latest/active run
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  fetchSingletonBacktest,
  fetchSingletonRisks,
  fetchSingletonRunHistory,
  invalidateCacheForPrefix,
  type StrategySelection,
} from "@/lib/api-client";
import type {
  MaSpreadSecType,
  RunHistoryItem,
  StrategyBacktestResponse,
  StrategyRiskResponse,
} from "@shared/types";
import type { SelectedPeriod } from "../_common/types";

export interface StrategyDataState {
  backtest: StrategyBacktestResponse | null;
  risks: StrategyRiskResponse | null;
  loading: boolean;
  error: string | null;
  selectedPeriod: SelectedPeriod | null;
  displayBacktest: StrategyBacktestResponse | null;
  backtestRef: React.MutableRefObject<StrategyBacktestResponse | null>;
  setSelectedPeriod: (p: SelectedPeriod | null) => void;
  /** Recent runs for the current identity (newest first). */
  runHistory: RunHistoryItem[];
  /** The displayed run's seq_id (null = the latest/active run). */
  selectedSeqId: number | null;
}

export interface StrategyDataControls {
  reset: () => void;
  /** Invalidate strategy caches + refetch everything for (code, secType)
   *   — used after a REMOTE process (process-id-tag poll) finishes.
   *   Resets the run selection (a finished run produces a NEW latest run). */
  reload: (code: string, secType: MaSpreadSecType) => void;
  /** Display a historical run (seq_id) or the latest (null). */
  selectRun: (seqId: number | null) => void;
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
  const [runHistory, setRunHistory] = useState<RunHistoryItem[]>([]);
  const [selectedSeqId, setSelectedSeqId] = useState<number | null>(null);

  // The selected seq rides a REF so loadFromDb stays identity-stable
  // w.r.t. it (no effect refetch loops); state mirrors it for the UI.
  const seqRef = useRef<number | null>(null);
  // The (code, secType) identity last loaded — a change resets the run
  // selection (history is per identity).
  const identityRef = useRef<string | null>(null);

  const backtestRef = useState<React.MutableRefObject<StrategyBacktestResponse | null>>(
    { current: null },
  )[0];

  const displayBacktest = backtest;

  useEffect(() => { backtestRef.current = displayBacktest; }, [displayBacktest]);

  const loadFromDb = useCallback((code: string, secType: MaSpreadSecType) => {
    invalidateCacheForPrefix("/api/strategy/singleton/backtest");
    invalidateCacheForPrefix("/api/strategy/singleton/risks");
    invalidateCacheForPrefix("/api/strategy/singleton/run-history");
    setLoading(true);
    setError(null);
    const seqId = seqRef.current;
    Promise.all([
      fetchSingletonBacktest(code, secType, selection, faultTolerance, seqId),
      fetchSingletonRisks(code, secType, selection, faultTolerance, seqId),
      fetchSingletonRunHistory(code, secType, selection, faultTolerance),
    ])
      .then(([bt, rk, hist]) => {
        setBacktest(bt);
        setRisks(rk);
        setRunHistory(hist.runs ?? []);
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
    setRunHistory([]);
    seqRef.current = null;
    setSelectedSeqId(null);
    identityRef.current = null;
  }, []);

  useEffect(() => {
    if (!searchCode) {
      reset();
      return;
    }
    setSelectedPeriod(null);
    // A (code, secType) change invalidates the run selection — history is
    // per identity; the selection state mirrors via a plain set (the fetch
    // itself reads seqRef, already reset here).
    if (identityRef.current !== `${searchCode}|${secType}`) {
      identityRef.current = `${searchCode}|${secType}`;
      seqRef.current = null;
      setSelectedSeqId(null);
    }
    loadFromDb(searchCode, secType);
  }, [searchCode, secType, loadFromDb, reset]);

  const selectRun = useCallback((seqId: number | null) => {
    seqRef.current = seqId;
    setSelectedSeqId(seqId);
    if (searchCode) loadFromDb(searchCode, secType);
  }, [loadFromDb, searchCode, secType]);

  const reload = useCallback((code: string, secType: MaSpreadSecType) => {
    // A finished run produces a NEW latest run — drop any historical
    // selection so the fresh run is what displays.
    seqRef.current = null;
    setSelectedSeqId(null);
    loadFromDb(code, secType);
  }, [loadFromDb]);

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
    reload,
    selectRun,
    runHistory,
    selectedSeqId,
  };
}
