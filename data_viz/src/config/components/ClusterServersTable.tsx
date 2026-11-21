/**
 * ClusterServersTable — the Service Config page's SERVER CLUSTER view (the
 * header toggle): one row per resident FastAPI area server (server_cluster/,
 * area→port from cluster_config.json) showing its HEARTBEAT state (the
 * monitor.service_heartbeat row of server_cluster.<area> — live / stale /
 * stopped / down vs the 240s stale threshold, the same vocabulary as the
 * Service Monitor's resident rows; STATE_CHIP / fmtAge / liveAge below are
 * deliberate mirrors of that page's constants). Clicking a row expands it
 * into the server's EXPOSED API surface — the endpoints of its FastAPI
 * openapi.json (the machine-readable twin of the /docs page), tag-grouped
 * with per-method chips; the fetch fires lazily on first expand, and a
 * down server surfaces the 502 message instead of the surface. Read-only:
 * the servers are supervised residents (start/stop lives on the Service
 * Monitor page), so this view has no controls beyond Refresh.
 */
import { useCallback, useEffect, useState, type ReactNode } from "react";
import {
  Alert,
  Box,
  Chip,
  CircularProgress,
  Collapse,
  IconButton,
  Link,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
} from "@mui/material";
import ExpandLess from "@mui/icons-material/ExpandLess";
import ExpandMore from "@mui/icons-material/ExpandMore";
import OpenInNew from "@mui/icons-material/OpenInNew";
import Refresh from "@mui/icons-material/Refresh";
import {
  fetchClusterOpenApi,
  fetchClusterServers,
  type ClusterApiGroup,
  type ClusterOpenApiSurface,
  type ClusterServerStatus,
} from "@/lib/api-client/config";
import { fmtDateTimeShanghai, fmtTimeShanghai } from "@/shared/datetime";

/** Table auto-refresh cadence (beat ages tick client-side every 1s). */
const REFRESH_MS = 30_000;

/** Mirror of the Service Monitor page's STATE_CHIP (ResidentProcessStatus
 *  vocabulary — the cluster servers ARE resident rows). */
const STATE_CHIP: Record<
  ClusterServerStatus["status"],
  { label: string; color: "success" | "warning" | "error" | "default" }
> = {
  live: { label: "live", color: "success" },
  stale: { label: "stale", color: "warning" },
  stopped: { label: "stopped", color: "default" },
  down: { label: "down", color: "error" },
};

/** HTTP verb → chip color for the surface table's method column. */
const METHOD_COLOR: Record<string, "success" | "secondary" | "warning" | "error" | "info" | "default"> = {
  get: "success",
  post: "secondary",
  put: "warning",
  patch: "info",
  delete: "error",
};

/** Compact duration — "42s" / "12m" / "3.4h"; null → em dash (mirror of
 *  the Service Monitor page's fmt.ts). */
function fmtAge(s: number | null): string {
  if (s == null) return "—";
  if (s < 90) return `${s}s`;
  if (s < 5400) return `${Math.round(s / 60)}m`;
  return `${(s / 3600).toFixed(1)}h`;
}

/** Age of ``iso`` as of the ticking ``nowMs`` (client-side, 1s cadence). */
function liveAge(iso: string | null, nowMs: number): number | null {
  if (!iso) return null;
  return Math.max(0, Math.round((nowMs - Date.parse(iso)) / 1000));
}

/** Fetch state of one expanded server's API surface. */
interface SurfaceState {
  loading: boolean;
  error: string | null;
  surface: ClusterOpenApiSurface | null;
}

const NO_SURFACE: SurfaceState = { loading: false, error: null, surface: null };

