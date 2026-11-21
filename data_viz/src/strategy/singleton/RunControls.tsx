/**
 * RunControls — the action bar for the singleton strategy page.
 *
 * Renders:
 *  - The MODE toggle (2026-11-19): Signals (the analysis signal-family
 *    tags) | Oracle (the HINDSIGHT mode — only analysis.oracle_signals
 *    pivot actions at the oracle's own forward-60d trend grade; the
 *    tags input is replaced by the oracle descriptor chip in that
 *    mode)
 *  - SignalTagsInput (the analysis signal-family TAG selector — no
 *    weights; options loaded from the analysis signals tier; signals
 *    mode only)
 *  - Run Strategy button (with spinner during execution)
 *  - Train Model SPLIT button (main = nested trainer; side down-triangle
 *    opens Model Configs (DB) / Training Logs — see TrainSplitButton)
 *  - Status REFRESH button — polls the backend process status on demand
 *    (the auto-check underneath runs at 60s idle / 5s active)
 *  - Status alerts (submitted / error) — NO backend command or
 *    completion internals: submission says "task submitted — running
 *    in backend"; errors surface the ACTUAL error detail (the API's
 *    error message / the process's stderr tail). Completion is the
 *    refreshed results themselves (2026-11-18, user-directed).
 *
 * Fault tolerance is PINNED at 10% (2026-11-17, user-directed) — no UI
 * control; the constant rides the page state (see SingletonStrategyPage).
 */
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
} from "@mui/material";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import RefreshButton from "@/components/RefreshButton";
import { SignalTagsInput } from "../_common";
import { selectionToStrategyName, type StrategySelection } from "@/lib/api-client";
import type { MaSpreadSecType, RunHistoryItem } from "@shared/types";
import { TrainSplitButton } from "./TrainSplitButton";
import { RunHistorySelect } from "./RunHistorySelect";

/** The strategy page's mode: the analysis signal families, or the
 *  hindsight oracle (strategy.factors_and_algos._oracle — only
 *  oracle pivot actions at the oracle's own forward-60d trend
 *  grade). */
export type StrategyMode = "signals" | "oracle";

export interface RunControlsProps {
  searchCode: string;
  secType: MaSpreadSecType;
  selection: StrategySelection;
  onSelectionChange: (s: StrategySelection) => void;
  /** The active mode (the page owns it; oracle forces the effective
   *  selection to ["oracle"] end-to-end). */
  mode: StrategyMode;
  onModeChange: (m: StrategyMode) => void;
  /** Recent runs for the current identity + the displayed one (null =
   *  latest/active); selecting a historical run displays it. */
  runHistory: RunHistoryItem[];
  selectedSeqId: number | null;
  onSelectRun: (seqId: number | null) => void;
  running: boolean;
  /** True when the status poll confirms the run process is live
   *  (switches the run alert from "submitted" to "process running"). */
  remoteRunning: boolean;
  checkingPk: boolean;
  runError: string | null;
  onRunClick: () => void;
  training: boolean;
  /** True when the status poll confirms the training process is live. */
  remoteTraining: boolean;
  trainError: string | null;
  onTrainClick: () => void;
  /** Manual backend-status check (the hook's refresh) + its in-flight
   *  flag — the auto-check runs at 60s idle / 5s active underneath. */
  statusRefreshing: boolean;
  onRefreshStatus: () => void;
}

