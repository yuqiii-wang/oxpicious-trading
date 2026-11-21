/**
 * Orchestration page — the ARRANGEMENT BOARD (monitor.
 * orchestration_task_state via GET /api/monitor/orchestration/board):
 * what the maintainer arranged to RUN vs SKIP, in three sections —
 * live running services (the heartbeat-live join), the to-run queue
 * (arranged due, priority-desc, the next chain pass's charge), and the
 * recently-no-need services (the freq window holds / once done / ran
 * since arranged, most-recent first with the reason).
 *
 * The arrangement is the monitor area's OrchestrationArranger
 * (server_cluster/monitor/arrangement.py): each service's
 * service_config ``freq`` mapped to the next Shanghai-CALENDAR boundary
 * after its last successful exit (daily = a new day, monthly = a new
 * month, quarterly = a new quarter, ...; once = any success ever; a
 * service without its own config file is always due — the run-every-
 * pass safeguard). The maintainer's nightly chain posts each tier with
 * arrange=true, so not-due steps are skipped (state skipped_not_due)
 * instead of spawning them to internally no-op off-cadence.
 *
 * Every row expands into the monitor-scope shared ServiceRunTrend (the
 * service's daily run-count curve; click a day for its run list, a run
 * click opens its log in the page-level LogViewerDialog).
 *
 * Actions (all with spinners — an arrange/evaluate pass is quick, a
 * backfill drive can be minutes): Refresh reloads the board; Arrange
 * now runs one arrangement pass over every tier file (what the chain
 * does per-tier); Evaluate re-runs every non-daily completeness check;
 * Backfill dry / Backfill now list / drive the weekend re-runs (what
 * the maintainer's Saturday 05:00 job fires). Auto-refreshes every 30s;
 * beat ages tick every 1s.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  IconButton,
  InputAdornment,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import { PlayArrow, Refresh, Search, Clear } from "@mui/icons-material";
import { fmtTimeShanghai, shanghaiDate } from "@/shared/datetime";
import ValueFilterSelect, {
  type ValueFilterOption,
} from "@/shared/components/ValueFilterSelect";
import LogViewerDialog, {
  type LogFileView,
} from "@/shared/components/LogViewerDialog";
import {
  arrangeOrchestration,
  backfillNonDailyTasks,
  evaluateNonDailyTasks,
  fetchOrchestrationBoard,
  fetchRunLog,
  type OrchestrationBoardResponse,
  type OrchestrationBoardTask,
  type RunLog,
  type ServiceAuditEntry,
} from "@/lib/api-client/monitor";
import { formatFetchError } from "@/lib/api-client/_retry";
import { useNowTick } from "@/monitor/hooks";
import BoardRow, { type BoardSection } from "./BoardRow";

/** Board auto-refresh cadence (the running section is a live-heartbeat
 *  join; the verdicts change with every arrange pass). */
const REFRESH_MS = 30_000;

