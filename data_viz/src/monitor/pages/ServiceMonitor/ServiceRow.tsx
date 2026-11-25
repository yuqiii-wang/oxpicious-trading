/**
 * ServiceRow — one always-on resident process of the liveness table: the
 * heartbeat state chip (hover for the stop reason), an expandable row
 * revealing the service's PROGRESS (the 2026-11-21 control unification
 * Phase 3 fold: the child-reported phase/state chips + the DERIVED
 * per-asset completeness the owning server computed from the data
 * tables — merged from GET /api/monitor/fleet by service name; never a
 * per-process call) and the watchdog restart trail (client-side filter
 * over the payload's recentRestarts window), and a ROW-CLICK log opener
 * — the service's CURRENT log (logs/<today>/<service>.log, one
 * append-only file per (date, service) shared by log_setup and the
 * nohup'd stdout, so the tail carries every restart life of the day)
 * via the page-level LogViewerDialog. The row click opens the log; the
 * chevron alone toggles the progress + restart trail (stopPropagation
 * keeps it from opening the log).
 */
import { Box, Chip, Collapse, Table, TableBody, TableCell, TableHead, TableRow, Tooltip, Typography } from "@mui/material";
import { ExpandLess, ExpandMore } from "@mui/icons-material";
import { fmtDate, fmtStamp, fmtTimeShanghai } from "@/shared/datetime";
import type { FleetProcessDetail, ResidentProcessStatus, ServiceCommandLabel, StreamRestartEntry } from "@/lib/api-client/monitor";
import { STATE_CHIP } from "./constants";
import { fmtAge, liveAge } from "@/monitor/fmt";
import ServiceCommandMenu from "./ServiceCommandMenu";

export default function ServiceRow({
  svc,
  nowMs,
  open,
  onToggle,
  onOpenLog,
  restarts,
  detail,
  cmdBusy,
  onCommand,
}: {
  svc: ResidentProcessStatus;
  nowMs: number;
  open: boolean;
  /** Toggle the progress + restart trail (the chevron). */
  onToggle: () => void;
  /** Open the service's current log (the row click). */
  onOpenLog: () => void;
  restarts: StreamRestartEntry[];
  /** The fleet fold's unified detail for this service (merged by the
   *  page from GET /api/monitor/fleet) — null when the fold is
   *  unreachable or the area server predates it. */
  detail: FleetProcessDetail | null;
  /** True while this service's command POST is in flight. */
  cmdBusy: boolean;
  /** Fire one downward signal (the page owns the POST + verdict). */
  onCommand: (command: ServiceCommandLabel) => void;
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
  const reported = [
    svc.progressPhase != null ? `phase ${svc.progressPhase}` : null,
    svc.progressState != null ? `state ${svc.progressState}` : null,
  ]
    .filter((s) => s != null)
    .join(" / ");
  const derived = detail?.derived_progress ?? null;
  const assets =
    derived?.kind === "intraday" ? (derived.detail.assets ?? []) : [];
  const missing = derived?.kind === "missing_set"
      ? (derived.detail as { trade_date?: string; expected_total?: number; missing?: number })
      : null;
  const staleFp = detail?.heartbeat?.fingerprint_stale === true;
  const hasProgress =
    reported !== "" || assets.length > 0 || missing != null || staleFp;
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
              aria-label={open ? "collapse progress and restart trail" : "expand progress and restart trail"}
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
          <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
            {stopTip != null ? <Tooltip title={stopTip}>{statusChip}</Tooltip> : statusChip}
            {svc.status === "paused" && (
              <Tooltip
                title="the child reports a DELIBERATE pause (Phase 4 command channel) — a fresh beat with paused is not a wedged runtime"
                arrow
              >
                <Chip size="small" variant="outlined" color="warning" label="paused" />
              </Tooltip>
            )}
          </Box>
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
        <TableCell onClick={(e) => e.stopPropagation()}>
          <ServiceCommandMenu
            service={svc.service}
            alive={svc.status === "live" || svc.status === "paused"}
            gate={detail?.config ?? null}
            busy={cmdBusy}
            onCommand={onCommand}
          />
        </TableCell>
      </TableRow>
      <TableRow>
        <TableCell colSpan={9} sx={{ py: 0, border: 0 }}>
          <Collapse in={open} unmountOnExit>
            <Box sx={{ px: 5, py: 1.5, display: "flex", flexDirection: "column", gap: 1 }}>
              {staleFp && (
                <Typography variant="caption" color="warning.main">
                  Code changed under the running process (heartbeat fingerprint
                  drifted from disk) — the supervisor restarts it after the
                  debounce.
                </Typography>
              )}
              {hasProgress ? (
                <>
                  {reported !== "" && (
                    <Typography variant="caption" color="text.secondary">
                      Reported progress: {reported}
                      {svc.progressAt != null
                        ? ` — reported ${fmtTimeShanghai(svc.progressAt)}` +
                          (svc.progressAgeS != null
                            ? ` (${fmtAge(svc.progressAgeS)} ago)`
                            : "")
                        : ""}
                    </Typography>
                  )}
                  {assets.length > 0 && (
                    <Table size="small" sx={{ maxWidth: 640 }}>
                      <TableHead>
                        <TableRow>
                          <TableCell>Asset</TableCell>
                          <TableCell>Date</TableCell>
                          <TableCell>Codes done</TableCell>
                          <TableCell>Latest bar</TableCell>
                          <TableCell>Close</TableCell>
                        </TableRow>
                      </TableHead>
                      <TableBody>
                        {assets.map((a) => {
                          const p = a.progress ?? {};
                          const pct =
                            p.n_total_codes != null && p.n_total_codes > 0 &&
                            p.n_codes_done != null
                              ? (p.n_codes_done / p.n_total_codes) * 100
                              : null;
                          return (
                            <TableRow key={a.name ?? a.intraday_table ?? ""}>
                              <TableCell sx={{ fontFamily: "monospace" }}>
                                {a.name ?? "—"}
                              </TableCell>
                              <TableCell>{fmtDate(p.trade_date)}</TableCell>
                              <TableCell>
                                {p.n_codes_done != null && p.n_total_codes != null
                                  ? `${p.n_codes_done.toLocaleString()} / ${p.n_total_codes.toLocaleString()}` +
                                    (pct != null ? ` (${pct.toFixed(1)}%)` : "")
                                  : "—"}
                              </TableCell>
                              <TableCell>{fmtStamp(p.max_time)}</TableCell>
                              <TableCell>
                                {p.close_reached === true ? (
                                  <Chip size="small" color="success" label="reached" />
                                ) : p.close_reached === false ? (
                                  <Chip size="small" color="default" label="pending" />
                                ) : (
                                  "—"
                                )}
                              </TableCell>
                            </TableRow>
                          );
                        })}
                      </TableBody>
                    </Table>
                  )}
                  {missing != null && (
                    <Typography variant="caption" color="text.secondary">
                      Missing set: {missing.missing ?? "—"} of{" "}
                      {missing.expected_total ?? "—"} keys missing for{" "}
                      {fmtDate(missing.trade_date)} (expected − missing, the
                      owning server's derived oracle).
                    </Typography>
                  )}
                </>
              ) : (
                detail != null && (
                  <Typography variant="caption" color="text.secondary">
                    No derived or reported progress on record (no service_config
                    progress block, never reported a phase).
                  </Typography>
                )
              )}
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
