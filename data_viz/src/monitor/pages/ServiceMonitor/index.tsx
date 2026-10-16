/**
 * Service Monitor page — liveness of the repo's monitored service processes
 * (the five downloads.stream.* pollers + the stream_watchdog supervisor),
 * grouped by scope (downloads / database / ...) and sub_scope, PLUS the
 * last-known state of every OTHER registered pipeline process (batch
 * builds / downloads / analyses / llm runs — "Pipeline processes").
 * Two tables, no standalone audit sections. Stream-service rows open the
 * service's CURRENT log on click (the shared LogViewerDialog over the
 * logs/<today>/<service>.log tail — one append-only file per (date,
 * service) shared by log_setup and the nohup'd stdout, so the tail
 * carries every restart life of the day) and expand to their watchdog
 * restart trail via the chevron (the dead lives' 'watchdog restart: …'
 * rows in monitor.service_audit). A pipeline process row expands to its
 * per-run audit history (monitor.service_audit via
 * GET /api/monitor/stream/runs, fetched on first expand; every history
 * run is CLICKABLE and opens that run's log in the shared
 * LogViewerDialog — the logs/<Shanghai start date>/<service>.log tail
 * via GET /api/monitor/stream/runs/log, one file per (date, service)).
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
 * it spawns a HIDDEN keeper session through the API server's shared WSL
 * runner (no cmd window — same spawn as every other API-triggered
 * python run), which runs the idempotent watchdog starter first (heals
 * leaked live_parallel slots from pid liveness, covers db_health), then
 * scripts/stream_always.sh — nohup each poller, sleep 1 apart — and
 * parks. The parked session is what keeps the services alive (a WSL
 * session's nohup'd children die with its wsl.exe client), so the
 * stream lives with the API server: an API restart takes it down and
 * the button is the recovery (deduped by process-id-tag while the
 * keeper lives). Redundant starts self-exit past their max_parallel
 * caps; first beats land within ~60s on the table's own refresh cadence.
 * Status: "live" while the heartbeat is fresher than staleAfterS (240s);
 * "stale" past that (dead or wedged — the watchdog restarts it);
 * "stopped" when the process recorded its exit (see the chip tooltip for
 * the reason); "down" when it has never beaten. Scope / sub-scope
 * dropdowns filter every table; the sub-scope items are always those
 * under the selected scope (disabled under "All scopes").
 * Batch processes show their LAST RUN: "running" while beating, "stopped"
 * after a recorded exit, "stale" when the beat went quiet without a
 * record (died silently — the watchdog's orphan pass then marks it
 * stopped), "never" for registered services (service_config/ static
 * tree) with no heartbeat row at all — synthesized by the endpoint so
 * full history covers the whole registration.
 *
 * Pipeline processes default to LIVE-ONLY (running rows — the registered
 * catalog spans ~30 mostly-on_demand jobs, so listing them all is noise);
 * the Live / History toggle flips to the full last-run catalog, and the
 * freq dropdown + search box narrow within the selected mode (freq is the
 * SUPPOSED-RUN cadence from the service_config/ static tree — hover a
 * freq for the schedule / notes / config source). Full history also lists
 * the never-run services with "—" cells. A row whose Last beat is older
 * than the freq's supposed window (daily 24h / monthly 30d /
 * semiannually 183d — the endpoint carries it as freqPeriodMs; windowless
 * freqs like always / on_demand / once never lapse) ambers that cell
 * (hover for the overdue detail).
 *
 * Each pipeline-process row also has a RUN button (per-service manual
 * run; the confirm dialog is RunServiceDialog): a confirm dialog previews
 * the freq auto-skip guard — the service_config freq vs the last
 * successful exit in monitor.service_audit (daily = 24h, monthly = 30d,
 * semiannually = 183d, once = any success; always / occasionally /
 * on_demand never auto-skip). A force-less run inside the period is
 * AUTO-SKIPPED; the dialog's Force-rerun switch (off by default) bypasses
 * the guard. The spawn is fire-and-forget (python -m <service> under the
 * shared py-runner, deduped per service): the POST resolves at spawn
 * time, the row's button spins while the tag is live, and the run itself
 * shows up via the heartbeat table + run history like any other
 * registration (first beat ~60s).
 *
 * Layout (one file per concern, this index composes them): constants.ts
 * (cadence + chip vocabularies), fmt.ts / hooks.ts (formatters + the 1s
 * tick), ServiceRow.tsx / ProcessRow.tsx (the two table rows),
 * RunServiceDialog.tsx (the run confirm dialog), and this index.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Box,
  Button,
  CircularProgress,
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
import { Clear, PlayArrow, Refresh, Search } from "@mui/icons-material";
import { fetchAnalysisRunStatus } from "@/lib/api-client/analysis-run";
import { fmtTimeShanghai, shanghaiDate } from "@/shared/datetime";
import ValueFilterSelect, {
  type ValueFilterOption,
} from "@/shared/components/ValueFilterSelect";
import LogViewerDialog, {
  type LogFileView,
} from "@/shared/components/LogViewerDialog";
import {
  fetchProcessRuns,
  fetchRunLog,
  fetchStreamMonitor,
  runHealthCheck,
  serviceRunTag,
  startStreamAlways,
  type HealthCheckResponse,
  type MonitorStreamResponse,
  type ProcessStatus,
  type RunLog,
  type ServiceAuditEntry,
  type StreamAlwaysResponse,
  type StreamServiceStatus,
} from "@/lib/api-client/monitor";
import {
  ALL,
  HEALTH_CHECK_TAG,
  HC_REMOTE_POLL_MS,
  NO_RUNS,
  REFRESH_MS,
  type ProcessRunsState,
} from "./constants";
import { useNowTick } from "./hooks";
import ProcessRow from "./ProcessRow";
import RunServiceDialog from "./RunServiceDialog";
import ServiceRow from "./ServiceRow";

/** What the shared LogViewerDialog is showing: a pipeline HISTORY run
 * (log of the run's Shanghai start date) or a stream service's CURRENT
 * life (log of today — the always-on pollers append to the one per-day
 * file across restart lives). */