export default function OrchestrationPage() {
  const [board, setBoard] = useState<OrchestrationBoardResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [arranging, setArranging] = useState(false);
  const [evaluating, setEvaluating] = useState(false);
  const [driving, setDriving] = useState(false);
  const [actionNote, setActionNote] = useState<string | null>(null);
  const [tierFilter, setTierFilter] = useState("");
  const [search, setSearch] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const nowMs = useNowTick();

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setBoard(await fetchOrchestrationBoard());
    } catch (err) {
      setError(formatFetchError(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    const id = setInterval(() => void load(), REFRESH_MS);
    return () => clearInterval(id);
  }, [load]);

  const onArrange = useCallback(async () => {
    setArranging(true);
    setActionNote(null);
    setError(null);
    try {
      const res = await arrangeOrchestration();
      setBoard({
        success: true,
        generatedAt: res.generatedAt,
        running: res.running,
        queue: res.queue,
        noNeed: res.noNeed,
      });
      setActionNote(
        `arrange: ${res.arranged.services} service(s) over ${res.arranged.tiers} tier(s) — ${res.arranged.due} due, ${res.arranged.notDue} not due`,
      );
    } catch (err) {
      setError(formatFetchError(err));
    } finally {
      setArranging(false);
    }
  }, []);

  const onEvaluate = useCallback(async () => {
    setEvaluating(true);
    setActionNote(null);
    setError(null);
    try {
      const res = await evaluateNonDailyTasks();
      setActionNote(
        `evaluate: ${res.evaluated} task(s) — ${res.complete} complete, ${res.missing} missing, ${res.checkFailed} check-failed`,
      );
      await load();
    } catch (err) {
      setError(formatFetchError(err));
    } finally {
      setEvaluating(false);
    }
  }, [load]);

  const onBackfill = useCallback(async (dryRun: boolean) => {
    setDriving(true);
    setActionNote(null);
    setError(null);
    try {
      const res = await backfillNonDailyTasks(dryRun);
      setActionNote(`backfill${dryRun ? " (dry)" : ""}: ${res.detail}`);
      await load();
    } catch (err) {
      setError(formatFetchError(err));
    } finally {
      setDriving(false);
    }
  }, [load]);

  // ---- row expansion + the page-level log viewer --------------------------
  const toggle = useCallback((service: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(service)) next.delete(service);
      else next.add(service);
      return next;
    });
  }, []);

  const [logRun, setLogRun] = useState<ServiceAuditEntry | null>(null);
  const [runLog, setRunLog] = useState<RunLog | null>(null);
  const [runLogLoading, setRunLogLoading] = useState(false);
  const [runLogError, setRunLogError] = useState<string | null>(null);

  const openLog = useCallback((run: ServiceAuditEntry) => {
    setLogRun(run);
    setRunLog(null);
    setRunLogError(null);
    setRunLogLoading(true);
    fetchRunLog(run.service, shanghaiDate(new Date(run.startedAt)))
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

  // ---- sections (tier filter + search narrow all three) --------------------
  const matches = useCallback(
    (t: OrchestrationBoardTask): boolean =>
      (tierFilter === "" || t.tier === tierFilter) &&
      (search.trim() === "" ||
        t.service.toLowerCase().includes(search.trim().toLowerCase())),
    [tierFilter, search],
  );
  const sections = useMemo(() => {
    const all = board == null
      ? []
      : [...board.running, ...board.queue, ...board.noNeed];
    // Tier dropdown options: distinct tiers over the FILTERED rows, each
    // with its row count (the filter narrows all three sections).
    const counts = new Map<string, number>();
    for (const t of all) {
      if (matches(t)) counts.set(t.tier, (counts.get(t.tier) ?? 0) + 1);
    }
    const tierOptions: ValueFilterOption[] = [...counts.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([value, count]) => ({ value, count }));
    return {
      tierOptions,
      running: (board?.running ?? []).filter(matches),
      queue: (board?.queue ?? []).filter(matches),
      noNeed: (board?.noNeed ?? []).filter(matches),
    };
  }, [board, matches]);

  const sectionPaper = (
    section: BoardSection,
    title: string,
    caption: string,
    tasks: OrchestrationBoardTask[],
    head: string[],
  ) => (
    <Paper>
      <Box
        sx={{
          px: 2,
          pt: 1.5,
          display: "flex",
          alignItems: "center",
          gap: 1,
          flexWrap: "wrap",
        }}
      >
        <Typography variant="subtitle2">{title}</Typography>
        <Chip
          size="small"
          variant="outlined"
          color={section === "running" ? "success" : section === "queue" ? "primary" : "default"}
          label={`${tasks.length}`}
        />
        <Typography variant="caption" color="text.secondary">
          {caption}
        </Typography>
      </Box>
      <Box sx={{ overflowX: "auto" }}>
        <Table size="small">
          <TableHead>
            <TableRow>
              {head.map((h, i) => (
                <TableCell
                  key={h}
                  align={i === 2 && section === "queue" ? "right" : "left"}
                  sx={{ minWidth: i === 0 ? 240 : undefined }}
                >
                  {h}
                </TableCell>
              ))}
            </TableRow>
          </TableHead>
          <TableBody>
            {tasks.map((t) => (
              <BoardRow
                key={`${section}:${t.service}`}
                task={t}
                section={section}
                nowMs={nowMs}
                open={expanded.has(t.service)}
                onToggle={() => toggle(t.service)}
                onOpenLog={openLog}
              />
            ))}
            {tasks.length === 0 && (
              <TableRow>
                <TableCell colSpan={head.length} align="center">
                  <Typography color="text.secondary" sx={{ py: 2 }}>
                    {loading ? "loading…" : "none"}
                  </Typography>
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </Box>
    </Paper>
  );

  return (
    <Box sx={{ p: 2, display: "grid", gap: 2 }}>
      <Paper sx={{ p: 2, display: "grid", gap: 2 }}>
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 1,
            flexWrap: "wrap",
          }}
        >
          <Typography variant="h6">Orchestration</Typography>
          <Chip
            size="small"
            variant="outlined"
            color="success"
            label={`${sections.running.length} running`}
          />
          <Chip
            size="small"
            variant="outlined"
            color="primary"
            label={`${sections.queue.length} to run`}
          />
          <Chip
            size="small"
            variant="outlined"
            label={`${sections.noNeed.length} no need`}
          />
          <Box sx={{ flexGrow: 1 }} />
          {(loading || arranging || evaluating || driving) && (
            <CircularProgress size={18} />
          )}
          <Tooltip title="Reload the board (auto-refreshes every 30s)">
            <IconButton
              size="small"
              aria-label="refresh board"
              disabled={arranging || evaluating || driving}
              onClick={() => void load()}
            >
              <Refresh fontSize="small" />
            </IconButton>
          </Tooltip>
          <Tooltip title="One arrangement pass over every orchestration tier file — the freq-window run/don't-run verdicts (what the nightly chain does per tier before running it)">
            <span>
              <Button
                size="small"
                variant="outlined"
                disabled={arranging || evaluating || driving}
                onClick={() => void onArrange()}
                startIcon={<PlayArrow fontSize="small" />}
              >
                Arrange now
              </Button>
            </span>
          </Tooltip>
          <Tooltip title="Re-run every configured completeness check now (the verdicts upsert into monitor.non_daily_task_state)">
            <IconButton
              size="small"
              aria-label="evaluate checks"
              disabled={arranging || evaluating || driving}
              onClick={() => void onEvaluate()}
            >
              <Refresh fontSize="small" />
            </IconButton>
          </Tooltip>
          <Tooltip title="List the due weekend re-runs without executing anything">
            <IconButton
              size="small"
              aria-label="backfill dry run"
              disabled={arranging || evaluating || driving}
              onClick={() => void onBackfill(true)}
            >
              <PlayArrow fontSize="small" color="action" />
            </IconButton>
          </Tooltip>
          <Tooltip title="Drive the weekend backfill now (evaluate + re-run each due missing task — what the maintainer's Saturday 05:00 job fires)">
            <span>
              <IconButton
                size="small"
                aria-label="backfill now"
                disabled={arranging || evaluating || driving}
                onClick={() => void onBackfill(false)}
              >
                <PlayArrow fontSize="small" color="primary" />
              </IconButton>
            </span>
          </Tooltip>
        </Box>
        <Box sx={{ display: "flex", gap: 1, flexWrap: "wrap" }}>
          <ValueFilterSelect
            label="Tier"
            options={sections.tierOptions}
            value={tierFilter}
            onChange={setTierFilter}
          />
          <TextField
            size="small"
            label="Search service"
            sx={{ minWidth: 260 }}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            InputProps={{
              startAdornment: (
                <InputAdornment position="start">
                  <Search fontSize="small" />
                </InputAdornment>
              ),
              endAdornment:
                search !== "" ? (
                  <InputAdornment position="end">
                    <IconButton
                      size="small"
                      aria-label="clear search"
                      onClick={() => setSearch("")}
                    >
                      <Clear fontSize="small" />
                    </IconButton>
                  </InputAdornment>
                ) : undefined,
            }}
          />
        </Box>
        {error != null && <Alert severity="error">{error}</Alert>}
        {actionNote != null && <Alert severity="info">{actionNote}</Alert>}
      </Paper>

      {sectionPaper(
        "running",
        "Live running",
        "heartbeat-live services of the arrangement universe",
        sections.running,
        ["Service", "Tier", "Freq", "PID", "Started", "Last beat"],
      )}
      {sectionPaper(
        "queue",
        "To-run queue",
        "arranged due — the next chain pass's charge, priority first",
        sections.queue,
        ["Service", "Tier", "#", "Freq", "P", "Last success", "Due since", "Non-daily"],
      )}
      {sectionPaper(
        "noNeed",
        "Recently no need",
        "the freq window holds (or once done / ran since arranged)",
        sections.noNeed,
        ["Service", "Tier", "Freq", "Last success", "Next due", "Reason"],
      )}

      <LogViewerDialog
        open={logRun != null}
        onClose={() => setLogRun(null)}
        title={
          logRun == null
            ? ""
            : `${logRun.service} — run log ${shanghaiDate(new Date(logRun.startedAt))}`
        }
        subtitle={
          logRun == null
            ? null
            : [
                `started ${fmtTimeShanghai(logRun.startedAt)}`,
                logRun.endedAt != null
                  ? `ended ${fmtTimeShanghai(logRun.endedAt)}`
                  : "still running",
                logRun.exitReason != null
                  ? `exit: ${logRun.exitReason} (${logRun.isNormalExit ? "normal" : "abnormal"})`
                  : null,
                `pid ${logRun.pid}`,
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
