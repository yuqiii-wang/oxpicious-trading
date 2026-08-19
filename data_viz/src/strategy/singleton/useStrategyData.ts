/**
 * Custom hook for managing singleton strategy data loading.
 *
 * Handles:
 *  - Loading PARENT backtest + risk + forecast results from DB
 *  - Switching between forecast scenarios (fast path — only fetches forecast decisions + risks)
 *  - Auto-selecting the default forecast scenario once data loads
 *  - Truncating backtest OHLC to forecast_date for chart alignment
 *  - `withForecast=false` (Forecast unticked) — skips forecast entirely and
 *    shows the parent actual-only run (which, when the last run used
 *    --no-forecast, ends with a FINAL LIQUIDATION sell of all remaining units)
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  fetchSingletonBacktest,
  fetchSingletonRisks,
  fetchSingletonForecast1m,
  fetchForecastScenarioDecisions,
  invalidateCacheForPrefix,
  type CheckExistingResult,
  type StrategySelection,
} from "@/lib/api-client";
import type {
  MaSpreadSecType,
  StrategyBacktestResponse,
  StrategyRiskResponse,
  StrategyForecast1mResponse,
} from "@shared/types";
import type { SelectedPeriod } from "../singletonStrategyChartOption";

const DEFAULT_SCENARIO = "flip_255d_std_scale";

export interface StrategyDataState {
  backtest: StrategyBacktestResponse | null;
  risks: StrategyRiskResponse | null;
  forecast: StrategyForecast1mResponse | null;
  loading: boolean;
  error: string | null;
  selectedPeriod: SelectedPeriod | null;
  selectedScenario: string | null;
  forecastScenarios: string[];
  displayBacktest: StrategyBacktestResponse | null;
  parentBacktestRef: React.MutableRefObject<StrategyBacktestResponse | null>;
  backtestRef: React.MutableRefObject<StrategyBacktestResponse | null>;
  forecastRef: React.MutableRefObject<StrategyForecast1mResponse | null>;
  hoveredScenarioRef: React.MutableRefObject<string | null>;
  setSelectedPeriod: (p: SelectedPeriod | null) => void;
  setSelectedScenario: (s: string | null) => void;
}

export interface StrategyDataControls {
  reset: () => void;
  /** Invalidate strategy caches + refetch everything for (code, secType)
   *  — used after a REMOTE process (process-id-tag poll) finishes. */
  reload: (code: string, secType: MaSpreadSecType) => void;
}

