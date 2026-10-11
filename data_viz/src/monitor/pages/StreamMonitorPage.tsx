/**
 * Service Monitor page — liveness of the repo's monitored service processes
 * (the five downloads.stream.* pollers + the stream_watchdog supervisor),
 * grouped by scope (downloads / database / ...) and sub_scope, PLUS the
 * last-known state of every OTHER registered pipeline process (batch
 * builds / downloads / analyses / llm runs — "Pipeline processes").
 * Two tables, no standalone audit sections: every row EXPANDS — a stream
 * service to its watchdog restart trail (the dead lives' 'watchdog
 * restart: …' rows in monitor.service_audit), a pipeline process to its
 * per-run audit history (monitor.service_audit via
 * GET /api/monitor/stream/runs, fetched on first expand).
 *
 * Data: monitor.service_heartbeat / monitor.service_audit via
 * GET /api/monitor/stream (read replica). Auto-refreshes every 30s while
 * beat ages tick every 1s (client-side).
 * The header refresh button FORCE-TRIGGERS the point-in-time health
 * check (POST /api/monitor/health-check → python -m database.monitoring:
 * replica sync + replay backlog + slot WAL retention + pg_wal dir size
 * vs max_wal_size + this page's stream liveness) and shows its [ok] /
 * [ALERT] verdict lines inline. Next to it, the "Start stream always"
 * button brings the always-on stream up (POST /api/monitor/stream-always):
 * it triggers the oxpicious-stream-always scheduled task, whose console
 * session runs the idempotent watchdog starter first (heals leaked
 * live_parallel slots from pid liveness, covers db_health), then
 * scripts/stream_always.sh — nohup each poller, sleep 1 apart. The task
 * session (not a transient wsl.exe) is what keeps the services alive: a
 * session's nohup'd children die with its client, and headless clients
 * don't hold the distro open. Redundant starts self-exit past their
 * max_parallel caps; first beats land within ~60s on the table's own
 * refresh cadence.
 * Status: "live" while the heartbeat is fresher than staleAfterS (240s);
 * "stale" past that (dead or wedged — the watchdog restarts it);
 * "stopped" when the process recorded its exit (see the chip tooltip for
 * the reason); "down" when it has never beaten. Scope / sub-scope
 * dropdowns filter every table; the sub-scope items are always those
 * under the selected scope (disabled under "All scopes").
 * Batch processes show their LAST RUN: "running" while beating, "stopped"
 * after a recorded exit, "stale" when the beat went quiet without a
 * record (died silently — the watchdog's orphan pass then marks it
 * stopped).
 *
 * Pipeline processes default to LIVE-ONLY (running rows — the registered
 * catalog spans ~30 mostly-on-demand jobs, so listing them all is noise);
 * the Live / History toggle flips to the full last-run catalog and the
 * search box narrows within the selected mode. Each row carries its
 * SUPPOSED-RUN freq (always / daily / monthly / semiannually / once /
 * occasionally / on_demand) from the service_config/ static tree
 * (database/sql/monitoring/static/service_config/ — one file per service,
 * mirrored from scripts/*.sh; hover for the schedule / notes / config
 * source).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Collapse,
  IconButton,
  InputAdornment,
  MenuItem,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from "@mui/material";
import {
  Clear,
  ExpandLess,
  ExpandMore,
  PlayArrow,
  Refresh,
  Search,
} from "@mui/icons-material";
import {
  fetchAnalysisRunStatus,
} from "@/lib/api-client/analysis-run";
import { fmtTimeShanghai } from "@/shared/datetime";
import {
  fetchProcessRuns,
  fetchStreamMonitor,
  runHealthCheck,
  startStreamAlways,
  type HealthCheckResponse,
  type MonitorStreamResponse,
  type ProcessStatus,
  type ServiceAuditEntry,
  type StreamAlwaysResponse,
  type StreamRestartEntry,
  type StreamServiceStatus,
} from "@/lib/api-client/monitor";

const REFRESH_MS = 30_000;
const ALL = "all";

/** Process-id-tag of the forced health check (must match the server's
 * POST /api/monitor/health-check spawn tag). */
