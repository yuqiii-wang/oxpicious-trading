/**
 * External Resources page — the monitoring tab over the external request
 * audit: monitor.external_resource_stats (per service × status_code ×
 * date, computed by python -m database.external_resource_stats) as
 *
 *   1. a trend chart — stacked per-service-group request bars per date +
 *      a total-failed line, dataZoom window (default trailing 30 days);
 *      services consolidate into display groups (serviceGroups.ts) —
 *      the full dotted names stay in the day breakdown and audit rows;
 *   2. ON CLICK of a date, a per-day breakdown of that date's
 *      (service, status_code) buckets — requests / failed / avg / max
 *      duration;
 *   3. ON CLICK of a bucket row, the raw monitor.external_resource_audit
 *      rows behind it (fetched on first expand via
 *      /api/monitor/ext-resources/audit).
 *
 * The stats payload is fetched whole (small by construction: days ×
 * services × statuses) and pivoted client-side; only the audit drill-down
 * hits the network per bucket.
 */
import { useMemo, useState } from "react";
import { Alert, Box, Typography } from "@mui/material";
import { useChartData } from "@/shared/charts/base-chart";
import {
  fetchExtResourceStats,
  type ExtResourceStatEntry,
} from "@/lib/api-client/monitor";
import { fmtDateTimeShanghai } from "@/shared/datetime";
import ExtResourceTrendChart from "./ExtResourceTrendChart";
import DateBreakdownTable from "./DateBreakdownTable";
import { serviceGroup, serviceGroups } from "./serviceGroups";

const DAYS = 30;

const NO_DATES: string[] = [];
const NO_SERVICES: string[] = [];
const NO_ROWS: ExtResourceStatEntry[] = [];

export default function ExternalResourcesPage() {
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const { data, loading, error } = useChartData(
    () => fetchExtResourceStats(DAYS),
    [],
  );

  const dates = data?.dates ?? NO_DATES;
  const services = data?.services ?? NO_SERVICES;
  const rows = data?.rows ?? NO_ROWS;

  // Pivot the grain rows into the trend series: per-group request counts
  // index-aligned with `dates`, plus the per-date total failed. Services
  // consolidate into display groups for the chart (stream.<source> keeps
  // the dominant pollers separate; the rest collapses to its category) —
  // the day breakdown below still lists exact services.
  const { perService, failed } = useMemo(
    () => ({
      perService: serviceGroups(services).map((group) => ({
        service: group,
        counts: dates.map((d) => {
          let n = 0;
          for (const r of rows) {
            if (r.date === d && serviceGroup(r.service) === group) {
              n += r.requestCount;
            }
          }
          return n;
        }),
      })),
      failed: dates.map((d) => {
        let n = 0;
        for (const r of rows) {
          if (r.date === d) n += r.failedCount;
        }
        return n;
      }),
    }),
    [dates, services, rows],
  );

  // The selected day's buckets, worst first (failed → requests) — the
  // drill-down reads from the already-fetched payload, no second fetch.
  const dayRows = useMemo(() => {
    if (selectedDate == null) return [];
    return rows
      .filter((r) => r.date === selectedDate)
      .sort(
        (a, b) =>
          b.failedCount - a.failedCount || b.requestCount - a.requestCount,
      );
  }, [rows, selectedDate]);

  const totalRequests = rows.reduce((a, r) => a + r.requestCount, 0);
  const totalFailed = rows.reduce((a, r) => a + r.failedCount, 0);
  const computedAt = data?.computedAt ?? null;

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <Box sx={{ display: "flex", alignItems: "center", gap: 2, flexWrap: "wrap" }}>
        <Typography variant="h6">External Resources</Typography>
        <Typography variant="caption" color="text.secondary">
          trailing {DAYS}d · {totalRequests.toLocaleString()} requests ·{" "}
          {totalFailed.toLocaleString()} failed
          {computedAt != null
            ? ` · stats computed ${fmtDateTimeShanghai(computedAt)}`
            : " · stats never computed"}
        </Typography>
      </Box>

      {error && <Alert severity="error">{String(error)}</Alert>}

      <ExtResourceTrendChart
        dates={dates}
        perService={perService}
        failed={failed}
        selectedDate={selectedDate}
        onSelectDate={setSelectedDate}
        loading={loading}
        error={error ? `Failed to load external-resource stats: ${error}` : null}
      />

      {selectedDate != null && <DateBreakdownTable date={selectedDate} rows={dayRows} />}
    </Box>
  );
}
