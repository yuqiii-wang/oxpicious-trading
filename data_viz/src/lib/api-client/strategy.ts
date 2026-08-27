import { fetchJson } from "./_cache";
import type {
  MaSpreadSecType,
  StrategyBacktestResponse,
  StrategyRiskResponse,
  StrategyDecision,
} from "@shared/types";

// ---------------------------------------------------------------------------
//  Strategy — singleton backtest
//  TTL-only cache (ephemeral backtest, no DB write).
//
//  Algo selection: the user picks a WEIGHT per algo (0.0-1.0, summing to 1.0).
//  Binary mode (one algo at 1.0, rest 0): strategy_name = algo name (e.g.
//  "macd"). Mixed mode (multiple non-zero weights): strategy_name =
//  "portfolio:macd*0.5" (built by portfolioName(), mirrors the Python
//  portfolio_name() in strategy/factors_and_algos/portfolio.py).
//
//  The resolved strategy_name drives the DATA load (backtest/risks
//  SQL-filter on strategy_name). The Run button passes the SERIALIZED
//  selection ("macd:0.5,bb:0.5") to Python's --algo arg, which
//  the Python _parse_algo_arg already understands.
//
//  Default: { macd: 1.0 } — binary MACD.
// ---------------------------------------------------------------------------
export type StrategyAlgo = "macd";
export const STRATEGY_ALGOS: StrategyAlgo[] = ["macd"];

/** Per-algo weight selection. Weights are 0.0-1.0 and SHOULD sum to 1.0. */
export type StrategySelection = Record<StrategyAlgo, number>;

/** Default selection: MACD-only (binary). User can mix from there. */
export const DEFAULT_STRATEGY_SELECTION: StrategySelection = {
  macd: 1.0,
};

/** Human-readable label for an algo (for the weight menu UI). */
export const ALGO_LABELS: Record<StrategyAlgo, string> = {
  macd: "MACD",
};

/** Abbreviation used in portfolio strategy_name (mirrors Python _ABBR). */
const ALGO_ABBR: Record<StrategyAlgo, string> = {
  macd: "macd",
};

/** Build the _ft{N} suffix for a fault tolerance percentage (0-20).
 *  0 → "" (no suffix); 10 → "_ft10". Mirrors Python append_ft_suffix(). */
export function ftSuffix(ft: number): string {
  if (!ft || ft <= 0) return "";
  return `_ft${Math.round(ft)}`;
}

/** Build the portfolio strategy_name from a selection (mirrors Python
 *  portfolio_name() in strategy/factors_and_algos/portfolio.py).
 *  - Binary (one algo non-zero): returns the algo name (e.g. "macd").
 *  - Mixed (multiple non-zero): returns "portfolio:macd*0.5".
 *  - All zero: returns "" (invalid — caller should guard).
 *  When ft > 0, appends _ft{N} suffix (e.g. "macd_ft10") so the FT variant
 *  is a distinct strategy in the DB. */
export function selectionToStrategyName(
  selection: StrategySelection,
  ft: number = 0,
): string {
  const active = STRATEGY_ALGOS.filter((a) => selection[a] > 0);
  if (active.length === 0) return "";
  // Binary: one algo at any non-zero weight — treat as that algo's binary run.
  // (Python normalizes a single-algo selection to weight 1.0 regardless.)
  let base: string;
  if (active.length === 1) {
    base = active[0];
  } else {
    // Mixed: build portfolio:name*weight+...
    const parts = active.map((a) => `${ALGO_ABBR[a]}*${selection[a]}`);
    base = "portfolio:" + parts.join("+");
  }
  return base + ftSuffix(ft);
}

/** Serialize a selection for the Python --algo CLI arg.
 *  Format: "macd:0.5,bb:0.5" (understood by _parse_algo_arg). */
export function serializeSelection(selection: StrategySelection): string {
  return STRATEGY_ALGOS
    .filter((a) => selection[a] > 0)
    .map((a) => `${a}:${selection[a]}`)
    .join(",");
}

/** True when exactly one algo has a non-zero weight (binary mode). */
export function isBinarySelection(selection: StrategySelection): boolean {
  return STRATEGY_ALGOS.filter((a) => selection[a] > 0).length === 1;
}

/** Sum of all weights (should be 1.0 for a valid selection). */
export function selectionSum(selection: StrategySelection): number {
  return STRATEGY_ALGOS.reduce((s, a) => s + (selection[a] || 0), 0);
}

/** Build a short label for the selection (for the menu button).
 *  "MACD 100%" or "MACD 50% + BB 50%" or "Invalid (sum=0.8)".
 *  Appends " FT{N}%" when fault tolerance is enabled. */
