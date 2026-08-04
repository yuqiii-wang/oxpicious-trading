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
  trading_shares: number;
  trading_amount: number;
  /** 融资余额 (cash borrow balance, yuan). Null when no margin data. */
  rz_balance: number | null;
  rq_balance_qty: number | null;
  /** 融券余额金额 (sec borrow balance in yuan). Null when no margin data. */
  rq_balance_amt: number | null;
  total_balance: number | null;
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
  trading_shares: number | null;
  trading_amount: number | null;
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
  /** Trading amount (成交金额, turnover) in yuan on latest_date — from
   *  v_etf_margin.trading_amount. NULL when no v_etf_margin rows. */
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
   *  date with an index_exts row — from stats.index_exts.total_etf_trading_amount.
   *  NULL when the index has no tracking ETF (no index_exts row). */
  total_etf_trading_amount: number | null;
  /** 5-trading-day moving average of total_etf_trading_amount (yuan) on the latest date —
   *  from stats.index_exts.total_etf_trading_amount_ma5. NULL when insufficient history. */
  total_etf_trading_amount_ma5: number | null;
  /** Latest date (YYYY-MM-DD) of the index_exts row used for total_etf_trading_amount.
   *  "" when the index has no index_exts row. */
  total_etf_trading_amount_date: string;
}

// ----------------------------------------------------------------------------
// Stock Baseline (v_stock_baseline view — stock_identity + stock_basic_stats)
// Used by the composition pie chart's per-stock OHLC expansion.
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
  /**
   * Rolling population σ (ddof=0) of price over the long MA's window
   * (e.g. std_20days when ma_long = 20). In price units. Used to draw the
   * Bollinger-style envelope (long_value ± k × long_std) on Price/MA charts.
   * NULL until the rolling window is fully populated. MA5/MA charts also
   * carry this field (the σ of the long MA's window) but the envelope is
   * only drawn around the long MA on Price/MA charts by convention.
   */
  long_std: number | null;
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
//    Per-row: code_sec_shared_weight, benchmark_sec_shared_weight,
//    benchmark_etf_trading_amount, code_etf_trading_amount, etf_trading_amount_ratio_benchmark_to_code
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
}

export interface PerfAttrCodesResponse {
  sec_type: PerfAttrSecType;
  codes: PerfAttrCodeRow[];
}

/** Per-benchmark breakdown for the latest date. */
export interface PerfAttrBenchmarkRow {
  benchmark_code: string;
  benchmark_name: string;
  date: string;
  code_sec_shared_weight: number | null;
  benchmark_sec_shared_weight: number | null;
  /** benchmark_etf_trading_amount / code_etf_trading_amount (GENERATED). A LIQUIDITY ratio
   *  (≥1 means benchmark ETF-market turnover exceeds subject's). Its inverse
   *  (1/ratio) is the subject's SHARE of the benchmark ETF market. NULL when
   *  either amount is NULL/0 (e.g. benchmark has no tracking ETF). */
  etf_trading_amount_ratio: number | null;
  /** Aggregate ETF turnover (yuan) tracking benchmark_code on this date
   *  (Σ etf_liquidity_margin.trading_amount where parent_index_code = benchmark_code).
   *  NULL when no ETF tracks the benchmark (e.g. 000001 上证指数). */
  benchmark_etf_trading_amount: number | null;
  /** Subject's ETF turnover (yuan). For sec_type='etf': the ETF's own trading_amount.
   *  For sec_type='index': aggregate ETF turnover tracking the subject index.
   *  NULL for stocks and for indices with no tracking ETF. */
  code_etf_trading_amount: number | null;
  /** TRUE iff the benchmark index is broad-market (any tag in
   *  stats.sec_index_tags with is_broad_market=TRUE). Sourced from the DB,
   *  replacing the former hardcoded BROAD_MARKET_BENCHMARKS list. NULL when
   *  the benchmark has no classification (e.g. unclassified index). */
  is_broad_market: boolean | null;
  /** Benchmark's FRACTIONAL daily return = (close_t - close_{t-1}) /
   *  close_{t-1} (e.g. 0.0125 = +1.25%). Computed on-the-fly in the
   *  attribution SQL via LATERAL joins to stats.index_basic_stats (NOT stored
   *  as a DB column). NULL when the benchmark has no close for the previous
   *  trading day. Used by the Fluctuation Attribution chart to compute the
   *  shared-weight contribution = benchmark_return × (shared_weight / 100). */
  benchmark_return: number | null;
  /** Subject's FRACTIONAL daily return (same formula as benchmark_return).
   *  Computed on-the-fly; NULL for ETF subjects (not currently populated)
   *  and when no previous-day close exists. */
  subject_return: number | null;
  /** subject_return - benchmark_return (both fractional). NULL when either
   *  is NULL. Shown in the Fluctuation Attribution tooltip. */
  active_return: number | null;
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
  /** benchmark_etf_trading_amount / code_etf_trading_amount (GENERATED). LIQUIDITY ratio,
   *  NOT a price-attribution proportion. */
  etf_trading_amount_ratio: number | null;
  /** 5-trading-day moving average of etf_trading_amount_ratio (populated by the
   *  analysis Python script via pandas rolling(5).mean(); NULL when the
   *  underlying ratio is NULL for the trailing 5-day window). */
  etf_trading_amount_ratio_ma5: number | null;
  /** Aggregate ETF turnover (yuan) tracking benchmark_code on this date. */
  benchmark_etf_trading_amount: number | null;
  /** Subject's ETF turnover (yuan) on this date. */
  code_etf_trading_amount: number | null;
  /** Number of ETFs tracking benchmark_code on this date (from stats.index_exts).
   *  NULL when no ETF tracks the benchmark. */
  benchmark_etf_num: number | null;
  /** Number of ETFs tracking the subject index on this date (from stats.index_exts).
   *  Only meaningful for sec_type='index'; NULL for ETF subjects. */
  code_etf_num: number | null;
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
  /** ETFs tracking the benchmark index (from stats.sec_classification where
   *  type='etf' AND parent_index_code = benchmark_code). Empty when no ETF
   *  tracks the benchmark (e.g. 000001 上证指数). */
  benchmark_linked_etfs: LinkedEtfName[];
  /** ETFs tracking the subject index (from stats.sec_classification where
   *  type='etf' AND parent_index_code = code). Empty for ETF subjects and
   *  for indices with no tracking ETF. */
  code_linked_etfs: LinkedEtfName[];
}

