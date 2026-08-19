/**
 * TrainLogsDialog — "Training Logs" view of the Train Model split-button
 * dropdown.
 *
 * Shows one training run's process from strategy.training_runs +
 * strategy.training_trials (written by training_store.py):
 *   - Run selector (latest 10) + status + step-5 winner summary.
 *   - The TWO regime losses displayed SEPARATELY (tabs):
 *       Set A · Omega   — signal params TPE trials (Optuna trial no).
 *       Set B · Calmar  — execution grid points per top-K candidate
 *                         (cand # = the candidate's Stage A trial no).
 */
import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  Chip,
  CircularProgress,
  Dialog,
  DialogContent,
  DialogTitle,
  MenuItem,
  Stack,
  Tab,
  Tabs,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TablePagination,
  TableRow,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import {
  fetchTrainInfo,
  type TrainInfoResponse,
  type TrainTrialRow,
} from "@/lib/api-client";
import type { MaSpreadSecType } from "@shared/types";

export interface TrainLogsDialogProps {
  open: boolean;
  onClose: () => void;
  code: string;
  secType: MaSpreadSecType;
  strategyName: string;
}

function num(v: unknown, digits = 4): string {
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n.toFixed(digits) : "–";
}

function pct(v: unknown, digits = 1): string {
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? `${(n * 100).toFixed(digits)}%` : "–";
}

function statusColor(status: string): "success" | "error" | "warning" {
  if (status === "completed") return "success";
  if (status === "failed") return "error";
  return "warning";
}

function fmtTime(iso: string | null): string {
  return iso ? iso.slice(0, 19).replace("T", " ") : "–";
}

/** Omega display — the all-wins degenerate case is capped at 1e6; show
 *  it as "cap" instead of an exploded 1000000.000. */
function fmtOmega(v: unknown): string {
  const n = typeof v === "number" ? v : Number(v);
  if (!Number.isFinite(n)) return "–";
  if (n >= 999_999) return "1M (cap)";
  return n.toFixed(3);
}

/** One table column spec — `title` renders a header tooltip, `bold`
 *  bolds the cell (used to mark incumbent-improving trials). */
interface TrialColumn {
  key: string;
  label: string;
  align?: "right";
  title?: string;
  render: (r: TrainTrialRow) => string;
  bold?: (r: TrainTrialRow) => boolean;
}

