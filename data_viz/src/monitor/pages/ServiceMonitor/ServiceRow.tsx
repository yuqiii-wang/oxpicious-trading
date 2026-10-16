/**
 * ServiceRow — one always-on stream service of the liveness table: the
 * heartbeat state chip (hover for the stop reason), an expandable row
 * revealing the service's watchdog restart trail (client-side filter over
 * the payload's recentRestarts window), and a ROW-CLICK log opener — the
 * service's CURRENT log (logs/<today>/<service>.log, one append-only file
 * per (date, service) shared by log_setup and the nohup'd stdout, so the
 * tail carries every restart life of the day) via the page-level
 * LogViewerDialog. The row click opens the log; the chevron alone toggles
 * the restart trail (stopPropagation keeps it from opening the log).
 */
import { Box, Chip, Collapse, Table, TableBody, TableCell, TableHead, TableRow, Tooltip, Typography } from "@mui/material";
import { ExpandLess, ExpandMore } from "@mui/icons-material";
import { fmtTimeShanghai } from "@/shared/datetime";
import type { StreamRestartEntry, StreamServiceStatus } from "@/lib/api-client/monitor";
import { STATE_CHIP } from "./constants";
import { fmtAge, liveAge } from "./fmt";

export default function ServiceRow({
  svc,
  nowMs,
  open,
  onToggle,
  onOpenLog,
  restarts,
}: {
  svc: StreamServiceStatus;
  nowMs: number;
  open: boolean;
  /** Toggle the watchdog restart trail (the chevron). */
  onToggle: () => void;
  /** Open the service's current log (the row click). */
  onOpenLog: () => void;
  restarts: StreamRestartEntry[];
}) {
  const chip = STATE_CHIP[svc.status];
  const age = liveAge(svc.beatAt, nowMs) ?? svc.beatAgeS;
  const stopTip =
    svc.status === "stopped" && svc.stopReason != null
      ? `${svc.stopReason} (normal exit: ${svc.isNormalExit ? "yes" : "no"}) at ${fmtTimeShanghai(svc.stoppedAt)}`
      : undefined;
  const statusChip = (
    <Chip size="small" color={chip.color} label={chip.label} />
  );
  return (
    <>
      <TableRow hover onClick={onOpenLog} sx={{ cursor: "pointer" }}>
        <TableCell sx={{ fontFamily: "monospace" }}>
          <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
            <Box
              component="span"
              onClick={(e) => {
                e.stopPropagation();
                onToggle();
              }}
              aria-label={open ? "collapse restart trail" : "expand restart trail"}
              sx={{ display: "inline-flex", cursor: "pointer" }}
            >
              {open ? (
                <ExpandLess fontSize="small" sx={{ color: "text.secondary" }} />
              ) : (
                <ExpandMore fontSize="small" sx={{ color: "text.secondary" }} />
              )}
            </Box>
            {svc.service}
          </Box>
        </TableCell>
        <TableCell>{svc.scope}</TableCell>
        <TableCell>{svc.subScope ?? "—"}</TableCell>
        <TableCell>
          {stopTip != null ? <Tooltip title={stopTip}>{statusChip}</Tooltip> : statusChip}
        </TableCell>
        <TableCell>{svc.pid ?? "—"}</TableCell>
        <TableCell>{svc.startedAt ? fmtTimeShanghai(svc.startedAt) : "—"}</TableCell>
        <TableCell>{fmtAge(age)}</TableCell>
        <TableCell sx={{ color: svc.restarts24h > 0 ? "warning.main" : undefined }}>
          <Tooltip
            title={`live_parallel=${svc.liveParallel ?? 0}/max_parallel=${svc.maxParallel ?? 1} — a duplicate run auto-exits past the cap`}
          >
            <span>{svc.restarts24h}</span>
          </Tooltip>
        </TableCell>
      </TableRow>
      <TableRow>
        <TableCell colSpan={8} sx={{ py: 0, border: 0 }}>
          <Collapse in={open} unmountOnExit>
            <Box sx={{ px: 5, py: 1.5 }}>
              {restarts.length === 0 ? (
                <Typography variant="caption" color="text.secondary">
                  No watchdog restarts on record.
                </Typography>
              ) : (
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell>At</TableCell>
                      <TableCell>Exited PID</TableCell>
                      <TableCell>Reason</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {restarts.map((r) => (
                      <TableRow key={r.restartedAt}>
                        <TableCell>{fmtTimeShanghai(r.restartedAt)}</TableCell>
                        <TableCell>{r.pid}</TableCell>
                        <TableCell>{r.reason}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </Box>
          </Collapse>
        </TableCell>
      </TableRow>
    </>
  );
}
