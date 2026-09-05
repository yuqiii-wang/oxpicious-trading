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

/** Lifecycle state of a zone wall (analysis.options_walls.state). */
export type OptionsWallState = "ACTIVE" | "ERODED" | "BREACHED";

/** One row of analysis.options_walls — wall_type='zone' only (the legacy
 *  80pct / large_num wall types were removed from the backend). Price
 *  columns wall_low/high/center are in RAW strike units; wall_strike is
 *  the legacy center / 10000. */
export interface OptionsWallRow {
  date: string;
  option_type: OptionType;
  underlying_code: string;
  expiry_date: string;
  wall_type: "zone";
  wall_strike: number | null;
  wall_oi: number | null;
  wall_low: number | null;
  wall_high: number | null;
  wall_center: number | null;
  /** zone OI / total chain OI (call+put), 0..1 */
  mass_share: number | null;
  /** signed away-from-spot distance of the center, % of spot */
  gap_pct: number | null;
  days_persisted: number | null;
  state: OptionsWallState | null;
  strength_score: number | null;
}

export interface OptionsWallsResponse {
  underlying_code: string;
  rows: OptionsWallRow[];
}

/** Data-source selector for analysis.options_skewness_stats rows. */
export type SkewType =
  | "oi_moneyness"
  | "iv_smile"
  | "greek_delta"
  | "greek_gamma"
  | "greek_vega";

export const SKEW_TYPES: SkewType[] = [
  "oi_moneyness",
  "iv_smile",
  "greek_delta",
  "greek_gamma",
  "greek_vega",
];

export interface SkewnessCorrRow {
  date: string;
  expiry_month: string;
  corr_skewness_ma5_vs_spot_ma5: number | null;
  corr_skewness_ma20_vs_spot_ma20: number | null;
  corr_skewness_ma60_vs_spot_ma60: number | null;
}

export interface SkewnessCorrResponse {
  underlying_code: string;
  rows: SkewnessCorrRow[];
}

export interface SkewnessCrossCountRow {
  date: string;
  expiry_month: string;
  count_skewness_curve_crossed_spot: number;
}

export interface SkewnessCrossCountResponse {
  underlying_code: string;
  rows: SkewnessCrossCountRow[];
}

/** Daily raw skewness series per (date, expiry month) from the DB. */
export interface SkewnessSeriesRow {
  date: string;
  expiry_month: string;
  /** Latest exact expiry date in the month group (shade boundary). */
  expiry_date: string | null;
  /** Daily raw skewness (CALL/PUT averaged). NULL when not stored. */
  skewness: number | null;
}

export interface SkewnessSeriesResponse {
  underlying_code: string;
  rows: SkewnessSeriesRow[];
}

export interface IvSkewRow {
  date: string;
  expiry_month: string;
  /** Latest exact expiry date in the month group (null when absent). */
  expiry_date: string | null;
  atm_iv: number | null;
  iv_call25: number | null;
  iv_put25: number | null;
  risk_reversal_25d: number | null;
  put_skew_25d: number | null;
  call_skew_25d: number | null;
  smile_skewness: number | null;
  rr25_ma5: number | null;
  rr25_ma20: number | null;
  rr25_ma60: number | null;
}

export interface IvSkewResponse {
  underlying_code: string;
  rows: IvSkewRow[];
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
  /** Implied earnings per share (yuan) = close / pe (harmonic-weighted
   *  constituent PE). Null when pe is null or close is null. */
  eps: number | null;
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
  items: Array<{ code: string; name: string; is_dummy?: boolean }>;
}

/** L1 sector node — top level of the two-level selector. */
export interface SectorNode {
  sector_id: string;
  sector_label: string;
  count: number;
  industries: IndustryNode[];
}

// ----------------------------------------------------------------------------
// Parallel strategy classification (RIGHT column of the two-column selector).
// A security carries BOTH an industry classification (sector → industry, LEFT
// column) AND a strategy classification (sector → industry, RIGHT column) —
// BOTH use the same (sector_id, industry_id) column pair on sec_classification.
// is_industry_not_strategy on sec_classification determines which is PRIMARY:
//   TRUE  → sector_id/industry_id hold the INDUSTRY classification
//           (FIN/BANKS, TECH/SEMI, …). These rows feed the LEFT column tree.
//   FALSE → sector_id/industry_id hold the STRATEGY classification
//           (BROAD/BROAD_CSI, DIV/DIV_SOE, …). These rows feed the RIGHT
//           column tree.
// There is NO separate strategy_id/theme_id column — strategy IS a sector and
// a theme IS an industry in the unified column model. The RIGHT column tree
// therefore has the SAME shape as the LEFT column tree (SectorNode), just
// filtered to is_industry_not_strategy=FALSE rows.
// ----------------------------------------------------------------------------

/** L2 industry node within a strategy (RIGHT column). Same shape as
 *  IndustryNode — strategy rows reuse the (sector_id, industry_id) columns. */
export type ThemeNode = IndustryNode;

/** L1 strategy node — top of the parallel strategy selector (RIGHT column).
 *  Same shape as SectorNode — the only difference is the row filter
 *  (is_industry_not_strategy=FALSE). */
export type StrategyNode = SectorNode;

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
  /** L1 classification id. When is_industry_not_strategy=TRUE: industry
   *  sector (FIN, TECH, …). When FALSE: strategy id (BROAD, DIV, …). */
  sector_id: string;
  sector_label: string;
  /** L2 classification id. When is_industry_not_strategy=TRUE: industry
   *  id (BANKS, SEMI, …). When FALSE: theme id (BROAD_CSI, DIV_SOE, …). */
  industry_id: string;
  industry_label: string;
  /** TRUE → sector_id/industry_id hold INDUSTRY (LEFT column).
   *  FALSE → they hold STRATEGY (RIGHT column). */
  is_industry_not_strategy: boolean;
  /** TRUE for synthetic industry dummy indices (no OHLC data). */
  is_dummy?: boolean;
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
  /** Calendar quarter of the chosen snapshot ("2026Q2"). Populated whenever
   *  holdings exist (also for the no-date latest-snapshot path). */
  quarter?: string;
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
// Quarterly composition (per-season industry aggregation) — used by the ETF
// Holdings analysis page's 100% stacked bar chart. One entry per calendar
// quarter that HAS a snapshot (no carry-forward: quarters without data are
// absent). Weights are aggregated per industry via sec_classification.
// ----------------------------------------------------------------------------
/** One industry's aggregated weight within a quarterly snapshot. */
export interface QuarterlyIndustryWeight {
  industry: string;
  /** Industry id from sec_classification (type='stock') — shared taxonomy
   *  with index classifications; '' when the industry is 未分类/OTHER.
   *  Used to drill into per-industry stats (industry_basic_stats etc.). */
  industry_id: string;
  sector_id: string;
  sector_label: string;
  /** Sum of raw weight_pct across the industry's holdings (≈% of NAV). */
  weight_pct: number;
  /** Number of holding rows in this industry. */
  n_holdings: number;
}

/** One quarter's composition (latest snapshot within the quarter). */
export interface QuarterlyCompositionQuarter {
  /** Quarter label, e.g. "2026Q2". */
  quarter: string;
  /** Snapshot date used for this quarter (latest within the quarter). */
  snapshot_date: string;
  /** Total holding rows in the snapshot. */
  n_holdings: number;
  /** Sum of raw weight_pct (≈100). */
  total_weight_pct: number;
  /** Industry-aggregated weights, sorted by weight desc. */
  industries: QuarterlyIndustryWeight[];
}

export interface QuarterlyCompositionResponse {
  code: string;
  /** Same semantics as SecCompositionResponse.source — "full" = ETF
   *  snapshots, "index" = tracking/raw index snapshots used as fallback. */
  source: "full" | "index";
  /** Populated only when `source === "index"`. */
  index_source?: {
    code: string;
    name: string;
  };
  /** Chronological quarters with data. */
  quarters: QuarterlyCompositionQuarter[];
}

// ----------------------------------------------------------------------------
// Industry weight series — ONE industry's weight in a security's composition
// across ALL snapshot dates (roughly monthly; denser than the quarterly view).
// Used by the ETF Holdings page's Industry-changes row drill-down: the right
// y-axis of the dual-axis plot (industry mean_close on the left axis).
// ----------------------------------------------------------------------------
/** One snapshot date's weight for the requested industry. */
export interface IndustryWeightSeriesPoint {
  /** Snapshot date (YYYY-MM-DD). */
  date: string;
  /** Sum of raw weight_pct across the industry's holdings (≈% of NAV). */
  weight_pct: number;
  /** Sum of raw weight_pct across ALL holdings in this snapshot (≈100). */
  total_weight_pct: number;
}