type LogSource =
  | { kind: "run"; service: string; run: ServiceAuditEntry }
  | { kind: "live"; svc: StreamServiceStatus };

export default function ServiceMonitorPage() {
  const [data, setData] = useState<MonitorStreamResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [scopeFilter, setScopeFilter] = useState<string>(ALL);
  const [subFilter, setSubFilter] = useState<string>(ALL);
  // Pipeline processes: live-only by default; the Live / History toggle
  // picks running rows vs the full last-run catalog (stopped / stale /
  // never included), and the freq dropdown + search narrow within the
  // selected mode. Rows expand to their run history, fetched once per
  // service on first expand (the runsCacheRef guards double fetches).
  // Stream-service rows expand to their watchdog restarts (client-side
  // filter over the payload's recentRestarts window — the merged
  // service_audit trail).
  const [processSearch, setProcessSearch] = useState("");
  const [procMode, setProcMode] = useState<"live" | "history">("live");
  // Freq dropdown over the pipeline processes ("" = all).
  const [freqFilter, setFreqFilter] = useState("");
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

  // ---- Per-process Run (RunServiceDialog + fire-and-forget spawn) -----
  // `runProc` opens the dialog (the dialog owns guard / force / submit
  // state internally); `spawning` tracks the services this page spawned
  // and still in flight (py-runner tags, polled until they free — a page
  // refresh drops the tracking, but the heartbeat table then shows the
  // run as `running` anyway).
  const [runProc, setRunProc] = useState<ProcessStatus | null>(null);
  const [spawning, setSpawning] = useState<ReadonlySet<string>>(new Set());

  const onRunStarted = useCallback(
    (service: string, alreadyRunning: boolean) => {
      if (!alreadyRunning) {
        setSpawning((prev) => new Set(prev).add(service));
      }
      load(); // the run's first heartbeat beat lands within ~60s
    },
    [load],
  );

  // Poll the in-flight spawn tags until they free (then refresh — the
  // exit landed in the heartbeat / audit tables).
  useEffect(() => {
    if (spawning.size === 0) return;
    let cancelled = false;
    const poll = async () => {
      const services = [...spawning];
      try {
        const status = await fetchAnalysisRunStatus(
          services.map(serviceRunTag),
        );
        if (cancelled) return;
        const done = services.filter((s) => !status[serviceRunTag(s)]);
        if (done.length > 0) {
          setSpawning((prev) => {
            const next = new Set(prev);
            for (const s of done) next.delete(s);
            return next;
          });
          load();
        }
      } catch {
        // transient poll failure — the next tick retries
      }
    };
    const id = setInterval(poll, 3000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [spawning, load]);

  // ---- Run-log viewer (clickable history-run rows + stream rows) -------
  // `logSource` says what the dialog title / subtitle describe: a
  // pipeline history run or a stream service's current life (its row
  // click) — both resolve to the same logs/<date>/<service>.log tail.
  const [logSource, setLogSource] = useState<LogSource | null>(null);
  const [runLog, setRunLog] = useState<RunLog | null>(null);
  const [runLogLoading, setRunLogLoading] = useState(false);
  const [runLogError, setRunLogError] = useState<string | null>(null);

  const openLog = useCallback((source: LogSource) => {
    setLogSource(source);
    setRunLog(null);
    setRunLogError(null);
    setRunLogLoading(true);
    const { service, date } =
      source.kind === "run"
        ? {
            service: source.service,
            date: shanghaiDate(new Date(source.run.startedAt)),
          }
        : { service: source.svc.service, date: shanghaiDate(new Date()) };
    fetchRunLog(service, date)
      .then(setRunLog)
      .catch((e: unknown) => setRunLogError(String(e)))
      .finally(() => setRunLogLoading(false));
  }, []);

  const logView: LogFileView | null =
    runLog == null
      ? null
      : {
          file: runLog.file,
          found: runLog.found,
          content: runLog.content,
          truncated: runLog.truncated,
          totalLines: runLog.totalLines,
          sizeBytes: runLog.sizeBytes,
          candidates: runLog.candidates,
        };

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
  // Freq dropdown options: distinct freqs over ALL loaded process rows
  // (both modes), each with its row count — like the Service Config table.
  const freqOptions = useMemo<ValueFilterOption[]>(() => {
    const counts = new Map<string, number>();
    for (const r of processRows) {
      counts.set(r.freq, (counts.get(r.freq) ?? 0) + 1);
    }
    return [...counts.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([value, count]) => ({ value, count }));
  }, [processRows]);
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
  // (running + stale + stopped + never). The freq dropdown + search narrow
  // within the selected mode. All layers stack on the scope / sub-scope
  // dropdowns.
  const procQuery = processSearch.trim().toLowerCase();
  const procSearching = procQuery.length > 0;
  const processes = processRows.filter((r) => {
    if (!match(r.scope, r.subScope)) return false;
    if (procMode === "live" && r.status !== "running") return false;
    if (freqFilter !== "" && r.freq !== freqFilter) return false;
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
            "Start all always-on stream services — spawns a hidden keeper " +
            "session via the API's WSL runner (no cmd window): idempotent " +
            "watchdog starter first (heals leaked parallel slots, covers " +
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
            ? ` (triggered at ${fmtTimeShanghai(saResult.at)}) — the keeper session runs the watchdog starter then the five pollers 1s apart; if services stay down, see logs/<date>/ui_stream_always.log`
            : ""}
        </Alert>
      )}

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
          <Typography variant="subtitle2">Stream services</Typography>
          <Typography variant="caption" color="text.secondary">
            always-on pollers + watchdog · click a row for its current log ·
            chevron for the restart trail
          </Typography>
        </Box>
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
                onOpenLog={() => openLog({ kind: "live", svc })}
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
            <ValueFilterSelect
              label="Freq"
              options={freqOptions}
              value={freqFilter}
              onChange={setFreqFilter}
            />
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
                <TableCell>Run</TableCell>
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
                  onRun={() => setRunProc(proc)}
                  spawning={spawning.has(proc.service)}
                  onOpenLog={(run) =>
                    openLog({ kind: "run", service: proc.service, run })
                  }
                  runs={runsByService.get(proc.service) ?? NO_RUNS}
                />
              ))}
              {processes.length === 0 && (
                <TableRow>
                  <TableCell colSpan={10} align="center">
                    <Typography
                      variant="caption"
                      color="text.secondary"
                      sx={{ my: 2, display: "inline-block" }}
                    >
                      {procSearching
                        ? `No ${procMode === "live" ? "live " : ""}process matches “${processSearch.trim()}”`
                        : procMode === "live"
                          ? "No live pipeline processes — switch to History for stopped / stale / never-run ones"
                          : "No pipeline processes on record"}
                    </Typography>
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </Paper>
      )}

      <RunServiceDialog
        proc={runProc}
        onClose={() => setRunProc(null)}
        onStarted={onRunStarted}
      />

      <LogViewerDialog
        open={logSource != null}
        onClose={() => setLogSource(null)}
        title={
          logSource == null
            ? ""
            : logSource.kind === "run"
              ? `${logSource.service} — run log ${shanghaiDate(new Date(logSource.run.startedAt))}`
              : `${logSource.svc.service} — current log ${shanghaiDate(new Date())}`
        }
        subtitle={
          logSource == null
            ? null
            : logSource.kind === "run"
              ? [
                  `started ${fmtTimeShanghai(logSource.run.startedAt)}`,
                  logSource.run.endedAt != null
                    ? `ended ${fmtTimeShanghai(logSource.run.endedAt)}`
                    : "still running",
                  logSource.run.exitReason != null
                    ? `exit: ${logSource.run.exitReason} (${logSource.run.isNormalExit ? "normal" : "abnormal"})`
                    : null,
                  `pid ${logSource.run.pid}`,
                ]
                  .filter((s): s is string => s != null)
                  .join(" · ")
              : [
                  logSource.svc.startedAt != null
                    ? `started ${fmtTimeShanghai(logSource.svc.startedAt)}`
                    : null,
                  logSource.svc.pid != null
                    ? `pid ${logSource.svc.pid}`
                    : null,
                  `status ${logSource.svc.status}`,
                  logSource.svc.restarts24h > 0
                    ? `${logSource.svc.restarts24h} restart${logSource.svc.restarts24h === 1 ? "" : "s"} in 24h — every life appends to this one file`
                    : null,
                ]
                  .filter((s): s is string => s != null)
                  .join(" · ")
        }
        log={logView}
        loading={runLogLoading}
        error={runLogError}
      />

    </Box>
  );
}
