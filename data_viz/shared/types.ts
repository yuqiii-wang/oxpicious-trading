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
  lpr_1y: number | null;
  lpr_5y: number | null;
}

export interface DebtBaselineResponse {
  dates: string[];
  rows: DebtBaselineRow[];
  minDate: string;
  maxDate: string;
}

// ----------------------------------------------------------------------------
// PBoC Open Market Announcements (stats.pboc_oma)
//   High-level policy notices (公开市场业务公告) — NOT daily transaction
//   announcements. Loaded from temps/pboc_oma_news/oma_combined.csv by
//   build_debt_baseline.py. Composite PK (date, title); no FK to
//   debt_identity since announcements may occur on non-trading days.
// ----------------------------------------------------------------------------
export type PbocOmaType =
  | "primary_dealer"
  | "central_bank_bill"
  | "overnight_reverse_repo"
  | "outright_repo"
  | "interest_rate"
  | "mlf"
  | "tool_introduction"
  | "other";

export interface PbocOmaRow {
  date: string;
  title: string;
  type: string;
  content: string;
  detail_url: string;
  /** Pipe-separated matched keywords (e.g. "隔夜逆回购|一级交易商"). */
  keywords: string;
  serial_year: string;
  serial_no: string;
  detail_slug: string;
}

export interface PbocOmaResponse {
  rows: PbocOmaRow[];
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

export interface EtfBundle {
  code: string;
  name: string;
  is_bond: boolean;
  rows: EtfMarginRow[];
  sector_id: string;
  sector_label: string;
  industry_id: string;
  industry_label: string;
  /** Primary tracking index code (e.g. "000300" for 沪深300). Empty when no mapping exists. */
  index_code: string;
  /** Primary tracking index name (Chinese, e.g. "沪深300"). Empty when no mapping exists. */
  index_name: string;
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
  amount: number | null;
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
  /** Source:
   *   "full"  = all ETF holdings available,
   *   "index" = ETF had no holdings; fell back to the tracking index's
   *             composition (see `index_source`). */
  source: "full" | "index";
  /** Populated only when `source === "index"` — identifies the tracking
   *  index whose composition is being shown as a fallback. */
  index_source?: {
    code: string;
    name: string;
  };
}

// ----------------------------------------------------------------------------
// Stock Baseline (v_stock_baseline view — stock_identity + stock_basic_stats)
// Used by the composition pie chart's per-stock candlestick expansion.
// ----------------------------------------------------------------------------
export interface StockBaselineRow {
  date: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  prev_close: number | null;
  pct_change: number | null;
  pe: number | null;
  is_pe_estimated: boolean;
  has_intraday_5mins: boolean;
}

export interface StockBaselineResponse {
  code: string;
  name: string;
  dates: string[];
  rows: StockBaselineRow[];
}

// ----------------------------------------------------------------------------
// Stock Combined (v_stock_baseline view + stock_industry_map)
// Used by the Stock Baseline page — paginated stock list filtered by sector +
// industry, mirroring IndexCombinedResponse.
// ----------------------------------------------------------------------------
/** One stock with its daily baseline rows (used in the combined response). */
export interface StockBundle {
  code: string;
  name: string;
  sector_id: string;
  sector_label: string;
  industry_id: string;
  industry_label: string;
  rows: StockBaselineRow[];
}

/** Paginated response for the stock two-level selector page. */
export interface StockCombinedResponse {
  sector_id: string;
  industry_id: string;
  dates: string[];
  stocks: StockBundle[];
  total_stocks: number;
  total_pages: number;
  page: number;
  page_size: number;
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
  /** MAX(date) from stats.v_stock_baseline */
  stock_baseline: string;
}

// ----------------------------------------------------------------------------
// Snapshot dates (used by options dashboard)
// ----------------------------------------------------------------------------
export interface SnapshotDate {
  label: string;
  date: string;
}

// ----------------------------------------------------------------------------
// Analysis Commons — MA-Spread (ETF + Index)
//   Single-table model keyed by sec_type ('etf' | 'index'):
//     analysis.mov_ave_spreads_detail   (per-(sec_type, code, date) WIDE row)
//
//   9 gap pairs per asset:
//     • 5 price-vs-MA pairs:  ma_short = 0 (price sentinel), ma_long ∈ {5,20,60,120,255}
//     • 4 MA-vs-MA pairs:     ma_short = 5, ma_long ∈ {20,60,120,255}
//   gap_value = (short_value - long_value) / long_value
//
//   The sec_type is supplied as a query param on every endpoint; responses
//   are scoped to that sec_type (no sec_type field in the payload).
// ----------------------------------------------------------------------------
/** Security type discriminator for the MA-spread analysis. */
export type MaSpreadSecType = "etf" | "index" | "stock";

/** One row in the per-date per-pair detail series. */
export interface MovAveSpreadDetailRow {
  date: string;
  /** Value of the short series on this date (adj_close ?? close for price; MA value for MA). */
  short_value: number | null;
  /** Value of the long MA on this date. */
  long_value: number | null;
  /** (short_value - long_value) / long_value — signed fractional gap. */
  gap_value: number | null;
  /** 1st derivative of the short MA on this date. NULL when ma_short = 0 (price has no slope). */
  short_slope: number | null;
  /** 2nd derivative of the short MA on this date. NULL when ma_short = 0. */
  short_curvature: number | null;
  /** 1st derivative of the long MA on this date. */
  long_slope: number | null;
  /** 2nd derivative of the long MA on this date. */
  long_curvature: number | null;
}

/** One pair's full time series. */
export interface MovAveSpreadPairSeries {
  ma_short: number;
  ma_long: number;
  /** Display label, e.g. "Price/MA5" or "MA5/MA20". */
  pair_label: string;
  rows: MovAveSpreadDetailRow[];
}

/** Latest snapshot of one pair's gap_value at the asset's most recent date. */
export interface MovAveSpreadLatestGap {
  ma_short: number;
  ma_long: number;
  gap_value: number | null;
}

/** One asset (ETF or index) in the analysis-commons list (one row per code). */
export interface MovAveSpreadCodeRow {
  code: string;
  name: string;
  /** Earliest date with analysis data for this code. */
  first_date: string;
  /** Latest date with analysis data for this code. */
  last_date: string;
  /** Distinct trading-day count. */
  n_dates: number;
  /** Latest snapshot of all 9 gap values at last_date (for sparkline / sort). */
  latest_gaps: MovAveSpreadLatestGap[];
  /** All-time max gap_value across all 9 pairs (fractional, e.g. 0.05 = +5%). */
  max_gain: number | null;
  /** All-time min gap_value across all 9 pairs (fractional, e.g. -0.03 = -3%). */
  max_loss: number | null;
  /** max_gain - max_loss — the total observed gap range (fractional). */
  max_spread: number | null;
}

export interface MovAveSpreadCodesResponse {
  codes: MovAveSpreadCodeRow[];
}

/** Response for GET /chart?sec_type=etf&code=510050 — all 9 pair time series for one asset. */
export interface MovAveSpreadChartResponse {
  code: string;
  name: string;
  /** 9 pair time series (5 price-vs-MA + 4 ma5-vs-MA). */
  pairs: MovAveSpreadPairSeries[];
}

// ----------------------------------------------------------------------------
//  Analysis Commons — Perf Attribution (ETF/Index subjects × Index benchmarks)
//    analysis.sec_alloc_perf_attribution
//    PK: (code, date, sec_type, benchmark_code)
//
//    Per-row: subject_return, benchmark_return, active_return,
//    benchmark_amount, code_amount, amount_ratio_benchmark_to_code (GENERATED).
// ----------------------------------------------------------------------------
export type PerfAttrSecType = "etf" | "index";

export interface PerfAttrCodeRow {
  code: string;
  name: string;
  first_date: string;
  last_date: string;
  n_dates: number;
  /** Available benchmark index codes for this subject. */
  benchmarks: string[];
  /** Latest active_return for the default benchmark (000300, or first available). */
  latest_active_return: number | null;
  /** Average |active_return| across all dates and benchmarks. */
  avg_abs_active_return: number | null;
}

export interface PerfAttrCodesResponse {
  sec_type: PerfAttrSecType;
  codes: PerfAttrCodeRow[];
}

/** Per-benchmark breakdown for the latest date (rise/drop attribution). */
export interface PerfAttrBenchmarkRow {
  benchmark_code: string;
  benchmark_name: string;
  date: string;
  subject_return: number | null;
  benchmark_return: number | null;
  active_return: number | null;
  code_sec_shared_weight: number | null;
  benchmark_sec_shared_weight: number | null;
  amount_ratio: number | null;
  /** Benchmark's yuan amount on this date (src=index_basic_stats.amount×1e8). NULL when source amount is NULL. */
  benchmark_amount: number | null;
}

export interface PerfAttrAttributionResponse {
  code: string;
  name: string;
  sec_type: PerfAttrSecType;
  latest_date: string;
  benchmarks: PerfAttrBenchmarkRow[];
}

export interface PerfAttrChartRow {
  date: string;
  subject_return: number | null;
  benchmark_return: number | null;
  active_return: number | null;
  /** benchmark_amount / code_amount (GENERATED). NULL when either is 0/NULL. */
  amount_ratio: number | null;
}

export interface PerfAttrChartResponse {
  code: string;
  name: string;
  benchmark_code: string;
  benchmark_name: string;
  rows: PerfAttrChartRow[];
}
