/**
 * AlertsPanel — the write-heavy alert trail (GET /api/monitor/db/alerts):
 * monitor.db_table_alert's flag STATE TRANSITIONS, newest first — every
 * raise / downgrade / clear with the tripping rate vs the threshold in
 * effect. This is the monitor log behind the amber/red flags.
 */
import { Alert, Chip, Paper, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Typography } from "@mui/material";
import { useChartData } from "@/shared/charts/base-chart";
import { fetchDbAlerts } from "@/lib/api-client/monitor";
import { fmtDateTimeShanghai } from "@/shared/datetime";
import { fmtRate } from "./fmt";

const SEVERITY_COLOR: Record<number, "default" | "warning" | "error"> = {
  0: "default",
  1: "warning",
  2: "error",
};

export default function AlertsPanel() {
  const { data, loading, error } = useChartData(() => fetchDbAlerts(50), []);
  const rows = data?.rows ?? [];
  return (
    <Paper variant="outlined" sx={{ p: 2 }}>
      <Typography variant="subtitle2" gutterBottom>
        Write-heavy alerts
      </Typography>
      <Typography variant="caption" color="text.secondary" sx={{ mb: 1, display: "block" }}>
        flag transitions (raise / downgrade / clear) from monitor.db_table_alert ·
        newest first · thresholds amber 100 / red 1000 rows/s (sampler constants, env-tunable)
      </Typography>
      {error != null && <Alert severity="error">Failed to load alerts: {error}</Alert>}
      {!error && loading && (
        <Typography variant="body2" color="text.secondary">loading…</Typography>
      )}
      {!error && !loading && rows.length === 0 && (
        <Typography variant="body2" color="text.secondary">
          No write-heavy alerts on record — no table has crossed a threshold.
        </Typography>
      )}
      {rows.length > 0 && (
        <TableContainer sx={{ maxHeight: 320 }}>
          <Table stickyHeader size="small">
            <TableHead>
              <TableRow>
                <TableCell>Raised</TableCell>
                <TableCell>Table</TableCell>
                <TableCell align="center">Severity</TableCell>
                <TableCell align="right">Rate</TableCell>
                <TableCell align="right">Threshold</TableCell>
                <TableCell>Message</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {rows.map((r) => (
                <TableRow key={r.id}>
                  <TableCell sx={{ whiteSpace: "nowrap", fontVariantNumeric: "tabular-nums" }}>
                    {fmtDateTimeShanghai(r.raisedAt)}
                  </TableCell>
                  <TableCell sx={{ whiteSpace: "nowrap" }}>
                    {r.schema}.{r.table}
                  </TableCell>
                  <TableCell align="center">
                    <Chip
                      size="small"
                      variant="outlined"
                      color={SEVERITY_COLOR[r.severity] ?? "default"}
                      label={r.severity === 0 ? "cleared" : r.severityLabel}
                    />
                  </TableCell>
                  <TableCell align="right" sx={{ fontVariantNumeric: "tabular-nums" }}>
                    {fmtRate(r.writeRate)}
                  </TableCell>
                  <TableCell align="right" sx={{ fontVariantNumeric: "tabular-nums" }}>
                    {fmtRate(r.thresholdRate)}
                  </TableCell>
                  <TableCell sx={{ maxWidth: 420, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {r.message}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}
    </Paper>
  );
}
