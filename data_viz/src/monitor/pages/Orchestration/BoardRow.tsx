/**
 * BoardRow — one service row of the Orchestration page's three section
 * tables (running / to-run queue / recently no need). The whole row
 * click toggles the expansion: the monitor-scope shared ServiceRunTrend
 * (the service's daily run-count curve; click a day for its run list,
 * each run click opens its log in the page-level LogViewerDialog). The
 * cells shown depend on the section variant (each section renders its
 * own matching table head).
 */
import { Box, Chip, Collapse, TableCell, TableRow, Tooltip } from "@mui/material";
import { ExpandLess, ExpandMore } from "@mui/icons-material";
import { fmtDateTimeShanghai } from "@/shared/datetime";
import type {
  OrchestrationBoardTask,
  ServiceAuditEntry,
} from "@/lib/api-client/monitor";
import { fmtAge, liveAge } from "@/monitor/fmt";
import ServiceRunTrend from "@/monitor/components/service-run-trend";

export type BoardSection = "running" | "queue" | "noNeed";

export default function BoardRow({
  task,
  section,
  nowMs,
  open,
  onToggle,
  onOpenLog,
}: {
  task: OrchestrationBoardTask;
  section: BoardSection;
  nowMs: number;
  open: boolean;
  onToggle: () => void;
  /** Open one drill-down run's component log (trend day-panel click). */
  onOpenLog: (run: ServiceAuditEntry) => void;
}) {
  // The non-daily completeness tooltip (queue rows): missing count,
  // attempts, the next Saturday gate — when the task has a state row.
  const ndTip =
    section === "queue" && task.ndState != null
      ? [
          `non-daily: ${task.ndState}`,
          task.ndMissingCount != null && task.ndMissingCount > 0
            ? `${task.ndMissingCount} missing`
            : null,
          task.ndAttempts != null && task.ndAttempts > 0
            ? `${task.ndAttempts} backfill attempt(s)`
            : null,
          task.ndNextCheckAt != null
            ? `next check ${fmtDateTimeShanghai(task.ndNextCheckAt)}`
            : null,
        ]
          .filter((s): s is string => s != null)
          .join(" · ")
      : null;
  const freqTip = [
    task.schedule,
    task.sourceRef != null ? `config: ${task.sourceRef}` : null,
  ]
    .filter((s): s is string => s != null && s !== "")
    .join("\n");
  const serviceCell = (
    <TableCell sx={{ fontFamily: "monospace" }}>
      <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
        {open ? (
          <ExpandLess fontSize="small" sx={{ color: "text.secondary" }} />
        ) : (
          <ExpandMore fontSize="small" sx={{ color: "text.secondary" }} />
        )}
        {task.service}
      </Box>
    </TableCell>
  );
  const freqCell = (
    <TableCell>
      {freqTip !== "" ? (
        <Tooltip title={freqTip}>
          <span>{task.freq}</span>
        </Tooltip>
      ) : (
        task.freq
      )}
    </TableCell>
  );

  return (
    <>
      <TableRow hover onClick={onToggle} sx={{ cursor: "pointer" }}>
        {serviceCell}
        <TableCell sx={{ fontFamily: "monospace" }}>{task.tier}</TableCell>
        {section === "running" && (
          <>
            {freqCell}
            <TableCell>{task.pid ?? "—"}</TableCell>
            <TableCell>{fmtDateTimeShanghai(task.startedAt)}</TableCell>
            <TableCell>
              {fmtAge(liveAge(task.beatAt, nowMs) ?? task.beatAgeS)}
            </TableCell>
          </>
        )}
        {section === "queue" && (
          <>
            <TableCell align="right" sx={{ fontFamily: "monospace" }}>
              {task.step}
            </TableCell>
            {freqCell}
            <TableCell align="right">
              <Tooltip title="the service_config priority — downloads outrank non-downloads, then frequency (the give-way rank)">
                <span>{task.priority}</span>
              </Tooltip>
            </TableCell>
            <TableCell>
              <Tooltip
                title={
                  task.lastSuccessAt != null
                    ? `last successful exit ${fmtDateTimeShanghai(task.lastSuccessAt)}`
                    : "no successful exit on record"
                }
              >
                <span>
                  {task.lastSuccessAt != null
                    ? fmtAge(
                        Math.round(
                          (nowMs - Date.parse(task.lastSuccessAt)) / 1000,
                        ),
                      )
                    : "never"}
                </span>
              </Tooltip>
            </TableCell>
            <TableCell>
              {task.dueAt != null ? (
                <Tooltip title="the calendar boundary that put it back in the queue">
                  <span>{fmtDateTimeShanghai(task.dueAt)}</span>
                </Tooltip>
              ) : (
                <Tooltip title="no window to wait out — runs every pass (never succeeded / always / occasionally / on_demand)">
                  <span>every pass</span>
                </Tooltip>
              )}
            </TableCell>
            <TableCell>
              {ndTip != null ? (
                <Tooltip title={ndTip}>
                  <Chip
                    size="small"
                    variant="outlined"
                    color={task.ndState === "missing" ? "error" : "default"}
                    label={task.ndState}
                  />
                </Tooltip>
              ) : (
                "—"
              )}
            </TableCell>
          </>
        )}
        {section === "noNeed" && (
          <>
            {freqCell}
            <TableCell>
              <Tooltip
                title={
                  task.lastSuccessAt != null
                    ? `last successful exit ${fmtDateTimeShanghai(task.lastSuccessAt)}`
                    : "no successful exit on record"
                }
              >
                <span>
                  {task.lastSuccessAt != null
                    ? fmtDateTimeShanghai(task.lastSuccessAt)
                    : "never"}
                </span>
              </Tooltip>
            </TableCell>
            <TableCell>{fmtDateTimeShanghai(task.dueAt)}</TableCell>
            <TableCell>{task.reason ?? "—"}</TableCell>
          </>
        )}
      </TableRow>
      <TableRow>
        <TableCell
          colSpan={section === "queue" ? 8 : 6}
          sx={{ py: 0, border: 0 }}
        >
          <Collapse in={open} unmountOnExit>
            <Box sx={{ px: 4, py: 1.5 }}>
              <ServiceRunTrend service={task.service} onOpenLog={onOpenLog} />
            </Box>
          </Collapse>
        </TableCell>
      </TableRow>
    </>
  );
}