export interface IndustryWeightSeriesResponse {
  /** The requested ETF code (echoed back). */
  code: string;
  /** The requested industry_id (echoed back). */
  industry_id: string;
  /** Display label for the industry (from index classification; may be ''). */
  industry_label: string;
  /** Same fallback semantics as QuarterlyCompositionResponse.source. */
  source: "full" | "index";
  /** Populated only when `source === "index"`. */
  index_source?: {
    code: string;
    name: string;
  };
  /** Chronological snapshot points (snapshots without the industry are
   *  absent — no carry-forward). */
  points: IndustryWeightSeriesPoint[];
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
// Similar Indices — top-3 similar codes + top-3 similar/dissimilar
// industry-classified peer codes by mutual shared composition weight.
// Source: stats.sec_similars (built by builds.index._sec_similars).
// `date` is the index COMPOSITION snapshot_date (quarterly), looked up via
// latest-snapshot-<=today. Sharing weight is MUTUAL/symmetric:
//   (SUM(A.weight_pct) + SUM(B.weight_pct)) / 2 over shared constituents.
// All three categories store SEC CODES (index codes), not industry_ids.
// "industry" means the peer pool is filtered to is_industry_not_strategy=true.
// ----------------------------------------------------------------------------
export interface SimilarIndexRow {
  /** Rank 1 (most similar) .. 5. */
  rank: 1 | 2 | 3 | 4 | 5;
  /** Similar index code (e.g. "000906"). */
  code: string;
  /** Index display name from stats.sec_classification (type='index'). */
  name: string;
  /** Mutual sharing weight pct (0..100, may slightly exceed 100 due to
   *  source-data rounding). NULL when not computed. */
  sharing_weight_pct: number | null;
}

export interface SimilarIndicesResponse {
  /** The requested subject index code (echoed back, suffix-stripped). */
  index_code: string;
  /** Composition snapshot_date (YYYY-MM-DD) of the sec_similars row used.
   *  "" when the index has no composition snapshot / no similars. */
  snapshot_date: string;
  /** Up to 5 similar indices from ALL peers, rank 1..5. */
  similars: SimilarIndexRow[];
  /** Up to 5 similar indices from industry-classified peers only, rank 1..5. */
  similar_industries: SimilarIndexRow[];
  /** Up to 5 dissimilar indices from industry-classified peers only, rank 1..5. */
  dissimilar_industries: SimilarIndexRow[];
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
  /** Earnings per share (yuan) = close / pe. Null when pe is null/<=0 (loss-
   *  making / no PE recorded) or close is null. */
  eps: number | null;
  is_pe_estimated: boolean;
  has_intraday_5mins: boolean;
  /** Trading volume in shares (from stats.stock_liquidity_margin.trading_shares,
   *  converted from 成交量(万股) × 10000 by builds/stock). 0 when source row
   *  was PE-only (NULL OHLC). */
  trading_shares: number;
  /** Trading turnover in yuan (from stats.stock_liquidity_margin.trading_amount,
   *  converted from 成交金额(万元) × 10000 by builds/stock). 0 when source row
   *  was PE-only. */
  trading_amount: number;
  /** 融资余额 (cash borrow balance, yuan) from SZSE + SSE margin detail CSVs.
   *  Null when no margin data (most stocks have no margin activity most days). */
  rz_balance: number | null;
  /** 融资买入额 (cash borrow buy, yuan). Null when no margin data. */
  rz_buy: number | null;
  /** 融券余量 (sec borrow balance quantity, shares). Null when no margin data. */
  rq_balance_qty: number | null;
  /** 融券余额 (sec borrow balance amount, yuan). Null when no margin data. */
  rq_balance_amt: number | null;
  /** rz_balance + rq_balance_amt — total margin outstanding (yuan). */
  total_balance: number | null;
}

/** One dividend event (利润分配/分红) for a stock. Sourced from
 *  stats.stock_dividends (loaded by builds.stock.dividends from SSE
 *  {code}_dividend.csv files). The ex_dividend_date is the date the stock
 *  starts trading without the right to the dividend — it's the natural event
 *  marker date for the OHLC chart (price drops by ~ dividend_per_share_pre_tax
 *  on this day). */
export interface StockDividend {
  /** Ex-dividend date (除息交易日) — YYYY-MM-DD. Used as the event date on the
   *  OHLC chart's x-axis. */
  ex_dividend_date: string;
  /** Share registration date (股权登记日) — YYYY-MM-DD. Last day to buy the
   *  stock and still receive the dividend. NULL when the SSE API omits it. */
  record_date: string | null;
  /** Dividend per share, pre-tax (每股红利含税), in yuan. NULL when missing. */
  dividend_per_share_pre_tax: number | null;
  /** Dividend per share, post-tax (每股红利税后), in yuan. NULL when missing. */
  dividend_per_share_post_tax: number | null;
  /** Total dividend payout (分红总额), in 万元. NULL when missing. */
  total_dividend_wan: number | null;
  /** Closing price on the day BEFORE ex-dividend (除息前日收盘价), in yuan.
   *  NULL when missing. */
  pre_close_price: number | null;
  /** Ex-dividend opening quote (除息报价), in yuan. NULL when missing. */
  open_price: number | null;
}

export interface StockBaselineResponse {
  code: string;
  name: string;
  dates: string[];
  rows: StockBaselineRow[];
  /** Dividend events for this stock (all dates — not windowed). Empty when
   *  the stock has no dividend history (e.g. new listings, non-dividend-paying
   *  stocks). Sourced from stats.stock_dividends. */
  dividends: StockDividend[];
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
  /** Dividend events for this stock (all dates — not windowed). Empty when
   *  the stock has no dividend history. Sourced from stats.stock_dividends. */
  dividends: StockDividend[];
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
  /** MAX(date) from live.sec_alloc_live_attribution or stats.index_intraday_5min (raw) */
  intraday_movements: string;
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
   * Bollinger-style envelope (long_value ± k × long_std) on Price/MA and
   * Price/EMA charts. NULL until the rolling window is fully populated.
   * For SMA pairs, sourced from analysis.mov_ave_spreads_detail.std_*days;
   * for EMA pairs, from analysis.mov_ave_spreads_detail_ema.std_*days (same
   * source data — σ of price over W days). MA5/MA and EMA6/EMA charts also
   * carry this field but the envelope is only drawn around the long MA/EMA
   * on Price/MA and Price/EMA charts by convention.
   */
  long_std: number | null;
  /** Open price on this date (from basic_stats.open). */
  open: number | null;
  /** High price on this date (from basic_stats.high). */
  high: number | null;
  /** Low price on this date (from basic_stats.low). */
  low: number | null;
  /** Trading amount in yuan on this date (from basic_stats.trading_amount). */
  trading_amount: number | null;
  /**
   * The biz date of the most recent local turning point (high/low) detected
   * by price_slope sign change within the last 500 trading days (from
   * analysis.mov_ave_rsi). Carried forward from each turning point until the
   * next one. NULL when no turning point exists in the 500-trading-day
   * lookback window (early history before the first turn, or the most
   * recent extreme is older than 500 trading days) or when no mov_ave_rsi
   * row exists for this date.
   *
   * Shared across all 9 pairs for a given date — describes the price curve,
   * not a specific MA pair. The frontend plots a small green up-triangle
   * marker at each unique extreme date and surfaces this in the tooltip.
   */
  date_of_last_extreme_500days: string | null;
  /**
   * Signed fractional gap from the most recent local turning point within
   * the last 500 trading days: (price[t] - extreme_price) / extreme_price
   * (from analysis.mov_ave_rsi). Sign indicates the type of the last
   * extreme: positive = last extreme was a local MIN (price rebounded
   * upward), negative = last extreme was a local MAX (price fell). NULL
   * when no turning point exists in the 500-trading-day lookback window.
   */
  gap_since_last_extreme_500days: number | null;
  /**
   * Trading days since the most recent local turning point within the last
   * 500 trading days (from analysis.mov_ave_rsi). 0 on the extreme row
   * itself. NULL when no turning point exists in the 500-trading-day
   * lookback window.
   */
  days_since_last_extreme_500days: number | null;
  /**
   * Wilder Relative Strength Index over 6 trading days (alpha=1/6, ewm
   * adjust=False, min_periods=6). 0..100. NULL until 6 consecutive
   * gain/loss observations. From analysis.mov_ave_rsi. Surfaced in the
   * chart tooltip as part of the per-date RSI info.
   */
  rsi_6days: number | null;
  /** Wilder RSI over 10 trading days. 0..100. NULL until 10 periods. */
  rsi_10days: number | null;
  /** Wilder RSI over 14 trading days — the classic Wilder window. 0..100. */
  rsi_14days: number | null;
  /** Wilder RSI over 20 trading days. 0..100. */
  rsi_20days: number | null;
  /**
   * 5-trading-day moving average of trading_amount (yuan) from
   * analysis.mov_ave_spreads_detail.trading_amt_ma5. NULL until 5
   * consecutive rows. Used for the Trading Amt/MA envelope chart.
   */
  trading_amt_ma5: number | null;
  /** 20-trading-day MA of trading_amount (yuan). NULL until 20 rows. */
  trading_amt_ma20: number | null;
  /** 60-trading-day MA of trading_amount (yuan). NULL until 60 rows. */
  trading_amt_ma60: number | null;
  /** 120-trading-day MA of trading_amount (yuan). NULL until 120 rows. */
  trading_amt_ma120: number | null;
  /** 255-trading-day MA of trading_amount (yuan). NULL until 255 rows. */
  trading_amt_ma255: number | null;
  /**
   * Fractional daily change of trading_amt_ma5: (ma5[t] - ma5[t-1]) / ma5[t-1].
   * Signed ratio (e.g. 0.02 = +2%). NULL on first date or when ma is NULL/<=0.
   * Surfaced in the chart tooltip when trading-amt display is enabled.
   */
  trading_amt_ma5_slope: number | null;
  /** Fractional daily change of trading_amt_ma20 (see trading_amt_ma5_slope). */
  trading_amt_ma20_slope: number | null;
  /** Fractional daily change of trading_amt_ma60 (see trading_amt_ma5_slope). */
  trading_amt_ma60_slope: number | null;
  /** Fractional daily change of trading_amt_ma120 (see trading_amt_ma5_slope). */
  trading_amt_ma120_slope: number | null;
  /** Fractional daily change of trading_amt_ma255 (see trading_amt_ma5_slope). */
  trading_amt_ma255_slope: number | null;
  /**
   * 5-trading-day moving average of trading_amt_market_share (dimensionless
   * ratio 0..1). market_share = trading_amount / denominator, where
   * denominator = SUM(stats.exchange_trading_amt.total_trading_amount) across
   * primary exchanges. Surfaced in the chart tooltip as a percentage when
   * trading-amt display is enabled.
   */
  trading_amt_market_share_ma5: number | null;
  /** 20-trading-day MA of trading_amt_market_share (see trading_amt_market_share_ma5). */
  trading_amt_market_share_ma20: number | null;
  /** 60-trading-day MA of trading_amt_market_share (see trading_amt_market_share_ma5). */
  trading_amt_market_share_ma60: number | null;
  /** 120-trading-day MA of trading_amt_market_share (see trading_amt_market_share_ma5). */
  trading_amt_market_share_ma120: number | null;
  /** 255-trading-day MA of trading_amt_market_share (see trading_amt_market_share_ma5). */
  trading_amt_market_share_ma255: number | null;

  /**
   * 5-trading-day rolling population σ (ddof=0) of trading_amt_ma5
   *  (yuan). Bollinger band width for trading-amount MA5 envelope.
   *  Used with long_std on Amt/MA pair rows to draw Bollinger bands.
   */
  trading_amt_std5: number | null;
  /** 20-trading-day σ of trading_amt_ma20 (see trading_amt_std5). */
  trading_amt_std20: number | null;
  /** 60-trading-day σ of trading_amt_ma60 (see trading_amt_std5). */
  trading_amt_std60: number | null;
  /** 120-trading-day σ of trading_amt_ma120 (see trading_amt_std5). */
  trading_amt_std120: number | null;
  /** 255-trading-day σ of trading_amt_ma255 (see trading_amt_std5). */
  trading_amt_std255: number | null;

  // Rolling OHLC columns from analysis.mov_ave_spreads_detail_ohlc.
  // These show the Open, High, Low for the selected MA's window.
  // E.g., when MA60 is selected, open_60d shows the open 60 days ago,
  // high_60d shows the max high over the last 60 days, etc.
  open_20d: number | null;
  high_20d: number | null;
  low_20d: number | null;
  open_60d: number | null;
  high_60d: number | null;
  low_60d: number | null;
  open_120d: number | null;
  high_120d: number | null;
  low_120d: number | null;
  open_255d: number | null;
  high_255d: number | null;
  low_255d: number | null;
  open_500d: number | null;
  high_500d: number | null;
  low_500d: number | null;
  open_750d: number | null;
  high_750d: number | null;
  low_750d: number | null;
}

/**
 * Rolling-window OHLC extrema for one date — one row per date, index-aligned
 * with every pair's rows (all pairs share one date axis, so a single copy
 * serves all 23 pair series). Source: analysis.mov_ave_spreads_detail_ohlc
 * — the table is LONG format (one row per (code, sec_type, date, period)
 * with generic *_over_period columns); the API joins it once per period and
 * aliases the columns back to the per-window names below, so the response
 * shape is unchanged.
 *
 * For each window W ∈ {20, 60, 120, 255, 500, 750, 1275} (the table's
 * `period`):
 *   open_Wd           — open price on the W-th trading day before `date`
 *   high_Wd           — top-high anchor: highest CLOSE among window dates
 *                       more than 20%·W before `date`
 *   high_date_Wd      — date of the top-high anchor
 *   high_2nd_Wd       — second local-max peak (≥ 20%·W cooldown after the
 *                       top; candidates scored from today backwards with
 *                       exponential recency decay, half-life = the window,
 *                       so the nearer-today peak wins unless the far one is
 *                       proportionally higher; value = INTRADAY high)
 *   high_2nd_date_Wd  — date of the second peak
 *   low_Wd            — lowest-low anchor: lowest CLOSE among window dates
 *                       more than 20%·W before `date`
 *   low_date_Wd       — date of the lowest-low anchor
 *   low_2nd_Wd        — second local-min trough (≥ 20%·W cooldown after the
 *                       bottom; recency-decay scored from today backwards,
 *                       so a far trough must be proportionally lower to beat
 *                       a nearer one; value = INTRADAY low)
 *   low_2nd_date_Wd   — date of the second trough
 *
 * The (top, 2nd) high pairs determine the "roof" trendline and the (top,
 * 2nd) low pairs determine the "floor" trendline drawn on the MA-Spread
 * chart (two points determining a line).
 */
export interface MovAveSpreadOhlcRow {
  date: string;

  open_20d: number | null;
  high_20d: number | null;
  high_date_20d: string | null;
  high_2nd_20d: number | null;
  high_2nd_date_20d: string | null;
  high_line_slope_20d: number | null;
  low_20d: number | null;
  low_date_20d: string | null;
  low_2nd_20d: number | null;
  low_2nd_date_20d: string | null;
  low_line_slope_20d: number | null;

  open_60d: number | null;
  high_60d: number | null;
  high_date_60d: string | null;
  high_2nd_60d: number | null;
  high_2nd_date_60d: string | null;
  high_line_slope_60d: number | null;
  low_60d: number | null;
  low_date_60d: string | null;
  low_2nd_60d: number | null;
  low_2nd_date_60d: string | null;
  low_line_slope_60d: number | null;

