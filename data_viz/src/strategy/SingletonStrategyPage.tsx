/**
 * Singleton Strategy page — displays pre-computed backtest results.
 *
 * The backtest itself is run by the Python package `strategy.singleton_trading`
 * (python -m strategy.singleton_trading --sec-type index --codes <code>),
 * which writes results to the strategy schema. This page reads those results
 * via the API and renders the chart + B/S markers + decision table.
 *
 * Risk metrics (computed by `python -m strategy._risks`) are loaded in
 * parallel and shown in an expandable RiskPanel below the chart.
 *
 * The "Run Strategy" button spawns the Python backtest + risk computation
 * via the backend (POST /api/strategy/singleton/run). When the process
 * exits, the page invalidates cache and reloads from DB.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  CircularProgress,
  Stack,
} from "@mui/material";
import EChart from "@/components/EChart";
import { useStore } from "@/store/filters";
import {
  checkExistingStrategy,
  runSingletonStrategy,
  trainStrategyModel,
  fetchStrategyProcessStatus,
  singletonRunTag,
  singletonTrainTag,
  DEFAULT_STRATEGY_SELECTION,
  type CheckExistingResult,
  type StrategySelection,
} from "@/lib/api-client";
import { buildSingletonStrategyOption } from "./singletonStrategyChartOption";
import {
  StrategyPageShell,
  useStrategyNav,
  SummaryChip,
  DecisionTable,
  RiskPanel,
} from "./_common";
import {
  useStrategyData,
  useChartInteractions,
  RunControls,
  PkConfirmModal,
} from "./singleton";

export default function SingletonStrategyPage() {
  const themeMode = useStore((s) => s.themeMode);

  // Per-algo weight selection + fault tolerance
  const [selection, setSelection] = useState<StrategySelection>(DEFAULT_STRATEGY_SELECTION);
  const [faultTolerance, setFaultTolerance] = useState(10);

  // Run Strategy state
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);
  const [runSuccess, setRunSuccess] = useState(false);
  // PK-exists confirmation modal state
  const [checkingPk, setCheckingPk] = useState(false);
  const [pkModalOpen, setPkModalOpen] = useState(false);
  const [existingRun, setExistingRun] = useState<CheckExistingResult | null>(null);
  // Forecast toggle for Run Strategy
  const [runWithForecast, setRunWithForecast] = useState(true);

  // Train Model state (Optuna _optm_engine)
  const [training, setTraining] = useState(false);
  const [trainError, setTrainError] = useState<string | null>(null);
  const [trainSuccess, setTrainSuccess] = useState(false);

  // Remote-process state (process-id-tag registry): a Run/Train process
  // for the CURRENT (code, secType, selection, ft) may already be running
  // from before a page refresh (or another tab). While its tag is held,
  // the buttons spin and an info note is shown; when it exits, data is
  // reloaded from the DB automatically.
  const [remoteRunning, setRemoteRunning] = useState(false);
  const [remoteTraining, setRemoteTraining] = useState(false);
  const [remoteNote, setRemoteNote] = useState<string | null>(null);

  // Shared classification nav
  const nav = useStrategyNav((code) => {
    if (!code) {
      // Reset handled by useStrategyData
    }
  });

  // Data loading and state management
  const {
    backtest,
    risks,
    forecast,
    loading,
    error,
    selectedPeriod,
    selectedScenario,
    forecastScenarios,
    displayBacktest,
    backtestRef,
    forecastRef,
    hoveredScenarioRef,
    setSelectedPeriod,
    setSelectedScenario,
    reload,
  } = useStrategyData(
    nav.searchCode, nav.secType, selection, faultTolerance, runWithForecast,
  );

  // ---- Remote-process recovery (process-id-tag status poll) --------------
  // Poll the running-state of the CURRENT identity's run/train tags on
  // mount and every 5s while one is held. Covers: page refresh mid-run,
  // duplicate clicks (deduped server-side), and processes started in
  // another tab. When a remote process exits, reload data from the DB.
  const runTag = nav.searchCode
    ? singletonRunTag(nav.searchCode, nav.secType, selection, faultTolerance)
    : "";
  const trainTag = nav.searchCode
    ? singletonTrainTag(nav.searchCode, nav.secType, selection)
    : "";
  useEffect(() => {
    if (!runTag || !trainTag) return;
    let cancelled = false;
    let wasRun = false;
    let wasTrain = false;
    const poll = async () => {
      try {
        const status = await fetchStrategyProcessStatus([runTag, trainTag]);
        if (cancelled) return;
        const runActive = status[runTag] === true;
        const trainActive = status[trainTag] === true;
        if (runActive) {
          wasRun = true;
          setRemoteRunning(true);
          setRemoteNote("Strategy process already running — waiting for it to finish…");
        } else if (wasRun) {
          wasRun = false;
          setRemoteRunning(false);
          setRemoteNote(null);
          setRunSuccess(true);
          setSelectedScenario(null);
          reload(nav.searchCode, nav.secType);
        }
        if (trainActive) {
          wasTrain = true;
          setRemoteTraining(true);
          setRemoteNote("Training process already running — waiting for it to finish…");
        } else if (wasTrain) {
          wasTrain = false;
          setRemoteTraining(false);
          setRemoteNote(null);
          setTrainSuccess(true);
          reload(nav.searchCode, nav.secType);
        }
      } catch {
        /* status poll is best-effort */
      }
    };
    void poll();
    const timer = setInterval(poll, 5_000);
    return () => { cancelled = true; clearInterval(timer); };
  }, [runTag, trainTag, nav.searchCode, nav.secType, reload, setSelectedScenario]);

  // Chart interaction handlers
  const {
    chartInstanceRef,
    handleChartClick,
    handleCanvasClick,
    handleChartReady,
  } = useChartInteractions(
    displayBacktest,
    forecast,
    backtestRef,
    forecastRef,
    hoveredScenarioRef,
    setSelectedScenario,
  );

  // Chart option
  const chartOption = useMemo(() => {
    if (!displayBacktest) return null;
    return buildSingletonStrategyOption({
      data: displayBacktest, themeMode, selectedPeriod,
      forecast: forecast && forecast.rows.length > 0 ? forecast : null,
      hoveredScenarioRef,
    });
  }, [displayBacktest, themeMode, selectedPeriod, forecast]);

  // Run Strategy handler
  const handleRun = useCallback(async (force: boolean = false) => {
    if (!nav.searchCode || running || remoteRunning) return;
    setRunning(true);
    setRunError(null);
    setRunSuccess(false);
    try {
      const result = await runSingletonStrategy(
        nav.searchCode, nav.secType, runWithForecast, selection, faultTolerance,
        force,
        singletonRunTag(nav.searchCode, nav.secType, selection, faultTolerance),
      );
      if (result.already_running) {
        // Deduped by the process-id-tag registry — the running process
        // finishes under the status poll, which reloads data on exit.
        setRemoteNote("Strategy process already running — waiting for it to finish…");
        setRemoteRunning(true);
      } else if (result.success) {
        setRunSuccess(true);
        setSelectedScenario(null);
      } else {
        const tail = result.stderr.split("\n").slice(-5).join("\n");
        setRunError(`Exit code ${result.exitCode}: ${tail || "see server logs"}`);
      }
    } catch (e) {
      setRunError(String(e instanceof Error ? e.message : e));
    } finally {
      setRunning(false);
    }
  }, [nav.searchCode, nav.secType, running, remoteRunning, runWithForecast, selection, faultTolerance, setSelectedScenario]);

  // Pre-check for PK existence
  const handleRunClick = useCallback(async () => {
    if (!nav.searchCode || running) return;
    setCheckingPk(true);
    setRunError(null);
    try {
      const existing = await checkExistingStrategy(
        nav.searchCode, nav.secType, selection, faultTolerance,
      );
      if (existing.exists) {
        setExistingRun(existing);
        setPkModalOpen(true);
      } else {
        setExistingRun(null);
        await handleRun(true);
      }
    } catch (e) {
      setRunError(String(e instanceof Error ? e.message : e));
    } finally {
      setCheckingPk(false);
    }
  }, [nav.searchCode, nav.secType, running, selection, faultTolerance, handleRun]);

  const handleForceRerun = useCallback(() => {
    setPkModalOpen(false);
    setExistingRun(null);
    handleRun(true);
  }, [handleRun]);

  const handlePkModalClose = useCallback(() => {
    setPkModalOpen(false);
    setExistingRun(null);
  }, []);

  // Train Model handler — runs the Optuna study; no PK path (training
  // only upserts algo_configs, it writes no backtest rows).
  // After successful training, auto-triggers Run Strategy with the new params.
  const handleTrain = useCallback(async () => {
    if (!nav.searchCode || training || running || remoteTraining) return;
    setTraining(true);
    setTrainError(null);
    setTrainSuccess(false);
    try {
      const result = await trainStrategyModel(
        nav.searchCode, nav.secType, selection, 50,
        singletonTrainTag(nav.searchCode, nav.secType, selection),
      );
      if (result.already_running) {
        // Deduped by the process-id-tag registry — the running study
        // finishes under the status poll, which reloads data on exit.
        setRemoteNote("Training process already running — waiting for it to finish…");
        setRemoteTraining(true);
      } else if (result.success) {
        setTrainSuccess(true);
        await handleRun(true);
      } else {
        const tail = result.stderr.split("\n").slice(-5).join("\n");
        setTrainError(`Exit code ${result.exitCode}: ${tail || "see server logs"}`);
      }
    } catch (e) {
      setTrainError(String(e instanceof Error ? e.message : e));
    } finally {
      setTraining(false);
    }
  }, [nav.searchCode, nav.secType, training, running, remoteTraining, selection, handleRun]);

  return (
    <>
      <StrategyPageShell
        nav={nav}
        title="Singleton Strategy"
        subtitle="Select a security to view its pre-computed MA5/MA60 crossover backtest. B/S markers appear on the chart with hover tooltips showing full decision details. Click Run Strategy to (re)compute via Python."
      >
        {(searchCode) =>
          searchCode && (
            <Stack spacing={1.5}>
              <RunControls
                searchCode={searchCode}
                secType={nav.secType}
                selection={selection}
                onSelectionChange={setSelection}
                faultTolerance={faultTolerance}
                onFaultToleranceChange={setFaultTolerance}
                running={running || remoteRunning}
                checkingPk={checkingPk}
                runSuccess={runSuccess}
                runError={runError}
                remoteNote={remoteNote}
                runWithForecast={runWithForecast}
                onRunWithForecastChange={setRunWithForecast}
                onRunClick={handleRunClick}
                training={training || remoteTraining}
                trainSuccess={trainSuccess}
                trainError={trainError}
                onTrainClick={handleTrain}
              />

              {loading && (
                <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
                  <CircularProgress size={32} />
                </Box>
              )}

              {error && (
                <Alert severity="error" variant="filled">
                  Failed to load backtest: {error}
                </Alert>
              )}

              {/* Summary chips */}
              {!loading && backtest && backtest.decisions.length > 0 && (
                <Box sx={{ display: "flex", gap: 2, flexWrap: "wrap" }}>
                  <SummaryChip
                    label="Total Return"
                    value={`${backtest.summary.total_return_pct >= 0 ? "+" : ""}${backtest.summary.total_return_pct}%`}
                    color={backtest.summary.total_return_pct >= 0 ? "success" : "error"}
                  />
                  <SummaryChip
                    label="Realized P&L"
                    value={`${backtest.summary.realized_pnl >= 0 ? "+" : ""}${backtest.summary.realized_pnl.toLocaleString()}`}
                    color={backtest.summary.realized_pnl >= 0 ? "success" : "error"}
                  />
                  <SummaryChip
                    label="Trades"
                    value={`${backtest.summary.n_buys}B / ${backtest.summary.n_sells}S`}
                  />
                  <SummaryChip
                    label="MTM Equity"
                    value={`${backtest.summary.final_cash >= 0 ? "+" : ""}${backtest.summary.final_cash.toLocaleString()}`}
                    color={backtest.summary.final_cash >= 0 ? "success" : "error"}
                  />
                  <SummaryChip
                    label="Buy Cost"
                    value={backtest.summary.total_buy_cost.toLocaleString()}
                  />
                </Box>
              )}

              {/* Chart */}
              {!loading && chartOption && (
                <Box
                  sx={{
                    bgcolor: "background.paper",
                    border: 1,
                    borderColor: "divider",
                    borderRadius: 1.5,
                    p: 1,
                  }}
                >
                  <EChart
                    option={chartOption}
                    height={520}
                    onEvents={{ click: handleChartClick }}
                    onReady={handleChartReady}
                    onCanvasClick={handleCanvasClick}
                  />
                </Box>
              )}

              {/* No backtest found */}
              {!loading && backtest && backtest.decisions.length === 0 && (
                <Alert severity="info">
                  No backtest results found for <b>{searchCode}</b> ({nav.secType}).
                  Click <b>Run Strategy</b> above to compute.
                </Alert>
              )}

              {/* Risk analytics (expandable) */}
              {!loading && risks && risks.risk_seq && (
                <RiskPanel
                  risks={risks}
                  selectedPeriod={selectedPeriod}
                  onPeriodSelect={setSelectedPeriod}
                  selectedScenario={selectedScenario}
                  forecastDate={forecast?.forecast_date ?? null}
                />
              )}

              {/* Decision table (expandable) */}
              {!loading && backtest && backtest.decisions.length > 0 && (
                <DecisionTable
                  decisions={backtest.decisions}
                  forecastScenarios={forecastScenarios}
                  selectedScenario={selectedScenario}
                  onScenarioChange={setSelectedScenario}
                  faultTolerance={backtest.fault_tolerance ?? 0}
                />
              )}
            </Stack>
          )
        }
      </StrategyPageShell>
      <PkConfirmModal
        open={pkModalOpen}
        searchCode={nav.searchCode ?? ""}
        secType={nav.secType}
        existingRun={existingRun}
        onClose={handlePkModalClose}
        onForceRerun={handleForceRerun}
      />
    </>
  );
}
