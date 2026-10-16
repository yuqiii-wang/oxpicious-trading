/**
 * RunOrchestrationDialog — both orchestration Run buttons' confirm
 * dialog. Shows exactly what will spawn (one step's `python -m` module
 * with its args, or the tier's whole run_steps.sh serial runner), plus
 * a Checkbox (default unticked) that, when ticked and confirmed,
 * redirects to the Monitor Services page — where the Pipeline processes
 * table picks the run up via its heartbeat (~60s). Step runs POST
 * /api/monitor/stream/run with force=true (an explicit click means run;
 * the module itself stays incremental-by-default); tier runs POST
 * /api/monitor/orchestration/run (the shared run_steps.sh the tier
 * scripts delegate to — a manual run, so no trading-day/time gate).
 */
import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Button,
  Checkbox,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  Typography,
} from "@mui/material";
import { PlayArrow } from "@mui/icons-material";
import { useNavigate } from "react-router-dom";
import { runOrchestrationTier, runService } from "@/lib/api-client/monitor";

/** What the dialog confirms — one step of one tier, or a whole tier. */
export interface OrchestrationRunTarget {
  kind: "step" | "tier";
  tier: string;
  file: string;
  /** kind=step only: the step's number within the tier. */
  step?: number;
  /** kind=step only: the python -m module. */
  script: string;
  args: string[];
  /** kind=tier only: the step count, for the caption. */
  steps?: number;
}

interface RunOrchestrationDialogProps {
  target: OrchestrationRunTarget | null;
  onClose: () => void;
  /** A run was spawned — the page toasts; the dialog itself closes. */
  onStarted: (message: string) => void;
}

export default function RunOrchestrationDialog({
  target,
  onClose,
  onStarted,
}: RunOrchestrationDialogProps) {
  const navigate = useNavigate();
  // Default unticked on every open — ticking opts INTO the redirect.
  const [openMonitor, setOpenMonitor] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setOpenMonitor(false);
    setError(null);
    setSubmitting(false);
  }, [target]);

  const command =
    target == null
      ? ""
      : target.kind === "step"
        ? `python -m ${target.script}${target.args.length > 0 ? ` ${target.args.join(" ")}` : ""}`
        : `bash scripts/run_steps.sh scripts/orchestration/${target.file}`;

  const submit = useCallback(async () => {
    if (target == null) return;
    setSubmitting(true);
    setError(null);
    try {
      const resp =
        target.kind === "step"
          ? await runService(target.script, true, target.args)
          : await runOrchestrationTier(target.file);
      if (!resp.started) throw new Error(resp.message);
      onStarted(resp.message);
      onClose();
      if (openMonitor) navigate("/monitor");
    } catch (e) {
      setError(String(e).replace(/^Error: /, ""));
    } finally {
      setSubmitting(false);
    }
  }, [target, openMonitor, onStarted, onClose, navigate]);

  return (
    <Dialog
      open={target != null}
      onClose={() => {
        if (!submitting) onClose();
      }}
      maxWidth="sm"
      fullWidth
    >
      <DialogTitle>
        {target == null
          ? ""
          : target.kind === "step"
            ? `Run ${target.tier} step ${target.step}`
            : `Run tier ${target.tier}`}
      </DialogTitle>
      <DialogContent sx={{ display: "flex", flexDirection: "column", gap: 1.5, pt: 1 }}>
        <Typography variant="body2" sx={{ fontFamily: "monospace", overflowWrap: "anywhere" }}>
          {command}
        </Typography>
        <Typography variant="caption" color="text.secondary">
          {target?.kind === "step"
            ? "fire-and-forget: the run shows up as “running” in the Pipeline processes table within ~60s (heartbeat) and lands in its run history on exit."
            : `runs the file's ${target?.steps ?? 0} step${(target?.steps ?? 0) === 1 ? "" : "s"} one by one, strictly serially (the shared run_steps.sh runner — a manual run, no trading-day/time gate); each step heartbeats onto Pipeline processes as it starts.`}
        </Typography>
        <FormControlLabel
          sx={{ alignSelf: "flex-start" }}
          control={
            <Checkbox
              size="small"
              checked={openMonitor}
              onChange={(e) => setOpenMonitor(e.target.checked)}
            />
          }
          label="Open the Monitor Services page after confirming — watch the run in Pipeline processes"
        />
        {error != null && <Alert severity="error">{error}</Alert>}
      </DialogContent>
      <DialogActions>
        <Button color="inherit" onClick={onClose} disabled={submitting}>
          Cancel
        </Button>
        <Button
          variant="contained"
          onClick={submit}
          disabled={submitting}
          startIcon={<PlayArrow fontSize="small" />}
        >
          Run
        </Button>
      </DialogActions>
    </Dialog>
  );
}
