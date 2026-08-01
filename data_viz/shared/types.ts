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

/**
 * One (sector_id, industry_id) classification tag for an index.
 * Mirrors a row in stats.sec_index_tags. An index may carry multiple tags,
 * enabling multi-faceted browsing (e.g. "央企红利" is both DIV/DIV_SOE and
 * BROAD/BROAD_SOE). The PRIMARY tag matches the index's sector_id/industry_id
 * fields on sec_classification and is conventionally the first entry.
 */
export interface IndexTag {
  sector_id: string;
  industry_id: string;
}

/** One index with its daily baseline rows (used in the combined response). */
export interface IndexBundle {
  code: string;
  name: string;
  sector_id: string;
  sector_label: string;
  industry_id: string;
  industry_label: string;
  /** All classification tags for this index (primary first). Empty/absent
   *  when the index only carries the single (sector_id, industry_id) pair. */
  tags?: IndexTag[];
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
// Security Composition (sec_composition + sec_classification)
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
// Linked ETFs (sec_classification type='etf', parent_index_code = index code)
// Used by the Index Baseline page's "Linked ETFs" expansion beside the
// Composition pie — shows the ETFs tracking the displayed index.
// ----------------------------------------------------------------------------
/** One ETF that tracks the requested index. */
export interface LinkedEtfRow {
  /** ETF code WITH exchange suffix (e.g. "510300.SZ"). */
  code: string;
  name: string;
  /** Exchange suffix: SS | SZ | BJ | HK ("" when unknown). */
  exchange: string;
  sector_label: string;
  industry_label: string;
  /** Latest trading day with a row in stats.v_etf_margin (YYYY-MM-DD, "" if none). */
  latest_date: string;
  /** Close price on latest_date (NULL when no rows). */
  latest_close: number | null;
  /** Trading amount (成交金额, turnover) in 亿元 on latest_date — from
   *  v_etf_margin.amount_wan / 10000. NULL when no v_etf_margin rows. */
  latest_trading_amount: number | null;
  /** Valuation amount (NAV/AUM) in 亿元 — from sec_classification.aum_yi,
   *  populated from etf_index_map_all_*.csv. Available for ALL ETFs. */
  aum_yi: number | null;
  /** Number of trading-day rows in stats.v_etf_margin. */
  n_days: number;
}

export interface LinkedEtfsResponse {
  /** The requested index code (echoed back, suffix-stripped). */
  index_code: string;
  etfs: LinkedEtfRow[];
  /** Aggregate ETF trading turnover (yuan) tracking this index on the latest
   *  date with an index_exts row — from stats.index_exts.total_etf_amt.
   *  NULL when the index has no tracking ETF (no index_exts row). */
  total_etf_amt: number | null;
  /** 5-trading-day moving average of total_etf_amt (yuan) on the latest date —
   *  from stats.index_exts.total_etf_amt_ma5. NULL when insufficient history. */
  total_etf_amt_ma5: number | null;
  /** Latest date (YYYY-MM-DD) of the index_exts row used for total_etf_amt.
   *  "" when the index has no index_exts row. */
  total_etf_amt_date: string;
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
// Stock Combined (v_stock_baseline view + sec_classification)
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
//    benchmark_etf_amount, code_etf_amount, etf_amount_ratio_benchmark_to_code
//    (GENERATED), corr_{5,20,60,255}d.
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
  /** benchmark_etf_amount / code_etf_amount (GENERATED). A LIQUIDITY ratio
   *  (≥1 means benchmark ETF-market turnover exceeds subject's). Its inverse
   *  (1/ratio) is the subject's SHARE of the benchmark ETF market. NULL when
   *  either amount is NULL/0 (e.g. benchmark has no tracking ETF). */
  etf_amount_ratio: number | null;
  /** Aggregate ETF turnover (yuan) tracking benchmark_code on this date
   *  (Σ etf_liquidity_margin.amount_wan×1e4 where parent_index_code = benchmark_code).
   *  NULL when no ETF tracks the benchmark (e.g. 000001 上证指数). */
  benchmark_etf_amount: number | null;
  /** Subject's ETF turnover (yuan). For sec_type='etf': the ETF's own amount.
   *  For sec_type='index': aggregate ETF turnover tracking the subject index.
   *  NULL for stocks and for indices with no tracking ETF. */
  code_etf_amount: number | null;
  /** TRUE iff the benchmark index is broad-market (any tag in
   *  stats.sec_index_tags with is_broad_market=TRUE). Sourced from the DB,
   *  replacing the former hardcoded BROAD_MARKET_BENCHMARKS list. NULL when
   *  the benchmark has no classification (e.g. unclassified index). */
  is_broad_market: boolean | null;
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
  /** benchmark_etf_amount / code_etf_amount (GENERATED). LIQUIDITY ratio,
   *  NOT a price-attribution proportion. */
  etf_amount_ratio: number | null;
  /** Aggregate ETF turnover (yuan) tracking benchmark_code on this date. */
  benchmark_etf_amount: number | null;
  /** Subject's ETF turnover (yuan) on this date. */
  code_etf_amount: number | null;
  /** Number of ETFs tracking benchmark_code on this date (from stats.index_exts).
   *  NULL when no ETF tracks the benchmark. */
  benchmark_etf_num: number | null;
  /** Number of ETFs tracking the subject index on this date (from stats.index_exts).
   *  Only meaningful for sec_type='index'; NULL for ETF subjects. */
  code_etf_num: number | null;
  /** Industry id of the benchmark index (e.g. BANKS, SEMI, BROAD_CSI) from
   *  stats.sec_classification where type='index'. Constant across all dates
   *  for one benchmark. NULL when the benchmark has no classification. */
  benchmark_industry_id: string | null;
  /** Industry id of the subject. For sec_type='etf': the linked parent
   *  index's industry_id. For sec_type='index': the subject index's own
   *  industry_id. Constant across all dates for one subject. NULL when no
   *  classification is available. */
  code_industry_id: string | null;
  /** Aggregate ETF turnover (yuan) tracking ALL indices in the benchmark's
   *  industry on this date (from stats.etf_trading_amt where
   *  code = benchmark_industry_id). NULL when benchmark_industry_id is NULL
   *  or no ETF tracks any index in that industry on this date. */
  benchmark_industry_etf_amount: number | null;
  /** Aggregate ETF turnover (yuan) tracking ALL indices in the subject's
   *  industry on this date (from stats.etf_trading_amt where
   *  code = code_industry_id). NULL when code_industry_id is NULL or no ETF
   *  tracks any index in that industry on this date. */
  code_industry_etf_amount: number | null;
  /** Number of ETFs tracking indices in the benchmark's industry on this date. */
  benchmark_industry_etf_num: number | null;
  /** Number of ETFs tracking indices in the subject's industry on this date. */
  code_industry_etf_num: number | null;
  /** Subject close price on this date (COALESCE(adj_close, close) for ETFs;
   *  close for indices). Used for the two-curve close-price comparison chart. */
  subject_close: number | null;
  /** Benchmark index close on this date. */
  benchmark_close: number | null;
  /** Rolling Pearson correlation of subject close vs benchmark close over the
   *  trailing N trading days. NULL when fewer than N non-NaN closes in window. */
  corr_5d: number | null;
  corr_20d: number | null;
  corr_60d: number | null;
  corr_255d: number | null;
}

export interface PerfAttrChartResponse {
  code: string;
  name: string;
  benchmark_code: string;
  benchmark_name: string;
  rows: PerfAttrChartRow[];
}

// ----------------------------------------------------------------------------
//  Analysis Commons — Capital Flow (Industry × Broad-Market Benchmark)
//    analysis.capital_flow
//    PK: (date, industry_id, benchmark_code)
//
//    Captures each industry's "trending popularity" after removing the
//    dilution caused by broad-market ETFs that share overlapping stock
//    holdings with the industry. Pure metrics:
//      • pure_flow         = I * (1 - w_i * O_b / (O_b + O_i))
//      • pure_growth       = g_i - w_i * g_b
//      • pure_popularity   = pure_flow * pure_growth
//      • observed_popularity = I * g_i  (no removal)
//      • popularity_retention = pure / observed
// ----------------------------------------------------------------------------
/** One industry in the capital-flow list. */
export interface CapitalFlowIndustryRow {
  industry_id: string;
  industry_label: string;
  first_date: string;
  last_date: string;
  n_dates: number;
  /** Number of distinct benchmarks the industry is paired against. */
  n_benchmarks: number;
  /** Latest pure_popularity (summed or max across benchmarks) — used to sort. */
  latest_pure_popularity: number | null;
  /** Latest observed_popularity (raw, no broad-market removal). */
  latest_observed_popularity: number | null;
  /** Latest popularity_retention = pure / observed. */
  latest_retention: number | null;
  /** Average pure_growth across all dates and benchmarks (fractional). */
  avg_pure_growth: number | null;
}

export interface CapitalFlowIndustriesResponse {
  industries: CapitalFlowIndustryRow[];
}

/** Per-date row of the chart for one (industry, benchmark) pair. */
export interface CapitalFlowChartRow {
  date: string;
  /** I (yuan): aggregate industry ETF trading amount on this date. */
  industry_etf_amount: number | null;
  /** Number of ETFs in the industry on this date. */
  industry_etf_num: number | null;
  /** g_i: amount-weighted avg ETF return in the industry (fractional). */
  industry_return: number | null;
  /** B (yuan): aggregate ETF trading amount tracking the benchmark. */
  benchmark_etf_amount: number | null;
  benchmark_etf_num: number | null;
  /** g_b: benchmark index daily return (fractional). */
  benchmark_return: number | null;
  /** w_i (PERCENT): fraction of industry weight on overlap stocks. */
  industry_overlap_weight: number | null;
  /** w_b (PERCENT): fraction of benchmark weight on overlap stocks. */
  benchmark_overlap_weight: number | null;
  /** O_i = I * w_i / 100 (yuan). */
  industry_overlap_amount: number | null;
  /** O_b = B * w_b / 100 (yuan). */
  benchmark_overlap_amount: number | null;
  /** I * (1 - w_i * O_b / (O_b + O_i)) (yuan). */
  pure_flow: number | null;
  /** g_i - w_i * g_b (fractional). */
  pure_growth: number | null;
  /** pure_flow * pure_growth. */
  pure_popularity: number | null;
  /** I * g_i (raw popularity, no removal). */
  observed_popularity: number | null;
  /** pure_popularity / observed_popularity. Unbounded (NULL when observed=0). */
  popularity_retention: number | null;
}

export interface CapitalFlowChartResponse {
  industry_id: string;
  industry_label: string;
  benchmark_code: string;
  benchmark_label: string;
  rows: CapitalFlowChartRow[];
}

/** One benchmark in the list (industry_id is implicit from the request). */
export interface CapitalFlowBenchmarkRow {
  benchmark_code: string;
  benchmark_label: string;
  /** Average overlap weight w_i across all dates for this pair (PERCENT). */
  avg_w_i: number | null;
  /** Average overlap weight w_b across all dates for this pair (PERCENT). */
  avg_w_b: number | null;
  /** Sum of pure_popularity across all dates. */
  total_pure_popularity: number | null;
  /** Sum of observed_popularity across all dates. */
  total_observed_popularity: number | null;
  /** Average pure_growth across all dates (fractional). */
  avg_pure_growth: number | null;
  /** Number of dates with data. */
  n_dates: number;
  first_date: string;
  last_date: string;
}

/** Response for /capital-flow/benchmarks?industry_id=AI. */
export interface CapitalFlowBenchmarksResponse {
  industry_id: string;
  industry_label: string;
  benchmarks: CapitalFlowBenchmarkRow[];
}