/** Lightweight linked-ETF descriptor (code + name only) for the PerfAttr
 *  chart's "linked ETFs" button. */
export interface LinkedEtfName {
  /** ETF code WITH exchange suffix (e.g. "510300.SH"). */
  code: string;
  /** ETF display name (e.g. "沪深300ETF"). */
  name: string;
}

// ----------------------------------------------------------------------------
//  Analysis Commons — Industry Sentiments
//  (member index values rebased-to-100 client-side + server-side mean/var
//   per pool_size slice, anchored at history start)
//
//    analysis.industry_sentiments (PK: date, industry_id, pool_size)
//
//    Each industry's plot shows its member INDEX VALUES directly, rebased to
//    100 at the start of the displayed (zoom) window. Rebased-to-100 makes
//    member indices comparable regardless of absolute price level — e.g. CSI
//    500 (~5500pts) and SSE 50 (~2600pts) plot on a common scale, so a +10%
//    move on either looks equally large. The LINE rebasing is computed in
//    the BROWSER (IndustrySentimentsPage.tsx) from raw daily closes.
//
//    ADDITIONALLY, the server precomputes MEAN and VARIANCE of rebased-to-100
//    values across member indices, per (date, industry_id, pool_size) slice.
//    pool_size ∈ ('small' <51 stocks, 'mid' <301, 'large' otherwise, 'all').
//    The MEAN anchor is the START OF ALL HISTORY (per-index first available
//    close) — a fixed server-side point. When the client-side slider narrows,
//    the lines re-rebase to the slider's window-start but the mean/var overlay
//    STAYS anchored at history start (aligned only at full slider range).
//
//    BROAD-MARKET indices (BROAD_CSI, BROAD_SSE, BROAD_SZSE, BROAD_STAR) are
//    classified as industries under the FIN sector in stats.sec_classification
//    and are aggregated IDENTICALLY to industry indices.
//
//    Data source (queried directly by the API):
//      stats.index_basic_stats.close   (raw daily index closes)
//      JOIN stats.sec_classification   (type='index') for industry membership
//      stats.sec_composition           (stock_num → pool_size classification)
// ----------------------------------------------------------------------------
/** One daily close for a member index in an industry. */
export interface IndustrySentimentsIndexRow {
  date: string;
  /** Raw daily close from stats.index_basic_stats.close. NULL when the index
   *  has no row on this date (e.g. not yet listed). The frontend rebases to
   *  100 using the first non-null close in the visible (zoom) window. */
  close: number | null;
  /** Number of constituent stocks from the latest stats.sec_composition
   *  snapshot <= this date. NULL when the index has no composition snapshot
   *  (index contributes only to the 'all' pool_size slice). Drives the
   *  pool_size classification shown in the tooltip. */
  stock_num: number | null;
}

