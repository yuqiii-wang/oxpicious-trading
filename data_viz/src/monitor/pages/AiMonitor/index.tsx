/**
 * AI Monitor page — the Monitor app's AI tab, a usage + runs audit over
 * the llm_agents layer:
 *
 *   • USAGE — text.llm_requests (the source request log every llm_agents
 *     provider writes) aggregated by GET /api/monitor/ai/usage per
 *     (service, model, GMT date) over a trailing window: a KPI row
 *     (requests / failed / token sums), a trend chart (stacked per-
 *     service token bars + a request-count line on a second axis), and
 *     ON CLICK of a date the day's service × model breakdown.
 *
 *   • DEEP AGENTS — the runs panel below: the daemon's in-flight runs
 *     plus the recent run history, each row opening the AI summary's
 *     LangGraph thread view on click (RunsPanel).
 *
 * The usage fetch is live (no-store) with a window selector + refresh;
 * the runs panel subscribes to the AI event channel so a generation
 * started on the AI page shows up here the moment it registers.
 */
import { useMemo, useState } from "react";
import {
  Alert,
  Box,
  Chip,
  IconButton,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from "@mui/material";
import { Refresh } from "@mui/icons-material";
import { useChartData } from "@/shared/charts/base-chart";
import { fetchAiUsage, type AiUsageEntry } from "@/lib/api-client/monitor";
import { fmtDateTimeShanghai } from "@/shared/datetime";
import UsageTrendChart from "./UsageTrendChart";
import UsageDayBreakdown from "./UsageDayBreakdown";
import RunsPanel from "./RunsPanel";

const WINDOWS = [7, 14, 30] as const;

const NO_ROWS: AiUsageEntry[] = [];

export default function AiMonitorPage() {
  const [days, setDays] = useState<number>(14);
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const { data, loading, error, refresh } = useChartData(
    () => fetchAiUsage(days),
    [days],
  );

  const rows = data?.rows ?? NO_ROWS;
  const kpis = useMemo(() => {
    const requests = rows.reduce((a, r) => a + r.requestCount, 0);
    const failed = rows.reduce((a, r) => a + r.failedCount, 0);
    const inputTokens = rows.reduce((a, r) => a + r.inputTokens, 0);
    const outputTokens = rows.reduce((a, r) => a + r.outputTokens, 0);
    const totalTokens = rows.reduce((a, r) => a + r.totalTokens, 0);
    return { requests, failed, inputTokens, outputTokens, totalTokens };
  }, [rows]);

  // Trend pivots — index-aligned with data.dates: one token series per
  // service (stacked bars) + the per-date request count (line axis).
  const perService = useMemo(() => {
    const dates = data?.dates ?? [];
    return (data?.services ?? []).map((service) => ({
      service,
      tokens: dates.map((d) =>
        rows
          .filter((r) => r.service === service && r.date === d)
          .reduce((a, r) => a + r.totalTokens, 0),
      ),
    }));
  }, [data, rows]);
  const requestsPerDate = useMemo(
    () =>
      (data?.dates ?? []).map((d) =>
        rows.filter((r) => r.date === d).reduce((a, r) => a + r.requestCount, 0),
      ),
    [data, rows],
  );
  const dayRows = useMemo(
    () => (selectedDate ? rows.filter((r) => r.date === selectedDate) : []),
    [rows, selectedDate],
  );

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 2, p: 2 }}>
      <Box
        sx={{ display: "flex", alignItems: "center", gap: 2, flexWrap: "wrap" }}
      >
        <Typography variant="h6">AI Usage</Typography>
        <Typography variant="caption" color="text.secondary">
          {data
            ? `text.llm_requests audit · aggregated ${fmtDateTimeShanghai(data.generatedAt)}`
            : "loading…"}
        </Typography>
        <Box sx={{ flexGrow: 1 }} />
        <ToggleButtonGroup
          size="small"
          exclusive
          value={days}
          onChange={(_, v) => {
            if (v != null) {
              setDays(v);
              setSelectedDate(null);
            }
          }}
        >
          {WINDOWS.map((w) => (
            <ToggleButton key={w} value={w}>
              {w}d
            </ToggleButton>
          ))}
        </ToggleButtonGroup>
        <Tooltip title="Reload the usage aggregation">
          <span>
            <IconButton size="small" aria-label="refresh" onClick={refresh}>
              <Refresh fontSize="small" />
            </IconButton>
          </span>
        </Tooltip>
      </Box>

      {error != null && (
        <Alert severity="error">Failed to load AI usage: {error}</Alert>
      )}

      <Box sx={{ display: "flex", gap: 1, flexWrap: "wrap" }}>
        <Chip
          label={`${kpis.requests.toLocaleString()} requests`}
          variant="outlined"
        />
        <Chip
          label={`${kpis.failed.toLocaleString()} failed`}
          variant="outlined"
          color={kpis.failed > 0 ? "error" : "default"}
        />
        <Chip
          label={`${kpis.inputTokens.toLocaleString()} input tokens`}
          variant="outlined"
        />
        <Chip
          label={`${kpis.outputTokens.toLocaleString()} output tokens`}
          variant="outlined"
        />
        <Chip
          label={`${kpis.totalTokens.toLocaleString()} total tokens`}
          variant="outlined"
          color="primary"
        />
      </Box>

      <UsageTrendChart
        dates={data?.dates}
        perService={perService}
        requests={requestsPerDate}
        selectedDate={selectedDate}
        onSelectDate={setSelectedDate}
        loading={loading}
        error={error != null ? String(error) : null}
      />

      {selectedDate != null && (
        <UsageDayBreakdown date={selectedDate} rows={dayRows} />
      )}

      <RunsPanel />
    </Box>
  );
}
