/**
 * Shared TypeScript types mirroring the CSV schemas produced by the project's
 * build_*.py scripts. These types are consumed by both the Express backend
 * (api/services/*) and the React frontend (src/features/*).
 */

// ----------------------------------------------------------------------------
// Debt Baseline (debt_baseline.csv)
// ----------------------------------------------------------------------------
export interface DebtBaselineRow {
  date: string;
  omo_rate: number | null;
  omo_quantity: number | null;
  omo_tenor_days: number | null;
  omo_tenor_label: string;
  repo_start_quantity: number;
  repo_end_quantity: number;
  repo_net_injection: number;
  repo_cumulative: number;
  outright_repo_marker: 0 | 1;
  outright_repo_quantity: number | null;
  outright_repo_tenor_days: number | null;
  outright_repo_tenor_label: string;
  outright_repo_serial: string;
  mlf_marker: 0 | 1;
  mlf_quantity: number | null;
  mlf_tenor_days: number | null;
  mlf_tenor_label: string;
  mlf_serial: string;
  shibor_o_n: number | null;
  shibor_1w: number | null;
  shibor_1m: number | null;
  shibor_3m: number | null;
  shibor_6m: number | null;
  shibor_1y: number | null;
  cb_1y: number | null;
  cb_5y: number | null;
  cb_10y: number | null;
  cb_30y: number | null;
}

export interface DebtBaselineResponse {
  dates: string[];
  rows: DebtBaselineRow[];
  minDate: string;
  maxDate: string;
}

// ----------------------------------------------------------------------------
// SZSE Options (options_combined.csv)
// ----------------------------------------------------------------------------
export type OptionType = "CALL" | "PUT";

export interface OptionsRow {
  date: string;
  contract_code: string;
  contract_name: string;
  underlying_code: string;
  underlying_name: string;
  option_type: OptionType;
  expiry_month: string;
  expiry_date: string;
  days_to_expiry: number;
  strike_price: number; // raw 厘
  settle: number; // 元/张
  underlying_close: number; // 厘
  moneyness_ratio: number;
  open_interest: number;
  volume: number;
  implied_vol: number | null;
  delta: number | null;
  theta: number | null;
  gamma: number | null;
  vega: number | null;
  rho: number | null;
}

export interface OptionsUnderlying {
  code: string;
  name: string;
}

export interface OptionsCombinedResponse {
  dates: string[];
  underlying_code: string;
  rows: OptionsRow[];
}

export interface EtfOhlcvResponse {
  dates: string[];
  code: string;
  rows: Array<{
    date: string;
    open: number;
    high: number;
    low: number;
    close: number;
    volume: number;
  }>;
}

// ----------------------------------------------------------------------------
// ETF + Margin (etf_margin_combined.csv)
// ----------------------------------------------------------------------------
export interface EtfMarginRow {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  prev_close: number;
  adj_open: number | null;
  adj_high: number | null;
  adj_low: number | null;
  adj_close: number | null;
  adj_prev_close: number | null;
  /** 1 when a corporate action (dividend/split) was detected on this day. */
  is_split_event_day: number;
  /** 'dividend' | 'split_or_conv' | '' | null — type of corp-action event. */
  action_type: string | null;
  /** Per-share dividend amount (negative = price drop). Null when no event. */
  implied_dividend_per_share: number | null;
  /** Cumulative split/adjustment factor (1.0 = no adjustment). */
  cum_split_factor: number | null;
  volume_wan: number;
  amount_wan: number;
  rz_balance: number;
  rq_balance_qty: number;
  rq_balance_amt: number;
  total_balance: number;
}

export interface Top5Holding {
  stock_name: string;
  weight_pct: number;
}

export interface EtfBundle {
  code: string;
  name: string;
  is_bond: boolean;
  rows: EtfMarginRow[];
  top5: Top5Holding[];
  sector_id: string;
  sector_label: string;
  industry_id: string;
  industry_label: string;
}

/** L2 industry group — a flat list of ETFs sharing the same L2 classification. */
export interface ThemeGroup {
  slug: string;
  label: string;
  sector_id: string;
  sector_label: string;
  etfs: Array<{ code: string; name: string }>;
}