/** One member index's full close time series within an industry. */
export interface IndustrySentimentsIndex {
  code: string;
  name: string;
  /** Exchange code from stats.sec_classification.exchange. 'HK' for Hong
   *  Kong-linked indices, NULL/empty for mainland. Used by the frontend's
   *  show/hide HK toggle. */
  exchange: string | null;
  rows: IndustrySentimentsIndexRow[];
}

/** One per-date aggregation row for a pool_size slice. mean_price and
 *  var_price are computed across rebased-to-100 close values of member
 *  indices in this pool_size slice on this date (anchored at history start).
 *  mean_pe and total_trading_amount are computed on RAW values (no rebasing). */
export interface IndustrySentimentsAggRow {
  date: string;
  pool_size: "small" | "mid" | "large" | "all";
  /** Number of member indices with close data contributing to this slice on
   *  this date. PE/amount means may be computed over fewer indices. */
  index_count: number | null;
  /** AVG(rebased_to_100 close) across member indices in this slice.
   *  100 = members flat vs history start. NULL when no members in slice. */
  mean_price: number | null;
  /** VARIANCE(rebased_to_100 close) across member indices in this slice.
   *  NULL when fewer than 2 members (can't compute variance). */
  var_price: number | null;
  /** AVG(raw PE) across member indices in this slice. Source:
   *  stats.index_valuation.pe. NULL PE excluded. NULL when no PE data. */
  mean_pe: number | null;
  /** SUM(stock trading amount in yuan) across the UNION of stocks from all
   *  member indices' active compositions in this slice. Each stock counted
   *  ONCE (union, not sum-per-index). Source: stats.stock_basic_stats.trading_amount.
   *  NULL when no stock amount data is available for the union set. */
  total_trading_amount: number | null;
}

/** Response for GET /api/analysis/industry-sentiments/chart?industry_id=...
 *  Returns one entry per member index in the industry (raw close series for
 *  the multi-line plot) PLUS the precomputed mean/var aggregation rows per
 *  pool_size slice (for the overlay) PLUS a set of broad-market benchmark
 *  close series (CSI 300, SSE 50, CSI 1000) for the optional benchmark
 *  dropdown overlay. */
export interface IndustrySentimentsChartResponse {
  industry_id: string;
  industry_label: string;
  /** All indices classified into this industry (stats.sec_classification
   *  type='index' AND industry_id = $1). Indices with no index_basic_stats
   *  rows are omitted (nothing to plot). */
  indices: IndustrySentimentsIndex[];
  /** Precomputed mean/var aggregation rows for this industry, one per
   *  (date, pool_size) — across all 4 pool_size slices. The frontend filters
   *  to the user-selected pool_size for the overlay. Empty when the analysis
   *  hasn't been run yet. */
  aggregation: IndustrySentimentsAggRow[];
  /** Broad-market benchmark close series — CSI 300 (000300), SSE 50
   *  (000016), CSI 1000 (000852). The frontend renders a multi-select
   *  dropdown so the user can tick any subset to overlay (each rebased to
   *  100 at the visible window start, same as member indices). Benchmarks
   *  with no close data are omitted. Empty when no benchmarks have data. */
  benchmarks: IndustrySentimentsIndex[];
}

// ----------------------------------------------------------------------------
//  Industry Correlations — pairwise rolling Pearson correlation between two
//  industries' mean_price series (analysis.industry_sentiments.mean_price).
//  Drives the expandable Correlation chart on the IndustrySentiments page
//  (multi-industry mode only — Correlation button is disabled when fewer
//  than 2 industries are selected).
//
//  Source: analysis.industry_correlations (built by
//  analyze_industry_correlations.py). One row per (date, pair, pool_size)
//  with corr_5d / corr_20d / corr_60d / corr_255d.
//
//  Order convention: rows are stored with industry_id < benchmark_industry_id
//  (lexicographic, COLLATE "C") to deduplicate (A,B) vs (B,A). The API
//  returns rows matching either direction of the user-selected industry_ids
//  set — the frontend renders each pair as a single line.
//
//  Same-pool slices only: a single `pool_size` column captures the slice in
//  which both industries are compared. Cross-pool comparisons are not
//  materialized (see SQL comments for why).
// ----------------------------------------------------------------------------
/** One pairwise correlation row — the rolling Pearson correlation between
 *  industry_id and benchmark_industry_id's mean_price series at `date`
 *  over 4 trailing windows. NULL (corr_*) when insufficient overlap. */
