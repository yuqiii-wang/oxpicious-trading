/**
 * TrainLogsDialog — "Training Logs" view of the Train Model split-button
 * dropdown.
 *
 * Shows one training run's process from strategy.training_runs +
 * strategy.training_trials (written by training_store.py):
 *   - Run selector (latest 10, labeled by what ACTUALLY ran — TPE
 *     trials for binary, comb cells + set-B points for mixed) + status
 *     + step-5 winner summary.
 *   - The regime losses displayed SEPARATELY (tabs, shown when the run
 *     produced them):
 *       Set A · Omega   — BINARY signal params TPE trials (Optuna trial no).
 *       Set C · Calmar  — MIXED comb-grid cells (weights mode × gate ×
 *                         vol-state × reject × no-action twins ×
 *                         threshold; cell # = the grid index). The row
 *                         label lists ONLY the axes that vary within the
 *                         run — the reject/no-action twin cells used to
 *                         render identical labels (the whole table read
 *                         like duplicates).
 *       Set B · Calmar  — execution grid points per top-K candidate
 *                         (cand # = the candidate's Stage A/C trial no).
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
  type TrainRunRow,
  type TrainTrialRow,
} from "@/lib/api-client";
import { fmtStamp } from "@/shared/datetime";
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
  // Display-only remap — the date part renders as yyyy-Mmm-dd via the
  // shared helper, the time-of-day remainder is kept verbatim.
  return iso ? fmtStamp(iso.slice(0, 19)) : "–";
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
  { key: "cand", label: "Cand (stage-1)", render: (r) => String(r.trial_no) },
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

/** One Set C comb cell's structural axes, extracted from its trial
 *  params (works on the merged winner `best_params` too). `rej`/`na`
 *  are null when the axis is absent from the params (older runs). */
interface CombAxes {
  mode: string;
  gate: string;
  gateEnabled: boolean;
  u3: boolean;
  rej: boolean | null;
  na: boolean | null;
  thr: number | undefined;
}

/** A Set C cell is a trial row's params — or the merged winner
 *  `best_params` (same shape: `comb` block + `conf_threshold`). */
type CombCellLike = { params: Record<string, unknown> };

function combAxes(cell: CombCellLike): CombAxes {
  const comb = cell.params?.comb as Record<string, Record<string, unknown>> | undefined;
  const gateOn = Boolean(comb?.regime_gate?.enabled);
  return {
    mode: String(comb?.weights?.mode ?? "?"),
    gate: gateOn ? `w${String(comb?.regime_gate?.window ?? "?")}` : "off",
    gateEnabled: gateOn,
    u3: Boolean(comb?.vol_state?.enabled),
    rej: comb?.reject ? Boolean(comb.reject.enabled) : null,
    na: comb?.no_action ? Boolean(comb.no_action.enabled) : null,
    thr: typeof cell.params?.conf_threshold === "number"
      ? (cell.params.conf_threshold as number)
      : undefined,
  };
}

/**
 * Per-run Set C rule labeler — the label carries ONLY the axes that
 * actually VARY across the run's cells (plus mode and threshold): a
 * fixed axis would repeat on every row and HIDE what distinguishes two
 * rows from each other. The reject / no-action twin cells (a 180-cell
 * grid when no-action marks are present) used to render identical
 * "Comb rules" for both twins — every label appeared twice and the
 * table read like duplicated rows. A constant-but-ON boolean axis
 * stays visible (info beats brevity).
 */
function buildCombLabeler(trials: TrainTrialRow[]): (r: CombCellLike) => string {
  const axes = trials.map(combAxes);
  const varies = (
    pick: (a: CombAxes) => string | boolean | number | null | undefined,
  ) => new Set(axes.map(pick)).size > 1;
  const gateV = varies((a) => a.gate);
  const u3V = varies((a) => a.u3);
  const rejV = varies((a) => a.rej);
  const naV = varies((a) => a.na);
  return (r: CombCellLike): string => {
    const a = combAxes(r);
    const parts: string[] = [a.mode];
    if (gateV || a.gateEnabled) parts.push(`gate ${a.gate}`);
    if (u3V || a.u3) parts.push(a.u3 ? "u3 on" : "u3 off");
    if (a.rej !== null && (rejV || a.rej)) parts.push(a.rej ? "rej on" : "rej off");
    if (a.na !== null && (naV || a.na)) parts.push(a.na ? "na on" : "na off");
    parts.push(`thr ${a.thr ?? "?"}`);
    return parts.join(" · ");
  };
}

/** Set C columns — the mixed-mode comb grid (calmar on IS + risk tiebreak).
 *  `label` is the run's varying-axes comb labeler; the Step-5 winner cell
 *  gets an explicit "← winner" marker so it is identifiable among its
 *  twin rows (same label, different no-action/reject axis). */
