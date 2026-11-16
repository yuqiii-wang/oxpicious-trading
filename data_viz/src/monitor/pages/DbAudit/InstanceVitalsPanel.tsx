/**
 * InstanceVitalsPanel — the DB tab's instance half: the LATEST
 * monitor.db_health row per instance (the 60s sampler — reachability,
 * latency, connections, cache hit, WAL shipping state + replay backlog,
 * pg_wal size, container CPU/mem) plus the trailing connections /
 * backlog trend. Surfaces data that existed in the DB but had no UI
 * before this tab.
 */
import { useMemo } from "react";
import { Alert, Box, Chip, Grid, Paper, Typography } from "@mui/material";
import {
  BaseChart,
  useChartData,
  useChartThemeMode,
} from "@/shared/charts/base-chart";
import { fetchDbInstances, type DbInstanceVitals, type DbInstanceTrendPoint } from "@/lib/api-client/monitor";
import { fmtBytes, fmtCount } from "./fmt";
import { buildInstanceTrendOption } from "./instanceTrendOption";

/** Identity-stable empty defaults — fresh [] per render would dirty the
 *  option memo and force a full notMerge rebuild every render. */
const NO_LATEST: DbInstanceVitals[] = [];
const NO_TREND: DbInstanceTrendPoint[] = [];

function fmtUptime(s: number | null): string {
  if (s == null) return "—";
  if (s < 5400) return `${Math.round(s)}s`;
  if (s < 172800) return `${(s / 3600).toFixed(1)}h`;
  return `${(s / 86400).toFixed(1)}d`;
}

function VitalsGrid({ v }: { v: DbInstanceVitals }) {
  const cells: Array<[string, string]> = [
    ["connect", v.connectMs != null ? `${v.connectMs}ms` : "—"],
    ["conns", fmtCount(v.activeConnections)],
    ["cache hit", v.cacheHitRatio != null ? `${(v.cacheHitRatio * 100).toFixed(2)}%` : "—"],
    ["backlog", fmtBytes(v.replayBacklogBytes)],
    ["wal dir", fmtBytes(v.walDirBytes)],
    ["db size", fmtBytes(v.dbSizeBytes)],
    ["cpu", v.cpuPct != null ? `${v.cpuPct.toFixed(1)}%` : "—"],
    ["mem", v.memPct != null ? `${v.memPct.toFixed(1)}%` : "—"],
    ["uptime", fmtUptime(v.uptimeS)],
  ];
  return (
    <Box sx={{ display: "flex", gap: 2, flexWrap: "wrap" }}>
      {cells.map(([k, val]) => (
        <Box key={k}>
          <Typography variant="caption" color="text.secondary">{k}</Typography>
          <Typography variant="body2" sx={{ fontVariantNumeric: "tabular-nums" }}>
            {val}
          </Typography>
        </Box>
      ))}
    </Box>
  );
}

function InstanceCard({ v }: { v: DbInstanceVitals }) {
  return (
    <Paper variant="outlined" sx={{ p: 1.5 }}>
      <Box sx={{ display: "flex", alignItems: "center", gap: 1, mb: 0.5 }}>
        <Typography variant="subtitle2">{v.instanceLabel}</Typography>
        <Chip
          size="small"
          variant="outlined"
          color={v.healthy ? "success" : "error"}
          label={v.healthy ? "healthy" : "unhealthy"}
        />
        {v.replicationStateLabel != null && (
          <Chip
            size="small"
            variant="outlined"
            color={v.replicationState === 0 ? "success" : "warning"}
            label={v.replicationStateLabel}
          />
        )}
      </Box>
      <VitalsGrid v={v} />
      {v.error != null && (
        <Typography variant="caption" color="error.main" sx={{ display: "block", mt: 0.5 }}>
          {v.error}
        </Typography>
      )}
    </Paper>
  );
}

export default function InstanceVitalsPanel({ hours }: { hours: number }) {
  const themeMode = useChartThemeMode();
  const { data, loading, error } = useChartData(
    () => fetchDbInstances(hours),
    [hours],
  );
  const latest = data?.latest ?? NO_LATEST;
  const trend = data?.trend ?? NO_TREND;
  const instanceLabels = useMemo(
    () => Object.fromEntries(latest.map((v) => [v.instance, v.instanceLabel])),
    [latest],
  );
  const option = useMemo(
    () =>
      trend.length > 0 ? buildInstanceTrendOption(trend, instanceLabels, themeMode) : null,
    [trend, instanceLabels, themeMode],
  );
  return (
    <Paper variant="outlined" sx={{ p: 2 }}>
      <Typography variant="subtitle2" gutterBottom>
        Instance vitals
      </Typography>
      <Typography variant="caption" color="text.secondary" sx={{ mb: 1, display: "block" }}>
        monitor.db_health · sampled every 60s by database.db_health.py · conns (solid) +
        replay backlog MB (dashed, right axis){" "}
        {hours === 0 ? "in the full retained history (archive)" : `over ${hours}h`}
      </Typography>
      {error != null && <Alert severity="error">Failed to load instance vitals: {error}</Alert>}
      {!error && loading && latest.length === 0 && (
        <Typography variant="body2" color="text.secondary">loading…</Typography>
      )}
      <Grid container spacing={1} sx={{ mb: 1 }}>
        {latest.map((v) => (
          <Grid key={v.instance} item xs={12} md={6}>
            <InstanceCard v={v} />
          </Grid>
        ))}
      </Grid>
      <BaseChart
        variant="bare"
        option={option}
        // Plain `loading` (not gated on first load): a window-selector
        // refetch overlays the corner spinner while stale rows stay up.
        loading={loading}
        emptyText="No db_health samples in the window"
        height={220}
      />
    </Paper>
  );
}
