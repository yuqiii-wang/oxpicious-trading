import { fetchJson } from "./_cache";
import { HttpError } from "./_retry";
import type {
  MaSpreadSecType,
  RunHistoryResponse,
  StrategyBacktestResponse,
  StrategyRiskResponse,
} from "@shared/types";

// Re-exported for the api-client barrel (the run-history selector's types).
export type { RunHistoryResponse } from "@shared/types";
import type { RunHistoryItem } from "@shared/types";
export type { RunHistoryItem };

// ---------------------------------------------------------------------------
//  Strategy — singleton backtest
//  TTL-only cache (ephemeral backtest, no DB write).
//
//  Signal selection (signals-picked-up era, 2026-11-07): the user picks a
//  SET of analysis signal families as TAGS — NO weights (weighting was
//  removed, user-directed). The families are DATA-DRIVEN from the
//  analysis tier (analysis_signals.history_signals events; see
//  fetchSignalFamilies).
//
//  - Single family: strategy_name = the family name (e.g. "macd").
//  - Multiple families: strategy_name = "portfolio:macd+mov_rsi" (equal
//    blend; built by selectionToStrategyName, mirrors the Python
//    portfolio_name in strategy/factors_and_algos/portfolio.py).
//
//  The resolved strategy_name drives the DATA load (backtest/risks
//  SQL-filter on strategy_name). The Run button passes the SERIALIZED
//  selection ("macd,mov_rsi") to Python's --algo arg.
//
//  Default: ["macd"].
// ---------------------------------------------------------------------------

/** The signal-family selection: family names, NO weights. */
export type StrategySelection = string[];

/** Default selection: MACD only. User can add families from there. */
export const DEFAULT_STRATEGY_SELECTION: StrategySelection = ["macd"];

/** Fault tolerance pinned to 10% (2026-11-17, user-directed) — mirrors
 *  DEFAULT_FAULT_TOLERANCE in strategy/_trading/constants.py and the API
 *  service default. No UI control varies it. */
export const DEFAULT_FAULT_TOLERANCE = 10;

/** Build the _ft{N} suffix for a fault tolerance percentage (0-20).
 *  0 → "" (no suffix); 10 → "_ft10". Mirrors Python append_ft_suffix(). */
export function ftSuffix(ft: number): string {
  if (!ft || ft <= 0) return "";
  return `_ft${Math.round(ft)}`;
}

/** Build the strategy_name from a family selection (mirrors Python
 *  portfolio_name() in strategy/factors_and_algos/portfolio.py).
 *  - Single family: returns the family name (e.g. "macd").
 *  - Multiple families: returns "portfolio:macd+mov_rsi" (equal blend,
 *    names sorted for stability — no weights).
 *  - Empty: returns "" (invalid — caller should guard).
 *  When ft > 0, appends _ft{N} suffix (e.g. "macd_ft10") so the FT variant
 *  is a distinct strategy in the DB. */
export function selectionToStrategyName(
  selection: StrategySelection,
  ft: number = 0,
): string {
  const active = [...new Set(selection)].sort();
  if (active.length === 0) return "";
  const base = active.length === 1 ? active[0] : `portfolio:${active.join("+")}`;
  return base + ftSuffix(ft);
}

/** Serialize a selection for the Python --algo CLI arg.
 *  Format: "macd,mov_rsi" (family names only — no weights). */
export function serializeSelection(selection: StrategySelection): string {
  return [...new Set(selection)].sort().join(",");
}

/** Build a short label for the selection (for running alerts).
 *  "macd" or "macd + mov_rsi". Appends " FT{N}%" when FT is enabled. */
export function selectionLabel(
  selection: StrategySelection,
  ft: number = 0,
): string {
  const active = [...new Set(selection)].sort();
  if (active.length === 0) return "No signal family";
  const base = active.join(" + ");
  return ft && ft > 0 ? `${base} · FT${Math.round(ft)}%` : base;
}

// ---------------------------------------------------------------------------
//  Signal family tags (GET /api/strategy/singleton/signal-families)
// ---------------------------------------------------------------------------

/** One selectable signal family: name + evidence footprint. */
export interface SignalFamilyInfo {
  /** The family label — also the Python algo name + single-run
   *  strategy_name (e.g. "macd", "mov_rsi"). */
  name: string;
  /** Recorded standalone-tier events for this sec_type. */
  n_events: number;
  /** Distinct codes with events. */
  n_codes: number;
  /** Newest event date (the data horizon). */
  latest_date: string | null;
}

