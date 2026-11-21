/**
 * Cautions page — the cross-service caution trail (monitor.caution_log
 * via GET /api/monitor/cautions): one OPEN row per issue per service —
 * running-service errors (abnormal pipeline exits, orchestration step
 * failures, maintenance job crashes, the API's own unhandled errors),
 * AI-found suspicions (validation complaints that survive their repair
 * round, [CRITICAL]-flagged findings, below-floor jev reads, deep-agents
 * daemon failures) and manual pending bugs (python -m
 * _common.caution_log, the dev-skill rule).
 *
 * The working set is the OPEN rows (the default state filter, open-first
 * ordering: worst severity, most-recent-hit on top); switching to
 * resolved / all shows history. Resolve stamps is_resolved +
 * resolved_timestamp (spinner per row while the POST is in flight, then
 * a refresh); occurrence_count > 1 marks a RECURRING issue — a resolved
 * issue re-raising opens a fresh row, so an empty open view means every
 * known issue is handled.
 *
 * Table chrome mirrors the Non-daily / Alerts panels (Paper → named
 * header box with filters, small dense table); detail (the logged
 * context: error tail, traceback, tier/step, thread id…) expands
 * per-row. detail.log_context (stamped by every writer — the day's
 * service log file, an optional grep keyword, the time range) renders
 * as the LOCATE-IN-LOGS line: copyable grep one-liner + a jump into
 * the day's log (LogViewerDialog, the keyword pre-filled as filter).
 */
