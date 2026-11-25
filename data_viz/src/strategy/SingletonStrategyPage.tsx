/**
 * Singleton Strategy page — displays pre-computed backtest results.
 *
 * The backtest itself is run by the Python package `strategy.singleton_trading`
 * (python -m strategy.singleton_trading --sec-type index --codes <code>),
 * which writes results to the strategy schema. This page reads those results
 * via the API and renders the shared CodeTrendChart — INHERITED, not
 * hand-rolled (user-directed 2026-11-18): the market rows come from the
 * same synced sec-baseline data path every code-trend consumer uses, and
 * the strategy's OWN data (B/S decision markers, run/sync subtitle,
 * sync-gap band) layers on top via SingletonStrategyCodeTrendChart.
 * The DecisionTable mirrors the chart's visible window; clicking a row
 * emphasizes + pans to its marker.
 *
 * Risk metrics (computed by `python -m strategy._risks`) are loaded in
 * parallel and shown in an expandable RiskPanel below the chart.
 *
 * The "Run Strategy" / "Train Model" buttons SUBMIT the Python job via
 * the backend (POST /api/strategy/singleton/run|train resolves at the
 * cluster accept, 2026-11-18 async submit) — the process keeps running
 * server-side. useProcessStatusPoll drives the spinners from the
 * process-status endpoint (60s idle / 5s active auto-check + a manual
 * refresh button); when a process exits, the page invalidates cache and
 * reloads from DB (a non-zero exit surfaces the process's log tail via
 * /singleton/process-result).
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  CircularProgress,
  Stack,
} from "@mui/material";
import type { StrategyDecision } from "@shared/types";
import {
  checkExistingStrategy,
  runSingletonStrategy,
  trainStrategyModel,
  fetchStrategyProcessResult,
  singletonRunTag,
  singletonTrainTag,
  formatFetchError,
  DEFAULT_FAULT_TOLERANCE,
  DEFAULT_STRATEGY_SELECTION,
  type CheckExistingResult,
  type StrategySelection,
} from "@/lib/api-client";
import type { CodeTrendSecType } from "@/ui/OhlcChart";
import {
  StrategyPageShell,
  useStrategyNav,
  SummaryChip,
  DecisionTable,
  type DecisionWindowInfo,
  RiskPanel,
} from "./_common";
import {
  useStrategyData,
  useProcessStatusPoll,
  RunControls,
  PkConfirmModal,
  SingletonStrategyCodeTrendChart,
} from "./singleton";

/** AI-Ask seed keywords for the inherited code-trend "?" button (the
 *  page's own backtest AI addon retired with the hand-rolled chart). */
const SINGLETON_AI_KEYWORDS = [
  "singleton strategy",
  "backtest decisions",
  "fault tolerance",
];