export interface IndustryCorrelationRow {
  /** Subject industry (lexicographically smaller). */
  industry_id: string;
  /** Benchmark industry (lexicographically larger). */
  benchmark_industry_id: string;
  /** Display label for the subject industry (looked up from
   *  stats.sec_classification.industry_label). Falls back to industry_id
   *  when the label is missing or empty. */
  industry_label: string;
  /** Display label for the benchmark industry. */
  benchmark_industry_label: string;
  /** End date of the rolling window (YYYY-MM-DD). */
  date: string;
  /** Pool_size slice (same for both industries — cross-pool is not
   *  materialized). small / mid / large / all. */
  pool_size: "small" | "mid" | "large" | "all";
  /** 5-day rolling Pearson correlation. NULL when < 5 overlapping days. */
  corr_5d: number | null;
  /** 20-day rolling Pearson correlation. NULL when < 20 overlapping days. */
  corr_20d: number | null;
  /** 60-day rolling Pearson correlation. NULL when < 60 overlapping days. */
  corr_60d: number | null;
  /** 255-day rolling Pearson correlation. NULL when < 255 overlapping days. */
  corr_255d: number | null;
}

/** Response for GET /api/analysis/industry-correlations?industry_ids=...
 *  &pool_size=all */
export interface IndustryCorrelationsResponse {
  /** Distinct industry_ids requested (deduplicated). */
  industry_ids: string[];
  /** Pool_size slice used. */
  pool_size: "small" | "mid" | "large" | "all";
  /** Pairwise correlation rows — one per (date, lexicographic pair) where
   *  both endpoints are in industry_ids. Empty when the analysis hasn't
   *  been run or no pairs have enough overlapping history. */
  correlations: IndustryCorrelationRow[];
}

// ----------------------------------------------------------------------------
//  Industry-level Benchmark Attribution — reads pre-materialized rows from
//  analysis.industry_attributions (PK: date, industry_id, benchmark_code).
//  Each row carries industry_shared_weight (SUM of member indices' overlap
//  with the benchmark — can exceed 100) and benchmark_shared_weight (the
//  benchmark's weight on the UNION of industry member stocks — bounded
//  [0, 100]). benchmark_return is computed on-the-fly via a LATERAL join to
//  stats.index_basic_stats.
//
//  Drives the per-industry attribution bar charts (2nd plot onward) on the
//  Industry Sentiments page in "Benchmark Attribution" mode. The 1st plot is
//  the benchmark price chart (clickable to pick a date); each subsequent plot
//  shows the attribution bars for ONE selected industry at the clicked date.
//
//  Source: analysis.industry_attributions (built by
//  analyze.industry_sentiments.attributions — truncate-then-recompute).
// ----------------------------------------------------------------------------
/** One row per (industry_id, benchmark_code, date) — pre-materialized in
 *  analysis.industry_attributions. */
export interface IndustryBenchmarkAttributionRow {
  /** Benchmark index code (e.g. "000300"). */
  benchmark_code: string;
  /** Display name of the benchmark (looked up from stats.index_identity). */
  benchmark_name: string;
  /** As-of date (YYYY-MM-DD). */
  date: string;
  /** SUM of member indices' code_sec_shared_weight with the benchmark, from
   *  analysis.industry_attributions. Can exceed 100 (sum of multiple member
   *  portfolios — expected, NOT double-counting). NULL when no overlap data. */
  industry_shared_weight: number | null;
  /** Benchmark's weight on the UNION of industry member stocks, from
   *  analysis.industry_attributions. Bounded [0, 100] (percent). NULL when
   *  the benchmark has no composition data; 0 when no overlap. */
  benchmark_shared_weight: number | null;
  /** TRUE iff the benchmark index is broad-market (from stats.sec_index_tags). */
  is_broad_market: boolean | null;
  /** Benchmark's FRACTIONAL daily return = (close_t - close_{t-1}) /
   *  close_{t-1}. Computed on-the-fly via LATERAL join to
   *  stats.index_basic_stats (NOT stored as a DB column). NULL when no
   *  previous-day close. */
  benchmark_return: number | null;
}

