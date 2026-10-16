/**
 * RunServiceDialog — the per-process Run button's confirm dialog,
 * self-contained: it fetches the guard preview on open (GET
 * /api/monitor/stream/run/guard — the freq auto-skip evaluation PLUS the
 * cross-service parallel-services verdict against the live services),
 * keeps the Force-rerun switch OFF by default, and submits POST
 * /api/monitor/stream/run. A freq auto-skip (started=false, skipped=true)
 * keeps the dialog OPEN with the reason so the switch can be flipped and
 * the run retried; a parallel-services conflict (started=false,
 * blocked=true) keeps it open with the conflicting pairs — force rerun
 * does NOT bypass that guard (edit the service_config lists instead); a
 * started run closes the dialog and hands the service to the page via
 * onStarted (spawn-spinner tracking + table refresh).
 */
import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  Switch,
  Typography,
} from "@mui/material";
import { PlayArrow } from "@mui/icons-material";
import {
  fetchServiceRunGuard,
  runService,
  type ProcessStatus,
  type ServiceRunGuardResponse,
} from "@/lib/api-client/monitor";
import { fmtAge, fmtPeriod } from "./fmt";
import { fmtTimeShanghai } from "@/shared/datetime";

interface RunServiceDialogProps {
  /** The process the dialog confirms (null = closed). */
  proc: ProcessStatus | null;
  onClose: () => void;
  /** A run was started (or deduped onto an in-flight one): the page adds
   *  the service to its spawn-spinner tracking and refreshes the table. */
  onStarted: (service: string, alreadyRunning: boolean) => void;
}