import { Fragment, useCallback, useEffect, useState } from "react";
import {
  Alert,
  Box,
  Chip,
  CircularProgress,
  Collapse,
  IconButton,
  MenuItem,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import {
  Article,
  ContentCopy,
  KeyboardArrowDown,
  KeyboardArrowUp,
  Refresh,
} from "@mui/icons-material";
import { fmtDateTimeShanghai, shanghaiDate } from "@/shared/datetime";
import LogViewerDialog, { type LogFileView } from "@/shared/components/LogViewerDialog";
import {
  fetchCautions,
  fetchRunLog,
  resolveCaution,
  type CautionEntry,
  type CautionSeverity,
  type CautionSource,
  type CautionState,
  type RunLog,
} from "@/lib/api-client/monitor";
import { formatFetchError } from "@/lib/api-client/_retry";

/** Severity chip palette — code order is semantic, best -> worst. */
const SEVERITY_COLOR: Record<string, "default" | "info" | "warning" | "error"> = {
  info: "default",
  warning: "warning",
  error: "error",
  critical: "error",
};

const SOURCE_COLOR: Record<string, "primary" | "secondary" | "default"> = {
  service: "primary",
  ai: "secondary",
  manual: "default",
};

/** detail.log_context — how to locate the issue in the logs: the log
 *  FILE (repo-relative logs/<date>/<service>.log), an optional grep
 *  KEYWORD and the time_from/time_to range to narrow to. Stamped by
 *  every writer (auto-derived on the Python side, logContext body field
 *  on the POST route). */
interface LogContext {
  file?: string;
  keyword?: string;
  time_from?: string;
  time_to?: string;
}

/** Extract a row's log_context, keeping only non-empty string fields —
 *  null when the row predates the contract or carries nothing usable. */
function logContextOf(detail: Record<string, unknown>): LogContext | null {
  const raw = detail.log_context;
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return null;
  const ctx: LogContext = {};
  for (const key of ["file", "keyword", "time_from", "time_to"] as const) {
    const v = (raw as Record<string, unknown>)[key];
    if (typeof v === "string" && v.trim() !== "") ctx[key] = v.trim();
  }
  return Object.keys(ctx).length > 0 ? ctx : null;
}

/** One log_context time bound for display — ISO bounds render Shanghai,
 *  free-text bounds (e.g. "09:31" from the manual CLI) pass through. */
function fmtLogTime(v: string): string {
  return Number.isNaN(Date.parse(v)) ? v : fmtDateTimeShanghai(v);
}

/** The copyable locate one-liner: grep the keyword in the file, or tail
 *  the file when no keyword was recorded. Null when there is no file. */
function grepCommand(ctx: LogContext): string | null {
  if (ctx.file == null) return null;
  return ctx.keyword != null
    ? `grep -n '${ctx.keyword.replace(/'/g, `'\\''`)}' ${ctx.file}`
    : `tail -n 200 ${ctx.file}`;
}

/** Compact pretty-print of a row's detail JSON (the expandable context;
 *  log_context is excluded — it renders as its own locate-in-logs line). */
function detailText(detail: Record<string, unknown>): string {
  const rest = Object.fromEntries(
    Object.entries(detail).filter(([key]) => key !== "log_context"),
  );
  if (Object.keys(rest).length === 0) return "(no detail recorded)";
  try {
    return JSON.stringify(rest, null, 2);
  } catch {
    return String(rest);
  }
}

export default function CautionsPage() {
  const [rows, setRows] = useState<CautionEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [state, setState] = useState<CautionState>("open");
  const [source, setSource] = useState<CautionSource | "all">("all");
  const [severity, setSeverity] = useState<CautionSeverity | "all">("all");
  const [resolvingId, setResolvingId] = useState<number | null>(null);
  const [actionNote, setActionNote] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  // The locate-in-logs jump: the day's service log opened in the shared
  // LogViewerDialog, the caution's keyword landing as its filter.
  const [logDialog, setLogDialog] = useState<
    { service: string; date: string; keyword: string | null } | null
  >(null);
  const [runLog, setRunLog] = useState<RunLog | null>(null);
  const [runLogLoading, setRunLogLoading] = useState(false);
  const [runLogError, setRunLogError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchCautions(
        state,
        source === "all" ? null : source,
        severity === "all" ? null : severity,
      );
      setRows(res.rows);
    } catch (err) {
      setError(formatFetchError(err));
    } finally {
      setLoading(false);
    }
  }, [state, source, severity]);

  useEffect(() => {
    void load();
  }, [load]);

  const onResolve = useCallback(async (row: CautionEntry) => {
    setResolvingId(row.id);
    setActionNote(null);
    try {
      await resolveCaution(row.id);
      await load();
    } catch (err) {
      setActionNote(formatFetchError(err));
    } finally {
      setResolvingId(null);
    }
  }, [load]);

  const openLog = useCallback((row: CautionEntry, ctx: LogContext) => {
    // A house path logs/<YYYY-MM-DD>/<service>.log carries its own
    // (service, date); any other pointer falls back to the row's own
    // service + its first-seen day (Shanghai date — the logs/ tree is
    // keyed by local day).
    const m = ctx.file?.match(/^logs\/(\d{4}-\d{2}-\d{2})\/(.+)\.log$/);
    const service = m != null ? m[2] : row.service;
    const date = m?.[1] ?? shanghaiDate(new Date(row.createdAt));
    setLogDialog({ service, date, keyword: ctx.keyword ?? null });
    setRunLog(null);
    setRunLogError(null);
    setRunLogLoading(true);
    fetchRunLog(service, date)
      .then(setRunLog)
      .catch((e: unknown) => setRunLogError(formatFetchError(e)))
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

  return (
    <>
      <Paper variant="outlined" sx={{ p: 2 }}>
        <Box sx={{ display: "flex", alignItems: "center", gap: 1, flexWrap: "wrap", mb: 1 }}>
          <Typography variant="subtitle2">Cautions</Typography>
          {rows != null && (
            <Chip
              size="small"
              label={`${rows.length} row${rows.length === 1 ? "" : "s"}`}
              variant="outlined"
            />
          )}
          <Box sx={{ flex: 1 }} />
          <TextField
            select
            size="small"
            label="state"
            value={state}
            onChange={(e) => setState(e.target.value as CautionState)}
            sx={{ minWidth: 120 }}
          >
            <MenuItem value="open">open</MenuItem>
            <MenuItem value="resolved">resolved</MenuItem>
            <MenuItem value="all">all</MenuItem>
          </TextField>
          <TextField
            select
            size="small"
            label="source"
            value={source}
            onChange={(e) => setSource(e.target.value as CautionSource | "all")}
            sx={{ minWidth: 110 }}
          >
            <MenuItem value="all">all</MenuItem>
            <MenuItem value="service">service</MenuItem>
            <MenuItem value="ai">ai</MenuItem>
            <MenuItem value="manual">manual</MenuItem>
          </TextField>
          <TextField
            select
            size="small"
            label="severity"
            value={severity}
            onChange={(e) => setSeverity(e.target.value as CautionSeverity | "all")}
            sx={{ minWidth: 110 }}
          >
            <MenuItem value="all">all</MenuItem>
            <MenuItem value="info">info</MenuItem>
            <MenuItem value="warning">warning</MenuItem>
            <MenuItem value="error">error</MenuItem>
            <MenuItem value="critical">critical</MenuItem>
          </TextField>
          <Tooltip title="Refresh">
            <IconButton onClick={() => void load()} disabled={loading}>
              {loading ? <CircularProgress size={20} /> : <Refresh />}
            </IconButton>
          </Tooltip>
        </Box>
        <Typography variant="caption" color="text.secondary" sx={{ mb: 1, display: "block" }}>
          monitor.caution_log · one open row per issue per service (recurrences bump
          occurrence_count / last_seen_at) · resolve closes the row — a resolved issue
          re-raising opens a fresh one · writers: pipeline/daemon exits, orchestration
          steps, maintainer jobs, the API's own errors, AI suspicion seams,
          python -m _common.caution_log (manual)
        </Typography>
        {error != null && <Alert severity="error">Failed to load cautions: {error}</Alert>}
        {actionNote != null && (
          <Alert severity="error" sx={{ mt: 1 }}>Resolve failed: {actionNote}</Alert>
        )}
        {!error && rows != null && rows.length === 0 && (
          <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
            {state === "open"
              ? "No open cautions — every known issue is resolved."
              : "No caution rows match the filters."}
          </Typography>
        )}
        {rows != null && rows.length > 0 && (
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell width={36} />
                  <TableCell>Severity</TableCell>
                  <TableCell>Source</TableCell>
                  <TableCell>Service</TableCell>
                  <TableCell>Title</TableCell>
                  <TableCell align="center">Hits</TableCell>
                  <TableCell>First seen</TableCell>
                  <TableCell>Last seen</TableCell>
                  <TableCell>Resolved</TableCell>
                  <TableCell align="right">Action</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {rows.map((row) => {
                const logCtx = logContextOf(row.detail);
                const grep = logCtx != null ? grepCommand(logCtx) : null;
                return (
                  <Fragment key={row.id}>
                    <TableRow
                      hover
                      onClick={() => setExpandedId(expandedId === row.id ? null : row.id)}
                      sx={{ "& > *": { borderBottom: "unset" }, cursor: "pointer" }}
                    >
                      <TableCell>
                        <IconButton size="small">
                          {expandedId === row.id ? (
                            <KeyboardArrowUp fontSize="small" />
                          ) : (
                            <KeyboardArrowDown fontSize="small" />
                          )}
                        </IconButton>
                      </TableCell>
                      <TableCell>
                        <Chip
                          size="small"
                          variant="outlined"
                          color={SEVERITY_COLOR[row.severityLabel] ?? "default"}
                          label={row.severityLabel}
                        />
                      </TableCell>
                      <TableCell>
                        <Chip
                          size="small"
                          variant="outlined"
                          color={SOURCE_COLOR[row.sourceLabel] ?? "default"}
                          label={row.sourceLabel}
                        />
                      </TableCell>
                      <TableCell sx={{ whiteSpace: "nowrap", fontFamily: "monospace", fontSize: "0.75rem" }}>
                        {row.service}
                      </TableCell>
                      <TableCell sx={{ maxWidth: 420, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {row.title}
                      </TableCell>
                      <TableCell align="center" sx={{ fontVariantNumeric: "tabular-nums" }}>
                        {row.occurrenceCount > 1 ? (
                          <Tooltip title={`recurring issue — hit ${row.occurrenceCount} times while open`}>
                            <Chip size="small" color="warning" variant="outlined" label={`×${row.occurrenceCount}`} />
                          </Tooltip>
                        ) : (
                          1
                        )}
                      </TableCell>
                      <TableCell sx={{ whiteSpace: "nowrap", fontVariantNumeric: "tabular-nums" }}>
                        {fmtDateTimeShanghai(row.createdAt)}
                      </TableCell>
                      <TableCell sx={{ whiteSpace: "nowrap", fontVariantNumeric: "tabular-nums" }}>
                        {fmtDateTimeShanghai(row.lastSeenAt)}
                      </TableCell>
                      <TableCell sx={{ whiteSpace: "nowrap", fontVariantNumeric: "tabular-nums" }}>
                        {row.resolvedTimestamp != null
                          ? fmtDateTimeShanghai(row.resolvedTimestamp)
                          : "—"}
                      </TableCell>
                      <TableCell align="right">
                        {!row.isResolved && (
                          <Tooltip title="Resolve — close this issue (a recurrence re-raises it)">
                            <span>
                              <IconButton
                                size="small"
                                color="success"
                                disabled={resolvingId !== null}
                                onClick={(e) => {
                                  e.stopPropagation();
                                  void onResolve(row);
                                }}
                              >
                                {resolvingId === row.id ? (
                                  <CircularProgress size={18} />
                                ) : (
                                  "✓"
                                )}
                              </IconButton>
                            </span>
                          </Tooltip>
                        )}
                      </TableCell>
                    </TableRow>
                    <TableRow>
                      <TableCell colSpan={10} sx={{ py: 0 }}>
                        <Collapse in={expandedId === row.id} unmountOnExit>
                          <Box sx={{ py: 1.5, px: 1 }}>
                            <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 0.5 }}>
                              dedupe key: <code>{row.dedupeKey}</code> · id {row.id}
                            </Typography>
                            {logCtx != null && (
                              <Box sx={{ display: "flex", alignItems: "center", gap: 1, flexWrap: "wrap", mb: 0.5 }}>
                                <Typography variant="caption" color="text.secondary">
                                  Locate in logs:
                                </Typography>
                                {logCtx.file != null && (
                                  <Tooltip title={logCtx.file}>
                                    <Chip
                                      size="small"
                                      variant="outlined"
                                      sx={{ fontFamily: "monospace", maxWidth: 420 }}
                                      label={logCtx.file}
                                    />
                                  </Tooltip>
                                )}
                                {logCtx.keyword != null && (
                                  <Tooltip title={`grep keyword: ${logCtx.keyword}`}>
                                    <Chip
                                      size="small"
                                      color="info"
                                      variant="outlined"
                                      sx={{ fontFamily: "monospace", maxWidth: 260 }}
                                      label={logCtx.keyword}
                                    />
                                  </Tooltip>
                                )}
                                {(logCtx.time_from != null || logCtx.time_to != null) && (
                                  <Typography
                                    variant="caption"
                                    color="text.secondary"
                                    sx={{ fontVariantNumeric: "tabular-nums" }}
                                  >
                                    {[logCtx.time_from, logCtx.time_to]
                                      .filter((t): t is string => t != null)
                                      .map(fmtLogTime)
                                      .join(" – ")}
                                  </Typography>
                                )}
                                <Box sx={{ flex: 1 }} />
                                {grep != null && (
                                  <Tooltip title={`Copy: ${grep}`}>
                                    <IconButton
                                      size="small"
                                      aria-label="copy grep command"
                                      onClick={() => {
                                        navigator.clipboard.writeText(grep).catch(() => {
                                          // clipboard unavailable — nothing to do
                                        });
                                      }}
                                    >
                                      <ContentCopy fontSize="small" />
                                    </IconButton>
                                  </Tooltip>
                                )}
                                <Tooltip title="Open the day's service log (keyword pre-filtered)">
                                  <IconButton
                                    size="small"
                                    aria-label="open service log"
                                    color="primary"
                                    disabled={runLogLoading}
                                    onClick={() => openLog(row, logCtx)}
                                  >
                                    {runLogLoading ? (
                                      <CircularProgress size={16} />
                                    ) : (
                                      <Article fontSize="small" />
                                    )}
                                  </IconButton>
                                </Tooltip>
                              </Box>
                            )}
                            <Box
                              component="pre"
                              sx={{
                                m: 0, p: 1, borderRadius: 1, overflow: "auto",
                                maxHeight: 260, fontSize: "0.75rem",
                                bgcolor: "action.hover", fontFamily: "monospace",
                                whiteSpace: "pre-wrap", wordBreak: "break-word",
                              }}
                            >
                              {detailText(row.detail)}
                            </Box>
                          </Box>
                        </Collapse>
                      </TableCell>
                    </TableRow>
                  </Fragment>
                );
                })}
              </TableBody>
            </Table>
          </TableContainer>
        )}
      </Paper>
      <LogViewerDialog
        open={logDialog != null}
        onClose={() => setLogDialog(null)}
        title={logDialog != null ? `${logDialog.service}.log` : ""}
        subtitle={
          logDialog != null
            ? `${logDialog.date} · located from the caution's log context`
            : null
        }
        log={logView}
        loading={runLogLoading}
        error={runLogError}
        initialFilter={logDialog?.keyword ?? undefined}
      />
    </>
  );
}
