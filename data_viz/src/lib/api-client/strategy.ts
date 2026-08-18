import { fetchJson } from "./_cache";
import type {
  MaSpreadSecType,
  StrategyBacktestResponse,
  StrategyRiskResponse,
  StrategyForecast1mResponse,
  StrategyDecision,
} from "@shared/types";

// ---------------------------------------------------------------------------
//  Strategy — singleton backtest
//  TTL-only cache (ephemeral backtest, no DB write).
//
//  Algo selection: the user picks a WEIGHT per algo (0.0-1.0, summing to 1.0).
//  Binary mode (one algo at 1.0, rest 0): strategy_name = algo name (e.g.
//  "macd"). Mixed mode (multiple non-zero weights): strategy_name =
//  "portfolio:bb*0.5+macd*0.5" (built by portfolioName(), mirrors the Python
//  portfolio_name() in strategy/factors_and_algos/portfolio.py).
//
//  The resolved strategy_name drives the DATA load (backtest/risks/forecast
//  SQL-filter on strategy_name). The Run button passes the SERIALIZED
//  selection ("bollinger_bands:0.5,macd:0.5") to Python's --algo arg, which
//  the Python _parse_algo_arg already understands.
//
//  Default: { bollinger_bands: 0, macd: 1.0, ma_spread: 0 } — binary MACD.
// ---------------------------------------------------------------------------
export type StrategyAlgo = "bollinger_bands" | "macd" | "ma_spread";
export const STRATEGY_ALGOS: StrategyAlgo[] = ["bollinger_bands", "macd", "ma_spread"];

/** Per-algo weight selection. Weights are 0.0-1.0 and SHOULD sum to 1.0. */
export type StrategySelection = Record<StrategyAlgo, number>;

/** Default selection: MACD-only (binary). User can mix from there. */
export const DEFAULT_STRATEGY_SELECTION: StrategySelection = {
  bollinger_bands: 0,
  macd: 1.0,
  ma_spread: 0,
};

/** Human-readable label for an algo (for the weight menu UI). */
export const ALGO_LABELS: Record<StrategyAlgo, string> = {
  bollinger_bands: "Bollinger Bands",
  macd: "MACD",
  ma_spread: "MA Spread",
};

/** Abbreviation used in portfolio strategy_name (mirrors Python _ABBR). */
const ALGO_ABBR: Record<StrategyAlgo, string> = {
  bollinger_bands: "bb",
  macd: "macd",
  ma_spread: "ma",
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
 *  - Mixed (multiple non-zero): returns "portfolio:bb*0.5+macd*0.5".
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
 *  Format: "bollinger_bands:0.5,macd:0.5" (understood by _parse_algo_arg). */
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
 *  "MACD 100%" or "BB 50% + MACD 50%" or "Invalid (sum=0.8)".
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
  scenario: string | null = null,
  selection: StrategySelection = DEFAULT_STRATEGY_SELECTION,
  ft: number = 0,
): Promise<StrategyBacktestResponse> {
  const strategyName = selectionToStrategyName(selection, ft);
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  if (secType) params.set("sec_type", secType);
  if (scenario) params.set("scenario", scenario);
  params.set("strategy_name", strategyName);
  const qs = params.toString();
  return fetchJson<StrategyBacktestResponse>(
    `/api/strategy/singleton/backtest${qs ? `?${qs}` : ""}`,
  );
}

export function fetchSingletonRisks(
  code: string,
  secType: MaSpreadSecType,
  scenario: string | null = null,
  selection: StrategySelection = DEFAULT_STRATEGY_SELECTION,
  ft: number = 0,
): Promise<StrategyRiskResponse> {
  const strategyName = selectionToStrategyName(selection, ft);
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  if (secType) params.set("sec_type", secType);
  if (scenario) params.set("scenario", scenario);
  params.set("strategy_name", strategyName);
  const qs = params.toString();
  return fetchJson<StrategyRiskResponse>(
    `/api/strategy/singleton/risks${qs ? `?${qs}` : ""}`,
  );
}

/** 1-month forward sell-confidence forecast (7 sigma scenarios + mean). */
export function fetchSingletonForecast1m(
  code: string,
  secType: MaSpreadSecType,
  selection: StrategySelection = DEFAULT_STRATEGY_SELECTION,
  ft: number = 0,
): Promise<StrategyForecast1mResponse> {
  const strategyName = selectionToStrategyName(selection, ft);
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  if (secType) params.set("sec_type", secType);
  params.set("strategy_name", strategyName);
  const qs = params.toString();
  return fetchJson<StrategyForecast1mResponse>(
    `/api/strategy/singleton/forecast${qs ? `?${qs}` : ""}`,
  );
}

/** Lightweight forecast-only decisions for a scenario (20 SELL rows + summary).
 *  Used when switching forecast scenarios to avoid reloading the entire
 *  parent backtest (OHLC + actual decisions + daily are reused from cache). */
export interface ForecastScenarioResponse {
  code: string;
  sec_type: string;
  scenario: string;
  forecast_decisions: StrategyDecision[];
  summary: {
    n_buys: number;
    n_sells: number;
    realized_pnl: number;
    final_cash: number;
    total_return_pct: number;
    total_buy_cost: number;
    first_buy_date: string | null;
    first_buy_fill_price: number | null;
  };
}

export function fetchForecastScenarioDecisions(
  code: string,
  secType: MaSpreadSecType,
  scenario: string,
  selection: StrategySelection = DEFAULT_STRATEGY_SELECTION,
  ft: number = 0,
): Promise<ForecastScenarioResponse> {
  const strategyName = selectionToStrategyName(selection, ft);
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  if (secType) params.set("sec_type", secType);
  if (scenario) params.set("scenario", scenario);
  params.set("strategy_name", strategyName);
  const qs = params.toString();
  return fetchJson<ForecastScenarioResponse>(
    `/api/strategy/singleton/forecast-decisions${qs ? `?${qs}` : ""}`,
  );
}

/** Result of POST /api/strategy/singleton/run. */
export interface RunStrategyResult {
  success: boolean;
  stdout: string;
  stderr: string;
  exitCode: number;
}

/**
 * Run the singleton backtest + risk computation for one (code, secType) by
 * spawning the Python scripts via the backend. Returns when both processes
 * exit. NOT cached (always a fresh POST).
 *
 * The selection is serialized as "bollinger_bands:0.5,macd:0.5" and passed
 * to Python's --algo arg (which _parse_algo_arg understands). */
export async function runSingletonStrategy(
  code: string,
  secType: MaSpreadSecType,
  forecast: boolean = true,
  selection: StrategySelection = DEFAULT_STRATEGY_SELECTION,
  ft: number = 0,
): Promise<RunStrategyResult> {
  const serialized = serializeSelection(selection);
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  if (secType) params.set("sec_type", secType);
  params.set("forecast", String(forecast));
  params.set("algo", serialized);
  if (ft && ft > 0) params.set("fault_tolerance", String(ft));
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
