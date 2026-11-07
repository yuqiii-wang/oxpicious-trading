/**
 * Train Model history — reads the training-process tables written by
 * `python -m strategy.factors_and_algos._optm_engine` (training_store.py):
 *
 *   - strategy.algo_configs      the DEFAULT row (is_default, algo
 *     DEFAULT_PARAMS, reserved wide range) + the TRAINED rows
 *     ([train_date, 9999-12-31], written by each study).
 *   - strategy.training_runs     one header row per Train Model click
 *     (status running/completed/failed + step-5 winner outcome).
 *   - strategy.training_trials   per-point log for BOTH loss types
 *     ('set_a_omega' = Stage A signal TPE trials, 'set_b_calmar' =
 *     Stage B execution grid points) — the UI displays them separately.
 *
 * Trials are returned for the LATEST run only (keeps the payload sane;
 * the UI run selector re-fetches when the user picks an older run via
 * ?run_id=).
 */
import { querySqlView } from "@api/lib/sql-views";
import type { MaSpreadSecType } from "../../../shared/types.js";
import { DEFAULT_STRATEGY_NAME } from "./_shared.js";

// ---------------------------------------------------------------------------
//  Response types (shared with the frontend api-client)
// ---------------------------------------------------------------------------
export interface TrainConfigRow {
  start_date: string | null;
  end_date: string | null;
  params: Record<string, unknown>;
  updated_at: string | null;
}

export interface TrainRunRow {
  run_id: number;
  status: string;
  trials: number;
  top_k: number;
  seed: number | null;
  oos_frac: number;
  started_at: string | null;
  finished_at: string | null;
  winner_trial_no: number | null;
  n_candidates: number | null;
  grid_size: number | null;
  best_params: Record<string, unknown> | null;
  best_a_metrics: Record<string, unknown> | null;
  best_b_metrics: Record<string, unknown> | null;
  kelly: Record<string, unknown> | null;
  error_text: string | null;
}

export type TrainLossType = "set_a_omega" | "set_b_calmar";

export interface TrainTrialRow {
  run_id: number;
  loss_type: TrainLossType;
  trial_no: number;
  grid_idx: number;
  params: Record<string, unknown>;
  metrics: Record<string, unknown>;
  loss: number;
  constraint_ok: boolean;
  no_trades: boolean;
}

export interface TrainInfoResponse {
  default: TrainConfigRow | null;
  trained: TrainConfigRow[];
  runs: TrainRunRow[];
  /** Trials of the latest run (or the ?run_id-selected run). */
  trials: TrainTrialRow[];
  trials_run_id: number | null;
}

// ---------------------------------------------------------------------------
//  SQL — the SELECTs live in the sql views
//  data_viz/api/services/strategy/train-history/{default_config,
//  trained_configs, runs, trials}
// ---------------------------------------------------------------------------
//  Helpers
// ---------------------------------------------------------------------------
// JSONB cells arrive as JSON strings (no pg codec registered in this app).
function parseJsonObj(v: unknown): Record<string, unknown> | null {
  if (v === null || v === undefined) return null;
  const parsed = typeof v === "string" ? JSON.parse(v) : v;
  return parsed && typeof parsed === "object" ? (parsed as Record<string, unknown>) : null;
}

// Defensive date formatting: the wide-range rows carry PG date 'infinity'
// (asyncpg maps Python date(9999,12,31) <-> PG infinity), which node-pg
// hands back as the string 'infinity' / Number Infinity — new Date() on
// either would throw or yield Invalid Date.
function fmtDate(v: unknown): string | null {
  if (v === null || v === undefined) return null;
  if (typeof v === "number") {
    return Number.isFinite(v) ? new Date(v).toISOString().slice(0, 10) : "∞";
  }
  const s = String(v);
  if (s === "infinity" || s === "Infinity") return "∞";
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? s : d.toISOString().slice(0, 10);
}

