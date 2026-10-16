/**
 * ProcessRow — one batch pipeline process of the Pipeline processes table:
 * the last-run state chip (hover for the stop reason / the never-run
 * note), the supposed-run freq (hover for schedule / notes / config
 * source), and the per-service Run button (opens the confirm dialog via
 * onRun — disabled while the process is running, spinner while a
 * page-spawned run is in flight). The expandable row reveals the run
 * audit history; every history run is clickable (onOpenLog) and opens its
 * service log in the page-level LogViewerDialog.
 */
import {
  Box,
  Chip,
  CircularProgress,
  Collapse,
  IconButton,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
} from "@mui/material";
import { ExpandLess, ExpandMore, PlayArrow } from "@mui/icons-material";
import { fmtDateTimeShanghai, fmtTimeShanghai } from "@/shared/datetime";
import type { ProcessStatus, ServiceAuditEntry } from "@/lib/api-client/monitor";
import { PROCESS_CHIP, type ProcessRunsState } from "./constants";
import { fmtAge, fmtPeriod, liveAge } from "./fmt";

export default function ProcessRow({
  proc,
  nowMs,
  open,
  onToggle,
  onRun,
  spawning,
  onOpenLog,
  runs,
}: {
  proc: ProcessStatus;
  nowMs: number;
  open: boolean;
  onToggle: () => void;
  onRun: () => void;
  /** True while a run spawned from this page is still in flight. */
  spawning: boolean;
  /** Open one history run's component log (row click). */
  onOpenLog: (run: ServiceAuditEntry) => void;
  runs: ProcessRunsState;
}) {
  const chip = PROCESS_CHIP[proc.status];
  const age = liveAge(proc.beatAt, nowMs) ?? proc.beatAgeS;
  // Overdue vs the supposed cadence: the last beat is older than the
  // freq's window (server-computed freqPeriodMs; null = no checkable
  // period — always / occasionally / on_demand / once never amber). The
  // Last beat cell ambers so a lapsed schedule shows at a glance.
  const overdue =
    proc.freqPeriodMs != null && age != null && age * 1000 > proc.freqPeriodMs;
  const statusTip =
    proc.status === "stopped" && proc.stopReason != null
      ? `${proc.stopReason} (normal exit: ${proc.isNormalExit ? "yes" : "no"}) at ${fmtTimeShanghai(proc.stoppedAt)}`
      : proc.status === "never"
        ? "registered in the service_config tree but no heartbeat row on record — never ran"
        : undefined;
  // Supposed-run cadence from the service_config/ static tree — the
  // timing detail + quirks + where the config lives, on hover.
  const freqTip = [
    proc.schedule,
    proc.notes,
    proc.sourceRef ? `config: ${proc.sourceRef}` : null,
  ]
    .filter((s): s is string => s != null && s !== "")
    .join("\n");
  const statusChip = (
    <Chip size="small" color={chip.color} label={chip.label} />
  );
  return (
    <>
      <TableRow hover onClick={onToggle} sx={{ cursor: "pointer" }}>
        {/* Run cell first — clicks must not toggle the row's expand. */}
        <TableCell onClick={(e) => e.stopPropagation()}>
          {spawning ? (
            <Tooltip title="run in flight — spawned from this page, waiting for it to exit">
              <span>
                <CircularProgress size={16} thickness={5} sx={{ mx: 0.5 }} />
              </span>
            </Tooltip>
          ) : proc.status === "running" ? (
            <Tooltip title="already running">
              <span>
                <IconButton
                  size="small"
                  disabled
                  aria-label={`${proc.service} is running`}
                  sx={{
                    border: 1.5,
                    borderColor: "primary.main",
                    "&:disabled": { borderColor: "action.disabled" },
                  }}
                >
                  <PlayArrow fontSize="small" />
                </IconButton>
              </span>
            </Tooltip>
          ) : (
            <Tooltip
              title={
                `Run python -m ${proc.service} — a force-less run is ` +
                "auto-skipped while the last successful exit sits inside " +
                "the freq period (confirm dialog next)"
              }
            >
              <IconButton
                size="small"
                aria-label={`run ${proc.service}`}
                onClick={onRun}
                sx={{ border: 1.5, borderColor: "primary.main" }}
              >
                <PlayArrow fontSize="small" />
              </IconButton>
            </Tooltip>
          )}
        </TableCell>
        <TableCell sx={{ fontFamily: "monospace" }}>
          <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
            {open ? (
              <ExpandLess fontSize="small" sx={{ color: "text.secondary" }} />
            ) : (
              <ExpandMore fontSize="small" sx={{ color: "text.secondary" }} />
            )}
            {proc.service}
          </Box>
        </TableCell>
        <TableCell>{proc.scope}</TableCell>
        <TableCell>{proc.subScope ?? "—"}</TableCell>
        <TableCell>
          {statusTip != null ? <Tooltip title={statusTip}>{statusChip}</Tooltip> : statusChip}
        </TableCell>
        <TableCell>
          {freqTip !== "" ? (
            <Tooltip title={freqTip}>
              <span>{proc.freq}</span>
            </Tooltip>
          ) : (
            proc.freq
          )}
        </TableCell>
        <TableCell>{proc.pid ?? "—"}</TableCell>
        <TableCell>{fmtDateTimeShanghai(proc.startedAt)}</TableCell>
        <TableCell sx={overdue ? { color: "warning.main" } : undefined}>
          {overdue ? (
            <Tooltip
              title={
                `overdue — last beat ${fmtAge(age)} ago exceeds the ` +
                `${proc.freq} cadence (${fmtPeriod(proc.freqPeriodMs)})`
              }
            >
              <span>{fmtAge(age)}</span>
            </Tooltip>
          ) : (
            fmtAge(age)
          )}
        </TableCell>
        <TableCell>{proc.stopReason ?? "—"}</TableCell>
      </TableRow>
      <TableRow>
        <TableCell colSpan={10} sx={{ py: 0, border: 0 }}>
          <Collapse in={open} unmountOnExit>
            <Box sx={{ px: 5, py: 1.5 }}>
              {runs.loading && <CircularProgress size={16} sx={{ my: 1 }} />}
              {runs.error != null && (
                <Typography variant="caption" color="error">
                  {runs.error}
                </Typography>
              )}
              {!runs.loading && runs.error == null && runs.runs.length === 0 && (
                <Typography variant="caption" color="text.secondary">
                  No recorded runs in service_audit.
                </Typography>
              )}
              {runs.runs.length > 0 && (
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell>Started</TableCell>
                      <TableCell>Ended</TableCell>
                      <TableCell>Duration</TableCell>
                      <TableCell>PID</TableCell>
                      <TableCell>Exit</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {runs.runs.map((r) => (
                      <TableRow
                        key={r.id}
                        hover
                        sx={{ cursor: "pointer" }}
                        onClick={() => onOpenLog(r)}
                      >
                        <TableCell>{fmtDateTimeShanghai(r.startedAt)}</TableCell>
                        <TableCell>{r.endedAt ? fmtDateTimeShanghai(r.endedAt) : "—"}</TableCell>
                        <TableCell>
                          {r.endedAt
                            ? fmtAge(
                                Math.round(
                                  (Date.parse(r.endedAt) - Date.parse(r.startedAt)) / 1000,
                                ),
                              )
                            : "—"}
                        </TableCell>
                        <TableCell>{r.pid}</TableCell>
                        <TableCell>
                          {r.exitReason != null
                            ? `${r.exitReason} (${r.isNormalExit ? "normal" : "abnormal"})`
                            : "running"}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
              {runs.runs.length > 0 && (
                <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.5 }}>
                  Click a run to view its service log (logs/&lt;date&gt;/&lt;service&gt;.log, tail).
                </Typography>
              )}
            </Box>
          </Collapse>
        </TableCell>
      </TableRow>
    </>
  );
}