/** Fetch the selectable signal families for a sec_type — the analysis
 *  families with recorded standalone-tier events (the tag menu options;
 *  data-driven from the analysis signals tier). */
export function fetchSignalFamilies(
  secType: MaSpreadSecType,
): Promise<SignalFamilyInfo[]> {
  return fetchJson<{ sec_type: string; families: SignalFamilyInfo[] }>(
    `/api/strategy/singleton/signal-families?sec_type=${secType}`,
  ).then((j) => j.families ?? []);
}

export function fetchSingletonBacktest(
  code: string,
  secType: MaSpreadSecType,
  selection: StrategySelection = DEFAULT_STRATEGY_SELECTION,
  ft: number = 0,
  /** When given, load THIS run (the run-history selector) instead of the
   *  latest/active one. */
  seqId: number | null = null,
): Promise<StrategyBacktestResponse> {
  const strategyName = selectionToStrategyName(selection, ft);
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  if (secType) params.set("sec_type", secType);
  params.set("strategy_name", strategyName);
  if (seqId != null) params.set("seq_id", String(seqId));
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
  /** When given, load THIS run's risks (the run-history selector). */
  seqId: number | null = null,
): Promise<StrategyRiskResponse> {
  const strategyName = selectionToStrategyName(selection, ft);
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  if (secType) params.set("sec_type", secType);
  params.set("strategy_name", strategyName);
  if (seqId != null) params.set("seq_id", String(seqId));
  const qs = params.toString();
  return fetchJson<StrategyRiskResponse>(
    `/api/strategy/singleton/risks${qs ? `?${qs}` : ""}`,
  );
}

/** Fetch the run history for a selection — recent strategy_identity runs
 *  (newest first), each with its stored params config. Feeds the run
 *  selector; pair a picked run's seq_id with fetchSingletonBacktest /
 *  fetchSingletonRisks to display it. */
export function fetchSingletonRunHistory(
  code: string,
  secType: MaSpreadSecType,
  selection: StrategySelection = DEFAULT_STRATEGY_SELECTION,
  ft: number = 0,
): Promise<RunHistoryResponse> {
  const strategyName = selectionToStrategyName(selection, ft);
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  if (secType) params.set("sec_type", secType);
  params.set("strategy_name", strategyName);
  const qs = params.toString();
  return fetchJson<RunHistoryResponse>(
    `/api/strategy/singleton/run-history${qs ? `?${qs}` : ""}`,
  );
}

/** Result of POST /api/strategy/singleton/run|train — the SUBMIT receipt
 *  (2026-11-18 async submit; the request resolves at the cluster accept,
 *  not when the process exits). The job's outcome travels through
 *  fetchStrategyProcessStatus / fetchStrategyProcessResult under the
 *  same process-id-tag. */
export interface RunStrategyResult {
  /** True when the cluster accepted the job (it may still fail later). */
  accepted: boolean;
  /** True when NO process was spawned because one with the SAME
   *  process-id-tag is already running (dedupe path). */
  already_running: boolean;
  /** The spawned child's pid (null while unknown / deduped). */
  pid?: number | null;
  /** The process-id-tag the run was registered under. */
  process_id_tag?: string;
  /** The verbatim failure reason when accepted is false (cluster
   *  rejection text or the unreachable-area operator message). */
  error?: string;
}

/** Build the process-id-tag for a Run Strategy invocation. Must match the
 *  server-side default (singleton-run:<sec_type>:<code>:<algo>:<ft>) so
 *  UI-passed and server-default tags dedupe identically.
 *  The selection joins with "+" (NOT the comma serialization): the
 *  cluster's tag charset forbids commas (jobs.py TAG_RE) and the
 *  process-status query splits its tag list ON commas — a comma tag
 *  would be rejected by the cluster AND self-fragment in the status
 *  poll. py-runner's clusterSafeTag converges the server-side default
 *  tag to this same string. */
export function singletonRunTag(
  code: string,
  secType: MaSpreadSecType,
  selection: StrategySelection = DEFAULT_STRATEGY_SELECTION,
  ft: number = 0,
): string {
  return `singleton-run:${secType}:${code}:${[...new Set(selection)].sort().join("+")}:${ft}`;
}

