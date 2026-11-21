/**
 * DayRunsPanel — the day drill-down under a run-trend curve: the runs of
 * ONE Shanghai-local day (GET /api/monitor/stream/runs/day), optionally
 * narrowed to ONE service (the per-service trend's drill; unset = every
 * service — the all-services day view), grouped by service with the
 * same run-table format as a per-service expansion (Started / Ended /
 * Duration / PID / Exit). Rows are clickable (onOpenLog) and open that
 * run's service log in the page-level LogViewerDialog. Self-fetching on
 * mount — mounted only while a day is selected.
 */
import { Box, CircularProgress, IconButton, Paper, Table, TableBody, TableCell, TableHead, TableRow, Typography } from "@mui/material";
import { Close } from "@mui/icons-material";
import { useChartData } from "@/shared/charts/base-chart";
import { fetchDayRuns, type ServiceAuditEntry } from "@/lib/api-client/monitor";
import { fmtDateTimeShanghai } from "@/shared/datetime";
import { fmtAge } from "@/monitor/fmt";

export default function DayRunsPanel({
  day,
  service,
  onClose,
  onOpenLog,
}: {
  /** The selected Shanghai-local day (YYYY-MM-DD). */
  day: string;
  /** Narrow the day's runs to one service (the trend drill); unset =
   *  every service. */
  service?: string;
  /** Clear the selection (the ✕ in the header). */
  onClose: () => void;
  /** Open one run's component log (row click); unset renders the rows
   *  without the click affordance (the caller opts in). */
  onOpenLog?: (run: ServiceAuditEntry) => void;
}) {
  const { data, loading, error } = useChartData(() => fetchDayRuns(day), [day]);
  const allRuns = data ?? [];
  const runs = service != null
    ? allRuns.filter((r) => r.service === service)
    : allRuns;
  const abnormal = runs.filter((r) => r.isNormalExit === false).length;
  // Group by service, preserving first-start order (rows come oldest
  // first from the query).
  const byService = new Map<string, ServiceAuditEntry[]>();
  for (const r of runs) {
    const list = byService.get(r.service);
    if (list != null) list.push(r);
    else byService.set(r.service, [r]);
  }

  const runTable = (serviceRuns: ServiceAuditEntry[]) => (
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
        {serviceRuns.map((r) => (
          <TableRow
            key={r.id}
            hover
            sx={onOpenLog != null ? { cursor: "pointer" } : undefined}
            onClick={onOpenLog != null ? () => onOpenLog(r) : undefined}
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
  );

  return (
    <Paper sx={{ px: 2, py: 1.5 }}>
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 1,
          flexWrap: "wrap",
          mb: 0.5,
        }}
      >
        <Typography variant="subtitle2">Pipeline runs on {day}</Typography>
        <Typography variant="caption" color="text.secondary">
          {loading
            ? "loading…"
            : error != null
              ? ""
              : `${runs.length} run${runs.length === 1 ? "" : "s"}${service != null ? "" : ` across ${byService.size} service${byService.size === 1 ? "" : "s"}`}${abnormal > 0 ? ` · ${abnormal} abnormal` : ""}`}
        </Typography>
        <Box sx={{ flexGrow: 1 }} />
        <IconButton size="small" aria-label="close day runs" onClick={onClose}>
          <Close fontSize="small" />
        </IconButton>
      </Box>
      {loading && <CircularProgress size={16} sx={{ my: 1 }} />}
      {error != null && (
        <Typography variant="caption" color="error">
          Failed to load the day's runs: {error}
        </Typography>
      )}
      {!loading && error == null && runs.length === 0 && (
        <Typography variant="caption" color="text.secondary">
          No runs on record for this {service != null ? "service on this day" : "day"}.
        </Typography>
      )}
      {[...byService.entries()].map(([svc, serviceRuns]) => (
        <Box key={svc} sx={{ mb: 1.5 }}>
          <Typography
            variant="caption"
            sx={{ fontFamily: "monospace", display: "block", mb: 0.25 }}
          >
            {svc}{" "}
            <span style={{ color: "text.secondary" }}>
              ({serviceRuns.length})
            </span>
          </Typography>
          {runTable(serviceRuns)}
        </Box>
      ))}
      {runs.length > 0 && onOpenLog != null && (
        <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.5 }}>
          Click a run to view its service log (logs/&lt;date&gt;/&lt;service&gt;.log, tail).
        </Typography>
      )}
    </Paper>
  );
}