export default function ClusterServersTable() {
  const [rows, setRows] = useState<ClusterServerStatus[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());
  const [surfaceByArea, setSurfaceByArea] = useState<Map<string, SurfaceState>>(
    new Map(),
  );
  const [nowMs, setNowMs] = useState(() => Date.now());

  const load = useCallback(async () => {
    setRefreshing(true);
    try {
      const resp = await fetchClusterServers();
      setRows(resp.rows);
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, REFRESH_MS);
    return () => clearInterval(id);
  }, [load]);

  useEffect(() => {
    const id = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  // Lazy surface fetch: expanding a row loads its openapi surface once
  // (a failed fetch clears the cache entry so the next expand retries).
  const toggle = useCallback(
    (area: string) => {
      setExpanded((prev) => {
        const next = new Set(prev);
        if (next.has(area)) {
          next.delete(area);
        } else {
          next.add(area);
          setSurfaceByArea((s) =>
            s.has(area) ? s : new Map(s).set(area, { ...NO_SURFACE, loading: true }),
          );
          fetchClusterOpenApi(area)
            .then((surface) =>
              setSurfaceByArea((s) =>
                new Map(s).set(area, { loading: false, error: null, surface }),
              ),
            )
            .catch((e) =>
              setSurfaceByArea((s) =>
                new Map(s).set(area, {
                  loading: false,
                  error: String(e).replace(/^Error: /, ""),
                  surface: null,
                }),
              ),
            );
        }
        return next;
      });
    },
    [],
  );

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
      <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
        <Typography variant="caption" color="text.secondary">
          {rows != null
            ? `${rows.length} resident FastAPI servers · heartbeat from monitor.service_heartbeat (stale after 240s) · expand a row for its exposed APIs (FastAPI openapi)`
            : "loading…"}
        </Typography>
        <Box sx={{ flexGrow: 1 }} />
        {refreshing && <CircularProgress size={16} />}
        <Tooltip title="Reload heartbeats">
          <span>
            <IconButton size="small" aria-label="reload cluster" onClick={load}>
              <Refresh fontSize="small" />
            </IconButton>
          </span>
        </Tooltip>
      </Box>

      {error && <Alert severity="error">{error}</Alert>}

      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Server</TableCell>
            <TableCell>Base URL</TableCell>
            <TableCell>Heartbeat</TableCell>
            <TableCell align="right">PID</TableCell>
            <TableCell>Started</TableCell>
            <TableCell align="right">Last beat</TableCell>
            <TableCell align="right">Live parallel</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {(rows ?? []).map((r) => {
            const open = expanded.has(r.area);
            const state = surfaceByArea.get(r.area) ?? NO_SURFACE;
            const chip = STATE_CHIP[r.status];
            const stopTip =
              r.status === "stopped" && r.stopReason != null
                ? `${r.stopReason} (normal exit: ${r.isNormalExit ? "yes" : "no"}) at ${fmtTimeShanghai(r.stoppedAt)}`
                : undefined;
            const statusChip = (
              <Chip size="small" color={chip.color} label={chip.label} />
            );
            return (
              <ClusterServerRow
                key={r.area}
                server={r}
                open={open}
                state={state}
                nowMs={nowMs}
                statusChip={stopTip != null ? <Tooltip title={stopTip}>{statusChip}</Tooltip> : statusChip}
                onToggle={() => toggle(r.area)}
              />
            );
          })}
          {rows != null && rows.length === 0 && (
            <TableRow>
              <TableCell colSpan={7} align="center">
                <Typography variant="caption" color="text.secondary" sx={{ my: 2, display: "inline-block" }}>
                  No area servers in cluster_config.json
                </Typography>
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
    </Box>
  );
}

/** One server row + its expandable API-surface panel. */
function ClusterServerRow({
  server,
  open,
  state,
  nowMs,
  statusChip,
  onToggle,
}: {
  server: ClusterServerStatus;
  open: boolean;
  state: SurfaceState;
  nowMs: number;
  statusChip: ReactNode;
  onToggle: () => void;
}) {
  const age = liveAge(server.beatAt, nowMs) ?? server.beatAgeS;
  return (
    <>
      <TableRow hover onClick={onToggle} sx={{ cursor: "pointer" }}>
        <TableCell sx={{ fontFamily: "monospace" }}>
          <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
            {open ? (
              <ExpandLess fontSize="small" sx={{ color: "text.secondary" }} />
            ) : (
              <ExpandMore fontSize="small" sx={{ color: "text.secondary" }} />
            )}
            {server.service}
          </Box>
        </TableCell>
        <TableCell sx={{ fontFamily: "monospace", fontSize: "0.75rem" }}>
          {server.baseUrl}
        </TableCell>
        <TableCell>{statusChip}</TableCell>
        <TableCell align="right">{server.pid ?? "—"}</TableCell>
        <TableCell>
          {server.startedAt ? fmtDateTimeShanghai(server.startedAt) : "—"}
        </TableCell>
        <TableCell align="right">{fmtAge(age)}</TableCell>
        <TableCell align="right">{server.liveParallel ?? "—"}</TableCell>
      </TableRow>
      <TableRow>
        <TableCell colSpan={7} sx={{ py: 0, border: 0 }}>
          <Collapse in={open} unmountOnExit>
            <Box sx={{ px: 5, py: 1.5 }}>
              {state.loading && (
                <Box sx={{ display: "flex", alignItems: "center", gap: 1, py: 1 }}>
                  <CircularProgress size={16} />
                  <Typography variant="caption" color="text.secondary">
                    fetching the openapi surface from {server.baseUrl}…
                  </Typography>
                </Box>
              )}
              {!state.loading && state.error != null && (
                <Alert severity="warning">
                  {state.error} — collapse and expand the row to retry once the
                  server is up
                </Alert>
              )}
              {!state.loading && state.surface != null && (
                <SurfacePanel surface={state.surface} />
              )}
            </Box>
          </Collapse>
        </TableCell>
      </TableRow>
    </>
  );
}

/** The expanded panel: the server's openapi title + /docs link, then its
 *  endpoints grouped by FastAPI router tag. */
function SurfacePanel({ surface }: { surface: ClusterOpenApiSurface }) {
  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
      <Box sx={{ display: "flex", alignItems: "center", gap: 1, flexWrap: "wrap" }}>
        <Typography variant="subtitle2">
          {surface.title || surface.area}
        </Typography>
        {surface.version !== "" && (
          <Chip size="small" variant="outlined" label={`v${surface.version}`} />
        )}
        <Chip size="small" variant="outlined" label={`${surface.count} endpoints`} />
        <Link
          href={surface.docsUrl}
          target="_blank"
          rel="noreferrer"
          sx={{ display: "inline-flex", alignItems: "center", gap: 0.25 }}
        >
          FastAPI docs
          <OpenInNew sx={{ fontSize: 14 }} />
        </Link>
      </Box>
      {surface.groups.map((g: ClusterApiGroup) => (
        <Box key={g.tag}>
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ textTransform: "uppercase" }}
            component="div"
          >
            {g.tag} ({g.operations.length})
          </Typography>
          <Table size="small">
            <TableBody>
              {g.operations.map((op) => (
                <TableRow key={`${op.method} ${op.path}`}>
                  <TableCell sx={{ width: 70, borderBottom: "none", py: 0.4 }}>
                    <Chip
                      size="small"
                      label={op.method.toUpperCase()}
                      color={METHOD_COLOR[op.method] ?? "default"}
                      sx={{ fontFamily: "monospace" }}
                    />
                  </TableCell>
                  <TableCell
                    sx={{
                      fontFamily: "monospace",
                      fontSize: "0.75rem",
                      borderBottom: "none",
                      py: 0.4,
                      whiteSpace: "nowrap",
                    }}
                  >
                    {op.path}
                  </TableCell>
                  <TableCell
                    sx={{
                      borderBottom: "none",
                      py: 0.4,
                      color: op.summary === "" ? "text.disabled" : "text.secondary",
                      fontSize: "0.75rem",
                    }}
                  >
                    {op.summary === "" ? "—" : op.summary}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Box>
      ))}
    </Box>
  );
}