/** Build the process-id-tag for a Train Model invocation (must match the
 *  server-side default singleton-train:<sec_type>:<code>:<algo>). The
 *  selection joins with "+" (clusterSafeTag converts the server-side
 *  comma serialization to the same form — the cluster tag charset
 *  forbids commas). */
export function singletonTrainTag(
  code: string,
  secType: MaSpreadSecType,
  selection: StrategySelection = DEFAULT_STRATEGY_SELECTION,
): string {
  const active = [...new Set(selection)].sort();
  if (active.length === 0) active.push(...DEFAULT_STRATEGY_SELECTION);
  return `singleton-train:${secType}:${code}:${active.join("+")}`;
}

/** One strategy process tag's ENRICHED registry entry — the cluster's
 *  snake_case status payload (running / known / pid / started_at /
 *  ended_at / exit_code / adopted; epoch seconds; exit_code null =
 *  unfinished or unknowable, e.g. a run adopted after a cluster
 *  restart). */
export interface StrategyProcessStatus {
  running: boolean;
  known: boolean;
  pid: number | null;
  started_at: number | null;
  ended_at: number | null;
  exit_code: number | null;
  adopted: boolean;
}

/** Running-state of strategy process tags — polled on mount and on an
 *  adaptive cadence (60s idle, faster while a REMOTE process runs), so a
 *  page refresh puts the Run/Train buttons back into their spinning
 *  state and the exit edge (with exit_code) reloads data / surfaces the
 *  failure tail. */
export async function fetchStrategyProcessStatus(
  tags: ReadonlyArray<string>,
): Promise<Record<string, StrategyProcessStatus>> {
  const params = new URLSearchParams();
  if (tags.length) params.set("process_id_tag", tags.join(","));
  const res = await fetch(
    `/api/strategy/singleton/process-status?${params.toString()}`,
  );
  if (!res.ok) {
    throw new HttpError(res.status, await res.text());
  }
  const json = (await res.json()) as {
    status: Record<string, StrategyProcessStatus>;
  };
  return json.status ?? {};
}

/** One submitted job's outcome + log tail (the cluster's /jobs/result
 *  payload). known=false for an unknown or LRU-evicted tag. The strategy
 *  page fetches this when the status poll sees a job exit with a
 *  non-zero exit code, to surface the process's own stderr tail. */
export interface StrategyProcessResult {
  known: boolean;
  running: boolean;
  exit_code: number | null;
  stdout: string;
  stderr: string;
}

export async function fetchStrategyProcessResult(
  tag: string,
): Promise<StrategyProcessResult> {
  const params = new URLSearchParams();
  params.set("process_id_tag", tag);
  const res = await fetch(
    `/api/strategy/singleton/process-result?${params.toString()}`,
  );
  if (!res.ok) {
    throw new HttpError(res.status, await res.text());
  }
  const json = (await res.json()) as { result: StrategyProcessResult };
  return json.result;
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
    throw new HttpError(res.status, await res.text());
  }
  return (await res.json()) as CheckExistingResult;
}

/**
 * Submit the singleton backtest + risk computation for one (code,
 * secType) via the backend. Returns at the cluster ACCEPT — the process
 * keeps running server-side; poll fetchStrategyProcessStatus under the
 * same process-id-tag for the running state (NOT cached — always a
 * fresh POST).
 *
 * The selection is serialized as "macd,mov_rsi" (family names, no
 * weights) and passed to Python's --algo arg.
 *
 * When `force` is true (default), passes --force to Python so existing
 * rows are deleted and replaced. When false, the Python script skips
 * already-existing rows.
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
    throw new HttpError(res.status, text);
  }
  return (await res.json()) as RunStrategyResult;
}

/**
 * Train Model — submit the Optuna param optimizer
 * (`python -m strategy.factors_and_algos._optm_engine`) for one
 * (code, secType) via the backend; on completion it upserts the best
 * params into strategy.algo_configs (the NEXT Run Strategy uses them
 * automatically). Returns at the cluster ACCEPT — training runs minutes
 * server-side; poll fetchStrategyProcessStatus under the same
 * process-id-tag. Training writes no backtest rows itself, so there is
 * no PK conflict path.
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
    throw new HttpError(res.status, text);
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
  /** The run's final report bundle (training_runs.full_series_metrics):
   *  the flat full-series sanity check plus the optional 2026-11-19
   *  ``gate`` (winner vs the untrained default on the untouched tail)
   *  and ``pbo`` ({pbo, n_pairs, spearman_rho}) blocks. Null/flat on old
   *  runs. */
  report: Record<string, unknown> | null;
  /** REAL per-loss-type trial counts (the header `trials` column is the
   *  --trials CLI arg — unused by the mixed-mode Set C comb grid). */
  n_set_a: number;
  n_set_c: number;
  n_set_b: number;
}