function buildSetCColumns(
  incumbent: Map<string, IncumbentMeta>,
  label: (r: TrainTrialRow) => string,
  winnerTrialNo: number | null,
): TrialColumn[] {
  const bestOf = (r: TrainTrialRow): IncumbentMeta =>
    incumbent.get(`${r.trial_no}-${r.grid_idx}`) ?? { best: r.loss, isBest: false };
  return [
    { key: "cell", label: "Cell", render: (r) => String(r.trial_no) },
    {
      key: "rules", label: "Comb rules",
      title: "The cell's comb rule — only the axes that VARY within this run are listed (fixed axes would repeat identically on every row). The Step-5 winner cell is marked.",
      render: (r) =>
        label(r) + (r.trial_no === winnerTrialNo ? "  ← winner" : ""),
      bold: (r) => r.trial_no === winnerTrialNo,
    },
    {
      key: "loss", label: "Loss", align: "right",
      title: "Feasible cell → −Calmar (graded on IS, never omega).  DD breach (>25%) → penalty band.  No trades → 1000.  Tiebreak: official risk score.",
      render: (r) => num(r.loss),
    },
    {
      key: "best", label: "Best ↓", align: "right",
      title: "Running best (incumbent) loss across the exhaustive grid scan. ★ = this cell improved it.",
      render: (r) => (bestOf(r).isBest ? `★ ${num(bestOf(r).best)}` : num(bestOf(r).best)),
      bold: (r) => bestOf(r).isBest,
    },
    { key: "calmar", label: "Calmar", align: "right", render: (r) => num(r.metrics.calmar, 3) },
    { key: "dd", label: "Max DD", align: "right", render: (r) => pct(r.metrics.max_dd_pct) },
    { key: "ret", label: "IS Ret", align: "right", render: (r) => pct(r.metrics.total_return) },
    { key: "risk", label: "Risk", align: "right", render: (r) => num(r.metrics.risk_score, 2) },
    { key: "trades", label: "Trades", align: "right", render: (r) => String(r.metrics.n_decisions ?? "–") },
    {
      key: "ok", label: "Constraint",
      render: (r) => (r.no_trades ? "no trades" : r.constraint_ok ? "ok (DD ≤ 25%)" : "violated"),
    },
  ];
}

/** Run-selector label — what the run ACTUALLY executed. The header's
 *  `trials` column is the --trials CLI arg, meaningless for mixed runs
 *  (Set C replaces the TPE study with the exhaustive comb grid). */
function runCountsLabel(r: TrainRunRow): string {
  if (r.n_set_c > 0) return `${r.n_set_c} cells · ${r.n_set_b} set-B pts`;
  if (r.n_set_a > 0) return `${r.n_set_a} trials`;
  return `${r.trials} trials`;
}

