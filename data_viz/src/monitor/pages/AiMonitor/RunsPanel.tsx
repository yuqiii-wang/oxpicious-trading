/**
 * RunsPanel — the AI tab's deep-agent half: the daemon's RUNNING runs
 * (GET /api/ai/summary/active through the bridge) above the recent run
 * history (GET /api/ai/summary-runs — one row per planned thread,
 * paginated client-side at PAGE_SIZE rows per page).
 * Clicking a run imports and renders the AI summary's LangGraph
 * component (ThreadGraphView from the analysis AI feature) for that
 * thread — live for a running run (the graph 404-polls until the plan
 * rows persist), settled for a finished one. The AI event channel
 * (subscribeAiEvents) was the instant path — 2026-11-11 it is
 * INTENTIONALLY DISABLED (AI_EVENTS_CHANNEL_DISABLED in aiEvents.ts,
 * bypassed not removed; its handler sits dormant) and a 60s poll
 * (AI_EVENTS_FALLBACK_POLL_MS) refreshes the history + running list in
 * its place; the graph view keeps its own 60s live poll. Each row ends
 * in a delete cross (the AI page's Summary History pattern) wired to
 * DELETE /api/ai/summary-runs/:threadId — disabled while the run is
 * live, the same live-run refusal the server already enforces.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  Box,
  Chip,
  CircularProgress,
  IconButton,
  Pagination,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import { Close } from "@mui/icons-material";
import {
  deleteSummaryRun,
  fetchSummaryRuns,
  fetchActiveSummaryRuns,
  stopAnalysisSummary,
  type AiActiveSummaryRun,
  type AiSummaryRun,
} from "@/lib/api-client/aiSummary";
import {
  subscribeAiEvents,
  AI_EVENTS_FALLBACK_POLL_MS,
} from "@/lib/api-client/aiEvents";
import ThreadGraphView from "@/analysis/pages/Ai/ThreadGraph";

const RUNS_LIMIT = 50;

/** History rows per page — the client-side pager below the table. */
const PAGE_SIZE = 10;

/** The daemon's monotonic started_at is opaque ordering info — the
 *  running chip shows the short thread id, not a wall-clock start. */
function shortThreadId(threadId: string): string {
  return threadId.slice(0, 8);
}

type RunState = "running" | "failed" | "done";

function runState(
  run: AiSummaryRun,
  activeIds: ReadonlySet<string>,
): RunState {
  if (activeIds.has(run.thread_id)) return "running";
  if (run.steps_failed > 0) return "failed";
  return "done";
}

const STATE_CHIP: Record<
  RunState,
  { label: string; color: "success" | "error" | "default" }
> = {
  running: { label: "running", color: "success" },
  failed: { label: "failed", color: "error" },
  done: { label: "done", color: "default" },
};