  open_120d: number | null;
  high_120d: number | null;
  high_date_120d: string | null;
  high_2nd_120d: number | null;
  high_2nd_date_120d: string | null;
  high_line_slope_120d: number | null;
  low_120d: number | null;
  low_date_120d: string | null;
  low_2nd_120d: number | null;
  low_2nd_date_120d: string | null;
  low_line_slope_120d: number | null;

  open_255d: number | null;
  high_255d: number | null;
  high_date_255d: string | null;
  high_2nd_255d: number | null;
  high_2nd_date_255d: string | null;
  high_line_slope_255d: number | null;
  low_255d: number | null;
  low_date_255d: string | null;
  low_2nd_255d: number | null;
  low_2nd_date_255d: string | null;
  low_line_slope_255d: number | null;

  open_500d: number | null;
  high_500d: number | null;
  high_date_500d: string | null;
  high_2nd_500d: number | null;
  high_2nd_date_500d: string | null;
  high_line_slope_500d: number | null;
  low_500d: number | null;
  low_date_500d: string | null;
  low_2nd_500d: number | null;
  low_2nd_date_500d: string | null;
  low_line_slope_500d: number | null;

  open_750d: number | null;
  high_750d: number | null;
  high_date_750d: string | null;
  high_2nd_750d: number | null;
  high_2nd_date_750d: string | null;
  high_line_slope_750d: number | null;
  low_750d: number | null;
  low_date_750d: string | null;
  low_2nd_750d: number | null;
  low_2nd_date_750d: string | null;
  low_line_slope_750d: number | null;