function fmtTs(v: unknown): string | null {
  if (v === null || v === undefined) return null;
  const d = v instanceof Date ? v : new Date(String(v));
  return Number.isNaN(d.getTime()) ? String(v) : d.toISOString();
}

interface RawConfigRow {
  start_date: string | Date;
  end_date: string | Date;
  params: unknown;
  updated_at: string | Date | null;
}

function mapConfig(r: RawConfigRow): TrainConfigRow {
  return {
    start_date: fmtDate(r.start_date),
    end_date: fmtDate(r.end_date),
    params: parseJsonObj(r.params) ?? {},
    updated_at: fmtTs(r.updated_at),
  };
}

// ---------------------------------------------------------------------------
//  Service
// ---------------------------------------------------------------------------
export async function fetchTrainInfo(
  rawCode: string,
  rawSecType: string | undefined | null,
  strategyName: string = DEFAULT_STRATEGY_NAME,
  runId?: number | null,
): Promise<TrainInfoResponse> {
  const secType = (rawSecType as MaSpreadSecType) ?? "index";
  const code = rawCode.trim();
  const configArgs = {
    sec_type: secType,
    sec_code: code,
    strategy_name: strategyName,
  };

  const [defaultRows, trainedRows, runRows] = await Promise.all([
    querySqlView<RawConfigRow>(
      "data_viz/api/services/strategy/train-history/default_config",
      configArgs,
    ),
    querySqlView<RawConfigRow>(
      "data_viz/api/services/strategy/train-history/trained_configs",
      configArgs,
    ),
    querySqlView<Record<string, unknown>>(
      "data_viz/api/services/strategy/train-history/runs",
      configArgs,
    ),
  ]);

  const runs: TrainRunRow[] = runRows.map((r) => ({
    run_id: Number(r.run_id),
    status: String(r.status),
    trials: Number(r.trials),
    top_k: Number(r.top_k),
    seed: r.seed === null || r.seed === undefined ? null : Number(r.seed),
    oos_frac: Number(r.oos_frac),
    started_at: fmtTs(r.started_at),
    finished_at: fmtTs(r.finished_at),
    winner_trial_no: r.winner_trial_no === null || r.winner_trial_no === undefined
      ? null : Number(r.winner_trial_no),
    n_candidates: r.n_candidates === null || r.n_candidates === undefined
      ? null : Number(r.n_candidates),
    grid_size: r.grid_size === null || r.grid_size === undefined
      ? null : Number(r.grid_size),
    best_params: parseJsonObj(r.best_params),
    best_a_metrics: parseJsonObj(r.best_a_metrics),
    best_b_metrics: parseJsonObj(r.best_b_metrics),
    kelly: parseJsonObj(r.kelly),
    error_text: r.error_text === null || r.error_text === undefined
      ? null : String(r.error_text),
  }));

  // Trials: for the ?run_id-selected run, else the latest run.
  const targetRunId = runId && Number.isFinite(runId)
    ? Number(runId)
    : (runs.length > 0 ? runs[0].run_id : null);

  const trialRows = targetRunId !== null
    ? await querySqlView<Record<string, unknown>>(
        "data_viz/api/services/strategy/train-history/trials",
        { run_id: targetRunId },
      )
    : [];

  const trials: TrainTrialRow[] = trialRows.map((r) => ({
    run_id: Number(r.run_id),
    loss_type: r.loss_type as TrainLossType,
    trial_no: Number(r.trial_no),
    grid_idx: Number(r.grid_idx),
    params: parseJsonObj(r.params) ?? {},
    metrics: parseJsonObj(r.metrics) ?? {},
    loss: Number(r.loss),
    constraint_ok: Boolean(r.constraint_ok),
    no_trades: Boolean(r.no_trades),
  }));

  return {
    default: defaultRows.length > 0 ? mapConfig(defaultRows[0]) : null,
    trained: trainedRows.map(mapConfig),
    runs,
    trials,
    trials_run_id: targetRunId,
  };
}