export function selectionLabel(
  selection: StrategySelection,
  ft: number = 0,
): string {
  const active = STRATEGY_ALGOS.filter((a) => selection[a] > 0);
  if (active.length === 0) return "No algo";
  const base = active
    .map((a) => `${ALGO_ABBR[a]} ${Math.round(selection[a] * 100)}%`)
    .join(" + ");
  return ft && ft > 0 ? `${base} · FT${Math.round(ft)}%` : base;
}

export function fetchSingletonBacktest(
  code: string,
  secType: MaSpreadSecType,
  selection: StrategySelection = DEFAULT_STRATEGY_SELECTION,
  ft: number = 0,
): Promise<StrategyBacktestResponse> {
  const strategyName = selectionToStrategyName(selection, ft);
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  if (secType) params.set("sec_type", secType);
  params.set("strategy_name", strategyName);
  const qs = params.toString();
  return fetchJson<StrategyBacktestResponse>(
    `/api/strategy/singleton/backtest${qs ? `?${qs}` : ""}`,
  );
}

export function fetchSingletonRisks(
  code: string,
  secType: MaSpreadSecType,
  selection: StrategySelection = DEFAULT_STRATEGY_SELECTION,
  ft: number = 0,
): Promise<StrategyRiskResponse> {
  const strategyName = selectionToStrategyName(selection, ft);
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  if (secType) params.set("sec_type", secType);
  params.set("strategy_name", strategyName);
  const qs = params.toString();
  return fetchJson<StrategyRiskResponse>(
    `/api/strategy/singleton/risks${qs ? `?${qs}` : ""}`,
  );
}

/** Result of POST /api/strategy/singleton/run. */
export interface RunStrategyResult {
  success: boolean;
  stdout: string;
  stderr: string;
  exitCode: number;
  /** True when NO process was spawned because one with the SAME
   *  process-id-tag is already running (dedupe path). */
  already_running?: boolean;
  /** The process-id-tag the run was registered under. */
  process_id_tag?: string;
}

/** Build the process-id-tag for a Run Strategy invocation. Must match the
 *  server-side default (singleton-run:<sec_type>:<code>:<algo>:<ft>) so
 *  UI-passed and server-default tags dedupe identically. */
export function singletonRunTag(
  code: string,
  secType: MaSpreadSecType,
  selection: StrategySelection = DEFAULT_STRATEGY_SELECTION,
  ft: number = 0,
): string {
  return `singleton-run:${secType}:${code}:${serializeSelection(selection)}:${ft}`;
}

/** Build the process-id-tag for a Train Model invocation (must match the
 *  server-side default singleton-train:<sec_type>:<code>:<algo>). */
export function singletonTrainTag(
  code: string,
  secType: MaSpreadSecType,
  selection: StrategySelection = DEFAULT_STRATEGY_SELECTION,
): string {
  const algoName = serializeSelection(selection).split(",")[0].split(":")[0].trim() ||
    STRATEGY_ALGOS[0];
  return `singleton-train:${secType}:${code}:${algoName}`;
}

/** Running-state of strategy process tags — polled on mount and while a
 *  REMOTE process runs, so a page refresh puts the Run/Train buttons back
 *  into their spinning state and reloads data when the process exits. */
export async function fetchStrategyProcessStatus(
  tags: ReadonlyArray<string>,
): Promise<Record<string, boolean>> {
  const params = new URLSearchParams();
  if (tags.length) params.set("process_id_tag", tags.join(","));
  const res = await fetch(
    `/api/strategy/singleton/process-status?${params.toString()}`,
  );
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}: ${await res.text()}`);
  }
  const json = (await res.json()) as { status: Record<string, boolean> };
  return json.status ?? {};
}

/** Result of GET /api/strategy/singleton/check. */
export interface CheckExistingResult {
  exists: boolean;
  seq_id?: number;
  seq_no?: number;
  start_date?: string;
  end_date?: string | null;
  scenario?: string | null;
  status?: string;
  fault_tolerance?: number | null;
}

/**
 * Check if a strategy_identity row already exists for the current
 * (code, secType, selection, ft). Returns the existing row metadata if
 * found, or { exists: false } if no run exists yet.
 */
export async function checkExistingStrategy(
  code: string,
  secType: MaSpreadSecType,
  selection: StrategySelection = DEFAULT_STRATEGY_SELECTION,
  ft: number = 0,
): Promise<CheckExistingResult> {
  const strategyName = selectionToStrategyName(selection, ft);
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  if (secType) params.set("sec_type", secType);
  params.set("strategy_name", strategyName);
  const qs = params.toString();
  const res = await fetch(
    `/api/strategy/singleton/check${qs ? `?${qs}` : ""}`,
  );
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}: ${await res.text()}`);
  }
  return (await res.json()) as CheckExistingResult;
}