/** The three regime loss types — displayed SEPARATELY in the logs UI
 *  (set_c_calmar = the mixed-mode Set C comb grid; binary runs carry
 *  set_a_omega instead). */
export type TrainLossType = "set_a_omega" | "set_c_calmar" | "set_b_calmar";

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
 * Fetch the Train Model info bundle from the DB: the config key's default
 * row, the trained config rows, the training run headers, and the
 * per-point trial log (all loss types) of the latest run — or of `runId`
 * when given (the UI run selector re-fetches on change).
 *
 * `strategyName` is the FT-LESS config key (the family name, or
 * "portfolio:…" for a mixed selection — `selectionToStrategyName(sel)`
 * WITHOUT the ft argument): algo_configs / training_runs are keyed
 * ft-less; the _ft{N} suffix is run identity only.
 *
 * NOT cached (plain fetch) — must be fresh right after a training run.
 */
export async function fetchTrainInfo(
  code: string,
  secType: MaSpreadSecType,
  strategyName: string = selectionToStrategyName(DEFAULT_STRATEGY_SELECTION),
  runId: number | null = null,
): Promise<TrainInfoResponse> {
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
    throw new HttpError(res.status, await res.text());
  }
  return (await res.json()) as TrainInfoResponse;
}

// ---------------------------------------------------------------------------
//  Forecast next day — the σ-ladder scenario register (Strategy → Forecast)
// ---------------------------------------------------------------------------

import type {
  ForecastNextDayResponse,
  ForecastNextDayRow,
} from "@shared/types";
export type { ForecastNextDayResponse, ForecastNextDayRow };

/** Build the process-id-tag for a Run Forecast invocation (must match
 *  the server-side default forecast-run:<sec_type> so UI-passed and
 *  server-default tags dedupe identically). */
export function forecastRunTag(secType: MaSpreadSecType): string {
  return `forecast-run:${secType}`;
}

/** Fetch the forecast register (GET /api/strategy/forecast) — one
 *  code's σ ladder when `code` is given, else every code's LATEST
 *  anchor (the page's default all-codes view). Cached briefly — the
 *  rows only change on a Run Forecast / the nightly tier. */
export async function fetchForecast(
  secType: MaSpreadSecType,
  code: string | null = null,
  anchorDate: string | null = null,
): Promise<ForecastNextDayResponse> {
  const params = new URLSearchParams();
  if (secType) params.set("sec_type", secType);
  if (code) params.set("code", code);
  if (anchorDate) params.set("anchor_date", anchorDate);
  const qs = params.toString();
  return fetchJson<ForecastNextDayResponse>(
    `/api/strategy/forecast${qs ? `?${qs}` : ""}`,
  );
}

/** Run Forecast — submit the forecast pipeline
 *  (`python -m strategy.forecast_next_day`) to the backend (it
 *  delegates to the cluster strategist). The module is incremental and
 *  auto-links to the strategy run; resolves at the cluster ACCEPT —
 *  poll fetchStrategyProcessStatus under the forecast tag. Always a
 *  fresh POST (never cached). */
export async function runForecast(
  secType: MaSpreadSecType,
  processIdTag?: string,
): Promise<RunStrategyResult> {
  const params = new URLSearchParams();
  if (secType) params.set("sec_type", secType);
  if (processIdTag) params.set("process_id_tag", processIdTag);
  const qs = params.toString();
  const res = await fetch(
    `/api/strategy/forecast/run${qs ? `?${qs}` : ""}`,
    { method: "POST" },
  );
  if (!res.ok) {
    const text = await res.text();
    throw new HttpError(res.status, text);
  }
  return (await res.json()) as RunStrategyResult;
}