export default function RunServiceDialog({
  proc,
  onClose,
  onStarted,
}: RunServiceDialogProps) {
  const [guard, setGuard] = useState<ServiceRunGuardResponse | null>(null);
  const [guardLoading, setGuardLoading] = useState(false);
  const [guardError, setGuardError] = useState<string | null>(null);
  const [forceRerun, setForceRerun] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [outcome, setOutcome] = useState<{
    severity: "info" | "error";
    text: string;
  } | null>(null);

  // Every open (proc change) starts fresh: force OFF, no outcome, guard
  // re-fetched for the service. The cancelled flag drops late responses
  // after a close / service switch.
  useEffect(() => {
    setForceRerun(false);
    setOutcome(null);
    setGuard(null);
    setGuardError(null);
    if (proc == null) return;
    setGuardLoading(true);
    let cancelled = false;
    fetchServiceRunGuard(proc.service)
      .then((g) => {
        if (cancelled) return;
        setGuard(g);
        setGuardLoading(false);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setGuardError(String(e));
        setGuardLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [proc]);

  const submit = useCallback(async () => {
    if (proc == null) return;
    setSubmitting(true);
    setOutcome(null);
    try {
      const resp = await runService(proc.service, forceRerun);
      if (resp.started) {
        onStarted(proc.service, resp.alreadyRunning === true);
        onClose();
      } else {
        // Freq auto-skip (info — force bypasses) or a parallel-services
        // conflict (error — force does NOT bypass it): keep the dialog
        // open with the reason either way.
        setOutcome({
          severity: resp.blocked ? "error" : "info",
          text: resp.message,
        });
      }
    } catch (e) {
      setOutcome({ severity: "error", text: String(e) });
    } finally {
      setSubmitting(false);
    }
  }, [proc, forceRerun, onStarted, onClose]);

  return (
    <Dialog
      open={proc != null}
      onClose={() => {
        if (!submitting) onClose();
      }}
      maxWidth="sm"
      fullWidth
    >
      <DialogTitle>Run {proc?.service}</DialogTitle>
      <DialogContent sx={{ display: "flex", flexDirection: "column", gap: 1.5, pt: 1 }}>
        <Typography variant="body2" sx={{ fontFamily: "monospace" }}>
          python -m {proc?.service}
        </Typography>
        <Typography variant="caption" color="text.secondary">
          freq {proc?.freq}
          {proc?.schedule ? ` · ${proc.schedule}` : ""} — the spawn is
          fire-and-forget: the run shows up as “running” in the heartbeat
          table within ~60s and lands in the row's run history on exit.
        </Typography>
        {guardLoading && <CircularProgress size={18} sx={{ my: 1 }} />}
        {guardError != null && (
          <Alert severity="warning">
            Freq guard unavailable: {guardError} — the server still applies
            the auto-skip on a force-less run.
          </Alert>
        )}
        {guard != null && (
          <Alert severity={guard.wouldSkip ? "warning" : "success"}>
            <Box>
              <Typography variant="subtitle2">
                {guard.wouldSkip
                  ? "Auto-skip applies — a force-less run will be skipped"
                  : "Freq guard passes — the run will start"}
              </Typography>
              <Typography variant="caption" color="text.secondary" component="div">
                last successful exit:{" "}
                {guard.lastSuccessAt != null
                  ? `${fmtTimeShanghai(guard.lastSuccessAt)} (${fmtAge(guard.lastSuccessAgeS)} ago)`
                  : "never exited cleanly"}
                {" · "}
                {guard.skipRule === "within"
                  ? `${guard.freq} auto-skip window ${fmtPeriod(guard.periodMs)}`
                  : guard.skipRule === "any_success"
                    ? "freq once — any successful exit satisfies it"
                    : `freq ${guard.freq} — no auto-skip`}
              </Typography>
            </Box>
          </Alert>
        )}
        {guard?.parallel.dbError != null && (
          <Alert severity="warning">
            Parallel guard unavailable: {guard.parallel.dbError} — the run
            still goes through (the registration-time check skips too).
          </Alert>
        )}
        {guard != null && guard.parallel.dbError == null && (
          <Alert
            severity={guard.parallel.conflicts.length > 0 ? "error" : "success"}
          >
            <Box>
              <Typography variant="subtitle2">
                {guard.parallel.conflicts.length > 0
                  ? "Parallel conflict — the run will be refused (force rerun does NOT bypass it)"
                  : "Parallel guard passes"}
              </Typography>
              <Typography variant="caption" color="text.secondary" component="div">
                {guard.parallel.conflicts.length > 0 ? (
                  guard.parallel.conflicts.map((c, i) => (
                    <Box key={i} component="div">
                      {c.running} (running) — {c.owner}&apos;s{" "}
                      {c.list}
                      {c.entry !== "" ? ` entry '${c.entry}'` : " whitelist (not listed)"}
                    </Box>
                  ))
                ) : (
                  <>
                    {guard.parallel.includedParallelServices.length > 0
                      ? `included: ${guard.parallel.includedParallelServices.join(", ")}`
                      : "included: — (unrestricted)"}
                    {" · "}
                    {guard.parallel.excludedParallelServices.length > 0
                      ? `excluded: ${guard.parallel.excludedParallelServices.join(", ")}`
                      : "excluded: —"}
                    {" · "}
                    {guard.parallel.runningChecked} running service
                    {guard.parallel.runningChecked === 1 ? "" : "s"} checked
                  </>
                )}
              </Typography>
            </Box>
          </Alert>
        )}
        {outcome != null && <Alert severity={outcome.severity}>{outcome.text}</Alert>}
        <FormControlLabel
          sx={{ alignSelf: "flex-start" }}
          control={
            <Switch
              size="small"
              checked={forceRerun}
              onChange={(e) => setForceRerun(e.target.checked)}
            />
          }
          label="Force rerun — bypass the freq auto-skip"
        />
      </DialogContent>
      <DialogActions>
        <Button color="inherit" onClick={onClose} disabled={submitting}>
          Cancel
        </Button>
        <Button
          variant="contained"
          onClick={submit}
          disabled={submitting || guardLoading}
          startIcon={
            submitting ? (
              <CircularProgress size={14} thickness={5} />
            ) : (
              <PlayArrow fontSize="small" />
            )
          }
        >
          Run
        </Button>
      </DialogActions>
    </Dialog>
  );
}