/**
 * Run the singleton backtest + risk computation for one (code, secType) by
 * spawning the Python scripts via the backend. Returns when both processes
 * exit. NOT cached (always a fresh POST).
 *
 * The selection is serialized as "macd:0.5,bb:0.5" and passed
 * to Python's --algo arg (which _parse_algo_arg understands).
 *
 * When `force` is true (default), passes --force to Python so existing
 * rows are deleted and replaced. When false, the Python script skips
 * already-existing runs.
 */
export async function runSingletonStrategy(
  code: string,
  secType: MaSpreadSecType,
  selection: StrategySelection = DEFAULT_STRATEGY_SELECTION,
  ft: number = 0,
  force: boolean = true,
  processIdTag?: string,
): Promise<RunStrategyResult> {
  const serialized = serializeSelection(selection);
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  if (secType) params.set("sec_type", secType);
  params.set("algo", serialized);
  if (ft && ft > 0) params.set("fault_tolerance", String(ft));
  if (!force) params.set("force", "false");
  if (processIdTag) params.set("process_id_tag", processIdTag);
  const qs = params.toString();
  const res = await fetch(
    `/api/strategy/singleton/run${qs ? `?${qs}` : ""}`,
    { method: "POST" },
  );
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`HTTP ${res.status}: ${text}`);
  }
  return (await res.json()) as RunStrategyResult;
}

/**
 * Train Model — run the Optuna param optimizer
 * (`python -m strategy.factors_and_algos._optm_engine`) for one
 * (code, secType) via the backend. The study minimizes
 * 0.8·(−pnl) + 0.2·risk over the algo's tunable space + the common
 * trading space, then upserts the best params into strategy.algo_configs
 * (the NEXT Run Strategy uses them automatically). Training writes no
 * backtest rows, so there is no PK conflict path.
 *
 * Training is binary-mode: the FIRST algo of the selection is trained.
 */
export async function trainStrategyModel(
  code: string,
  secType: MaSpreadSecType,
  selection: StrategySelection = DEFAULT_STRATEGY_SELECTION,
  trials: number = 50,
  processIdTag?: string,
): Promise<RunStrategyResult> {
  const serialized = serializeSelection(selection);
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  if (secType) params.set("sec_type", secType);
  params.set("algo", serialized);
  params.set("trials", String(trials));
  if (processIdTag) params.set("process_id_tag", processIdTag);
  const qs = params.toString();
  const res = await fetch(
    `/api/strategy/singleton/train${qs ? `?${qs}` : ""}`,
    { method: "POST" },
  );
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`HTTP ${res.status}: ${text}`);
  }
  return (await res.json()) as RunStrategyResult;
}

// ---------------------------------------------------------------------------
//  Train Model history (GET /api/strategy/singleton/train-info)
// ---------------------------------------------------------------------------

/** One algo_configs row: default (is_default) or trained. */
export interface TrainConfigRow {
  start_date: string | null;
  end_date: string | null;
  params: Record<string, unknown>;
  updated_at: string | null;
}

/** One training_runs header (a Train Model invocation). */
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

/** The two regime loss types — displayed SEPARATELY in the logs UI. */
export type TrainLossType = "set_a_omega" | "set_b_calmar";

/** One evaluated point (Stage A TPE trial or Stage B grid point). */
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
  trials: TrainTrialRow[];
  trials_run_id: number | null;
}

/**
 * Fetch the Train Model info bundle from the DB: the algo's default
 * config row, the trained config rows, the training run headers, and
 * the per-point trial log (both loss types) of the latest run — or of
 * `runId` when given (the UI run selector re-fetches on change).
 *
 * NOT cached (plain fetch) — must be fresh right after a training run.
 */
export async function fetchTrainInfo(
  code: string,
  secType: MaSpreadSecType,
  selection: StrategySelection = DEFAULT_STRATEGY_SELECTION,
  ft: number = 0,
  runId: number | null = null,
): Promise<TrainInfoResponse> {
  const strategyName = selectionToStrategyName(selection, ft);
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  if (secType) params.set("sec_type", secType);
  params.set("strategy_name", strategyName);
  if (runId !== null) params.set("run_id", String(runId));
  const qs = params.toString();
  const res = await fetch(
    `/api/strategy/singleton/train-info${qs ? `?${qs}` : ""}`,
  );
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}: ${await res.text()}`);
  }
  return (await res.json()) as TrainInfoResponse;
}