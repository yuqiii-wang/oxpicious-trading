/**
 * Non-daily tasks page — the monthly / quarterly / semiannual / once
 * tasks' completeness monitor (monitor.non_daily_task_state via
 * GET /api/monitor/non-daily): every non-daily orchestration tier's
 * tasks with their identity-grounded check verdicts, the check config
 * kind, the weekend backfill's attempt counter and the next Saturday
 * re-run gate, merged with each task's service_config cadence (freq +
 * schedule — how the task should run).
 *
 * The charge: non-daily tasks' own cadence guards make off-cadence runs
 * no-ops (quarterly compositions refresh only at quarter start), so a
 * failed cadence window leaves data missing for weeks — invisible to
 * the nightly self-heal. The checks compare what the task has loaded
 * against the DAILY data identities (index/etf/stock identity universes
 * vs sec_composition snapshots, dividend freshness, temps snapshots);
 * missing data re-runs on the next Saturday 05:00 China time (the
 * maintainer's non_daily_backfill job), then weekly until loaded.
 *
 * Actions (both with spinners — a check pass is quick, a drive can be
 * minutes): Evaluate re-runs every check now; Backfill (dry) lists the
 * due re-runs; Backfill now drives them (what the Saturday job fires).
 * Table chrome mirrors the Src Data Audit page (Paper → named header
 * box with counts + filter + search, small dense table).
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  Chip,
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
  Tooltip,
  Typography,
} from "@mui/material";
import { PlayArrow, Refresh, Search, Clear } from "@mui/icons-material";
import { fmtDateTimeShanghai } from "@/shared/datetime";
import {
  backfillNonDailyTasks,
  evaluateNonDailyTasks,
  fetchNonDailyTasks,
  type NonDailyTask,
} from "@/lib/api-client/monitor";
import { formatFetchError } from "@/lib/api-client/_retry";

const STATE_CHIP: Record<string, { color: "success" | "error" | "warning" | "default"; label: string }> = {
  complete: { color: "success", label: "complete" },
  missing: { color: "error", label: "missing" },
  check_failed: { color: "warning", label: "check failed" },
  unknown: { color: "default", label: "unknown" },
};

export default function NonDailyTasksPage() {
  const [tasks, setTasks] = useState<NonDailyTask[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [evaluating, setEvaluating] = useState(false);
  const [driving, setDriving] = useState(false);
  const [actionNote, setActionNote] = useState<string | null>(null);
  const [tierFilter, setTierFilter] = useState("");
  const [search, setSearch] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchNonDailyTasks();
      setTasks(res.tasks);
    } catch (err) {
      setError(formatFetchError(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const onEvaluate = useCallback(async () => {
    setEvaluating(true);
    setActionNote(null);
    setError(null);
    try {
      const res = await evaluateNonDailyTasks();
      setActionNote(
        `evaluate: ${res.evaluated} task(s) — ${res.complete} complete, ${res.missing} missing, ${res.checkFailed} check-failed`,
      );
      const refreshed = await fetchNonDailyTasks();
      setTasks(refreshed.tasks);
    } catch (err) {
      setError(formatFetchError(err));
    } finally {
      setEvaluating(false);
    }
  }, []);

  const onBackfill = useCallback(async (dryRun: boolean) => {
    setDriving(true);
    setActionNote(null);
    setError(null);
    try {
      const res = await backfillNonDailyTasks(dryRun);
      setActionNote(`backfill${dryRun ? " (dry)" : ""}: ${res.detail}`);
      const refreshed = await fetchNonDailyTasks();
      setTasks(refreshed.tasks);
    } catch (err) {
      setError(formatFetchError(err));
    } finally {
      setDriving(false);
    }
  }, []);

  const tiers = useMemo(() => {
    const set = new Set<string>((tasks ?? []).map((t) => t.tier));
    return Array.from(set).sort();
  }, [tasks]);

  const visible = useMemo(() => {
    const q = search.trim().toLowerCase();
    return (tasks ?? []).filter(
      (t) =>
        (tierFilter === "" || t.tier === tierFilter) &&
        (q === "" || t.service.toLowerCase().includes(q)),
    );
  }, [tasks, tierFilter, search]);

  const missingCount = visible.filter((t) => t.state === "missing").length;

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
          <Typography variant="h6">Non-daily tasks</Typography>
          <Chip
            size="small"
            color={missingCount > 0 ? "error" : "success"}
            variant="outlined"
            label={`${missingCount} missing / ${visible.length} shown`}
          />
          <Box sx={{ flexGrow: 1 }} />
          {(loading || evaluating || driving) && <CircularProgress size={18} />}
          <Tooltip title="Re-run every configured completeness check now (the verdicts upsert into monitor.non_daily_task_state)">
            <IconButton
              size="small"
              aria-label="evaluate checks"
              disabled={evaluating || driving}
              onClick={() => void onEvaluate()}
            >
              <Refresh fontSize="small" />
            </IconButton>
          </Tooltip>
          <Tooltip title="List the due weekend re-runs without executing anything">
            <IconButton
              size="small"
              aria-label="backfill dry run"
              disabled={evaluating || driving}
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
                disabled={evaluating || driving}
                onClick={() => void onBackfill(false)}
              >
                <PlayArrow fontSize="small" color="primary" />
              </IconButton>
            </span>
          </Tooltip>
        </Box>
        <Box sx={{ display: "flex", gap: 1, flexWrap: "wrap" }}>
          <TextField
            select
            size="small"
            label="Tier"
            sx={{ minWidth: 200 }}
            value={tierFilter}
            onChange={(e) => setTierFilter(e.target.value)}
          >
            <MenuItem value="">(all)</MenuItem>
            {tiers.map((tier) => (
              <MenuItem key={tier} value={tier}>
                {tier}
              </MenuItem>
            ))}
          </TextField>
          <TextField
            size="small"
            label="Search task"
            sx={{ minWidth: 260 }}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            InputProps={{
              startAdornment: (
                <InputAdornment position="start">
                  <Search fontSize="small" />
                </InputAdornment>
              ),
              endAdornment: search !== "" && (
                <InputAdornment position="end">
                  <IconButton size="small" aria-label="clear search" onClick={() => setSearch("")}>
                    <Clear fontSize="small" />
                  </IconButton>
                </InputAdornment>
              ),
            }}
          />
        </Box>
        {error != null && <Alert severity="error">{error}</Alert>}
        {actionNote != null && <Alert severity="info">{actionNote}</Alert>}
      </Paper>

      <Paper>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell sx={{ minWidth: 170 }}>Tier</TableCell>
              <TableCell align="right" sx={{ width: 40 }}>#</TableCell>
              <TableCell sx={{ minWidth: 240 }}>Task</TableCell>
              <TableCell sx={{ width: 100 }}>Freq</TableCell>
              <TableCell sx={{ width: 150 }}>Check</TableCell>
              <TableCell sx={{ width: 110 }}>State</TableCell>
              <TableCell align="right" sx={{ width: 80 }}>Missing</TableCell>
              <TableCell align="right" sx={{ width: 70 }}>Attempts</TableCell>
              <TableCell sx={{ width: 160 }}>Last check</TableCell>
              <TableCell sx={{ width: 160 }}>Next re-run</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {visible.map((t) => {
              const chip = STATE_CHIP[t.state] ?? STATE_CHIP.unknown;
              const detail = t.missingDetail != null
                ? JSON.stringify(t.missingDetail)
                : null;
              return (
                <TableRow
                  key={`${t.tier}#${t.step}`}
                  hover
                  sx={{
                    ...(t.state === "missing" ? { "& .MuiTableCell-root": {} } : {}),
                  }}
                >
                  <TableCell sx={{ fontFamily: "monospace" }}>{t.tier}</TableCell>
                  <TableCell align="right" sx={{ fontFamily: "monospace" }}>
                    {t.step}
                  </TableCell>
                  <TableCell sx={{ fontFamily: "monospace" }}>
                    {t.service}
                    {t.schedule != null && (
                      <Tooltip title={t.schedule}>
                        <Typography
                          variant="caption"
                          color="text.secondary"
                          sx={{ display: "block", maxWidth: 360 }}
                          noWrap
                        >
                          {t.schedule}
                        </Typography>
                      </Tooltip>
                    )}
                  </TableCell>
                  <TableCell>{t.freq ?? "—"}</TableCell>
                  <TableCell sx={{ fontFamily: "monospace", fontSize: "0.75rem" }}>
                    {t.check ?? (t.rerunEligible ? "—" : "no re-run")}
                  </TableCell>
                  <TableCell>
                    <Tooltip title={detail ?? chip.label}>
                      <Chip size="small" variant="outlined" color={chip.color} label={chip.label} />
                    </Tooltip>
                  </TableCell>
                  <TableCell align="right">
                    {t.state === "missing" || t.state === "check_failed"
                      ? t.missingCount
                      : "—"}
                  </TableCell>
                  <TableCell align="right">{t.attempts > 0 ? t.attempts : "—"}</TableCell>
                  <TableCell>{fmtDateTimeShanghai(t.lastCheckAt) ?? "—"}</TableCell>
                  <TableCell>{fmtDateTimeShanghai(t.nextCheckAt) ?? "—"}</TableCell>
                </TableRow>
              );
            })}
            {tasks != null && visible.length === 0 && (
              <TableRow>
                <TableCell colSpan={10} align="center">
                  <Typography color="text.secondary" sx={{ py: 3 }}>
                    {loading ? "loading…" : "no non-daily task matches"}
                  </Typography>
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </Paper>
    </Box>
  );
}