/** Paginated table with a stable column spec. */
function TrialsTable({
  columns,
  rows,
}: {
  columns: TrialColumn[];
  rows: TrainTrialRow[];
}) {
  const [page, setPage] = useState(0);
  const [rowsPerPage, setRowsPerPage] = useState(25);
  const paged = rows.slice(page * rowsPerPage, page * rowsPerPage + rowsPerPage);
  return (
    <Box>
      <Table size="small" stickyHeader sx={{ "& th, & td": { px: 1, py: 0.5 } }}>
        <TableHead>
          <TableRow>
            {columns.map((c) => (
              <TableCell key={c.key} align={c.align ?? "left"}>
                {c.title ? (
                  <Tooltip title={c.title}>
                    <span>{c.label}</span>
                  </Tooltip>
                ) : (
                  c.label
                )}
              </TableCell>
            ))}
          </TableRow>
        </TableHead>
        <TableBody>
          {paged.map((r) => (
            <TableRow
              key={`${r.loss_type}-${r.trial_no}-${r.grid_idx}`}
              hover
              sx={{
                opacity: r.no_trades ? 0.45 : 1,
                bgcolor: r.constraint_ok ? undefined : "error.dark",
                "&:hover": { bgcolor: r.constraint_ok ? undefined : "error.dark" },
              }}
            >
              {columns.map((c) => (
                <TableCell
                  key={c.key}
                  align={c.align ?? "left"}
                  sx={{ fontFamily: "monospace", fontWeight: c.bold?.(r) ? 700 : 400 }}
                >
                  {c.render(r)}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <TablePagination
        component="div"
        count={rows.length}
        page={page}
        onPageChange={(_, p) => setPage(p)}
        rowsPerPage={rowsPerPage}
        onRowsPerPageChange={(e) => {
          setRowsPerPage(parseInt(e.target.value, 10));
          setPage(0);
        }}
        rowsPerPageOptions={[10, 25, 50, 100]}
        size="small"
      />
    </Box>
  );
}

/** Loss-semantics tooltip shared by both tabs — explains why per-trial
 *  losses BOUNCE (TPE explores) and what the penalty band means. */
const LOSS_TITLE_A =
  "Feasible trial → −Ω.  Violating (>55% pos months) → 100 + 20·deficit − min(Ω,10) ∈ [90,120].  " +
  "No trades → 1000. TPE trial losses legitimately bounce; watch Best ↓ for convergence.";
const LOSS_TITLE_B =
  "Feasible grid point → −Calmar.  DD breach (>25%) → 100 + 20·deficit − min(C,10) ∈ [90,120].  " +
  "No trades → 1000.";

interface IncumbentMeta {
  best: number;
  isBest: boolean;
}

/** Set A columns — built per-run so the "Best ↓" column can read the
 *  incumbent map (running min of loss, chronological). */
function buildSetAColumns(incumbent: Map<string, IncumbentMeta>): TrialColumn[] {
  const bestOf = (r: TrainTrialRow): IncumbentMeta =>
    incumbent.get(`${r.trial_no}-${r.grid_idx}`) ?? { best: r.loss, isBest: false };
  return [
    { key: "trial", label: "Trial", render: (r) => String(r.trial_no) },
    {
      key: "loss", label: "Loss", align: "right", title: LOSS_TITLE_A,
      render: (r) => num(r.loss),
    },
    {
      key: "best", label: "Best ↓", align: "right",
      title: "Running best (incumbent) loss — the convergence trend. ★ = this trial improved it.",
      render: (r) => (bestOf(r).isBest ? `★ ${num(bestOf(r).best)}` : num(bestOf(r).best)),
      bold: (r) => bestOf(r).isBest,
    },
    {
      key: "omega", label: "Omega", align: "right",
      title: "Σ gains / Σ losses of per-exit returns (capped at 1e6 when all trades win).",
      render: (r) => fmtOmega(r.metrics.omega),
    },
    {
      key: "posm", label: "Pos months", align: "right",
      render: (r) => pct(r.metrics.positive_month_fraction),
    },
    {
      key: "trades", label: "Trades", align: "right",
      render: (r) => String(r.metrics.n_trades ?? "–"),
    },
    {
      key: "ok", label: "Constraint",
      render: (r) => (r.no_trades ? "no trades" : r.constraint_ok ? "ok" : "violated"),
    },
  ];
}

const SET_B_COLUMNS: TrialColumn[] = [
  { key: "cand", label: "Cand (A-trial)", render: (r) => String(r.trial_no) },
  { key: "grid", label: "Grid", align: "right", render: (r) => String(r.grid_idx) },
  {
    key: "loss", label: "Loss", align: "right", title: LOSS_TITLE_B,
    render: (r) => num(r.loss),
  },
  { key: "calmar", label: "Calmar", align: "right", render: (r) => num(r.metrics.calmar, 3) },
  { key: "dd", label: "Max DD", align: "right", render: (r) => pct(r.metrics.max_dd_pct) },
  { key: "ret", label: "OOS Ret", align: "right", render: (r) => pct(r.metrics.total_return) },
  { key: "trades", label: "Trades", align: "right", render: (r) => String(r.metrics.n_trades ?? "–") },
  {
    key: "ok", label: "Constraint",
    render: (r) => (r.no_trades ? "no trades" : r.constraint_ok ? "ok (DD ≤ 25%)" : "violated"),
  },
];

export function TrainLogsDialog(props: TrainLogsDialogProps) {
  const { open, onClose, code, secType, strategyName } = props;
  const [data, setData] = useState<TrainInfoResponse | null>(null);
  const [runId, setRunId] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState(0);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchTrainInfo(code, secType, undefined, 0, runId)
      .then((res) => {
        if (cancelled) return;
        setData(res);
        if (runId === null && res.trials_run_id !== null) {
          setRunId(res.trials_run_id);
        }
      })
      .catch((e) => {
        if (!cancelled) setError(String(e instanceof Error ? e.message : e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, code, secType, strategyName, runId]);

  const run = useMemo(
    () => data?.runs.find((r) => r.run_id === (runId ?? data.trials_run_id)) ?? null,
    [data, runId],
  );
  const setATrials = useMemo(
    () => (data?.trials.filter((t) => t.loss_type === "set_a_omega") ?? [])
      .slice()
      .sort((a, b) => a.trial_no - b.trial_no || a.grid_idx - b.grid_idx),
    [data],
  );
  const setBTrials = useMemo(
    () => data?.trials.filter((t) => t.loss_type === "set_b_calmar") ?? [],
    [data],
  );

  /** Running-best (incumbent) map + convergence summary for Set A —
   *  makes TPE convergence visible against the bouncing raw losses. */
  const aIncumbent = useMemo(() => {
    const map = new Map<string, IncumbentMeta>();
    let best = Number.POSITIVE_INFINITY;
    let firstFeasible: number | null = null;
    for (const t of setATrials) {
      const isBest = t.loss < best;
      if (isBest) best = t.loss;
      if (firstFeasible === null && t.constraint_ok && !t.no_trades) {
        firstFeasible = t.trial_no;
      }
      map.set(`${t.trial_no}-${t.grid_idx}`, { best, isBest });
    }
    return { map, finalBest: best, firstFeasible };
  }, [setATrials]);
  const setAColumns = useMemo(
    () => buildSetAColumns(aIncumbent.map),
    [aIncumbent],
  );

  return (
    <Dialog open={open} onClose={onClose} maxWidth="lg" fullWidth>
      <DialogTitle sx={{ pb: 1 }}>
        Training Logs · {secType} {code} · {strategyName}
      </DialogTitle>
      <DialogContent dividers>
        {loading && (
          <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
            <CircularProgress size={28} />
          </Box>
        )}
        {error && <Alert severity="error">{error}</Alert>}
        {!loading && !error && data && data.runs.length === 0 && (
          <Alert severity="info">
            No training runs recorded yet — click Train Model to run a study.
          </Alert>
        )}
        {!loading && !error && data && data.runs.length > 0 && (
          <Stack spacing={1.5}>
            <Stack direction="row" spacing={2} alignItems="center" flexWrap="wrap">
              <TextField
                select
                size="small"
                label="Run"
                sx={{ minWidth: 300 }}
                value={runId ?? data.trials_run_id ?? ""}
                onChange={(e) => setRunId(Number(e.target.value))}
              >
                {data.runs.map((r) => (
                  <MenuItem key={r.run_id} value={r.run_id}>
                    #{r.run_id} · {fmtTime(r.started_at)} · {r.trials} trials · {r.status}
                  </MenuItem>
                ))}
              </TextField>
              {run && <Chip size="small" color={statusColor(run.status)} label={run.status} />}
              {run?.winner_trial_no !== null && run?.winner_trial_no !== undefined && (
                <Tooltip title="Step 5 winner: the (Set A, Set B) combo with the best Calmar under the DD cap">
                  <Chip size="small" color="primary" label={`winner A-trial ${run.winner_trial_no}`} />
                </Tooltip>
              )}
              {run && (
                <Typography variant="caption" color="text.secondary">
                  started {fmtTime(run.started_at)} · finished {fmtTime(run.finished_at)} ·{" "}
                  {run.n_candidates ?? "–"} cand × {run.grid_size ?? "–"} grid
                </Typography>
              )}
            </Stack>

            {run?.error_text && <Alert severity="error">Run failed: {run.error_text}</Alert>}

            {run && (
              <Stack direction="row" spacing={1} flexWrap="wrap">
                <Chip
                  size="small"
                  variant="outlined"
                  label={`Set A omega ${num(run.best_a_metrics?.omega, 3)} (pos months ${pct(run.best_a_metrics?.positive_month_fraction)})`}
                />
                <Chip
                  size="small"
                  variant="outlined"
                  label={`Set B calmar ${num(run.best_b_metrics?.calmar, 3)} (OOS ret ${pct(run.best_b_metrics?.total_return)}, DD ${pct(run.best_b_metrics?.max_dd_pct)})`}
                />
                {run.kelly && (
                  <Chip
                    size="small"
                    variant="outlined"
                    label={`Kelly f* ${num(run.kelly.full_kelly, 3)} → frac ${num(run.kelly.fractional_kelly, 3)} → notional ${Number(run.kelly.notional ?? 0).toLocaleString()}`}
                  />
                )}
              </Stack>
            )}

            <Box sx={{ borderBottom: 1, borderColor: "divider" }}>
              <Tabs value={tab} onChange={(_, v: number) => setTab(v)}>
                <Tab label={`Set A · Omega — signal (${setATrials.length})`} />
                <Tab label={`Set B · Calmar — execution (${setBTrials.length})`} />
              </Tabs>
            </Box>
            {tab === 0 && (
              <Stack spacing={1}>
                <Typography variant="caption" color="text.secondary">
                  Set A convergence: best loss{" "}
                  {aIncumbent.finalBest === Number.POSITIVE_INFINITY
                    ? "–"
                    : num(aIncumbent.finalBest)}
                  {aIncumbent.firstFeasible !== null
                    ? ` · first feasible (>55% pos months) at trial ${aIncumbent.firstFeasible}`
                    : " · NO trial satisfied the >55% pos-months constraint (best-under-penalty used)"}
                  {" · per-trial losses bounce by design (TPE explores); follow Best ↓"}
                </Typography>
                <TrialsTable columns={setAColumns} rows={setATrials} />
              </Stack>
            )}
            {tab === 1 && <TrialsTable columns={SET_B_COLUMNS} rows={setBTrials} />}
          </Stack>
        )}
      </DialogContent>
    </Dialog>
  );
}