const HEALTH_CHECK_TAG = "monitor:health-check";
/** Poll interval (ms) while a health check started elsewhere is running. */
const HC_REMOTE_POLL_MS = 3000;

/** Ticking wall clock (1s) — beat ages count up live between API polls. */
function useNowTick(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}

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

const STATE_CHIP: Record<
  StreamServiceStatus["status"],
  { label: string; color: "success" | "warning" | "error" | "default" }
> = {
  live: { label: "live", color: "success" },
  stale: { label: "stale", color: "warning" },
  stopped: { label: "stopped", color: "default" },
  down: { label: "down", color: "error" },
};

const PROCESS_CHIP: Record<
  ProcessStatus["status"],
  { label: string; color: "success" | "warning" | "default" }
> = {
  running: { label: "running", color: "success" },
  stale: { label: "stale", color: "warning" },
  stopped: { label: "stopped", color: "default" },
};

/** Fetch state of one expanded process's run history. */
interface ProcessRunsState {
  loading: boolean;
  error: string | null;
  runs: ServiceAuditEntry[];
}

const NO_RUNS: ProcessRunsState = { loading: false, error: null, runs: [] };

function ProcessRow({
  proc,
  nowMs,
  open,
  onToggle,
  runs,
}: {
  proc: ProcessStatus;
  nowMs: number;
  open: boolean;
  onToggle: () => void;
  runs: ProcessRunsState;
}) {
  const chip = PROCESS_CHIP[proc.status];
  const age = liveAge(proc.beatAt, nowMs) ?? proc.beatAgeS;
  const stopTip =
    proc.status === "stopped" && proc.stopReason != null
      ? `${proc.stopReason} (normal exit: ${proc.isNormalExit ? "yes" : "no"}) at ${fmtTimeShanghai(proc.stoppedAt)}`
      : undefined;
  // Supposed-run cadence from the service_config/ static tree — the
  // timing detail + quirks + where the config lives, on hover.
  const freqTip = [
    proc.schedule,
    proc.notes,
    proc.sourceRef ? `config: ${proc.sourceRef}` : null,
  ]
    .filter((s): s is string => s != null && s !== "")
    .join("\n");
  const statusChip = (
    <Chip size="small" color={chip.color} label={chip.label} />
  );
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
            {proc.service}
          </Box>
        </TableCell>
        <TableCell>{proc.scope}</TableCell>
        <TableCell>{proc.subScope ?? "—"}</TableCell>
        <TableCell>
          {stopTip != null ? <Tooltip title={stopTip}>{statusChip}</Tooltip> : statusChip}
        </TableCell>
        <TableCell>
          {freqTip !== "" ? (
            <Tooltip title={freqTip}>
              <span>{proc.freq}</span>
            </Tooltip>
          ) : (
            proc.freq
          )}
        </TableCell>
        <TableCell>{proc.pid}</TableCell>
        <TableCell>{fmtTimeShanghai(proc.startedAt)}</TableCell>
        <TableCell>{fmtAge(age)}</TableCell>
        <TableCell>{proc.stopReason ?? "—"}</TableCell>
      </TableRow>
      <TableRow>
        <TableCell colSpan={9} sx={{ py: 0, border: 0 }}>
          <Collapse in={open} unmountOnExit>
            <Box sx={{ px: 5, py: 1.5 }}>
              {runs.loading && <CircularProgress size={16} sx={{ my: 1 }} />}
              {runs.error != null && (
                <Typography variant="caption" color="error">
                  {runs.error}
                </Typography>
              )}
              {!runs.loading && runs.error == null && runs.runs.length === 0 && (
                <Typography variant="caption" color="text.secondary">
                  No recorded runs in service_audit.
                </Typography>
              )}
              {runs.runs.length > 0 && (
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
                    {runs.runs.map((r) => (
                      <TableRow key={r.id}>
                        <TableCell>{fmtTimeShanghai(r.startedAt)}</TableCell>
                        <TableCell>{r.endedAt ? fmtTimeShanghai(r.endedAt) : "—"}</TableCell>
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
              )}
            </Box>
          </Collapse>
        </TableCell>
      </TableRow>
    </>
  );
}

function ServiceRow({
  svc,
  nowMs,
  open,
  onToggle,
  restarts,
}: {
  svc: StreamServiceStatus;
  nowMs: number;
  open: boolean;
  onToggle: () => void;
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
      <TableRow hover onClick={onToggle} sx={{ cursor: "pointer" }}>
        <TableCell sx={{ fontFamily: "monospace" }}>
          <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
            {open ? (
              <ExpandLess fontSize="small" sx={{ color: "text.secondary" }} />
            ) : (
              <ExpandMore fontSize="small" sx={{ color: "text.secondary" }} />
            )}
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

export default function StreamMonitorPage() {
  const [data, setData] = useState<MonitorStreamResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [scopeFilter, setScopeFilter] = useState<string>(ALL);
  const [subFilter, setSubFilter] = useState<string>(ALL);
  // Pipeline processes: live-only by default; the Live / History toggle
  // picks running rows vs the full last-run catalog (stopped / stale
  // included), and the search narrows within the selected mode. Rows
  // expand to their run history, fetched once per service on first
  // expand (the runsCacheRef guards double fetches).
  // Stream-service rows expand to their watchdog restarts (client-side
  // filter over the payload's recentRestarts window — the merged
  // service_audit trail).
  const [processSearch, setProcessSearch] = useState("");
  const [procMode, setProcMode] = useState<"live" | "history">("live");
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());
  const [expandedServices, setExpandedServices] = useState<ReadonlySet<string>>(
    new Set(),
  );
  const [runsByService, setRunsByService] = useState<Map<string, ProcessRunsState>>(
    new Map(),
  );
  const runsCacheRef = useRef(new Set<string>());
  const nowMs = useNowTick();

  const load = useCallback(async () => {
    try {
      setData(await fetchStreamMonitor());
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, REFRESH_MS);
    return () => clearInterval(id);
  }, [load]);

  // ---- Forced health check (header refresh button) --------------------
  // A page refresh / second tab must keep seeing the spinner while a
  // check started elsewhere is still in flight, so the running state is
  // restored from the py-runner tag registry on mount (and while
  // spinning, re-polled until the tag frees).
  const [hcRunning, setHcRunning] = useState(false);
  const [hcError, setHcError] = useState<string | null>(null);
  const [hcResult, setHcResult] = useState<{
    at: number;
    resp: HealthCheckResponse;
  } | null>(null);
  // ---- Start stream always (header button) ----------------------------
  // The POST resolves as soon as the detached keeper session is spawned
  // (services go live within ~60s, on the table's own refresh cadence),
  // so saRunning is just the click-to-spawn spinner — no tag-registry
  // tracking (the keeper never exits); the verdict shows as a
  // dismissible alert.
  const [saRunning, setSaRunning] = useState(false);
  const [saError, setSaError] = useState<string | null>(null);
  const [saResult, setSaResult] = useState<{
    at: number;
    resp: StreamAlwaysResponse;
  } | null>(null);

  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      try {
        const status = await fetchAnalysisRunStatus([HEALTH_CHECK_TAG]);
        if (cancelled) return;
        if (status[HEALTH_CHECK_TAG]) {
          setHcRunning(true);
          setTimeout(poll, HC_REMOTE_POLL_MS);
        } else {
          setHcRunning(false);
        }
      } catch {
        if (!cancelled) setHcRunning(false);
      }
    };
    poll();
    return () => {
      cancelled = true;
    };
  }, []);

  const runCheck = useCallback(async () => {
    setHcRunning(true);
    setHcError(null);
    try {
      let resp = await runHealthCheck();
      // Another tab / click owns the in-flight run — wait for the tag to
      // free, then run our own so the verdict lines shown are ours.
      while (resp.alreadyRunning) {
        await new Promise((r) => setTimeout(r, HC_REMOTE_POLL_MS));
        const status = await fetchAnalysisRunStatus([HEALTH_CHECK_TAG]);
        if (status[HEALTH_CHECK_TAG]) continue;
        resp = await runHealthCheck();
      }
      setHcResult({ at: Date.now(), resp });
    } catch (e) {
      setHcError(String(e));
    } finally {
      setHcRunning(false);
      // The check re-beat / restarted services — pull the fresh table.
      load();
    }
  }, [load]);

  const startAlways = useCallback(async () => {
    setSaRunning(true);
    setSaError(null);
    try {
      const resp = await startStreamAlways();
      setSaResult({ at: Date.now(), resp });
    } catch (e) {
      setSaError(String(e));
    } finally {
      setSaRunning(false);
      // The spawned pollers' first beats land within ~60s — refresh now,
      // the 30s auto-refresh carries the table the rest of the way.
      load();
    }
  }, [load]);

  const toggleService = useCallback((service: string) => {
    setExpandedServices((prev) => {
      const next = new Set(prev);
      if (next.has(service)) next.delete(service);
      else next.add(service);
      return next;
    });
  }, []);

  const toggleProcess = useCallback((service: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(service)) next.delete(service);
      else next.add(service);
      return next;
    });
    if (runsCacheRef.current.has(service)) return;
    runsCacheRef.current.add(service);
    setRunsByService((prev) =>
      new Map(prev).set(service, { loading: true, error: null, runs: [] }),
    );
    fetchProcessRuns(service)
      .then((runs) =>
        setRunsByService((prev) =>
          new Map(prev).set(service, { loading: false, error: null, runs }),
        ),
      )
      .catch((e: unknown) => {
        runsCacheRef.current.delete(service); // allow a retry on next expand
        setRunsByService((prev) =>
          new Map(prev).set(service, { loading: false, error: String(e), runs: [] }),
        );
      });
  }, []);

  // All known service rows (watchdog first), and the scope/sub-scope option
  // sets derived from them + the batch process rows — placeholders for
  // never-beaten "down" services carry the catalog's scope too, so filters
  // cover them.
  const allRows = useMemo(
    () =>
      data
        ? [data.watchdog, ...data.services].filter(
            (s): s is StreamServiceStatus => s != null,
          )
        : [],
    [data],
  );
  const processRows = useMemo(() => data?.processes ?? [], [data]);
  const scopeOptions = useMemo(
    () =>
      [...new Set([...allRows, ...processRows].map((r) => r.scope))].sort(),
    [allRows, processRows],
  );
  const subOptions = useMemo(
    () =>
      // Sub-scope items are always those UNDER the selected scope — no
      // scope, no items (the cross-scope union was meaningless noise).
      scopeFilter === ALL
        ? []
        : [
            ...new Set(
              [...allRows, ...processRows]
                .filter((r) => r.scope === scopeFilter)
                .map((r) => r.subScope)
                .filter((s): s is string => s != null),
            ),
          ].sort(),
    [allRows, processRows, scopeFilter],
  );

  const match = useCallback(
    (scope: string | null, subScope: string | null): boolean =>
      (scopeFilter === ALL || scope === scopeFilter) &&
      (subFilter === ALL || subScope === subFilter),
    [scopeFilter, subFilter],
  );

  const rows = allRows.filter((r) => match(r.scope, r.subScope));
  // Live mode: running rows only. History mode: the full last-run catalog
  // (running + stale + stopped). The search narrows within the selected
  // mode on service / scope / sub-scope. All layers stack on the scope /
  // sub-scope dropdowns.
  const procQuery = processSearch.trim().toLowerCase();
  const procSearching = procQuery.length > 0;
  const processes = processRows.filter((r) => {
    if (!match(r.scope, r.subScope)) return false;
    if (procMode === "live" && r.status !== "running") return false;
    if (!procSearching) return true;
    return (
      r.service.toLowerCase().includes(procQuery) ||
      r.scope.toLowerCase().includes(procQuery) ||
      (r.subScope ?? "").toLowerCase().includes(procQuery)
    );
  });

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <Box sx={{ display: "flex", alignItems: "center", gap: 2, flexWrap: "wrap" }}>
        <Typography variant="h6">Service Monitor</Typography>
        {data && (
          <Typography variant="caption" color="text.secondary">
            updated {fmtTimeShanghai(data.generatedAt)} · stale threshold{" "}
            {Math.round(data.staleAfterS / 60)}m · beats ~60s · ages tick 1s ·
            data refresh 30s
          </Typography>
        )}
        {error && <CircularProgress size={16} />}
        <Tooltip
          title={
            "Force health check — replica sync · slot & WAL disk · stream " +
            "liveness (python -m database.monitoring)"
          }
          arrow
        >
          <span>
            <IconButton
              size="small"
              aria-label="force health check"
              onClick={runCheck}
              disabled={hcRunning}
              sx={{ bgcolor: "action.hover", "&:hover": { bgcolor: "action.selected" } }}
            >
              {hcRunning ? (
                <CircularProgress size={18} thickness={5} />
              ) : (
                <Refresh fontSize="small" />
              )}
            </IconButton>
          </span>
        </Tooltip>
        <Tooltip
          title={
            "Start all always-on stream services — triggers the " +
            "oxpicious-stream-always scheduled task: idempotent watchdog " +
            "starter first (heals leaked parallel slots, covers " +
            "db_health), then scripts/stream_always.sh (nohup each " +
            "poller, 1s apart). Redundant starts self-exit past their " +
            "max_parallel caps."
          }
          arrow
        >
          <span>
            <Button
              size="small"
              variant="outlined"
              aria-label="start stream always"
              onClick={startAlways}
              disabled={saRunning}
              startIcon={
                saRunning ? (
                  <CircularProgress size={14} thickness={5} />
                ) : (
                  <PlayArrow fontSize="small" />
                )
              }
              sx={{ whiteSpace: "nowrap" }}
            >
              Start stream always
            </Button>
          </span>
        </Tooltip>
        <Box sx={{ flexGrow: 1 }} />
        <TextField
          select
          size="small"
          label="Scope"
          value={scopeFilter}
          onChange={(e) => {
            setScopeFilter(e.target.value);
            setSubFilter(ALL);
          }}
          sx={{ minWidth: 140 }}
        >
          <MenuItem value={ALL}>All scopes</MenuItem>
          {scopeOptions.map((s) => (
            <MenuItem key={s} value={s}>
              {s}
            </MenuItem>
          ))}
        </TextField>
        <TextField
          select
          size="small"
          label="Sub-scope"
          value={subFilter}
          onChange={(e) => setSubFilter(e.target.value)}
          disabled={scopeFilter === ALL}
          sx={{ minWidth: 140 }}
        >
          <MenuItem value={ALL}>All sub-scopes</MenuItem>
          {subOptions.map((s) => (
            <MenuItem key={s} value={s}>
              {s}
            </MenuItem>
          ))}
        </TextField>
      </Box>

      {error && <Alert severity="error">{error}</Alert>}

      {hcError && (
        <Alert severity="error" onClose={() => setHcError(null)}>
          Health check failed to run: {hcError}
        </Alert>
      )}
      {hcResult && (
        <Alert
          severity={hcResult.resp.healthy ? "success" : "error"}
          onClose={() => setHcResult(null)}
          sx={{ alignItems: "flex-start" }}
        >
          <Box>
            <Typography variant="subtitle2">
              Health check{" "}
              {hcResult.resp.healthy ? "passed" : "flagged alerts"} at{" "}
              {fmtTimeShanghai(hcResult.at)} — exit{" "}
              {hcResult.resp.exitCode}
            </Typography>
            <Box
              component="pre"
              sx={{
                m: 0,
                mt: 0.5,
                fontFamily: "monospace",
                fontSize: "0.75rem",
                whiteSpace: "pre-wrap",
              }}
            >
              {hcResult.resp.lines.join("\n")}
            </Box>
          </Box>
        </Alert>
      )}
      {saError && (
        <Alert severity="error" onClose={() => setSaError(null)}>
          Start stream always failed: {saError}
        </Alert>
      )}
      {saResult && (
        <Alert
          severity={saResult.resp.success ? "success" : "error"}
          onClose={() => setSaResult(null)}
        >
          {saResult.resp.message}
          {saResult.resp.success
            ? ` (triggered at ${fmtTimeShanghai(saResult.at)}) — the task session runs the watchdog starter then the five pollers 1s apart; if services stay down, see logs/<date>/ui_stream_always.log`
            : ""}
        </Alert>
      )}

      <Paper sx={{ overflowX: "auto" }}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Service</TableCell>
              <TableCell>Scope</TableCell>
              <TableCell>Sub-scope</TableCell>
              <TableCell>Status</TableCell>
              <TableCell>PID</TableCell>
              <TableCell>Started</TableCell>
              <TableCell>Last beat</TableCell>
              <TableCell>Restarts 24h</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {rows.map((svc) => (
              <ServiceRow
                key={svc.service}
                svc={svc}
                nowMs={nowMs}
                open={expandedServices.has(svc.service)}
                onToggle={() => toggleService(svc.service)}
                restarts={(data?.recentRestarts ?? []).filter(
                  (r) => r.service === svc.service,
                )}
              />
            ))}
            {!data && !error && (
              <TableRow>
                <TableCell colSpan={8} align="center">
                  <CircularProgress size={20} sx={{ my: 2 }} />
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </Paper>

      {processRows.length > 0 && (
        <Paper sx={{ overflowX: "auto" }}>
          <Box
            sx={{
              px: 2,
              pt: 1.5,
              display: "flex",
              alignItems: "center",
              gap: 2,
              flexWrap: "wrap",
            }}
          >
            <Typography variant="subtitle2">Pipeline processes</Typography>
            <Typography variant="caption" color="text.secondary">
              {procSearching
                ? `matching “${processSearch.trim()}”`
                : procMode === "live"
                  ? "live only"
                  : "full history"}
            </Typography>
            <Box sx={{ flexGrow: 1 }} />
            <ToggleButtonGroup
              size="small"
              exclusive
              value={procMode}
              onChange={(_, v: "live" | "history" | null) => {
                if (v) setProcMode(v);
              }}
            >
              <ToggleButton value="live">Live</ToggleButton>
              <ToggleButton value="history">History</ToggleButton>
            </ToggleButtonGroup>
            <TextField
              size="small"
              value={processSearch}
              onChange={(e) => setProcessSearch(e.target.value)}
              placeholder="Search processes…"
              sx={{
                width: 260,
                mb: 0.5,
                "& .MuiInputBase-input": { fontSize: "0.8rem" },
              }}
              InputProps={{
                startAdornment: (
                  <InputAdornment position="start">
                    <Search fontSize="small" sx={{ color: "text.secondary" }} />
                  </InputAdornment>
                ),
                endAdornment: procSearching ? (
                  <InputAdornment position="end">
                    <IconButton
                      size="small"
                      aria-label="clear search"
                      onClick={() => setProcessSearch("")}
                    >
                      <Clear fontSize="small" />
                    </IconButton>
                  </InputAdornment>
                ) : undefined,
              }}
            />
          </Box>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Service</TableCell>
                <TableCell>Scope</TableCell>
                <TableCell>Sub-scope</TableCell>
                <TableCell>Status</TableCell>
                <TableCell>Freq</TableCell>
                <TableCell>PID</TableCell>
                <TableCell>Last run started</TableCell>
                <TableCell>Last beat</TableCell>
                <TableCell>Last exit</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {processes.map((proc) => (
                <ProcessRow
                  key={proc.service}
                  proc={proc}
                  nowMs={nowMs}
                  open={expanded.has(proc.service)}
                  onToggle={() => toggleProcess(proc.service)}
                  runs={runsByService.get(proc.service) ?? NO_RUNS}
                />
              ))}
              {processes.length === 0 && (
                <TableRow>
                  <TableCell colSpan={9} align="center">
                    <Typography
                      variant="caption"
                      color="text.secondary"
                      sx={{ my: 2, display: "inline-block" }}
                    >
                      {procSearching
                        ? `No ${procMode === "live" ? "live " : ""}process matches “${processSearch.trim()}”`
                        : procMode === "live"
                          ? "No live pipeline processes — switch to History for stopped / stale ones"
                          : "No pipeline processes on record"}
                    </Typography>
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </Paper>
      )}

    </Box>
  );
}