export function TrainLogsDialog(props: TrainLogsDialogProps) {
  const { open, onClose, code, secType, strategyName } = props;
  const [data, setData] = useState<TrainInfoResponse | null>(null);
  const [runId, setRunId] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<string>("stage1");

  // The dialog component stays MOUNTED while closed (only its Paper
  // hides) — clear the per-run selections on close so reopening for a
  // different strategy_name never shows the previous run's trials.
  useEffect(() => {
    if (!open) {
      setRunId(null);
      setTab("stage1");
    }
  }, [open]);

  // A scope change while mounted (code / strategy_name) invalidates the
  // selected run_id too — a stale id from the previous scope makes the
  // trials fetch return THAT run's rows (the trials view keys on run_id
  // alone), so the new strategy's dialog renders another strategy's log
  // with an empty run selector.
  useEffect(() => {
    setRunId(null);
    setTab("stage1");
  }, [code, secType, strategyName]);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchTrainInfo(code, secType, strategyName, runId)
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
  const setCTrials = useMemo(
    () => (data?.trials.filter((t) => t.loss_type === "set_c_calmar") ?? [])
      .slice()
      .sort((a, b) => a.trial_no - b.trial_no || a.grid_idx - b.grid_idx),
    [data],
  );
  const setBTrials = useMemo(
    () => data?.trials.filter((t) => t.loss_type === "set_b_calmar") ?? [],
    [data],
  );

  /** Running-best (incumbent) map + convergence summary for a trial list
   *  — makes TPE convergence (Set A) / grid-scan progress (Set C) visible
   *  against the bouncing raw losses. */
  const buildIncumbent = (trials: TrainTrialRow[]) => {
    const map = new Map<string, IncumbentMeta>();
    let best = Number.POSITIVE_INFINITY;
    let firstFeasible: number | null = null;
    for (const t of trials) {
      const isBest = t.loss < best;
      if (isBest) best = t.loss;
      if (firstFeasible === null && t.constraint_ok && !t.no_trades) {
        firstFeasible = t.trial_no;
      }
      map.set(`${t.trial_no}-${t.grid_idx}`, { best, isBest });
    }
    return { map, finalBest: best, firstFeasible };
  };
  const aIncumbent = useMemo(() => buildIncumbent(setATrials), [setATrials]);
  const cIncumbent = useMemo(() => buildIncumbent(setCTrials), [setCTrials]);
  /** This run's varying-axes comb labeler (Set C rows + the winner chip). */
  const setCLabel = useMemo(() => buildCombLabeler(setCTrials), [setCTrials]);
  const setAColumns = useMemo(
    () => buildSetAColumns(aIncumbent.map),
    [aIncumbent],
  );
  const setCColumns = useMemo(
    () => buildSetCColumns(cIncumbent.map, setCLabel, run?.winner_trial_no ?? null),
    [cIncumbent, setCLabel, run?.winner_trial_no],
  );
  /** The Step-5 winner's own comb rule (mixed runs) — from the run's
   *  merged best_params; null for binary runs (no comb block). */
  const winnerCombLabel = useMemo(() => {
    if (!run?.best_params || typeof run.best_params.comb !== "object") return null;
    return setCLabel({ params: run.best_params });
  }, [run, setCLabel]);

  /** Keep the selected tab on an AVAILABLE one (binary runs have no Set C
   *  tab, mixed runs have no Set A tab — snap on data change). Skipped
   *  while loading: the empty pre-fetch state would snap to Set B before
   *  the actual loss types are known. */
  useEffect(() => {
    if (loading || !data) return;
    const available = [
      ...(setATrials.length > 0 ? ["a"] : []),
      ...(setCTrials.length > 0 ? ["c"] : []),
      "b",
    ];
    if (!available.includes(tab)) setTab(available[0]);
  }, [loading, data, setATrials.length, setCTrials.length, tab]);

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
                    #{r.run_id} · {fmtTime(r.started_at)} · {runCountsLabel(r)} · {r.status}
                  </MenuItem>
                ))}
              </TextField>
              {run && <Chip size="small" color={statusColor(run.status)} label={run.status} />}
              {run?.winner_trial_no !== null && run?.winner_trial_no !== undefined && (
                <Tooltip
                  title={`Step 5 winner: the (Set A/C, Set B) combo with the best Calmar under the DD cap${winnerCombLabel ? ` — winner cell: ${winnerCombLabel}` : ""}`}
                >
                  <Chip
                    size="small"
                    color="primary"
                    label={winnerCombLabel
                      ? `winner cell #${run.winner_trial_no}`
                      : `winner stage-1 #${run.winner_trial_no}`}
                  />
                </Tooltip>
              )}
              {run && (
                <Typography variant="caption" color="text.secondary">
                  started {fmtTime(run.started_at)} · finished {fmtTime(run.finished_at)} ·{" "}
                  {run.n_candidates ?? "–"} cand × {run.grid_size ?? "–"} set-B pts
                </Typography>
              )}
            </Stack>

            {run?.error_text && <Alert severity="error">Run failed: {run.error_text}</Alert>}

            {run && (
              <Stack direction="row" spacing={1} flexWrap="wrap">
                {setATrials.length > 0 && (
                  <Chip
                    size="small"
                    variant="outlined"
                    label={`Set A omega ${num(run.best_a_metrics?.omega, 3)} (pos months ${pct(run.best_a_metrics?.positive_month_fraction)})`}
                  />
                )}
                {setCTrials.length > 0 && (
                  <Chip
                    size="small"
                    variant="outlined"
                    label={`Set C calmar ${num(run.best_a_metrics?.calmar, 3)} (IS ret ${pct(run.best_a_metrics?.total_return)}, DD ${pct(run.best_a_metrics?.max_dd_pct)})`}
                  />
                )}
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
              <Tabs value={tab} onChange={(_, v: string) => setTab(v)}>
                {setATrials.length > 0 && (
                  <Tab value="a" label={`Set A · Omega — signal (${setATrials.length})`} />
                )}
                {setCTrials.length > 0 && (
                  <Tab value="c" label={`Set C · Calmar — comb grid (${setCTrials.length})`} />
                )}
                <Tab value="b" label={`Set B · Calmar — execution (${setBTrials.length})`} />
              </Tabs>
            </Box>
            {tab === "a" && setATrials.length > 0 && (
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
                <TrialsTable key={`a-${data.trials_run_id}`} columns={setAColumns} rows={setATrials} />
              </Stack>
            )}
            {tab === "c" && setCTrials.length > 0 && (
              <Stack spacing={1}>
                <Typography variant="caption" color="text.secondary">
                  Set C comb grid: best loss{" "}
                  {cIncumbent.finalBest === Number.POSITIVE_INFINITY
                    ? "–"
                    : num(cIncumbent.finalBest)}
                  {" · exhaustive curated cells (weights mode × regime gate × vol-state × reject × no-action twins × threshold — each row lists only the axes that vary in this run), graded by Calmar on IS with the official risk score as tiebreak"}
                </Typography>
                <TrialsTable key={`c-${data.trials_run_id}`} columns={setCColumns} rows={setCTrials} />
              </Stack>
            )}
            {(tab === "b" || (tab === "stage1" && setATrials.length === 0 && setCTrials.length === 0)) && (
              <TrialsTable key={`b-${data.trials_run_id}`} columns={SET_B_COLUMNS} rows={setBTrials} />
            )}
          </Stack>
        )}
      </DialogContent>
    </Dialog>
  );
}
