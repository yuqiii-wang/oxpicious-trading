/**
 * ServiceRunTrend — the MONITOR-SCOPE shared per-service run trend:
 * the service's daily run-count curve (ServiceRunTrendChart over
 * GET /api/monitor/stream/runs/daily) plus, under it, the clicked
 * day's run list narrowed to the service (DayRunsPanel). Extracted from
 * the ServiceMonitor page's RunHistoryPanel (2026-11-20) with its
 * service-picker filter REMOVED — the service now comes from the caller
 * (a clicked table row's expansion), so any monitor surface (the
 * ServiceMonitor pipeline rows, the Orchestration board rows) expands a
 * service into its run trend identically. Self-fetching on mount.
 */
import { useState } from "react";
import { Box } from "@mui/material";
import { useChartData } from "@/shared/charts/base-chart";
import {
  fetchDailyRunCounts,
  type ServiceAuditEntry,
} from "@/lib/api-client/monitor";
import ServiceRunTrendChart from "./ServiceRunTrendChart";
import DayRunsPanel from "./DayRunsPanel";

/** Trailing window of the curve (Shanghai-local days). */
export const TREND_DAYS = 90;

export default function ServiceRunTrend({
  service,
  days = TREND_DAYS,
  onOpenLog,
  showDayDrill = true,
}: {
  /** The service whose run trend this shows (a clicked row's service). */
  service: string;
  /** Trailing window in Shanghai-local days. */
  days?: number;
  /** Open one drill-down run's component log (day-panel row click);
   *  unset still shows the day list, just without log-open clicks. */
  onOpenLog?: (run: ServiceAuditEntry) => void;
  /** Mount the day drill-down panel under the curve (default true). */
  showDayDrill?: boolean;
}) {
  const [selectedDay, setSelectedDay] = useState<string | null>(null);
  const {
    data: countsData,
    loading,
    error,
  } = useChartData(() => fetchDailyRunCounts(service, days), [service, days]);

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
      <ServiceRunTrendChart
        service={service}
        dates={countsData?.days}
        counts={countsData?.counts}
        abnormalCounts={countsData?.abnormalCounts}
        selectedDate={selectedDay}
        onSelectDate={setSelectedDay}
        loading={loading}
        freezeOnLoading
        error={error != null ? `Failed to load daily run counts: ${error}` : null}
      />
      {showDayDrill && selectedDay != null && (
        <DayRunsPanel
          day={selectedDay}
          service={service}
          onClose={() => setSelectedDay(null)}
          onOpenLog={onOpenLog}
        />
      )}
    </Box>
  );
}