/** Response for GET /api/analysis/industry-benchmark-attribution?industry_id=...
 *  &date=YYYY-MM-DD (date optional, defaults to latest available). */
export interface IndustryBenchmarkAttributionResponse {
  industry_id: string;
  industry_label: string;
  /** As-of date for the attribution (latest available when no `date` was
   *  requested). Empty string when no rows exist for the industry. */
  latest_date: string;
  /** One row per benchmark. Empty when the analysis hasn't been run or the
   *  industry has no member indices. */
  benchmarks: IndustryBenchmarkAttributionRow[];
}

// ----------------------------------------------------------------------------
//  Industry Attribution Benchmark list + price chart — drives the benchmark
//  dropdown and the 1st plot (benchmark price chart, clickable to pick a
//  date) in "Benchmark Attribution" mode on the Industry Sentiments page.
// ----------------------------------------------------------------------------

/** One entry in the benchmark dropdown — a benchmark index code that appears
 *  in analysis.industry_attributions, enriched with display name and
 *  is_broad_market flag. Broad-market benchmarks are sorted first. */
export interface IndustryAttributionBenchmarkEntry {
  benchmark_code: string;
  benchmark_name: string;
  is_broad_market: boolean | null;
}

/** Response for GET /api/analysis/industry-attribution/benchmarks — the list
 *  of all benchmark codes that appear in analysis.industry_attributions. */
export interface IndustryAttributionBenchmarksResponse {
  benchmarks: IndustryAttributionBenchmarkEntry[];
}

/** One row in the benchmark price series — date, raw close, and fractional
 *  daily return. */
export interface BenchmarkPriceRow {
  date: string;
  close: number | null;
  daily_return: number | null;
}

/** Response for GET /api/analysis/industry-attribution/benchmark-price?code=... */
export interface BenchmarkPriceChartResponse {
  code: string;
  name: string;
  rows: BenchmarkPriceRow[];
}

// ----------------------------------------------------------------------------
//  Industry Attribution Non-This-Industry Price Series — drives the green/red
//  shade overlay on the BenchmarkPriceChart in "Benchmark Attribution" mode.
//  One row per date for a given (industry_id, benchmark_code) pair, containing
//  the benchmark close + the non-this-industry price columns. Only broad-market
//  benchmarks have non-NULL non_this_industry_* values.
// ----------------------------------------------------------------------------

/** One row in the non-this-industry price series. */
export interface IndustryAttributionPriceSeriesRow {
  date: string;
  /** Raw benchmark close on the date (from stats.index_basic_stats). */
  benchmark_close: number | null;
  /** Benchmark close rebased to 100 at the first date in the response.
   *  Computed server-side so the frontend doesn't need to scan the full
   *  series to find the base. */
  benchmark_rolling: number | null;
  /** Today's non-industry price = bench_prev_close × (1 + non_industry_return).
   *  NULL for non-broad-market benchmarks. */
  non_this_industry_price: number | null;
  /** Accumulated non-industry price, rebased to 100 at benchmark start.
   *  NULL for non-broad-market benchmarks. */
  non_this_industry_rolling_price: number | null;
}

/** Response for GET /api/analysis/industry-attribution/non-this-industry-price
 *  ?industry_id=BANKS&benchmark_code=000300 */
export interface IndustryAttributionPriceSeriesResponse {
  industry_id: string;
  industry_label: string;
  benchmark_code: string;
  benchmark_name: string;
  /** TRUE iff the benchmark is broad-market (non_this_industry_* will be
   *  non-NULL). FALSE or NULL when the benchmark is not broad-market — the
   *  frontend shows a placeholder message. */
  is_broad_market: boolean | null;
  rows: IndustryAttributionPriceSeriesRow[];
}