export function RunControls(props: RunControlsProps) {
  const {
    searchCode,
    secType,
    selection,
    onSelectionChange,
    mode,
    onModeChange,
    runHistory,
    selectedSeqId,
    onSelectRun,
    running,
    remoteRunning,
    checkingPk,
    runError,
    onRunClick,
    training,
    remoteTraining,
    trainError,
    onTrainClick,
    statusRefreshing,
    onRefreshStatus,
  } = props;

  const busy = running || training;
  const oracle = mode === "oracle";

  return (
    <Box sx={{ display: "flex", alignItems: "center", gap: 2, flexWrap: "wrap" }}>
      <ToggleButtonGroup
        size="small"
        exclusive
        value={mode}
        onChange={(_e, v) => {
          if (v !== null) onModeChange(v as StrategyMode);
        }}
        disabled={busy}
        aria-label="strategy mode"
      >
        <ToggleButton value="signals">Signals</ToggleButton>
        <ToggleButton value="oracle">Oracle</ToggleButton>
      </ToggleButtonGroup>
      {oracle ? (
        <Tooltip
          title="Hindsight mode — replays ONLY the oracle's graded local min/max pivots (analysis.oracle_signals): buy at a local minimum, sell at a local maximum (one per side per 5-day window); each side speaks only at a GENUINE extreme of its own horizon — a buy passing the genuine-deep-min test (drawdown into the pivot from the prior 20 rows' high 5%+ — a genuine deep min always buys, even with a deeper min coming within 20d; a shallower min omits only when a materially deeper level is imminent), a sell only at a genuine high (no 5%+ fall from the prior 20 rows' ceiling, no close 5%+ above within the next 20 rows — a weak bounce or a soon-exceeded waypoint is not a high); each surviving pivot is graded 0-100 by its favorable move on EITHER side — 0.3 x the move into it + 0.7 x the move after it (each leg ramped 2% no-action floor / 10% full; both legs below the floor = not in the table); a NULL grade (unrealized frontier window) replays full size. Perfect-foresight reference, not a tradable signal."
        >
          <Chip
            size="small"
            color="secondary"
            variant="outlined"
            label="oracle — hindsight pivots (favorable-move graded)"
          />
        </Tooltip>
      ) : (
        <SignalTagsInput
          secType={secType}
          selection={selection}
          onChange={onSelectionChange}
          disabled={busy}
        />
      )}
      <RunHistorySelect
        runs={runHistory}
        selectedSeqId={selectedSeqId}
        onSelect={onSelectRun}
        disabled={busy}
      />
      <Button
        variant="contained"
        size="small"
        color="primary"
        disabled={busy || checkingPk}
        startIcon={running ? <CircularProgress size={16} color="inherit" /> : checkingPk ? <CircularProgress size={16} color="inherit" /> : <PlayArrowIcon />}
        onClick={onRunClick}
      >
        {running ? "Running…" : checkingPk ? "Checking…" : "Run Strategy"}
      </Button>
      <TrainSplitButton
        training={training}
        disabled={busy}
        onTrainClick={onTrainClick}
        code={searchCode}
        secType={secType}
        strategyName={selectionToStrategyName(oracle ? ["oracle"] : selection)}
      />
      {/* Backend-status check — manual, on top of the 60s-idle / 5s-
          active auto-check (spins while a status fetch is in flight). */}
      <RefreshButton
        onClick={onRefreshStatus}
        loading={statusRefreshing}
        tooltip="Refresh backend status"
        label="Status"
      />
      {selectedSeqId != null && !running && (
        <Alert severity="warning" sx={{ py: 0, flex: 1 }}>
          Viewing a historical run — pick “Latest” in the Run selector to return to the
          active one.
        </Alert>
      )}
      {runError && !running && (
        <Alert severity="error" sx={{ py: 0, flex: 1 }}>
          {runError}
        </Alert>
      )}
      {/* ONE in-progress alert per lane: "submitted" until the status
          poll confirms the process, then "process running" wording. */}
      {running && (
        <Alert severity="info" sx={{ py: 0, flex: 1 }}>
          {remoteRunning
            ? "Strategy process running — waiting for it to finish…"
            : "Task submitted — running in backend… this may take a moment."}
        </Alert>
      )}
      {training && (
        <Alert severity="info" sx={{ py: 0, flex: 1 }}>
          {remoteTraining
            ? "Training process running — waiting for it to finish…"
            : "Task submitted — training running in backend… this may take a few minutes."}
        </Alert>
      )}
      {trainError && !training && (
        <Alert severity="error" sx={{ py: 0, flex: 1 }}>
          {trainError}
        </Alert>
      )}
    </Box>
  );
}