/** L2 industry node within a sector (used by the two-level selector). */
export interface IndustryNode {
  industry_id: string;
  industry_label: string;
  industry_slug: string;
  count: number;
  items: Array<{ code: string; name: string }>;
}

/** L1 sector node — top level of the two-level selector. */
export interface SectorNode {
  sector_id: string;
  sector_label: string;
  count: number;
  industries: IndustryNode[];
}

export interface EtfMarginCombinedResponse {
  theme_slug: string;
  sector_id: string;
  industry_id: string;
  dates: string[];
  etfs: EtfBundle[];
  total_etfs: number;
  total_pages: number;
  page: number;
  page_size: number;
}

// ----------------------------------------------------------------------------
// Index Baseline (v_index_baseline view)
// ----------------------------------------------------------------------------
export interface IndexBaselineRow {
  date: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  volume: number | null;
  turnover: number | null;
  change_pct: number | null;
  pe: number | null;
  cons_number: number | null;
  ma5: number | null;
  ma20: number | null;
  ma60: number | null;
  ma120: number | null;
  ma255: number | null;
  /** TRUE when 5-minute intraday bars exist for this (date, code). */
  has_intraday_5mins: boolean;
}

export interface IndexInfo {
  code: string;
  name: string;
  n_days: number;
  first_date: string;
  last_date: string;
}

export interface IndexBaselineResponse {
  code: string;
  name: string;
  dates: string[];
  rows: IndexBaselineRow[];
}

/** One index with its daily baseline rows (used in the combined response). */
export interface IndexBundle {
  code: string;
  name: string;
  sector_id: string;
  sector_label: string;
  industry_id: string;
  industry_label: string;
  rows: IndexBaselineRow[];
}

/** Paginated response for the index two-level selector page. */
export interface IndexCombinedResponse {
  sector_id: string;
  industry_id: string;
  dates: string[];
  indices: IndexBundle[];
  total_indices: number;
  total_pages: number;
  page: number;
  page_size: number;
}

/** One 5-minute intraday OHLC bar. */
export interface IndexIntraday5minRow {
  time: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  change: number | null;
  change_pct: number | null;
}

export interface IndexIntraday5minResponse {
  code: string;
  date: string;
  name: string;
  bars: IndexIntraday5minRow[];
}

// ----------------------------------------------------------------------------
// Security Composition (sec_composition + stock_industry_map)
// ----------------------------------------------------------------------------
export interface SecCompositionHolding {
  stock_code: string;
  stock_name: string;
  weight_pct: number;
  industry: string;
  sector_id: string;
  sector_label: string;
}

export interface SecCompositionResponse {
  code: string;
  snapshot_date: string;
  /** All holdings (from sec_composition — full composition when available). */
  holdings: SecCompositionHolding[];
  /** Top 5 by weight — for the text list display. */
  top5: SecCompositionHolding[];
  /** Source:
   *   "full"  = all ETF holdings available,
   *   "top5"  = only top 5 ETF holdings available,
   *   "index" = ETF had no holdings; fell back to the tracking index's
   *             composition (see `index_source`). */
  source: "full" | "top5" | "index";
  /** Populated only when `source === "index"` — identifies the tracking
   *  index whose composition is being shown as a fallback. */
  index_source?: {
    code: string;
    name: string;
  };
}

// ----------------------------------------------------------------------------
// Cache versioning — latest (MAX) date per data source, used by the frontend
// to decide whether cached UI data is stale (DB has newer rows).
// ----------------------------------------------------------------------------
export interface LatestDatesResponse {
  /** MAX(date) from stats.v_debt_baseline */
  debt: string;
  /** MAX(date) from stats.v_etf_margin */
  etf_margin: string;
  /** MAX(date) from stats.v_index_baseline */
  index_baseline: string;
  /** MAX(date) from stats.v_options_quote */
  options: string;
  /** MAX(snapshot_date) from stats.sec_composition */
  sec_composition: string;
}

// ----------------------------------------------------------------------------
// Snapshot dates (used by options dashboard)
// ----------------------------------------------------------------------------
export interface SnapshotDate {
  label: string;
  date: string;
}
