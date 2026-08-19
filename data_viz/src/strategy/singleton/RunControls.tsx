/**
 * RunControls — the action bar for the singleton strategy page.
 *
 * Renders:
 *  - AlgoWeightMenu (per-algo weight sliders + FT + Forecast toggle)
 *  - Run Strategy button (with spinner during execution)
 *  - Train Model SPLIT button (main = nested trainer; side down-triangle
 *    opens Model Configs (DB) / Training Logs — see TrainSplitButton)
 *  - Status alerts (success / error / running)
 */
import {
  Alert,
  Box,
  Button,
  CircularProgress,
} from "@mui/material";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import { AlgoWeightMenu } from "../_common";
import { serializeSelection, selectionToStrategyName, type StrategySelection } from "@/lib/api-client";
import type { MaSpreadSecType } from "@shared/types";
import { TrainSplitButton } from "./TrainSplitButton";

export interface RunControlsProps {
  searchCode: string;
  secType: MaSpreadSecType;
  selection: StrategySelection;
  onSelectionChange: (s: StrategySelection) => void;
  faultTolerance: number;
  onFaultToleranceChange: (ft: number) => void;
  running: boolean;
  checkingPk: boolean;
  runSuccess: boolean;
  runError: string | null;
  /** Info note shown while a REMOTE process (started before a page
   *  refresh or in another tab) holds the process-id-tag. */
  remoteNote?: string | null;
  runWithForecast: boolean;
  onRunWithForecastChange: (v: boolean) => void;
  onRunClick: () => void;
  training: boolean;
  trainSuccess: boolean;
  trainError: string | null;
  onTrainClick: () => void;
}

export function RunControls(props: RunControlsProps) {
  const {
    searchCode,
    secType,
    selection,
    onSelectionChange,
    faultTolerance,
    onFaultToleranceChange,
    running,
    checkingPk,
    runSuccess,
    runError,
    remoteNote,
    runWithForecast,
    onRunWithForecastChange,
    onRunClick,
    training,
    trainSuccess,
    trainError,
    onTrainClick,
  } = props;

  const busy = running || training;

  return (
    <Box sx={{ display: "flex", alignItems: "center", gap: 2 }}>
      <AlgoWeightMenu
        selection={selection}
        onChange={onSelectionChange}
        disabled={busy}
        faultTolerance={faultTolerance}
        onFaultToleranceChange={onFaultToleranceChange}
        runWithForecast={runWithForecast}
        onRunWithForecastChange={onRunWithForecastChange}
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
        strategyName={selectionToStrategyName(selection)}
      />
      {remoteNote && (running || training) && (
        <Alert severity="info" sx={{ py: 0, flex: 1 }}>
          {remoteNote}
        </Alert>
      )}
      {runSuccess && !running && (
        <Alert severity="success" sx={{ py: 0, flex: 1 }}>
          Strategy completed — reloaded from DB.
        </Alert>
      )}
      {runError && !running && (
        <Alert severity="error" sx={{ py: 0, flex: 1 }}>
          {runError}
        </Alert>
      )}
      {running && (
        <Alert severity="info" sx={{ py: 0, flex: 1 }}>
          Running <code>python -m strategy.singleton_trading --algo {serializeSelection(selection)}
        --sec-type{" "}
        {secType} --codes {searchCode} --force{!runWithForecast ? " --no-forecast" : ""}{faultTolerance > 0 ? ` --fault-tolerance ${faultTolerance}` : ""}</code>…
        this may take a moment{runWithForecast ? " (forecast adds ~10 child seqs)" : ""}.
        </Alert>
      )}
      {training && (
        <Alert severity="info" sx={{ py: 0, flex: 1 }}>
          Training <code>python -m strategy.factors_and_algos._optm_engine --algo{" "}
          {serializeSelection(selection).split(",")[0].split(":")[0]} --sec-type {secType} --codes{" "}
          {searchCode} --trials 50</code>… Optuna study in progress — best params will be
          upserted to algo_configs.
        </Alert>
      )}
      {trainSuccess && !training && !running && (
        <Alert severity="success" sx={{ py: 0, flex: 1 }}>
          Training completed — best params saved to algo_configs. Strategy is running with
          tuned params…
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