export default function SingletonStrategyPage() {

  // Signal-family tag selection (no weights — the tags input). Fault
  // tolerance is PINNED at 10% (user-directed) — a constant, not state.
  const [selection, setSelection] = useState<StrategySelection>(DEFAULT_STRATEGY_SELECTION);
  const faultTolerance = DEFAULT_FAULT_TOLERANCE;

  // The MODE toggle (2026-11-19): "signals" = the analysis signal
  // families (the tags input, analysis_signals.history_signals pickup);
  // "oracle" = the HINDSIGHT oracle mode (strategy.factors_and_algos.
  // _oracle — only analysis.oracle_signals pivot actions, swing-
  // proportional confidence). Oracle mode replaces the family selection
  // with the single "oracle" algo name end-to-end (run, train, check,
  // history).
  const [mode, setMode] = useState<"signals" | "oracle">("signals");
  const effectiveSelection: StrategySelection = useMemo(
    () => (mode === "oracle" ? ["oracle"] : selection),
    [mode, selection],
  );

  // Run Strategy state — submission + error only: completion is the
  // refreshed results themselves (no completion alert, user-directed
  // 2026-11-18 — no backend internals on the page).
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);
  // PK-exists confirmation modal state
  const [checkingPk, setCheckingPk] = useState(false);
  const [pkModalOpen, setPkModalOpen] = useState(false);
  const [existingRun, setExistingRun] = useState<CheckExistingResult | null>(null);

  // Train Model state (Optuna _optm_engine)
  const [training, setTraining] = useState(false);
  const [trainError, setTrainError] = useState<string | null>(null);

  // Shared classification nav (no onCodeChange callback — the data hook
  // resets on its own when the code clears)
  const nav = useStrategyNav();

  // Data loading and state management
  const {
    backtest,
    risks,
    loading,
    error,
    selectedPeriod,
    displayBacktest,
    setSelectedPeriod,
    reload,
    runHistory,
    selectedSeqId,
    selectRun,
  } = useStrategyData(
    nav.searchCode, nav.secType, effectiveSelection, faultTolerance,
  );

  // ---- Backend process state (process-id-tag status poll) ---------------
  // The run/train POSTs resolve at the SUBMIT — the process keeps
  // running server-side, so this poll is the single source of truth for
  // "is a backend process running for the CURRENT identity". Covers: a
  // just-submitted job, a page refresh mid-run, duplicate clicks
  // (deduped server-side), and processes started in another tab. On a
  // tag's exit edge the page reloads data from the DB (completion IS
  // the refreshed results — user-directed 2026-11-18) or, for a
  // non-zero exit, surfaces the process's own log tail via
  // /singleton/process-result.
  const runTag = nav.searchCode
    ? singletonRunTag(nav.searchCode, nav.secType, effectiveSelection, faultTolerance)
    : "";
  const trainTag = nav.searchCode
    ? singletonTrainTag(nav.searchCode, nav.secType, effectiveSelection)
    : "";
  const onRunSettled = useCallback((exitCode: number | null) => {
    if (exitCode === null || exitCode === 0) {
      if (nav.searchCode) reload(nav.searchCode, nav.secType);
      return;
    }
    void (async () => {
      try {
        const result = await fetchStrategyProcessResult(runTag);
        const tail = result.stderr.split("\n").filter(Boolean).slice(-5).join("\n");
        setRunError(tail || `The strategy task failed (exit code ${exitCode}).`);
      } catch {
        setRunError(`The strategy task failed (exit code ${exitCode}).`);
      }
    })();
  }, [nav.searchCode, nav.secType, reload, runTag]);
  const onTrainSettled = useCallback((exitCode: number | null) => {
    if (exitCode === null || exitCode === 0) {
      if (nav.searchCode) reload(nav.searchCode, nav.secType);
      return;
    }
    void (async () => {
      try {
        const result = await fetchStrategyProcessResult(trainTag);
        const tail = result.stderr.split("\n").filter(Boolean).slice(-5).join("\n");
        setTrainError(tail || `The training task failed (exit code ${exitCode}).`);
      } catch {
        setTrainError(`The training task failed (exit code ${exitCode}).`);
      }
    })();
  }, [nav.searchCode, nav.secType, reload, trainTag]);
  const {
    runActive: remoteRunning,
    trainActive: remoteTraining,
    refreshing: statusRefreshing,
    refresh: refreshStatus,
  } = useProcessStatusPoll({ runTag, trainTag, onRunSettled, onTrainSettled });

  // Selected Trade-Decisions row — its marker on the code trend draws
  // with the inherited grow+glow emphasis (selectedSignalDate) and the
  // chart pans to reveal it when off-screen (focusDateRequest). Resets
  // with the displayed run: decision_no keys are per-run.
  const [selectedDecisionNo, setSelectedDecisionNo] = useState<number | null>(null);
  const [focusReq, setFocusReq] = useState<{ date: string; seq: number } | null>(null);
  useEffect(() => {
    setSelectedDecisionNo(null);
    setFocusReq(null);
  }, [backtest]);

  // ---- Visible-window mirror ------------------------------------------------
  // The Trade-Decisions table mirrors the chart's VISIBLE window (the
  // in-chart dataZoom): only decisions whose exec_date falls inside the
  // reported date range are listed. SingletonStrategyCodeTrendChart
  // reports the visible {start, end} dates on every zoom drag / rows
  // rebuild (onVisibleRangeChange). Reset when a new run loads.
  const [decisionWindow, setDecisionWindow] = useState<{ start: string; end: string } | null>(null);
  useEffect(() => { setDecisionWindow(null); }, [backtest]);
  const handleVisibleRange = useCallback(
    (r: { start: string; end: string } | null) => {
      setDecisionWindow(r ? { start: r.start, end: r.end } : null);
    },
    [],
  );

  // The decisions visible inside the current window (the table's rows) +
  // the window's date range and the run's unfiltered total, for
  // DecisionTable's "slider window … · N of M" caption. Dates compare as
  // ISO strings; a null window (pre-mount / no rows yet) lists everything.
  const { visibleDecisions, visibleRange } = useMemo(() => {
    if (!backtest) {
      return {
        visibleDecisions: [] as StrategyDecision[],
        visibleRange: null as DecisionWindowInfo | null,
      };
    }
    if (!decisionWindow) {
      return {
        visibleDecisions: backtest.decisions,
        visibleRange: null,
      };
    }
    return {
      visibleDecisions: backtest.decisions.filter(
        (d) => d.exec_date >= decisionWindow.start && d.exec_date <= decisionWindow.end,
      ),
      visibleRange: {
        start: decisionWindow.start,
        end: decisionWindow.end,
        total: backtest.decisions.length,
      },
    };
  }, [backtest, decisionWindow]);

  // Row click → select (toggle-off on the selected row) + pan the chart
  // window to the decision's exec_date when off-screen (the focus hook
  // keeps the current window span; a fresh seq per request re-applies).
  const handleSelectDecision = useCallback((no: number | null) => {
    setSelectedDecisionNo(no);
    if (no != null && displayBacktest) {
      const decision = displayBacktest.decisions.find((d) => d.decision_no === no);
      if (decision) {
        setFocusReq((prev) => ({
          date: decision.exec_date,
          seq: (prev?.seq ?? 0) + 1,
        }));
      }
    }
  }, [displayBacktest]);

  // Run Strategy handler — SUBMIT only (the POST resolves at the cluster
  // accept); the status poll owns the spinners/notes from here, and its
  // exit edge reloads the data. `running` now spans just the submit.
  const handleRun = useCallback(async (force: boolean = false) => {
    if (!nav.searchCode || running || remoteRunning) return;
    setRunning(true);
    setRunError(null);
    try {
      const result = await runSingletonStrategy(
        nav.searchCode, nav.secType, effectiveSelection, faultTolerance,
        force,
        singletonRunTag(nav.searchCode, nav.secType, effectiveSelection, faultTolerance),
      );
      if (result.accepted) {
        // Submitted (or deduped onto the in-flight process) — poll right
        // away so the buttons flip without waiting a full idle interval.
        refreshStatus();
      } else {
        // The run was NOT started: the cluster's verbatim rejection text
        // or the unreachable-area operator message.
        setRunError(result.error || "The strategy task could not be submitted.");
      }
    } catch (e) {
      setRunError(formatFetchError(e));
    } finally {
      setRunning(false);
    }
  }, [nav.searchCode, nav.secType, running, remoteRunning, effectiveSelection, faultTolerance, refreshStatus]);

  // Pre-check for PK existence. A STALE existing run (its rows end
  // before the code's analysis horizon — check.stale; a rerun extends
  // it) reruns DIRECTLY with force — no confirmation (user-directed
  // 2026-11-25); the PK modal only guards the destructive re-run of a
  // fresh run.
  const handleRunClick = useCallback(async () => {
    if (!nav.searchCode || running) return;
    setCheckingPk(true);
    setRunError(null);
    try {
      const existing = await checkExistingStrategy(
        nav.searchCode, nav.secType, effectiveSelection, faultTolerance,
      );
      if (existing.exists && !existing.stale) {
        setExistingRun(existing);
        setPkModalOpen(true);
      } else {
        // no run yet, or a STALE run (a rerun extends it) — rerun directly
        setExistingRun(null);
        await handleRun(true);
      }
    } catch (e) {
      setRunError(formatFetchError(e));
    } finally {
      setCheckingPk(false);
    }
  }, [nav.searchCode, nav.secType, running, effectiveSelection, faultTolerance, handleRun]);

  const handleForceRerun = useCallback(() => {
    setPkModalOpen(false);
    setExistingRun(null);
    handleRun(true);
  }, [handleRun]);

  const handlePkModalClose = useCallback(() => {
    setPkModalOpen(false);
    setExistingRun(null);
  }, []);

  // Train Model handler — SUBMIT only (the POST resolves at the cluster
  // accept; training runs minutes server-side). No PK path (training
  // only upserts algo_configs — it writes no backtest rows ITSELF).
  // The TRAINER's final step is the trained strategy's own run (it
  // spawns the run-once CLI, strategy.singleton_trading, with --force
  // at the pinned default FT, before the job exits), so a finished
  // training leaves a run-history record even with no browser open.
  // This handler therefore does NOT browser-drive a follow-up run; the
  // status poll's train-tag exit edge reloads the data with the trained
  // run in place.
  const handleTrain = useCallback(async () => {
    if (!nav.searchCode || training || running || remoteTraining) return;
    setTraining(true);
    setTrainError(null);
    try {
      const result = await trainStrategyModel(
        nav.searchCode, nav.secType, effectiveSelection, 50,
        singletonTrainTag(nav.searchCode, nav.secType, effectiveSelection),
      );
      if (result.accepted) {
        // Submitted (or deduped onto the in-flight study) — poll right
        // away so the buttons flip without waiting a full idle interval.
        refreshStatus();
      } else {
        setTrainError(result.error || "The training task could not be submitted.");
      }
    } catch (e) {
      setTrainError(formatFetchError(e));
    } finally {
      setTraining(false);
    }
  }, [nav.searchCode, nav.secType, training, running, remoteTraining, effectiveSelection, refreshStatus]);

  return (
    <>
      <StrategyPageShell
        nav={nav}
        title="Singleton Strategy"
        subtitle="Select a security to view its code trend (the shared, synced market data) with the strategy's B/S decision markers layered on. The subtitle reports where the displayed run's rows end vs the market data — uncovered days shade as a warning band. Click a decision row below to spotlight its marker on the chart — the table mirrors the chart's visible window, so pan/zoom to filter the rows. Click Run Strategy to (re)compute."
      >
        {(searchCode) =>
          searchCode && (
            <Stack spacing={1.5}>
              <RunControls
                searchCode={searchCode}
                secType={nav.secType}
                selection={selection}
                onSelectionChange={setSelection}
                mode={mode}
                onModeChange={setMode}
                runHistory={runHistory}
                selectedSeqId={selectedSeqId}
                onSelectRun={selectRun}
                running={running || remoteRunning}
                remoteRunning={remoteRunning}
                checkingPk={checkingPk}
                runError={runError}
                onRunClick={handleRunClick}
                training={training || remoteTraining}
                remoteTraining={remoteTraining}
                trainError={trainError}
                onTrainClick={handleTrain}
                statusRefreshing={statusRefreshing}
                onRefreshStatus={refreshStatus}
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

              {/* Code trend + the strategy's own data (the shared
                  CodeTrendChart, inherited — market rows from the synced
                  sec-baseline path; B/S decision markers, the run/sync
                  subtitle and the uncovered-days gap band from the
                  subclass). Renders even before any run exists. */}
              {!loading && (
                <SingletonStrategyCodeTrendChart
                  secType={nav.secType as CodeTrendSecType}
                  code={searchCode}
                  name={displayBacktest?.name}
                  backtest={displayBacktest}
                  height={520}
                  selectedDecisionNo={selectedDecisionNo}
                  selectedPeriod={selectedPeriod}
                  focusDateRequest={focusReq}
                  onVisibleRangeChange={handleVisibleRange}
                  aiSearchKeywords={SINGLETON_AI_KEYWORDS}
                />
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
                />
              )}

              {/* Decision table (expandable) — rows mirror the chart's
                  time-slider window; chips + the slider-window caption
                  describe the slice */}
              {!loading && backtest && backtest.decisions.length > 0 && (
                <DecisionTable
                  decisions={visibleDecisions}
                  faultTolerance={backtest.fault_tolerance ?? 0}
                  windowInfo={visibleRange ?? undefined}
                  selectedDecisionNo={selectedDecisionNo}
                  onSelectDecision={handleSelectDecision}
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