  open_1275d: number | null;
  high_1275d: number | null;
  high_date_1275d: string | null;
  high_2nd_1275d: number | null;
  high_2nd_date_1275d: string | null;
  high_line_slope_1275d: number | null;
  low_1275d: number | null;
  low_date_1275d: string | null;
  low_2nd_1275d: number | null;
  low_2nd_date_1275d: string | null;
  low_line_slope_1275d: number | null;
}

/** Kind of pair: price-based (Simple MA, default, backward-compatible),
 *  amt-based (trading-amount), or ema-based (Exponential MA). */
export type MovAveSpreadPairKind = "price" | "amt" | "ema";

export interface MovAveSpreadPairSeries {
  ma_short: number;
  ma_long: number;
  /** Display label, e.g. "Price/MA5" or "MA5/MA20" or "Amt/MA20". */
  pair_label: string;
  /**
   * "price" = the 9 original Simple MA pairs (short=price or ma5, long=maW).
   * "amt" = the 5 trading-amount pairs (short=trading_amount,
   *        long=trading_amt_maW). When an amt pair is selected, the chart
   *        switches to "amt envelope" mode: OHLC + price MAs are shown
   *        lowkey (dimmed), and the trading amount + all 5 trading_amt_ma
   *        lines form a prominent envelope on the secondary y-axis.
   * "ema" = the 9 Exponential MA pairs (short=price or ema6, long=emaW).
   *        Rendered like price pairs but using EMA values from
   *        analysis.mov_ave_spreads_detail_ema. No Bollinger envelope
   *        (EMA detail table has no σ columns).
   * Defaults to "price" for backward compatibility.
   */
  kind?: MovAveSpreadPairKind;
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

/** One market-hype EPISODE from analysis.mov_ave_market_hypes: a
 *  CONCATENATED span of trading dates anchored on a maximal run of
 *  consecutive hyped dates and extended through the surrounding
 *  check-in evidence (the W rows before the run's first hyped date,
 *  back to its first check-in, and the W rows after the last hyped
 *  date, to its last check-in). startDate / endDate bracket the span;
 *  hypeDays is the span length in trading dates, BUCKETED into
 *  [minCheckinPeriod, next window) — minCheckinPeriod is the bucket's
 *  MINIMUM span, the next check-in window (5100 = the whole ±10y base
 *  for 255d) its exclusive maximum, so one calendar turmoil lands in
 *  exactly the bucket matching its length. A date is hyped when,
 *  within the last W (min_checkin_period) trading rows ending at it, more
 *  than min_checkin_satisfaction_threshold percent of the dates are
 *  check-ins (trading_amount AND std_{W}days both above their centered
 *  20-year — ±10 trading years around each audited date — percentile
 *  thresholds). */
export interface MovAveSpreadHypeEpisode {
  startDate: string;
  endDate: string;
  hypeDays: number;
  /** Days within the episode span on which the liquidity leg
   *  (trading_amount > its centered-20y percentile) individually
   *  checked in. Optional: absent on rows built before the column
   *  existed. */
  tradingAmtHypeDays?: number;
  /** Days within the episode span on which the volatility leg
   *  (std_{W}days > its centered-20y percentile) individually checked
   *  in. Optional: absent on rows built before the column existed. */
  stdHypeDays?: number;
}

/** Market-hype episodes keyed by check-in window (5/20/60/120/255) — one
 *  episode list per window; windows with no episodes are absent from the
 *  map. Source: analysis.mov_ave_market_hypes. */
export type MovAveSpreadHypeEpisodes = Record<number, MovAveSpreadHypeEpisode[]>;

/** One band-BREAK excursion streak from
 *  analysis.mov_ave_high_low_pct_streaks: a maximal run of trading days
 *  whose adjusted close stayed outside the (period, pctType) high/low
 *  percentile band (close > high_val = high leg, close < low_val = low
 *  leg), with in-band gaps of up to 5 trading days bridged inside one
 *  streak (a 6-day gap ends it). date spans are shipped flat for ALL
 *  (period, pctType) combos — the client filters by its nested
 *  period→pct selection. */
export interface MovAveSpreadHighLowStreak {
  /** Band lookback window in trading rows (255/500/750/1275). */
  period: number;
  /** Band tightness in percent (1/5/10) — the band's pct_type. */
  pctType: number;
  /** First OUT-OF-BAND day of the streak. */
  startDate: string;
  /** Last OUT-OF-BAND day of the streak (bridged in-band days may lie
   *  inside the span). */
  endDate: string;
  /** Which band leg the streak broke, derived at query time by comparing
   *  the streak's end close with its END month's band row — a streak
   *  never switches sides, so the end day's own-month band decides. */
  side: "high" | "low";
  /** Open price on start_date. */
  open: number;
  /** Close price on end_date. */
  close: number;
  /** Max adjusted high over the streak span. */
  high: number;
  /** Min adjusted low over the streak span. */
  low: number;
  /** End-month band's high_val (top edge) — a high streak shades from
   *  here UP to `high`. */
  bandHigh: number;
  /** End-month band's low_val (bottom edge) — a low streak shades from
   *  here DOWN to `low`. */
  bandLow: number;
  /** Trading-row count of the streak span (incl. bridged in-band days). */
  dayCount: number;
  /** Std-dev of daily price changes in the streak (price units). */
  stdDev: number;
  /** Average trading amount over the streak span (0.00 = no amt data). */
  dailyAveTradingAmt: number;
}

/** Response for GET /chart?sec_type=etf&code=510050 — all pair time series for one asset. */
export interface MovAveSpreadChartResponse {
  code: string;
  name: string;
  /** Pair time series (9 Simple MA + 9 EMA + 5 trading-amt = 23 total). */
  pairs: MovAveSpreadPairSeries[];
  /** Rolling-window OHLC extrema, one row per date, index-aligned with every
   *  pair's rows (all pairs share one date axis). Source:
   *  analysis.mov_ave_spreads_detail_ohlc — used by the OHLC-window
   *  roof/floor trendline overlay. */
  ohlc: MovAveSpreadOhlcRow[];
  /** Market-hype episodes keyed by check-in window (20/60/120/255), shared
   *  by all pairs (same date axis). Source: analysis.mov_ave_market_hypes —
   *  used by the Market Hype button row to shade hyped date periods (light
   *  purple). Optional so older cached responses without the field still
   *  typecheck. */
  hypeEpisodes?: MovAveSpreadHypeEpisodes;
  /** High/low band-BREAK excursion streaks shipped FLAT for ALL
   *  (period, pctType) combos — the client filters by its nested
   *  period→pct selection (source: analysis.mov_ave_high_low_pct_streaks,
   *  side derived at query time). Optional so older cached responses
   *  without the field still typecheck. */
  highLowStreaks?: MovAveSpreadHighLowStreak[];
}

// ----------------------------------------------------------------------------
//  Analysis Commons — Forecast buckets (analysis_forecasts schema)
//    mov_rsi / mov_std / mov_gap / px_vol — bucket-definition (motivation)
//      tables, each row linking 1:1 via forecast_id to its forecast_results
//      rows (normalized: 4 period rows next/5d/20d/60d).
//    Served by GET /api/analysis/mov-ave-spread/forecast — the MA-Spread
//    panel's second plot: a config→result table beneath the spread chart.
// ----------------------------------------------------------------------------

/** Which bucket family the forecast table shows. */
export type ForecastKind = "mov_rsi" | "mov_std" | "mov_gap" | "px_vol" | "margin_ratio";

/** The forecast_results numeric columns: mean + std-dev of the forward
 *  fractional changes at all 4 horizons; close-based max/min forward
 *  changes + the best-to-worst n-day outcome ratio
 *  (max_low_change_ratio — not a within-window path swing) at the
 *  5d/20d/60d horizons only; per-horizon P(>1% reversal) and occurrence
 *  counts. */
export interface ForecastResultCols {
  ave_next_change: number | null;
  ave_next_5d_change: number | null;
  ave_next_20d_change: number | null;
  ave_next_60d_change: number | null;
  /** Std-dev of the n-day forward fractional change over the same
   *  bucket days as the ave (NULL for pre-std_change rows). */
  std_next_change: number | null;
  std_next_5d_change: number | null;
  std_next_20d_change: number | null;
  std_next_60d_change: number | null;
  max_5d_change: number | null;
  max_20d_change: number | null;
  max_60d_change: number | null;
  min_5d_change: number | null;
  min_20d_change: number | null;
  min_60d_change: number | null;
  max_low_change_ratio_5d: number | null;
  max_low_change_ratio_20d: number | null;
  max_low_change_ratio_60d: number | null;
  reverse_prob: number | null;
  reverse_prob_5d: number | null;
  reverse_prob_20d: number | null;
  reverse_prob_60d: number | null;
  /** Bucket days with a valid {n}-day forward change — the mean/prob
   *  denominator per horizon. NULL for pre-migration rows. */
  occurrence_count_next: number | null;
  occurrence_count_5d: number | null;
  occurrence_count_20d: number | null;
  occurrence_count_60d: number | null;
}

/** One mov_rsi bucket row (RSI extreme-percentile bucket) + its results.
 *  Bucket key: (stat_month, rsi_window, side, pct, cooldown_days,
 *  is_market_hyped). */
export interface MovRsiForecastRow extends ForecastResultCols {
  stat_month: string;
  rsi_window: number;
  side: "top" | "bottom";
  pct: number;
  cooldown_days: number;
  is_market_hyped: boolean;
  /** TRUE when the bucket already has signal day(s) in
   *  analysis_signals.signals (config match + date inside stat_month). */
  in_signals: boolean;
}

/** One mov_std bucket row (Bollinger-breach bucket) + its results.
 *  Bucket key: (stat_month, ma_window, k, side, cooldown_days,
 *  is_market_hyped). */
export interface MovStdForecastRow extends ForecastResultCols {
  stat_month: string;
  ma_window: number;
  k: number;
  side: "upper" | "lower";
  cooldown_days: number;
  is_market_hyped: boolean;
  /** TRUE when the bucket already has signal day(s) in
   *  analysis_signals.signals (config match + date inside stat_month). */
  in_signals: boolean;
  /** Mean fractional close excursion beyond the band over breach days. */
  mean_excess_close: number | null;
  /** Mean fractional intraday excursion (high for upper / low for lower)
   *  over breach days with a usable extreme; NULL when none. */
  mean_excess_max: number | null;
  /** Max fractional intraday excursion (deepest single-day spike). */
  max_excess_max: number | null;
}

/** One mov_gap bucket row (N-day price-return extreme-percentile bucket)
 *  + its results. Bucket key: (stat_month, gap_window, side, pct,
 *  cooldown_days, is_market_hyped). */
export interface MovGapForecastRow extends ForecastResultCols {
  stat_month: string;
  gap_window: number;
  side: "top" | "bottom";
  pct: number;
  cooldown_days: number;
  is_market_hyped: boolean;
  /** TRUE when the bucket already has signal day(s) in
   *  analysis_signals.signals (config match + date inside stat_month). */
  in_signals: boolean;
}

/** One px_vol bucket row (σ-standardized price-speed × z-scored 量比
 *  state cell) + its results. Bucket key: (stat_month, px_speed,
 *  vol_state, is_market_hyped). State cells — NO cooldown (every
 *  qualifying day joins). */
export interface PxVolForecastRow extends ForecastResultCols {
  stat_month: string;
  /** σ-standardized 1-day price change state: sharp_up / slow_up / flat /
   *  slow_dn / sharp_dn (t = ret_1d / rolling-255 σ_ret of the code). */
  px_speed: "sharp_up" | "slow_up" | "flat" | "slow_dn" | "sharp_dn";
  /** Trading-amount state: heavy / normal / shrink (z-scored 量比). */
  vol_state: "heavy" | "normal" | "shrink";
  /** Reversal side of reverse_prob: top (up speeds) / bottom (down
   *  speeds) / flat (no directional claim — reverse_prob NULL). */
  side: "top" | "bottom" | "flat";
  is_market_hyped: boolean;
  /** TRUE when the bucket already has signal day(s) in
   *  analysis_signals.signals (px_speed + vol_state + side match + date
   *  inside stat_month). */
  in_signals: boolean;
  /** Mean σ-standardized price speed t over the bucket's days (from the
   *  linked forecast_results.config JSONB — descriptive magnitude). */
  mean_t: number | null;
  /** Mean z-scored 量比 over the bucket's days (same config JSONB). */
  mean_z: number | null;
}

/** One margin_ratio bucket row (margin-buy intensity z-score state —
 *  融资买入额/成交额 ratio vs the code's own trailing distribution) + its
 *  results. Bucket key: (stat_month, ratio_state, is_market_hyped).
 *  State cells — NO cooldown (every qualifying day joins). ETF + Stock
 *  only (index has no margin data). */
export interface MarginRatioForecastRow extends ForecastResultCols {
  stat_month: string;
  /** Margin-intensity state: no_buy (rz_buy <= 0 that day) / vlow
   *  (z <= -2) / low (-2,-1] / mid (-1,+1] / high (+1,+2] / vhigh
   *  (z > +2) of the code's rolling-1220-row (min 250) ratio moments
   *  shifted 1 row. */
  ratio_state: "no_buy" | "vlow" | "low" | "mid" | "high" | "vhigh";
  /** Reversal side of reverse_prob: top (high/vhigh crowding — the
   *  study's bearish reading) / bottom (vlow/low/no_buy) / flat (mid —
   *  reverse_prob NULL). */
  side: "top" | "bottom" | "flat";
  is_market_hyped: boolean;
  /** TRUE when the bucket already has signal day(s) in
   *  analysis_signals.signals (ratio_state + side match + date inside
   *  stat_month). */
  in_signals: boolean;
  /** Mean raw ratio (rz_buy / trading_amount, fraction) over the
   *  bucket's days (from the linked forecast_results.config JSONB —
   *  NULL for no_buy). */
  mean_ratio: number | null;
  /** Mean z-score over the bucket's days (same config JSONB — NULL for
   *  no_buy). */
  mean_z: number | null;
}

/** Response for GET /forecast?sec_type=&code=&kind=[&month=] — the code's
 *  buckets of the requested kind joined 1:1 with their forecast_results
 *  columns. `month` is a START month: rows cover every stat_month >= month
 *  (only the latest stat_month when month is omitted). `months` lists every
 *  stat_month available for the code (DESC) for the UI's start-month
 *  selector. */
export interface ForecastResponse {
  kind: ForecastKind;
  code: string;
  sec_type: string;
  /** All distinct stat_months (YYYY-MM-DD) with rows for this code, DESC. */
  months: string[];
  rows: MovRsiForecastRow[] | MovStdForecastRow[] | MovGapForecastRow[] | PxVolForecastRow[] | MarginRatioForecastRow[];
  /** Backend arg for the shared ExpandedTable: whether the table renders
   *  its per-column header filters. Default false (filters disabled). */
  enable_filters: boolean;
}

// ----------------------------------------------------------------------------
//  Analysis Commons — PE & Dividend Yield (per-(sec_type, code, date) valuation)
//    analysis.pe_and_dividends          — daily pe_ma20 + dividend_yield
//    analysis.pe_and_dividend_stats     — monthly 5y rolling stats snapshot
//    PK (detail): (sec_type, code, date)
//    PK (stats):  (sec_type, code, date, is_active)  [date = month-end]
//
//    Close price and raw PE ratio are NOT stored in analysis.pe_and_dividends
//    (they live in stats: index_basic_stats.close, index_valuation.pe,
//    etf_basic_stats.close, stock_basic_stats.close). The chart endpoint JOINs
//    stats live at request time so the UI always shows the freshest close/PE.
// ----------------------------------------------------------------------------
export type PeAndDividendSecType = "etf" | "index" | "stock";

/** One daily row from analysis.pe_and_dividends JOINed with stats for close + pe. */
export interface PeAndDividendChartRow {
  /** Trading date (YYYY-MM-DD). */
  date: string;
  /** Close price from stats (index_basic_stats.close / etf adj_close /
   *  stock_basic_stats.close). NULL when the source has no close on this date. */
  close: number | null;
  /** Raw PE ratio from stats.index_valuation.pe (index-only; NULL for etf/stock). */
  pe: number | null;
  /** 20-day MA of PE (index-only, from analysis.pe_and_dividends.pe_ma20). */
  pe_ma20: number | null;
  /** Trailing-12m dividend yield (D/P) as a fractional ratio (0.035 = 3.5%). */
  dividend_yield: number | null;
}

/** Response for GET /api/analysis/pe-and-dividend/chart. */
export interface PeAndDividendChartResponse {
  code: string;
  name: string;
  rows: PeAndDividendChartRow[];
}

/** One monthly snapshot row from analysis.pe_and_dividend_stats. */
export interface PeAndDividendStatsRow {
  /** Month-end trading date (YYYY-MM-DD). */
  date: string;
  /** TRUE for the most recent monthly snapshot per (sec_type, code). */
  is_active: boolean;
  /** Rolling 5y min/max of PE (index-only; NULL for etf/stock). */
  min_pe_5y: number | null;
  max_pe_5y: number | null;
  /** Rolling 5y population std (ddof=0) of dividend_yield, x100 as a
   *  percentage (e.g. 0.5 = 0.5%). NULL when < 2 values in the window. */
  dividend_var_5y: number | null;
  /** Frequency-robust stability score (0-100) of per-share dividend AMOUNT
   *  over trailing 5 calendar years (annualized per year so payment-frequency
   *  changes don't create artificial gaps). 100 = perfectly stable. */
  dividend_stability_5y: number | null;
  /** Rolling record of the latest single dividend_per_share_pre_tax as of
   *  the month-end date (stock/etf own events; NULL for index). */
  last_dividend_per_share: number | null;
  /** TRUE if at least one ex_dividend_date falls in the same (year, month)
   *  as the month-end date. Drives bold styling on the Last Div cell. */
  dividend_issued_this_month: boolean;
}

/** Response for GET /api/analysis/pe-and-dividend/stats. */
export interface PeAndDividendStatsResponse {
  code: string;
  name: string;
  rows: PeAndDividendStatsRow[];
}

// ----------------------------------------------------------------------------
//  PE & Dividend band-BREAK excursion streaks (analysis.pe_and_dividend_pct
//  + analysis.pe_and_dividend_pct_streaks) — the high/low streaks pattern of
//  analysis.mov_ave_high_low_pct[_streaks] applied to the pe_ma20 /
//  dividend_yield series. A day breaks out when its metric value is ABOVE
//  its own month-band high_val (high — the metric is stretched vs its own
//  trailing history) or BELOW low_val (low — compressed); a streak is the
//  maximal run of same-side break days with up to 5 consecutive in-band
//  trading days bridged (a 6-day gap or a side switch ends it).
// ----------------------------------------------------------------------------

/** The audited valuation series (`metric` column) — the two value columns
 *  of analysis.pe_and_dividends. */
export type PeAndDividendStreakMetric = "pe_ma20" | "dividend_yield";

/** One band-BREAK excursion streak from
 *  analysis.pe_and_dividend_pct_streaks. Shipped flat for ALL (metric,
 *  period, pctType) combos — the client filters by its nested selection. */
export interface PeAndDividendStreak {
  /** Which valuation series was audited. */
  metric: PeAndDividendStreakMetric;
  /** Band lookback window in observations (255/500/750/1275). */
  period: number;
  /** Band tightness in percent (1/5/10) — the band's pct_type. */
  pctType: number;
  /** First OUT-OF-BAND day of the streak. */
  startDate: string;
  /** Last OUT-OF-BAND day of the streak (bridged in-band days may lie
   *  inside the span). */
  endDate: string;
  /** Which band leg the streak broke, derived at query time by comparing
   *  the streak's end value with its END month's band row — a streak
   *  never switches sides, so the end day's own-month band decides. */
  side: "high" | "low";
  /** Metric value on start_date. */
  startValue: number;
  /** Metric value on end_date. */
  endValue: number;
  /** Max metric value over the streak span (high streaks: the stretch's
   *  peak). */
  maxValue: number;
  /** Min metric value over the streak span. */
  minValue: number;
  /** End-month band's high_val (top edge). */
  bandHigh: number;
  /** End-month band's low_val (bottom edge). */
  bandLow: number;
  /** Trading-row count of the streak span (incl. bridged in-band days). */
  dayCount: number;
  /** Population std of day-over-day value changes in the span (the
   *  metric's own units). */
  stdDev: number;
}

/** Response for GET /api/analysis/pe-and-dividend/streaks. */
export interface PeAndDividendStreaksResponse {
  code: string;
  name: string;
  /** Flat per-streak array, ascending by metric, period, pctType,
   *  startDate. */
  streaks: PeAndDividendStreak[];
}

/** One row in the codes list (analysis.pe_and_dividends DISTINCT ON code). */
export interface PeAndDividendCodeRow {
  code: string;
  name: string;
  first_date: string;
  last_date: string;
  n_dates: number;
  /** Latest snapshot's pe_ma20 (NULL for etf/stock). */
  latest_pe_ma20: number | null;
  /** Latest snapshot's dividend_yield (fractional ratio). */
  latest_dividend_yield: number | null;
}

/** Response for GET /api/analysis/pe-and-dividend/codes. */
export interface PeAndDividendCodesResponse {
  codes: PeAndDividendCodeRow[];
}

// ----------------------------------------------------------------------------
//  Analysis Commons — Recurring Cycles (recurring rise/drop periodicity)
//    analysis.recurring_cycles — per-(sec_type, code, last_date, range_days)
//    recurring rise/drop periodicity: every integer day period d (2..N/2)
//    audited for RECURRENCE in the time domain (extrema evidence × ACF
//    coherence, amplitude-gated). Headline period_days = argmax of strength.
//    Currently populated for sec_type='index' only.
export type RecurringCyclesSecType = "index";

/** One (last_date, range_days) row from analysis.recurring_cycles. */
export interface RecurringCyclesChartRow {
  /** Last trading date of the window (YYYY-MM-DD). */
  last_date: string;
  /** Window size in trading days (20 | 60 | 255 | 500 | 750 | 1275). */
  range_days: number;
  /** Recurring rise/drop period in trading days (argmax of strength);
   *  0 = no recurring period detected. */
  period_days: number;
  /** strength(d*) at period_days d* (0 when period_days = 0). */
  strength: number;
}

/** Response for GET /api/analysis/recurring-cycles/chart. */
export interface RecurringCyclesChartResponse {
  code: string;
  name: string;
  rows: RecurringCyclesChartRow[];
}

/** One (range_days) row from the spectrum endpoint — the per-day recurring
 *  periodicity factors for a single (code, last_date) and window size. */
export interface RecurringCyclesSpectrumRow {
  /** Window size in trading days (20 | 60 | 255 | 500 | 750 | 1275). */
  range_days: number;
  /** Recurring rise/drop period in trading days (argmax of strength + 2
   *  day offset); 0 = no recurring period detected. */
  period_days: number;
  /** strength(d*) at period_days d* (0 when period_days = 0). */
  strength: number;
  /** count(d*) at period_days d* — the raw recurrence evidence. */
  count_factor: number;
  /** amplitude(d*) at period_days d* — energy-merged FFT amplitude (yuan). */
  amplitude: number;
  /** Poisson audit at period_days d*: −log10 of the Bonferroni-adjusted
   *  tail p-value P(Poisson(λ̂₀) ≥ hits) — how far the observed
   *  swing-hit count exceeds the empirically calibrated chance
   *  expectation λ̂₀. 0 = not significant; ≥ 1.30 ⇔ p < 0.05;
   *  ≥ 2.0 ⇔ p < 0.01. 0 when period_days = 0. */
  significance: number;
  /** hits(d*)/λ̂₀(d*) — observed prominence-filtered swing-hit count over
   *  the Poisson null expectation at the headline period. */
  evidence_ratio: number;
  /** Per-day energy-merged FFT amplitude (yuan), DAY-ALIGNED: element j =
   *  day period d = j + 2; length = floor(range_days/2) − 1 (days 2..N/2).
   *  The Fourier REFERENCE for the amp bars — NOT recurrence evidence. */
  amplitude_spectrum: number[];
  /** Per-day recurrence COUNT factor, day-aligned like amplitude_spectrum:
   *  count(d) = extrema evidence × ACF coherence (prominence-filtered
   *  alternating-extrema hits over max possible cycles, capped 1; ×
   *  fraction of multiples m·d with biased acf ≥ 1.96/√N after MA
   *  detrending). Says WHETHER price actually repeated that spacing. */
  count_spectrum: number[];
  /** Per-day summarized recurring STRENGTH, day-aligned:
   *  strength(d) = (amp(d)/σ_band) × count(d), 0 for d > N/3 (under 3
   *  cycles in the window). period_days = argmax + 2. */
  strength_spectrum: number[];
  /** Per-day Poisson-audit significance, day-aligned like
   *  strength_spectrum: −log10 of the Bonferroni-adjusted tail p-value
   *  P(Poisson(λ̂₀(d)) ≥ hits(d)) vs the calibrated chance hit rate;
   *  0 where not auditable (d > N/3) or not significant (p ≥ 0.05);
   *  capped at 300. Empty when the row predates the audit. */
  significance_spectrum: number[];
  /** Per-day OBSERVED prominence-filtered swing-hit count (integral
   *  values), day-aligned like significance_spectrum: element j = day
   *  period d = j + 2. The raw recEXT numerator (uncapped) — the
   *  observed side of the Poisson audit table (hits vs λ̂₀ vs p).
   *  Empty when the row predates the audit. */
  hits_spectrum: number[];
  /** Per-day chance expectation λ̂₀(d) of the point-process null,
   *  day-aligned like hits_spectrum: the empirically calibrated
   *  expected hit rate n_pool × g(pool-bin, d) the observed hits are
   *  tested against. Empty when the row predates the audit. */
  lam0_spectrum: number[];
  /** Number of sliding windows (dates) analyzed for this (code,
   *  range_days). Title context only. */
  total_windows: number;
}

/** Response for GET /api/analysis/recurring-cycles/spectrum.
 *  Up to 6 rows (one per range_days) for one (code, last_date). */
export interface RecurringCyclesSpectrumResponse {
  code: string;
  name: string;
  /** The last_date these spectra are for. When the request omitted
   *  last_date, this is the latest available date for the code. */
  last_date: string;
  spectrums: RecurringCyclesSpectrumRow[];
  /** Backend arg for the shared ExpandedTable: whether the audit table
   *  renders its per-column header filters. Default false (filters
   *  disabled). */
  enable_filters: boolean;
}

// ----------------------------------------------------------------------------
//  Analysis Derivatives — Margin Trends (single-industry RONGZI margin flows)
//    analysis.margin_index_series (TABLE) — weighted-avg constituent-stock
//                                           margin per (index_code, date),
//                                           built by Python vectorization
//    analysis.margin_changes             — trend episodes (shade overlay)
//
//  RONGZI (融资 / cash-borrow) only — RONQIN (融券 / sec borrow) EXCLUDED.
//  Two series: margin_balance (rz_balance, yuan, STOCK) and margin_buy
//  (rz_buy, yuan, FLOW). Attribution: 'index' (weighted-avg stock margin
//  via the TABLE) or 'etf' (the ETF's own margin from etf_liquidity_margin).
//
//  Single-industry page layout (1 plot):
//    1. Margin trends — one line per security (indices or ETFs) in the
//       industry; toggle Balance | Buy. Trend-episode shades + per-episode
//       rz_buy_vs_trading_amt_ratio (on the Buy chart).
// ----------------------------------------------------------------------------
export type MarginAttributionType = "index" | "etf";

/** One security (index or ETF code) available in an industry for plotting. */
export interface MarginSecurity {
  /** Bare 6-digit index code or ETF code with exchange suffix. */
  code: string;
  /** Display name from stats.sec_classification.name. */
  label: string;
}

/** One daily margin data point for one security. */
export interface MarginSeriesRow {
  code: string;
  date: string;
  /** RONGZI outstanding balance (融资余额, yuan). */
  balance: number | null;
  /** RONGZI buy amount (融资买入额, yuan, FLOW). */
  buy: number | null;
  /** Underlying security close price (from index/etf_basic_stats). */
  close: number | null;
}

/** Response for GET /api/analysis/margin-trends/industry-series.
 *  Per-security daily margin series for ONE industry + ONE attribution.
 *  attribution='index' reads analysis.margin_index_series (weighted-avg
 *  constituent-stock margin); attribution='etf' reads
 *  stats.etf_liquidity_margin for the industry's ETFs. */
export interface MarginIndustrySeriesResponse {
  industry_id: string;
  industry_label: string;
  attribution: MarginAttributionType;
  securities: MarginSecurity[];
  rows: MarginSeriesRow[];
}

/** Margin trend episode for the margin trends shade overlay. */
export interface MarginTrendEpisode {
  code: string;
  start_date: string;
  end_date: string;
  is_trend_up_not_down: boolean;
  /** Σ rz_buy / Σ trading_amount over the episode window (fraction, e.g.
   *  0.12 = 12% of turnover from rongzi buys). Null when trading_amount
   *  is unavailable. Plotted on the Buy (融资买入额) chart. */
  rz_buy_vs_trading_amt_ratio: number | null;
}

/** Response for GET /api/analysis/margin-trends/trends. */
export interface MarginTrendsShadeResponse {
  industry_id: string;
  attribution: MarginAttributionType;
  episodes: MarginTrendEpisode[];
}

// ----------------------------------------------------------------------------
//  Analysis Commons — Perf Attribution (ETF/Index subjects × Index benchmarks)
//    stats.cross_stats (sec_type='index' pair grain; former
//    analysis.sec_alloc_perf_attribution)
//    PK: (code, benchmark_code, date, sec_type)
//
//    Per-row: code_sec_shared_weight, benchmark_sec_shared_weight,
//    benchmark_etf_trading_amount, code_etf_trading_amount, etf_trading_amount_ratio_benchmark_to_code,
//    etf_trading_amount_ratio_benchmark_to_code_ma5, corr_{20,60,255}d.
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
   *  trailing N trading days. NULL when fewer than N non-NaN closes in window.
   *  Materialized only on stride-20 grid dates (every 20 trading days). */
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
//    stats.industry_basic_stats (PK: industry_id, date, pool_size) — renamed
//    from analysis.industry_sentiments (2026-08-24), now built by builds.industry.
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
//    pool_size ∈ ('small' <51 stocks, 'mid' 51-180, 'large' >180, 'all').
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

/** One per-date aggregation row for a pool_size slice. mean_close and
 *  var_price are computed across rebased-to-100 close values of member
 *  indices in this pool_size slice on this date (anchored at history start).
 *  mean_pe and total_trading_amount are computed on RAW values (no rebasing). */
export interface IndustrySentimentsAggRow {
  date: string;
  pool_size: "small" | "mid" | "large" | "all";
  /** Number of member indices with close data contributing to this slice on
   *  this date. PE/amount means may be computed over fewer indices. */
  index_count: number | null;
  /** AVG(rebased_to_100 close) across member indices in this slice — the
   *  composite index close (former mean_price). 100 = members flat vs
   *  history start. NULL when no members in slice. */
  mean_close: number | null;
  /** VARIANCE(rebased_to_100 close) across member indices in this slice.
   *  NULL when fewer than 2 members (can't compute variance). */
  var_price: number | null;
  /** AVG(raw PE) across member indices in this slice. Source:
   *  stats.index_valuation.pe. NULL PE excluded. NULL when no PE data. */
  mean_pe: number | null;
  /** SUM(stock trading amount in yuan) across the UNION of stocks from all
   *  member indices' active compositions in this slice. Each stock counted
   *  ONCE (union, not sum-per-index). Source: stats.stock_liquidity_margin.trading_amount.
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
//  Industry Correlations — windowed pairwise Pearson correlation between
//  two industries' MA curves of mean_close (stats.industry_basic_stats.
//  mean_close). Drives the expandable Correlation chart on the
//  IndustrySentiments page (multi-industry mode only — Correlation button
//  is disabled when fewer than 2 industries are selected).
//
//  Source: analysis.industry_correlations (built by the correlations step
//  of analyze.industry_sentiments). One row per (start_date, pair,
//  pool_size, interval) with corr_ma20_20d / corr_ma60_60d /
//  corr_ma255_255d: corr_ma{W}_{W}d is the Pearson correlation between
//  the two industries' MA-W curves over the W trading days starting on
//  start_date. Window starts sit on the calendar grid every `interval`
//  (default 20) trading days.
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
/** One pairwise correlation row — the Pearson correlation between
 *  industry_id and benchmark_industry_id's MA-W curves over the W trading
 *  days starting on start_date. NULL (corr_ma*) when the window is not
 *  full or either MA curve is undefined on any window date. */
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
  /** Start date of the compute window on the calendar grid (YYYY-MM-DD).
   *  The window for corr_ma{W}_{W}d spans [start_date, start_date + W). */
  start_date: string;
  /** Stride in trading days between consecutive window starts (default
   *  20). */
  interval: number;
  /** Pool_size slice (same for both industries — cross-pool is not
   *  materialized). small / mid / large / all. */
  pool_size: "small" | "mid" | "large" | "all";
  /** Pearson correlation between the two industries' MA20 curves over the
   *  20 trading days starting on start_date. NULL when the window is not
   *  full. */
  corr_ma20_20d: number | null;
  /** Pearson correlation between the two industries' MA60 curves over the
   *  60 trading days starting on start_date. NULL when the window is not
   *  full. */
  corr_ma60_60d: number | null;
  /** Pearson correlation between the two industries' MA255 curves over
   *  the 255 trading days starting on start_date. NULL when the window is
   *  not full. */
  corr_ma255_255d: number | null;
}

/** Response for GET /api/analysis/industry-correlations?industry_ids=...
 *  &pool_size=all */
export interface IndustryCorrelationsResponse {
  /** Distinct industry_ids requested (deduplicated). */
  industry_ids: string[];
  /** Pool_size slice used. */
  pool_size: "small" | "mid" | "large" | "all";
  /** Pairwise correlation rows — one per (start_date, lexicographic pair)
   *  where both endpoints are in industry_ids. Empty when the analysis
   *  hasn't been run or no pairs have enough overlapping history. */
  correlations: IndustryCorrelationRow[];
}

// ----------------------------------------------------------------------------
//  Industry Correlations by Benchmark Offset (composite analysis) —
//  opposite industry correlations audited in
//  analysis_composites.industry_corr_benchmark_offsets. Each industry's
//  MA-W trend (mean_close) is offset by a broad-market benchmark: the
//  benchmark MA is rebased to the industry's MA level at each window start
//  (k = MA_X[s] / MA_B[s]), then subtracted (common market factor removed)
//  and rebuilt as a recomputed price starting at 100. The row audits, per
//  20/60/255 trading-day window:
//    overall_corr_ma{W}_{W}d    — RAW pairwise correlation of the MA-W
//      curves (same semantics as IndustryCorrelationRow.corr_ma{W}_{W}d).
//    offset_sub_corr_ma{W}_{W}d — correlation of the benchmark-offset
//      SUBTRACTED recomputed prices (common market factor removed).
//    opposite_score_ma{W}_{W}d  — (1 − offset_sub_corr) / 2 in [0, 1]
//      (1 = perfectly opposite once the benchmark is removed).
//
//  Source: analysis_composites.industry_corr_benchmark_offsets (built by
//  python -m analyze.analysis_composites; default benchmark 000300).
//  Drives the Composites → "Opposite Industry Correlations" page.
// ----------------------------------------------------------------------------
/** One benchmark-offset audit row — the pairwise correlation metrics for
 *  industry_id vs benchmark_industry_id over the W-day window starting on
 *  start_date, for one offset benchmark_code. */
export interface IndustryCorrOffsetRow {
  /** Subject industry (lexicographically smaller). */
  industry_id: string;
  /** Benchmark industry (lexicographically larger) — the OTHER industry
   *  of the pair, not the offset benchmark index. */
  benchmark_industry_id: string;
  /** Display label for the subject industry. */
  industry_label: string;
  /** Display label for the benchmark industry. */
  benchmark_industry_label: string;
  /** Offset benchmark index code (e.g. "000300" = CSI300). */
  benchmark_code: string;
  /** Start date of the compute window on the calendar grid (YYYY-MM-DD). */
  start_date: string;
  /** Stride in trading days between consecutive window starts (default
   *  20). */
  interval: number;
  /** Pool_size slice (same for both industries). */
  pool_size: "small" | "mid" | "large" | "all";
  /** RAW pairwise Pearson correlation of the MA20 curves over the 20d
   *  window. NULL when the window is not full. */
  overall_corr_ma20_20d: number | null;
  overall_corr_ma60_60d: number | null;
  overall_corr_ma255_255d: number | null;
  /** Correlation of the benchmark-offset SUBTRACTED recomputed prices
   *  (industry MA − rebased benchmark MA). NULL when the window is not
   *  full or the benchmark MA is undefined on a window date. */
  offset_sub_corr_ma20_20d: number | null;
  offset_sub_corr_ma60_60d: number | null;
  offset_sub_corr_ma255_255d: number | null;
  /** Opposite-correlation score = (1 − offset_sub_corr) / 2 in [0, 1]:
   *  1 = perfectly opposite after removing the benchmark component, 0.5 =
   *  uncorrelated residual, 0 = perfectly co-moving residual. */
  opposite_score_ma20_20d: number | null;
  opposite_score_ma60_60d: number | null;
  opposite_score_ma255_255d: number | null;
}

/** Response for GET /api/analysis/industry-corr-offsets?industry_ids=...
 *  &pool_size=all&benchmark=000300 */
export interface IndustryCorrOffsetsResponse {
  /** Distinct industry_ids requested (deduplicated). */
  industry_ids: string[];
  /** Pool_size slice used. */
  pool_size: "small" | "mid" | "large" | "all";
  /** Offset benchmark used. */
  benchmark_code: string;
  /** Backend arg for the shared ExpandedTable: whether the audit table
   *  renders its per-column header filters (explicitly enabled for this
   *  endpoint). */
  enable_filters: boolean;
  /** Audit rows — one per (start_date, lexicographic pair) where both
   *  endpoints are in industry_ids. */
  offsets: IndustryCorrOffsetRow[];
}

/** One offset benchmark available in
 *  analysis_composites.industry_corr_benchmark_offsets. */
export interface IndustryCorrOffsetBenchmark {
  /** Broad-market index code (e.g. "000300"). */
  benchmark_code: string;
  /** Display label (from stats.sec_classification; falls back to the
   *  code). */
  benchmark_label: string;
  /** Distinct industries covered (pairs materialized). */
  n_industries: number;
  /** First / last window start dates materialized. */
  first_start_date: string;
  last_start_date: string;
}

/** Response for GET /api/analysis/industry-corr-offsets/benchmarks */
export interface IndustryCorrOffsetBenchmarksResponse {
  benchmarks: IndustryCorrOffsetBenchmark[];
}

/** One selectable industry for the offset-correlations page. */
export interface IndustryCorrOffsetIndustry {
  industry_id: string;
  /** Display label (from stats.sec_classification; falls back to the
   *  id). */
  industry_label: string;
  /** True when this industry has at least one materialized audit row. */
  has_rows: boolean;
}

/** Response for GET /api/analysis/industry-corr-offsets/industries */
export interface IndustryCorrOffsetIndustriesResponse {
  industries: IndustryCorrOffsetIndustry[];
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

/** One row in the benchmark price series — date, raw close, fractional
 *  daily return, and trading amount (yuan). The trading amount drives the
 *  optional bar overlay on the BenchmarkPriceChart, where each selected
 *  industry's `benchmark_shared_weight` proportion of the bar is highlighted
 *  in the industry's color (bar total = benchmark trading amount). */
export interface BenchmarkPriceRow {
  date: string;
  close: number | null;
  daily_return: number | null;
  /** Benchmark's daily trading turnover in yuan (stats.index_basic_stats.trading_amount).
   *  NULL when no trading_amount row exists for this date. */
  trading_amount: number | null;
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
  /** Non-industry price rebased to 100, computed over the trailing
   *  5-trading-day window ending on `date`. NULL for non-broad-market
   *  benchmarks. Drives the BenchmarkPriceChart shade when the user
   *  selects "5 days" in the rolling-days dropdown. */
  non_this_industry_rolling_5days_price: number | null;
  /** Same as above over the trailing 20-trading-day window. */
  non_this_industry_rolling_20days_price: number | null;
  /** Same as above over the trailing 60-trading-day window. */
  non_this_industry_rolling_60days_price: number | null;
  /** Same as above over the trailing 120-trading-day window (~6 months).
   *  This is the DEFAULT rolling window for the BenchmarkPriceChart shade
   *  overlay AND for analysis.industry_hypes_and_drains. */
  non_this_industry_rolling_120days_price: number | null;
  /** Same as above over the trailing 255-trading-day window (~1 year). */
  non_this_industry_rolling_255days_price: number | null;
  /** Same as above over the trailing 500-trading-day window (~2 years). */
  non_this_industry_rolling_500days_price: number | null;
  /** Benchmark's weight % (0-100) on the UNION of this industry's member
   *  stocks (latest stats.sec_composition snapshot). Sourced directly from
   *  analysis.industry_attributions.benchmark_shared_weight. Used by the
   *  BenchmarkPriceChart bar overlay: highlighted portion of each bar =
   *  trading_amount × (benchmark_shared_weight / 100); the bar TOTAL always
   *  equals the benchmark's trading_amount on that date. NULL when the
   *  benchmark has no composition data. */
  benchmark_shared_weight: number | null;
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
//  Industry Hypes & Drains — pre-computed top-5 (HYPE) + bottom-5 (DRAIN)
//  industries ranked by attribution contribution to a COMPOSITE broad-market
//  benchmark. Drives the "Hypes & Drains" sub-toggle in "Market Trend" mode
//  on the Industry Sentiments page.
//
//  GET /api/analysis/industry-hypes-and-drains
//    ?benchmark_code=000300&period_days=120&date=YYYY-MM-DD
// ----------------------------------------------------------------------------

/** One ranked industry in the hypes_and_drains response. */
export interface IndustryHypesAndDrainsRow {
  rank_side: "HYPE" | "DRAIN";
  rank: number;
  industry_id: string;
  industry_label: string;
  /** Signed attribution contribution = benchmark_return_Nd
   *  minus non_industry_return_Nd. Positive = HYPE, negative =
   *  DRAIN. NULL when no overlap. */
  metric_value: number | null;
  /** Benchmark N-day return (signed). */
  benchmark_return_nd: number | null;
  /** Non-this-industry N-day return (signed). */
  non_industry_return_nd: number | null;
  /** Industry's benchmark_shared_weight (latest snapshot, percent 0-100). */
  benchmark_shared_weight: number | null;
}

/** One date row in the benchmark price series. */
export interface HypesDrainsBenchmarkRow {
  date: string;
  /** Raw benchmark close. */
  close: number | null;
  /** Fractional daily return = (close_t - close_{t-1}) / close_{t-1}. */
  daily_return: number | null;
  /** Benchmark trading amount (yuan). */
  trading_amount: number | null;
}

/** One date row in an industry's non-this-industry rolling price series
 *  (from analysis.industry_attributions, benchmark_non_this_industry_rolling_{N}days_price).
 *  100-based cumulative non-industry return factor over the trailing N-day
 *  window. The frontend uses this + benchmark_shared_weight to derive the
 *  industry's OWN return via the identity:
 *    ind_return = (bench_return - (1-swf) × non_ind_return) / swf
 *  and plots 100 × (1 + ind_return) as the industry curve. */
export interface HypesDrainsIndustrySeriesRow {
  date: string;
  /** benchmark_non_this_industry_rolling_{N}days_price (100-based factor). */
  rolling: number | null;
  /** benchmark_shared_weight (percent 0-100) on this date. Used as swf
   *  in the industry return formula. */
  benchmark_shared_weight: number | null;
}

/** One industry's full rolling price series (daily). The industry's
 *  seasonal ranking info is in SeasonalRankingRow[] — this interface
 *  only carries the daily price data. */
export interface HypesDrainsIndustrySeries {
  industry_id: string;
  industry_label: string;
  rows: HypesDrainsIndustrySeriesRow[];
}

/** One seasonal (monthly) ranking row: which industry is rank 1-5
 *  HYPE or DRAIN for a given month. */
export interface SeasonalRankingRow {
  /** Month key, e.g. "2026-08". */
  season_qkey: string;
  rank_side: "HYPE" | "DRAIN";
  /** Rank within the rank_side bucket (1-5). */
  rank: number;
  industry_id: string;
  industry_label: string;
  /** Peak attribution contribution within the month.
   *  HYPE = MAX of daily metric_value, DRAIN = MIN. */
  peak_metric_value: number | null;
}

/** One calendar month with its date boundaries. */
export interface SeasonInfo {
  /** Month key, e.g. "2026-08". */
  season_qkey: string;
  /** Inclusive start date (YYYY-MM-DD). */
  season_start: string;
  /** Inclusive end date (YYYY-MM-DD). */
  season_end: string;
}

/** Response for GET /api/analysis/industry-hypes-and-drains. */
export interface IndustryHypesAndDrainsResponse {
  benchmark_code: string;
  benchmark_name: string;
  period_days: number;
  /** Weighting method: 'equal' (raw attribution contribution) or 'amt'
   *  (contribution × shared_trading_amt — absolute yuan impact). */
  weighting: "equal" | "amt";
  /** Full benchmark price series (close + daily_return + trading_amount). */
  benchmark_series: HypesDrainsBenchmarkRow[];
  /** All seasonal (monthly) rankings — which industry is top/bottom 5
   *  per month. Drives the ACTIVE/FADING/HIDDEN state machine. */
  seasonal_rankings: SeasonalRankingRow[];
  /** All calendar months that have rankings, with date boundaries. */
  seasons: SeasonInfo[];
  /** Rolling price series for ALL industries that appear in ANY season's
   *  ranking. Each industry's daily curve is plotted; opacity is determined
   *  by the seasonal state machine. */
  industry_series: HypesDrainsIndustrySeries[];
}

// ----------------------------------------------------------------------------
//  Intraday Movements — per-5-min-tick % change vs previous trading day's
//  close for the benchmark + ALL industries (shaded areas) + member indices.
//  Populated by python -m live.sec_alloc_live_attribution into
//  live.sec_alloc_live_attribution (per-tick member + benchmark %) with
//  industry aggregates computed at query time from live.sec_alloc_live_prev_ref.
//
//  Top plot: benchmark_price_pct line + per-industry SHADED AREAS
//  (industry_price_pct with areaStyle). Clicking a 5-min tick selects it
//  for the middle + bottom plots.
//  Middle plot: bar chart of industry_price_pct at the clicked tick,
//  sorted by signed value. Green = positive, red = negative. Clicking an
//  industry bar selects it for the bottom plot.
//  Bottom plot: bar chart of code_price_pct for the clicked industry's
//  member indices at the clicked tick, sorted by signed value.
//
//  GET /api/live-data/intraday-movements
//    ?benchmark_code=000922&date=YYYY-MM-DD
//  (date optional → latest available)
// ----------------------------------------------------------------------------

/** One 5-min tick of benchmark_price_pct (top plot main line). */
export interface IntradayMovementsBenchmarkTick {
  /** "HH:MM:SS" — bar timestamp within the trading day. */
  time: string;
  /** Benchmark close / prev_day_close - 1 (decimal, e.g. 0.0035 = +0.35%). */
  benchmark_price_pct: number | null;
}

/** One (tick, industry) data point — drives the top plot SHADED AREAS and
 *  the middle plot bars at any clicked tick. */
export interface IntradayMovementsIndustryTick {
  time: string;
  industry_id: string;
  industry_label: string;
  /** TRUE for strategy themes, FALSE for industries. */
  is_strategy: boolean;
  /** Mean of member indices' code_price_pct across this industry at this tick. */
  industry_price_pct: number | null;
  /** industry_price_pct - benchmark_price_pct (signed diff). NULL when either
   *  side is NULL. Drives the top plot SHADE color (green > 0, red < 0)
   *  centered about the benchmark line — NOT a 0-baseline area. */
  industry_price_pct_vs_benchmark: number | null;
}

/** One (code, tick, industry) data point — drives the bottom plot bars at
 *  the clicked tick + clicked industry. */
export interface IntradayMovementsMemberTick {
  time: string;
  /** Member index code (e.g. "000016"). */
  code: string;
  /** Display name from stats.index_identity (falls back to code). */
  code_name: string;
  industry_id: string;
  /** Member index close / prev_day_close - 1. */
  code_price_pct: number | null;
}

/** Distinct industry — for the legend & color map. */
export interface IntradayMovementsIndustry {
  industry_id: string;
  industry_label: string;
  is_strategy: boolean;
}

/** Response for GET /api/live-data/intraday-movements. */
export interface IntradayMovementsResponse {
  benchmark_code: string;
  benchmark_name: string;
  /** As-of date (YYYY-MM-DD). Latest available when no `date` was requested. */
  date: string;
  /** "HH:MM:SS" — the latest 5-min tick (default selection for middle plot). */
  latest_time: string;
  /** Benchmark % change per 5-min tick — drives the top plot main line. */
  benchmark_series: IntradayMovementsBenchmarkTick[];
  /** All industries' % change per (tick, industry) — drives the top plot
   *  SHADED AREAS + middle plot bars at clicked tick. */
  industry_series: IntradayMovementsIndustryTick[];
  /** Member indices' % change per (code, tick, industry) — drives the
   *  bottom plot bars at clicked tick + clicked industry. */
  member_series: IntradayMovementsMemberTick[];
  /** Distinct industries — for the legend & color map. */
  industries: IntradayMovementsIndustry[];
}

// ----------------------------------------------------------------------------
//  Intraday Movements — Prev-Day OHLC (raw daily OHLC of the previous trading
//  day for the benchmark + every member index of the benchmark's universe).
//  Drives the single prev-day OHLC bar prepended BEFORE the 09:30 tick on
//  the Market Movements top plot (GET /api/live-data/intraday-movements/
//  prev-day-ohlc). The client converts to % vs the entry's own close
//  (close → 0.0) so the bar shares the "% change vs prev close" y-axis with
//  today's intraday curves; industry candles are aggregated client-side as
//  the MEAN of member %s (equal-weight, same semantics as industry_price_pct).
// ----------------------------------------------------------------------------
/** Raw OHLC of one code on the previous trading day. */
export interface PrevDayOhlcEntry {
  /** The previous trading day (YYYY-MM-DD). */
  date: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
}

/** One member index's prev-day OHLC (+ industry attribution). */
export interface PrevDayOhlcMember extends PrevDayOhlcEntry {
  /** Member index code. */
  code: string;
  /** Display name from stats.index_identity (falls back to code). */
  code_name: string;
  industry_id: string;
}

/** Response for GET /api/live-data/intraday-movements/prev-day-ohlc. */
export interface PrevDayOhlcResponse {
  benchmark_code: string;
  /** The live date (YYYY-MM-DD) this prev-day data serves. */
  date: string;
  /** Benchmark prev-day raw OHLC (drives the DEFAULT prev-day bar). */
  benchmark: PrevDayOhlcEntry | null;
  /** Every member index of the benchmark universe with its prev-day raw
   *  OHLC — aggregated client-side per industry when an industry is clicked. */
  members: PrevDayOhlcMember[];
}

// ----------------------------------------------------------------------------
//  Live Sec-Alloc Attribution (live schema) — per-industry aggregates at ONE
//  5-min tick, computed at query time from live.sec_alloc_live_attribution
//  joined with live.sec_alloc_live_prev_ref weights. Drives the
//  "By Trading Amt / Equal" toggle on the Intraday Attribution panel of the
//  Market Movements page (GET /api/live-data/sec-alloc-live/attribution).
// ----------------------------------------------------------------------------
/** One industry's aggregates at one tick. */
export interface SecAllocLiveAttributionIndustry {
  industry_id: string;
  /** TRUE for strategy themes, FALSE for real industries. */
  is_strategy: boolean;
  /** Trading-amount-weighted aggregate (FRACTION): SUM(weight × pct) /
   *  SUM(weight) over members with non-NULL pct. NULL while only fallback
   *  rows exist (no prev-day trading-amount ref yet). */
  weighted_pct: number | null;
  /** Plain member average pct (FRACTION) — works with and without the ref. */
  equal_pct: number | null;
  /** Members covered at this tick. */
  member_count: number;
}

/** Response for GET /api/live-data/sec-alloc-live/attribution. */
export interface SecAllocLiveAttributionResponse {
  benchmark_code: string;
  /** As-of date (YYYY-MM-DD). */
  date: string;
  /** "HH:MM:SS" tick. */
  time: string;
  /** TRUE iff a weighted (ref-based) row set exists for this benchmark+date
   *  — drives the UI: the "By Trading Amt" button is DISABLED while FALSE
   *  (only fallback is_without_trading_amt = TRUE rows exist, e.g. prev-day
   *  basic_stats lagging or the heavy ref pass still running). */
  weighted_available: boolean;
  industries: SecAllocLiveAttributionIndustry[];
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
//  Industry ETF Contribution — drives the "ETF Contribution" view on the
//  Industry Sentiments page. Mirrors "Benchmark Attribution" but replaces
//  benchmark-indices with ETFs as the unit of analysis.
//
//  1st plot: multi-ETF price line chart — each ETF's daily close rebased to
//  100 at its OWN first available date (cascading rebasing: a later-listed
//  ETF starts at the MEAN of already-active ETFs on its first date so it
//  blends in rather than jumping to 100). Clickable to pick the as-of date.
//
//  2nd+ plots: per-industry bar charts — each bar = one ETF showing its
//  trading amount (capital flow) and % share of the industry total.
//
//  Source: stats.etf_basic_stats (close), stats.etf_liquidity_margin
//  (trading_amount), stats.sec_classification (ETF→parent_index→industry_id
//  linkage). Industry aggregate from analysis.industry_etf_contribution.
// ----------------------------------------------------------------------------

/** One row in an ETF's daily close series (for the 1st plot). */
export interface IndustryEtfPriceRow {
  date: string;
  close: number | null;
  /** ETF daily trading turnover (yuan) from stats.etf_liquidity_margin.trading_amount.
   *  Drives the optional "Trading Amt" bar overlay on the price chart — each
   *  ETF's bar segment is its proportional share of the date's total ETF
   *  trading amount. NULL when no liquidity data. */
  trading_amount: number | null;
}

/** One ETF's price series entry — code, name, parent index, and close rows. */
export interface IndustryEtfPriceSeriesEntry {
  etf_code: string;
  etf_name: string;
  /** The member index code this ETF tracks (stats.sec_classification.parent_index_code). */
  parent_index_code: string;
  industry_id: string;
  industry_label: string;
  rows: IndustryEtfPriceRow[];
}

/** Response for GET /api/analysis/industry-etf-contribution/etf-price
 *  ?industry_ids=BANKS,AI — multi-ETF price series for the 1st plot. */
export interface IndustryEtfPriceSeriesResponse {
  industry_ids: string[];
  etfs: IndustryEtfPriceSeriesEntry[];
}

/** One ETF row in the per-industry contribution bar chart (2nd+ plots). */
export interface IndustryEtfContributionBarRow {
  etf_code: string;
  etf_name: string;
  parent_index_code: string;
  /** ETF daily trading turnover (yuan) from stats.etf_liquidity_margin.trading_amount. */
  trading_amount: number | null;
  /** ETF FRACTIONAL daily return = (close_t - close_{t-1}) / close_{t-1}.
   *  Computed on-the-fly. NULL when no previous-day close. */
  etf_return: number | null;
}

/** Response for GET /api/analysis/industry-etf-contribution/etf-bars
 *  ?industry_id=BANKS&date=YYYY-MM-DD (date optional → latest). */
export interface IndustryEtfContributionBarsResponse {
  industry_id: string;
  industry_label: string;
  /** As-of date (latest available when no `date` was requested). */
  date: string;
  /** Industry aggregate ETF trading amount (pool_size='all') from
   *  analysis.industry_etf_contribution. NULL when no aggregate row exists. */
  industry_etf_trading_amount: number | null;
  /** 5-day MA of the industry aggregate. NULL when no MA data. */
  industry_etf_trading_amount_ma5: number | null;
  /** 20-day MA of the industry aggregate. NULL when no MA data.
   *  Exposed by the UI "Trading Amt" MA selector alongside MA5. */
  industry_etf_trading_amount_ma20: number | null;
  etfs: IndustryEtfContributionBarRow[];
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

// ----------------------------------------------------------------------------
//  Strategy — singleton backtest
//  GET /api/strategy/singleton/backtest?sec_type=index&code=000970
//  Reads PRE-COMPUTED backtest results from strategy.strategy_identity +
//  strategy.strategy_results (1:1 results) + strategy.trade_decision. Each
//  decision carries normalized_fill_price (base = 100 at the first BUY fill),
//  and summary.first_buy_fill_price is the chart's normalization anchor.
// ----------------------------------------------------------------------------
export interface StrategyDecision {
  decision_no: number;
  side: "BUY" | "SELL";
  exec_date: string;
  qty: number;
  fill_price: number;
  /** fill_price rebased to 100 at the first BUY fill (= fill_price /
   *  summary.first_buy_fill_price * 100). First BUY = 100; later fills read
   *  as % change from entry (105 = +5%, 94 = -6%). */
  normalized_fill_price: number;
  /** Weighted-avg BUY normalized_fill_price across all historical BUYs still
   *  in the remaining position (the cost basis realized_pnl is computed
   *  against). For BUY: the post-BUY value (new weighted average including
   *  this BUY). For SELL: the pre-SELL value used to compute realized_pnl
   *  (= qty_sold × (sell_norm - this_value)); stays constant across partial
   *  SELLs and is the last cost basis before reset to 0 when shares reach 0. */
  normalized_mean_buy_price: number;
  position_before: number;
  position_after: number;
  cash_before: number;
  cash_after: number;
  /** Cumulative quantity (in qty/confidence units, NOT /100) before/after
   *  this decision. BUY adds qty (= confidence); SELL subtracts qty_sold
   *  (= (confidence/100) * total_qty_before). */
  total_qty_before: number;
  total_qty_after: number;
  realized_pnl: number;
  /** Slippage = |fill_price - close| / 100: how far the worst-case OHLC fill
   *  deviates from the day's close, normalized to per-100-shares scale.
   *  ≥ 0 for both BUY and SELL. */
  slippage: number | null;
  /** Fee = 0.2% of BUY notional (normalized money). BUY only; 0 for SELL.
   *  Deducted from cash_after on BUY. */
  fee: number | null;
  signal_value: number | null;
  signal_reason: string;
  /** FT stressed confidence when OHLC moved UP. NULL = no FT applied.
   *  0 = trade would be removed under UP stress (signal sign flipped).
   *  >0 = stressed signal magnitude (confidence was cut but trade fires). */
  ft_stressed_conf_up: number | null;
  /** FT stressed confidence when OHLC moved DOWN. NULL = no FT applied.
   *  0 = trade would be removed under DOWN stress. >0 = stressed magnitude. */
  ft_stressed_conf_down: number | null;
}

export interface StrategyOhlcRow {
  date: string;
  open: number | null;
  close: number | null;
  high: number | null;
  low: number | null;
  trading_amount: number | null;
  ma5: number | null;
  ma60: number | null;
}

/** Daily portfolio state (one row per trading day from first BUY to end).
 *  unrealized_pnl = (total_qty/100) * (normalized_close - cost_basis_norm)
 *  — P&L if all remaining position were sold at the day's close.
 *  normalized_mean_buy_period = weighted-avg BUY period (calendar days since
 *  first BUY), weighted on remaining qty; mean holding time =
 *  (trade_date − first_buy_date).days − normalized_mean_buy_period.
 *  return_rate = ANNUALIZED return on capital = (total_pnl / capital_deployed
 *  / max(mean_holding_days, 1)) × 255. 0 when total_qty = 0 or
 *  mean_holding_days <= 0. */
export interface StrategyDailyRow {
  trade_date: string;
  unrealized_pnl: number;
  total_pnl: number;
  realized_pnl_cum: number;
  total_qty: number;
  position_value: number;
  normalized_mean_buy_period: number;
  /** ANNUALIZED return on capital = (total_pnl / ((total_qty/100) *
   *  normalized_mean_buy_price) / max(mean_holding_days, 1)) × 255. 0 when
   *  total_qty = 0 (no capital at risk) or mean_holding_days <= 0. */
  return_rate: number;
  /** Annualized Sharpe ratio (×√255, rf=0) of daily Δtotal_pnl over ALL
   *  history up to this trade_date. 0 when < 2 deltas or σ = 0. */
  sharpe_ratio: number;
  /** Annualized Sharpe ratio over a rolling 255-trading-day window (~1 year). */
  sharpe_ratio_255d: number;
  /** Annualized Sharpe ratio over a rolling 500-trading-day window (~2 years). */
  sharpe_ratio_500d: number;
}

export interface StrategyBacktestResponse {
  code: string;
  name: string;
  sec_type: MaSpreadSecType;
  ohlc: StrategyOhlcRow[];
  decisions: StrategyDecision[];
  daily: StrategyDailyRow[];
  summary: {
    n_buys: number;
    n_sells: number;
    realized_pnl: number;
    final_cash: number;
    total_return_pct: number;
    total_buy_cost: number;
    /** exec_date of the FIRST BUY decision — the normalization anchor date
     *  (null if no BUY). */
    first_buy_date: string | null;
    /** fill_price of the FIRST BUY decision — the normalization anchor. The
     *  chart rebases OHLC/MA series off this so the first BUY sits at y=100.
     *  null if no BUY. */
    first_buy_fill_price: number | null;
  };
  /** Fault tolerance percentage (0-20) applied to this run. 0 = baseline. */
  fault_tolerance: number;
}

// ----------------------------------------------------------------------------
//  Strategy — internal risk metrics
//  GET /api/strategy/singleton/risks?sec_type=index&code=000970
//  Reads pre-computed risk metrics from strategy.strategy_risks +
//  strategy.strategy_risk_period (computed by python -m strategy._risks).
// ----------------------------------------------------------------------------
export type StrategyRiskGrade = "LITTLE" | "LOW" | "MODERATE" | "ELEVATED" | "HIGH";
export type StrategyPeriodType = "year" | "season" | "month";

export interface StrategyRiskSeq {
  seq_id: number;
  code: string;
  total_realized_pnl: number;
  total_abs_pnl: number;
  n_sells: number;
  n_buys: number;
  /** decision_no of the 1st/2nd/3rd-largest gain SELLs (FK → trade_decision). null if fewer trades. */
  pnl_gain_1st_decision_no: number | null;
  pnl_gain_2nd_decision_no: number | null;
  pnl_gain_3rd_decision_no: number | null;
  /** decision_no of the 1st/2nd/3rd-largest loss SELLs (FK → trade_decision). null if fewer trades. */
  pnl_loss_1st_decision_no: number | null;
  pnl_loss_2nd_decision_no: number | null;
  pnl_loss_3rd_decision_no: number | null;
  /** decision_no of the 1st/2nd/3rd-highest-confidence BUYs (by qty desc). FK → trade_decision. null if fewer BUYs. */
  confidence_buy_1st_decision_no: number | null;
  confidence_buy_2nd_decision_no: number | null;
  confidence_buy_3rd_decision_no: number | null;
  // Derived via LEFT JOIN to trade_decision (1st gain / 1st loss details)
  top_gain_pnl: number | null;
  top_gain_exec_date: string | null;
  top_gain_signal_reason: string | null;
  top_loss_pnl: number | null;
  top_loss_exec_date: string | null;
  top_loss_signal_reason: string | null;
  max_30d_abs_pnl: number | null;
  concentration_ratio: number | null;
  concentration_window_start: string | null;
  concentration_window_end: string | null;
  /** Trough date (SELL exec_date where cumulative realized P&L bottomed) of the
   *  WORST peak-to-trough drawdown in cumulative realized P&L. null if no
   *  drawdown episode. */
  drawdown_1st_date: string | null;
  drawdown_2nd_date: string | null;
  drawdown_3rd_date: string | null;
  /** Per-episode drawdown magnitude (trough_cum_pnl - peak_cum_pnl, signed <= 0).
   *  1st == max_drawdown magnitude used transiently for risk_score. null if
   *  no episode for that slot. */
  drawdown_1st_val: number | null;
  drawdown_2nd_val: number | null;
  drawdown_3rd_val: number | null;
  risk_score: number | null;
  risk_grade: StrategyRiskGrade | null;
  /** FT amplified strategy's approximate total PnL. NULL = no FT applied. */
  ft_amplified_total_pnl: number | null;
  /** Worst close-price peak-to-trough drawdown (fractional ratio <= 0) while position > 0. */
  deepest_drop_since_unzero_pos: number | null;
  deepest_drop_since_unzero_pos_peak_date: string | null;
  deepest_drop_since_unzero_pos_trough_date: string | null;
  /** Worst close-price drawdown (fractional ratio <= 0) from a BUY entry (seed = fill_price) to next decision. */
  deepest_drop_since_last_buy: number | null;
  deepest_drop_since_last_buy_peak_date: string | null;
  deepest_drop_since_last_buy_trough_date: string | null;
}

export interface StrategyRiskPeriod {
  seq_id: number;
  code: string;
  period_type: StrategyPeriodType;
  period_value: string;
  n_sells: number;
  n_buys: number;
  realized_pnl: number;
  /** FT amplified realized P&L for this period (sum of per-SELL amplified
   *  P&L). The amplified strategy picks the adverse OHLC direction per SELL
   *  (loss → sell more, gain → sell less). 0 when no FT was applied.
   *  UI plots a cumulative amplified P&L trend line vs the baseline curve. */
  ft_amplified_pnl: number;
  /** Mark-to-market change in unrealized_pnl during this period =
   *  unrealized_pnl(end of period) - unrealized_pnl(end of previous period).
   *  From strategy_daily. Realized + unrealized = total economic P&L for the period. */
  unrealized_pnl: number;
  /** Worst (min, most negative) daily unrealized_pnl within this period —
   *  the deepest intra-period MTM loss. UI draws a transparent red bar. */
  max_loss_unrealized_pnl: number;
  /** Peak (max, most positive) daily unrealized_pnl within this period —
   *  the highest intra-period MTM gain. UI draws a transparent green bar. */
  max_gain_unrealized_pnl: number;
  /** Unrealized_pnl at the LAST trading day of this period (absolute level,
   *  not a change). UI draws the period-end bar for this. */
  end_unrealized_pnl: number;
  abs_pnl: number;
  period_share: number | null;
  is_concentration_hotspot: boolean;
  is_counter_trend: boolean;
}

/** A single contribution factor to the risk_score. One row per
 *  (seq, code, component, sub_key). SUM(contribution) = risk_score. */
export interface StrategyRiskFactor {
  seq_id: number;
  code: string;
  /** realized / unrealized / streak / period_asymmetry / period_tail / fault_tolerance */
  component: "realized" | "unrealized" | "streak" | "period_asymmetry" | "period_tail" | "fault_tolerance";
  /** Human-readable label (e.g. "Realized Loss (30d window)"). */
  label: string;
  /** Window days (1/30/90/365), streak length, or period type (month/season/year). */
  sub_key: string;
  /** This factor's contribution to the total risk_score. */
  contribution: number;
  /** The raw input (loss amount, streak months, dominance ratio, z-score). */
  raw_value: number | null;
  /** The threshold at which this factor contributes 1.0 (or 6.0 for period signals). */
  threshold: number | null;
  /** raw_value / threshold (capped at 4.0) — the exponential driver. */
  ratio: number | null;
}

export interface StrategyRiskResponse {
  code: string;
  sec_type: MaSpreadSecType;
  risk_seq: StrategyRiskSeq | null;
  periods: StrategyRiskPeriod[];
  /** Risk score contribution factors (empty when no risk_seq). */
  risk_factors: StrategyRiskFactor[];
}

// ----------------------------------------------------------------------------
//  Futures Baseline — CFFEX futures curves (v_futures_baseline view)
//  One product at a time (IC/IF/IH/IM index or T/TF/TL/TS bond).
//  Frontend plots one curve per contract code; blue gradient for active
//  (farther maturity = lighter blue); grey for matured.
// ----------------------------------------------------------------------------
export type FuturesProductType = "index" | "bond";

/** One CFFEX product available for the Futures tab selector. */
export interface FuturesProduct {
  product_code: string;
  name: string;
  contract_type: FuturesProductType;
  underlying_code: string;
  underlying_name: string;
}

/** Meta info per contract (computed server-side, not a DB column). */
export interface FuturesContractMeta {
  code: string;
  contract_year_month: string;
  /** First trading date in product's calendar. */
  first_date: string;
  /** Last trading date in product's calendar. */
  last_date: string;
  /** TRUE if contract is still active on the product's latest date. */
  is_alive: boolean;
  /** TRUE if trading_amount > 0 on every trading day between first_date
   *  and last_date (continuity filter for "sufficient trading amt" rule). */
  is_continuous: boolean;
}

/** One daily row of a futures contract (from v_futures_baseline). */
export interface FuturesRow {
  date: string;
  code: string;
  settlement_price: number | null;
  close: number | null;
  trading_amount: number | null;
  open_interest: number | null;
  days_to_expiry: number | null;
}

/** Response for GET /api/futures/combined?product=IF */
export interface FuturesCombinedResponse {
  product: string;
  product_name: string;
  contract_type: FuturesProductType;
  underlying_code: string;
  underlying_name: string;
  /** Unified product trading-date calendar (union of all contract dates). */
  dates: string[];
  contracts: FuturesContractMeta[];
  rows: FuturesRow[];
  /** Daily close price of the underlying (index) or null for bond futures.
   *  Array length matches dates[]; null where spot unavailable. */
  spot_price: (number | null)[] | null;
}