export default function RunsPanel() {
  const [runs, setRuns] = useState<AiSummaryRun[] | null>(null);
  const [active, setActive] = useState<AiActiveSummaryRun[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [selectedThreadId, setSelectedThreadId] = useState<string | null>(null);
  const [graphSignal, setGraphSignal] = useState(0);
  const [page, setPage] = useState(1);
  // Rows whose delete is in flight — the clicked cross spins until the
  // refreshed history drops the row (on failure the entry clears and
  // the cross comes back).
  const [deletingIds, setDeletingIds] = useState<ReadonlySet<string>>(
    new Set(),
  );
  const selectedRef = useRef<string | null>(null);
  selectedRef.current = selectedThreadId;

  const refresh = useCallback(() => {
    // Both reads are live state (no-store endpoints) — a failure leaves
    // the previous rows up with the error surfaced above the table.
    fetchSummaryRuns(undefined, RUNS_LIMIT)
      .then((rows) => {
        setRuns(rows);
        setError(null);
      })
      .catch((e: unknown) => setError(String(e)));
    fetchActiveSummaryRuns()
      .then(setActive)
      .catch(() => setActive([])); // daemon down → nothing running
  }, []);

  useEffect(() => {
    refresh();
    // The event channel used to drive the refreshes while the page is
    // open (lifecycle events re-reading the history + running list,
    // hello re-syncing after a reconnect, open-thread events bumping
    // the graph's refreshSignal). 2026-11-11 the channel is
    // INTENTIONALLY DISABLED (AI_EVENTS_CHANNEL_DISABLED in aiEvents.ts
    // — bypassed, not removed): no frame ever arrives, so the 60s poll
    // below IS the refresh path and the handler stays dormant for the
    // re-enable day.
    const unsubscribe = subscribeAiEvents((ev) => {
      if (
        ev.type === "thread_started" ||
        ev.type === "thread_settled" ||
        ev.type === "hello" ||
        ev.type === "thread_deleted"
      ) {
        refresh();
        if (ev.type === "thread_deleted" && ev.thread_id === selectedRef.current) {
          setSelectedThreadId(null);
        }
      } else if (
        ev.thread_id != null &&
        ev.thread_id === selectedRef.current
      ) {
        setGraphSignal((s) => s + 1);
      }
    });
    const timer = setInterval(refresh, AI_EVENTS_FALLBACK_POLL_MS);
    return () => {
      unsubscribe();
      clearInterval(timer);
    };
  }, [refresh]);

  const activeIds = new Set(active.map((a) => a.thread_id));
  const activeUnplanned = active.filter(
    (a) => !runs?.some((r) => r.thread_id === a.thread_id),
  );

  const deleteRun = useCallback(
    async (threadId: string) => {
      setDeletingIds((prev) => new Set(prev).add(threadId));
      try {
        await deleteSummaryRun(threadId);
      } catch (err) {
        setError(
          `Failed to delete the run: ${
            err instanceof Error ? err.message : String(err)
          }`,
        );
        setDeletingIds((prev) => {
          const next = new Set(prev);
          next.delete(threadId);
          return next;
        });
        return;
      }
      refresh();
      if (selectedRef.current === threadId) {
        setSelectedThreadId(null);
      }
    },
    [refresh],
  );

  // The embedded ThreadGraphView's Stop (onStop prop): cancel the live
  // chain the canvas shows, keyed on its super thread id. The view owns
  // the button's busy state + the graph's post-stop refresh pulses;
  // this refreshes the panel's own running/history rows so the chips
  // follow (once now, once after the settle has had time to land — the
  // stop POST returns before the cancelled run finishes settling).
  const stopRun = useCallback(
    async (superThreadId: string) => {
      try {
        await stopAnalysisSummary(superThreadId);
      } catch (err) {
        setError(
          `Failed to stop the run: ${
            err instanceof Error ? err.message : String(err)
          }`,
        );
      }
      refresh();
      setTimeout(refresh, 3000);
    },
    [refresh],
  );

  const totalPages = Math.max(1, Math.ceil((runs?.length ?? 0) / PAGE_SIZE));
  // A lifecycle refresh can shrink the history while a later page is
  // open — clamp the rendered page instead of resetting, so the reader
  // keeps their place when rows come back.
  const curPage = Math.min(page, totalPages);
  const pageRuns = (runs ?? []).slice(
    (curPage - 1) * PAGE_SIZE,
    curPage * PAGE_SIZE,
  );

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 2,
          flexWrap: "wrap",
        }}
      >
        <Typography variant="subtitle2">Deep agents</Typography>
        {active.length > 0 ? (
          <Box sx={{ display: "flex", alignItems: "center", gap: 0.75, flexWrap: "wrap" }}>
            <Typography variant="caption" color="text.secondary">
              running now:
            </Typography>
            {active.map((a) => (
              <Chip
                key={a.thread_id}
                size="small"
                color="success"
                variant={a.thread_id === selectedThreadId ? "filled" : "outlined"}
                label={shortThreadId(a.thread_id)}
                onClick={() =>
                  setSelectedThreadId(
                    a.thread_id === selectedThreadId ? null : a.thread_id,
                  )
                }
                sx={{ fontFamily: "monospace", cursor: "pointer" }}
              />
            ))}
          </Box>
        ) : (
          <Typography variant="caption" color="text.secondary">
            none running
          </Typography>
        )}
        <Typography variant="caption" color="text.secondary" sx={{ ml: "auto" }}>
          click a run for its LangGraph thread
        </Typography>
      </Box>
      {error != null && (
        <Typography variant="caption" color="error">
          {error}
        </Typography>
      )}
      <Paper sx={{ overflowX: "auto" }}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Started</TableCell>
              <TableCell>Goal</TableCell>
              <TableCell>Scope</TableCell>
              <TableCell align="right">Steps</TableCell>
              <TableCell>Status</TableCell>
              <TableCell />
            </TableRow>
          </TableHead>
          <TableBody>
            {pageRuns.map((r) => {
              const state = runState(r, activeIds);
              const chip = STATE_CHIP[state];
              return (
                <TableRow
                  key={r.thread_id}
                  hover
                  selected={r.thread_id === selectedThreadId}
                  onClick={() =>
                    setSelectedThreadId(
                      r.thread_id === selectedThreadId ? null : r.thread_id,
                    )
                  }
                  sx={{ cursor: "pointer" }}
                >
                  <TableCell sx={{ whiteSpace: "nowrap" }}>
                    {r.first_at.slice(0, 16)}
                  </TableCell>
                  <TableCell>
                    <Typography
                      variant="caption"
                      sx={{
                        display: "inline-block",
                        maxWidth: 460,
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                        verticalAlign: "bottom",
                      }}
                    >
                      {r.goal ?? "(no plan persisted)"}
                    </Typography>
                  </TableCell>
                  <TableCell>
                    {r.scope_name ?? (r.scope_code ? r.scope_code : "—")}
                  </TableCell>
                  <TableCell align="right">
                    {r.steps_completed + r.steps_failed + r.steps_skipped}
                    /{r.steps_total}
                    {r.steps_failed > 0 && (
                      <Typography
                        component="span"
                        variant="caption"
                        color="error"
                        sx={{ ml: 0.5 }}
                      >
                        ({r.steps_failed} failed)
                      </Typography>
                    )}
                  </TableCell>
                  <TableCell>
                    <Chip
                      size="small"
                      color={chip.color}
                      variant={state === "running" ? "filled" : "outlined"}
                      label={chip.label}
                    />
                  </TableCell>
                  <TableCell sx={{ pr: 1 }}>
                    <IconButton
                      size="small"
                      aria-label="Delete run"
                      disabled={state === "running" || deletingIds.has(r.thread_id)}
                      onClick={(e) => {
                        // Not a row pick — purge the run entry instead.
                        e.stopPropagation();
                        void deleteRun(r.thread_id);
                      }}
                      sx={{
                        color: "text.secondary",
                        "&:hover": { color: "text.primary" },
                      }}
                    >
                      {deletingIds.has(r.thread_id) ? (
                        <CircularProgress size={14} />
                      ) : (
                        <Close fontSize="small" />
                      )}
                    </IconButton>
                  </TableCell>
                </TableRow>
              );
            })}
            {activeUnplanned.map((a) => (
              // A run registers its thread id BEFORE the plan phase
              // persists any rows — the history can't show it yet, so
              // the running chip gets its own placeholder row.
              <TableRow
                key={a.thread_id}
                hover
                selected={a.thread_id === selectedThreadId}
                onClick={() =>
                  setSelectedThreadId(
                    a.thread_id === selectedThreadId ? null : a.thread_id,
                  )
                }
                sx={{ cursor: "pointer" }}
              >
                <TableCell sx={{ whiteSpace: "nowrap" }}>—</TableCell>
                <TableCell>
                  <Typography variant="caption" color="text.secondary">
                    registering…
                  </Typography>
                </TableCell>
                <TableCell>—</TableCell>
                <TableCell align="right">—</TableCell>
                <TableCell>
                  <Chip size="small" color="success" variant="filled" label="running" />
                </TableCell>
                <TableCell sx={{ pr: 1 }}>
                  <IconButton
                    size="small"
                    aria-label="Delete run"
                    disabled
                    sx={{ color: "text.secondary" }}
                  >
                    <Close fontSize="small" />
                  </IconButton>
                </TableCell>
              </TableRow>
            ))}
            {runs != null && runs.length === 0 && activeUnplanned.length === 0 && (
              <TableRow>
                <TableCell colSpan={6} align="center">
                  <Typography
                    variant="caption"
                    color="text.secondary"
                    sx={{ my: 2, display: "inline-block" }}
                  >
                    No deep-agent runs recorded — generate an analysis summary
                    on the AI page.
                  </Typography>
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
        {runs == null && error == null && (
          <Box sx={{ display: "flex", justifyContent: "center", py: 2 }}>
            <CircularProgress size={20} />
          </Box>
        )}
        {runs != null && runs.length > 0 && (
          <Box sx={{ display: "flex", alignItems: "center", px: 1.5, pb: 0.5 }}>
            <Typography variant="caption" color="text.secondary">
              {runs.length} runs
            </Typography>
            {totalPages > 1 && (
              <Pagination
                count={totalPages}
                page={curPage}
                onChange={(_, v) => setPage(v)}
                size="small"
                siblingCount={1}
                boundaryCount={1}
                sx={{ ml: "auto" }}
              />
            )}
          </Box>
        )}
      </Paper>
      {selectedThreadId != null && (
        <Paper sx={{ p: 1.5 }}>
          <ThreadGraphView
            threadId={selectedThreadId}
            expectLive={activeIds.has(selectedThreadId)}
            onSettled={refresh}
            refreshSignal={graphSignal}
            onStop={stopRun}
          />
        </Paper>
      )}
    </Box>
  );
}