// ----------------------------------------------------------------------------
//  All-Industries Attribution Bar Chart — one row per industry for a given
//  (benchmark_code, date). Drives the industry-level bar chart in "Benchmark
//  Attribution" mode: each bar = one industry's benchmark_shared_weight
//  (the benchmark's weight % on that industry's union of stocks).
//
//  GET /api/analysis/industry-attribution/all-industries
//    ?benchmark_code=000300&date=YYYY-MM-DD (date optional → latest)
// ----------------------------------------------------------------------------
/** One industry row in the all-industries attribution bar chart. */
export interface AllIndustriesAttributionRow {
  industry_id: string;
  industry_label: string;
  sector_label: string | null;
  /** Benchmark's weight % on this industry's union of stocks (0-100). */
  benchmark_shared_weight: number | null;
  /** SUM of member indices' overlap with the benchmark (can exceed 100). */
  industry_shared_weight: number | null;
  /** Benchmark's FRACTIONAL daily return = (close_t - close_{t-1}) /
   *  close_{t-1}. Computed on-the-fly via LATERAL join to
   *  stats.index_basic_stats (NOT stored as a DB column). NULL when no
   *  previous-day close. Used to compute Contribution =
   *  benchmark_return × (benchmark_shared_weight / 100) — same convention
   *  as Sec Allocation Perf Attribution. */
  benchmark_return: number | null;
}

/** Response for GET /api/analysis/industry-attribution/all-industries. */
export interface AllIndustriesAttributionResponse {
  benchmark_code: string;
  benchmark_name: string;
  /** As-of date (latest available when no `date` was requested). */
  date: string;
  is_broad_market: boolean | null;
  /** Benchmark's FRACTIONAL daily return on the as-of date (same value
   *  across all industries — they share the benchmark). NULL when no
   *  previous-day close is available. */
  benchmark_return: number | null;
  industries: AllIndustriesAttributionRow[];
}

// ----------------------------------------------------------------------------
//  Member-Index Attribution Bar Chart — one row per member index for a given
//  (industry_id, benchmark_code, date). Drives the per-industry bar charts in
//  "Benchmark Attribution" mode: each bar = one member index's
//  code_sec_shared_weight (the index's own weight % on stocks shared with the
//  benchmark).
//
//  GET /api/analysis/industry-attribution/member-indices
//    ?industry_id=BANKS&benchmark_code=000300&date=YYYY-MM-DD (date optional → latest)
// ----------------------------------------------------------------------------
/** One member-index row in the per-industry attribution bar chart. */
export interface MemberIndexAttributionRow {
  /** Member index code (e.g. "399986"). */
  code: string;
  /** Display name of the member index. */
  name: string;
  /** The index's own weight % on stocks shared with the benchmark (0-100). */
  code_sec_shared_weight: number | null;
  /** The benchmark's weight % on stocks shared with this index (0-100). */
  benchmark_sec_shared_weight: number | null;
}

/** Response for GET /api/analysis/industry-attribution/member-indices. */
export interface MemberIndexAttributionResponse {
  industry_id: string;
  industry_label: string;
  benchmark_code: string;
  benchmark_name: string;
  /** As-of date (latest available when no `date` was requested). */
  date: string;
  is_broad_market: boolean | null;
  indices: MemberIndexAttributionRow[];
}

// ----------------------------------------------------------------------------
//  Live Data — intraday 5-min bars (index + stock)
//  Source: stats.index_intraday_5min / stats.stock_intraday_5min
//  ETF is currently unsupported (no stats.etf_intraday_5min table) — the
//  frontend renders an empty placeholder for the ETF tab.
// ----------------------------------------------------------------------------
/** Security type for Live Data. ETF returns an empty payload. */
export type LiveDataSecType = "index" | "stock";

/** One 5-minute intraday OHLC bar. The `volume` column is only present on
 *  the stock table (NULL for indices — the SSE index endpoint does not
 *  publish per-bar volume). */
export interface LiveDataIntradayBar {
  time: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  volume: number | null;
  change: number | null;
  change_pct: number | null;
}

/** One code with its intraday bars for a single date. */
export interface LiveDataBundle {
  code: string;
  name: string;
  sector_id: string;
  sector_label: string;
  industry_id: string;
  industry_label: string;
  bars: LiveDataIntradayBar[];
}

/** Paginated response for the Live Data combined endpoint. */
export interface LiveDataCombinedResponse {
  type: LiveDataSecType;
  /** Trading day (YYYY-MM-DD) the bars belong to. Empty when no data exists. */
  date: string;
  sector_id: string;
  industry_id: string;
  codes: LiveDataBundle[];
  total_codes: number;
  total_pages: number;
  page: number;
  page_size: number;
}

/** Response for GET /api/live-data/dates?type=index — distinct trading days
 *  with at least one intraday bar, descending (most recent first). */
export interface LiveDataDatesResponse {
  type: LiveDataSecType;
  dates: string[];
}