export function useStrategyData(
  searchCode: string | null,
  secType: MaSpreadSecType,
  selection: StrategySelection,
  faultTolerance: number,
  withForecast: boolean,
): StrategyDataState & StrategyDataControls {
  const [backtest, setBacktest] = useState<StrategyBacktestResponse | null>(null);
  const [risks, setRisks] = useState<StrategyRiskResponse | null>(null);
  const [forecast, setForecast] = useState<StrategyForecast1mResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [parentBacktest, setParentBacktest] = useState<StrategyBacktestResponse | null>(null);
  const [selectedPeriod, setSelectedPeriod] = useState<SelectedPeriod | null>(null);
  const [selectedScenario, setSelectedScenario] = useState<string | null>(null);
  const autoSelectJustHappenedRef = useRef<boolean>(false);

  const backtestRef = useRef<StrategyBacktestResponse | null>(null);
  const forecastRef = useRef<StrategyForecast1mResponse | null>(null);
  const hoveredScenarioRef = useRef<string | null>(null);

  // Truncated backtest for chart display — only truncate to forecast_date
  // when a forecast scenario is actively selected. Without a scenario,
  // show the full backtest data (no truncation).
  const displayBacktest = useMemo(() => {
    if (!backtest) return null;
    // Only truncate when a forecast scenario is selected
    if (!selectedScenario) return backtest;
    if (!forecast || forecast.rows.length === 0 || !forecast.stats?.forecast_date) return backtest;
    const fcDate = forecast.stats.forecast_date;
    const idx = backtest.ohlc.findIndex((r) => r.date === fcDate);
    if (idx < 0 || idx >= backtest.ohlc.length - 1) return backtest;
    return { ...backtest, ohlc: backtest.ohlc.slice(0, idx + 1) };
  }, [backtest, forecast, selectedScenario]);

  useEffect(() => { backtestRef.current = displayBacktest; }, [displayBacktest]);
  useEffect(() => { forecastRef.current = forecast; }, [forecast]);

  const forecastScenarios = useMemo(() => {
    if (!forecast || forecast.rows.length === 0) return [];
    const seen = new Set<string>();
    const result: string[] = [];
    for (const r of forecast.rows) {
      if (r.scenario !== "mean" && !seen.has(r.scenario)) {
        seen.add(r.scenario);
        result.push(r.scenario);
      }
    }
    return result;
  }, [forecast]);

  const loadParentFromDb = useCallback((code: string, secType: MaSpreadSecType) => {
    invalidateCacheForPrefix("/api/strategy/singleton/backtest");
    invalidateCacheForPrefix("/api/strategy/singleton/risks");
    invalidateCacheForPrefix("/api/strategy/singleton/forecast-decisions");
    setLoading(true);
    setError(null);
    Promise.all([
      fetchSingletonBacktest(code, secType, null, selection, faultTolerance),
      fetchSingletonRisks(code, secType, null, selection, faultTolerance),
      // Forecast is only fetched when the Forecast tick is on. Unticked →
      // actual-only view (the parent run; with --no-forecast it ends with
      // a FINAL LIQUIDATION sell of all remaining units on the last day).
      withForecast
        ? fetchSingletonForecast1m(code, secType, selection, faultTolerance)
        : Promise.resolve(null),
    ])
      .then(([bt, rk, fc]) => {
        // Compute forecast scenarios from the rows
        const forecastRows = fc?.rows ?? [];
        const scenariosList: string[] = [];
        const seen = new Set<string>();
        for (const r of forecastRows) {
          if (r.scenario !== "mean" && !seen.has(r.scenario)) {
            seen.add(r.scenario);
            scenariosList.push(r.scenario);
          }
        }

        // ALWAYS auto-select the default scenario on a full (re)load with
        // Forecast ticked — initial load, reload after a re-run, and
        // algo/FT selection changes — so the summary consistently reflects
        // the default forecast scenario (e.g. flip_255d_std_scale), never
        // silently reverting to the parent run's summary. With Forecast
        // unticked, no scenario is selected (actual-only view).
        let targetScenario: string | null = null;
        if (withForecast && scenariosList.length > 0) {
          autoSelectJustHappenedRef.current = true;
          targetScenario = scenariosList.includes(DEFAULT_SCENARIO)
            ? DEFAULT_SCENARIO
            : scenariosList[0];
        }

        // Set all parent data + forecast + (optionally) selected scenario
        setBacktest(bt);
        setParentBacktest(bt);
        setRisks(rk);
        setForecast(fc);
        if (targetScenario) {
          setSelectedScenario(targetScenario);
          // Chain the scenario load into the SAME Promise chain so that
          // loading stays true until ALL data (parent + scenario) is loaded.
          // This avoids a flicker where the chart briefly shows partial data.
          return loadScenarioForecastImpl(code, secType, targetScenario, true);
        }
        return null;
      })
      .catch((e) => {
        setError(String(e instanceof Error ? e.message : e));
      })
      .finally(() => setLoading(false));
  }, [selection, faultTolerance, withForecast]);

  // Internal implementation of scenario loading.
  // When called from loadParentFromDb (skipLoading=true), the caller
  // manages the loading state to avoid flicker.
  const loadScenarioForecastImpl = useCallback((
    code: string, secType: MaSpreadSecType, scenario: string, skipLoading: boolean = false,
  ) => {
    if (!skipLoading) {
      setLoading(true);
      setError(null);
    }
    return Promise.all([
      fetchForecastScenarioDecisions(code, secType, scenario, selection, faultTolerance),
      fetchSingletonRisks(code, secType, scenario, selection, faultTolerance),
    ])
      .then(([fcResp, rk]) => {
        setRisks(rk);
        setBacktest((prev) => {
          if (!prev) return prev;
          const actualDecisions = prev.decisions.filter(
            (d) => !d.signal_reason?.startsWith("FORECAST SELL"),
          );
          return {
            ...prev,
            decisions: [...actualDecisions, ...fcResp.forecast_decisions],
            summary: fcResp.summary,
          };
        });
      })
      .catch((e) => {
        const msg = String(e instanceof Error ? e.message : e);
        if (msg.includes("404")) {
          setSelectedScenario(null);
        } else {
          setError(msg);
        }
      })
      .finally(() => {
        if (!skipLoading) {
          setLoading(false);
        }
      });
  }, [selection, faultTolerance]);

  // Public API for scenario loading (used for manual scenario changes).
  // Manages loading state independently.
  const loadScenarioForecast = useCallback((code: string, secType: MaSpreadSecType, scenario: string) => {
    return loadScenarioForecastImpl(code, secType, scenario, false);
  }, [loadScenarioForecastImpl]);

  // Reset state
  const reset = useCallback(() => {
    setBacktest(null);
    setRisks(null);
    setForecast(null);
    setParentBacktest(null);
    setError(null);
    setSelectedPeriod(null);
    setSelectedScenario(null);
    autoSelectJustHappenedRef.current = false;
  }, []);

  // Auto-load when code / selection / fault tolerance / Forecast tick
  // change — a full reload re-auto-selects the default forecast scenario
  // (Forecast on) or shows the actual-only parent run (Forecast off).
  useEffect(() => {
    if (!searchCode) {
      reset();
      return;
    }
    setSelectedPeriod(null);
    setSelectedScenario(null);
    setForecast(null);
    autoSelectJustHappenedRef.current = false;
    loadParentFromDb(searchCode, secType);
  }, [searchCode, secType, loadParentFromDb, reset]);

  // Handle scenario changes (manual selection or dependency-driven reload).
  // When loadParentFromDb already handled the auto-select + scenario load,
  // autoSelectJustHappenedRef skips here to avoid a duplicate load.
  useEffect(() => {
    if (!searchCode) return;
    if (autoSelectJustHappenedRef.current) {
      autoSelectJustHappenedRef.current = false;
      return;
    }
    if (selectedScenario === null) {
      if (parentBacktest) {
        setBacktest(parentBacktest);
      }
      fetchSingletonRisks(searchCode, secType, null, selection, faultTolerance)
        .then((rk) => setRisks(rk))
        .catch((e) => setError(String(e instanceof Error ? e.message : e)));
    } else {
      loadScenarioForecast(searchCode, secType, selectedScenario);
    }
  }, [selectedScenario, selection, faultTolerance, loadScenarioForecast, parentBacktest]);

  return {
    backtest,
    risks,
    forecast,
    loading,
    error,
    selectedPeriod,
    selectedScenario,
    forecastScenarios,
    displayBacktest,
    parentBacktestRef: { current: parentBacktest },
    backtestRef,
    forecastRef,
    hoveredScenarioRef,
    setSelectedPeriod,
    setSelectedScenario,
    reset,
    /** Invalidate strategy caches + refetch backtest/risks/forecast for
     *  the current code — used after a REMOTE process (detected via the
     *  process-id-tag status poll) finishes writing to the DB. */
    reload: loadParentFromDb,
  };
}
