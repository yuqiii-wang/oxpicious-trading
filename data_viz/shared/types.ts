/**
 * Shared TypeScript types mirroring the CSV schemas produced by the project's
 * build_*.py scripts. These types are consumed by both the Express backend
 * (api/services/*) and the React frontend (src/features/*).
 */

// ----------------------------------------------------------------------------
// API envelope contract (data_viz/api/CONTRACTS.md, api/lib/http.ts)
// ----------------------------------------------------------------------------

/** Machine-readable error class carried by every non-2xx API body.
 *  MISSING_PARAM / INVALID_PARAM → 400, NOT_FOUND → 404, CONFLICT → 409,
 *  UPSTREAM_FAILED → failed python runner / LLM / daemon, INTERNAL → 500. */
export type ApiErrorCode =
  | "MISSING_PARAM"
  | "INVALID_PARAM"
  | "NOT_FOUND"
  | "CONFLICT"
  | "UPSTREAM_FAILED"
  | "INTERNAL";

/** Body of EVERY non-2xx API response. HTTP status stays the success
 *  discriminator; `error` is the human-readable message the UI surfaces
 *  (api-client formatFetchError reads exactly this field), `code` the
 *  optional machine-readable class, `details` optional per-field context.
 *  Note: a python-run command may answer 200 with `success: false` — that
 *  outcome is DATA (see the run-response types), never this shape. */
export interface ApiErrorBody {
  success: false;
  error: string;
  code?: ApiErrorCode;
  details?: Record<string, string>;
}

// ----------------------------------------------------------------------------
// Debt (debt_baseline.csv)
// ----------------------------------------------------------------------------
export interface DebtDailyRow {
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
  /** Overnight reverse-repo rate (%) — same PBoC bulletins as omo_rate;
   *  fixed rate = 7d OMO rate + 25bp (tool since 2026-06-29). The 1d side
   *  of the OMO panel's 1d/7d toggle. */
  omo_rate_1d: number | null;
  /** Overnight reverse-repo operation quantity (亿元) on op days. */
  omo_quantity_1d: number | null;
}

export interface DebtDailyResponse {
  dates: string[];
  rows: DebtDailyRow[];
  minDate: string;
  maxDate: string;
}

// ----------------------------------------------------------------------------
// Macro (precious metals — SGE Au99.99/Ag99.99 spot + COMEX GC/SI main
// continuous)
//   Daily bars per instrument: one row per (code, date) from
//   stats.v_macro_daily (macro_identity + macro_basic_stats), loaded by
//   builds.macro from temps/gold_silver/*.csv (downloads.macro.gold_silver).
//   Units are per-instrument (macro_identity.unit): Au99.99 CNY/克,
//   Ag99.99 CNY/kg, GC/SI USD/ozt — never convert client-side.
// ----------------------------------------------------------------------------
export interface MacroDailyRow {
  date: string;
  code: string;
  name: string;
  /** static.label_dim 'macro_metal' decode of metal_type (0 gold / 1 silver). */
  metal_label: string;
  unit: string;
  /** Data source slug ("sge" | "sina"). */
  source: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  /** vs the instrument's previous trading-day close (computed in builds.macro). */
  change: number | null;
  change_pct: number | null;
  /** COMEX klines only (lots); null for the SGE spot bars. */
  volume: number | null;
}

export interface MacroDailyResponse {
  /** Unique sorted trading dates across the returned rows. */
  dates: string[];
  rows: MacroDailyRow[];
  minDate: string;
  maxDate: string;
}

// ----------------------------------------------------------------------------
// PBoC Open Market Announcements (stats.pboc_oma)
//   High-level policy notices (公开市场业务公告) — NOT daily transaction
//   announcements. Loaded from temps/pboc_oma_news/oma_combined.csv by
//   the builds/bond pipeline. Composite PK (date, title); no FK to
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

// ----------------------------------------------------------------------------
//  Per-plot responses — each Options Analysis plot fetches its own SQL
//  aggregate instead of the full-history /combined blob (one plot, one
//  query; CFFEX underlyings ship ~200MB through /combined).
// ----------------------------------------------------------------------------

/** Distinct trading dates + volume counters for one underlying/venue. */
export interface OptionsDatesResponse {
  underlying_code: string;
  dates: string[];
  /** Total quote rows (contract-days) across the whole history. */
  row_count: number;
  /** Distinct contract codes ever quoted. */
  contract_count: number;
}

/** Quote rows for one or more specific trading dates (single small
 *  per-date query — the (underlying_code, date) index drives each date).
 *  Feeds the wall / smile / greeks panels and the StatTable snapshot
 *  columns (batched: one round trip for all 4 derived dates). */
export interface OptionsSnapshotRowsResponse {
  underlying_code: string;
  rows: OptionsRow[];
}

/** History-wide display meta for the wall panel (unified strike /
 *  expiry-month axes) — fetched once per underlying/venue, not per date. */
export interface OptionsSnapshotAxesResponse {
  underlying_code: string;
  /** Distinct strike_price values across the underlying's full history. */
  strikes: number[];
  /** Distinct expiry_month labels across the full history (e.g. "10月"). */
  expiry_months: string[];
  /** Latest trading date in the scope (client cache version anchor). */
  latest_date: string;
}

/** Per (date × real expiry) smile-anchor aggregate for the skew chronology
 *  panel — the nearest-|x| anchors the panel used to recompute in-browser
 *  from the full-history /combined blob (~200MB JSON on the biggest CFFEX
 *  underlying; ~10k rows here instead). Row validity mirrors the panel's
 *  old filter (0 < IV < 5, delta present, strike > 0, close > 0). IVs are
 *  in vol points (fraction × 100); spot is the RAW underlying close (ETF
 *  venues store 厘 — the panel applies PRICE_SCALE; CFFEX is native). */
export interface SkewDailyRow {
  date: string;
  expiry_date: string;
  /** MIN(days_to_expiry) over the group's valid rows. */
  dte: number;
  /** Underlying close on the date (raw venue scale, null when absent). */
  spot: number | null;
  /** IV of the row nearest |moneyness_ratio − 1| (null → last resort). */
  atm_iv: number | null;
  /** IV of the CALL row nearest |delta − 0.25| within (0, 0.5). */
  call25_iv: number | null;
  /** IV of the PUT row nearest |delta + 0.25| within (−0.5, 0). */
  put25_iv: number | null;
  call10_iv: number | null;
  put10_iv: number | null;
}

export interface OptionsSkewDailyResponse {
  underlying_code: string;
  rows: SkewDailyRow[];
}

/** Per (date × expiry) call/put open interest + per-date spot close. */
export interface OptionsOiDailyResponse {
  underlying_code: string;
  daily: Array<{
    date: string;
    expiry_date: string;
    call_oi: number;
    put_oi: number;
  }>;
  spot: Array<{ date: string; close: number | null }>;
}

/** Per (date × expiry × strike) call/put open interest cells for the OI
 *  bands chart, restricted to the requested expiry dates. */
export interface OptionsOiCellsResponse {
  underlying_code: string;
  cells: Array<{
    date: string;
    expiry_date: string;
    strike_price: number;
    call_oi: number;
    put_oi: number;
  }>;
}

/** Per-contract final open interest (last session ≤ its expiry) — the
 *  expiry markers. Per-expiry lifetime totals (the cohort list) are
 *  derived client-side from /oi-daily's per (date × expiry) rows instead
 *  of a second full-history SQL aggregation. */
export interface OptionsExpiryMetaResponse {
  underlying_code: string;
  markers: Array<{
    contract_code: string;
    contract_name: string;
    option_type: OptionType;
    expiry_date: string;
    last_date: string;
    open_interest: number;
  }>;
}

/**
 * Server-computed oi_moneyness skew series — the exact semantics of the
 * former in-browser computeDailySkewSeries (IV-valid filter 0<IV<5, OI
 * weight floored at 1, real-expiry month groups with MIN(expiry_date)
 * boundary, OTM shares over all active rows), aggregated in SQL.
 * e_m = Σ(OI·K)/Σ(OI)/S (raw units — identical to the browser ratio);
 * null when the date/group has <3 valid rows or no spot. `spot.close`
 * carries the raw underlying close (null when the loader stored 0).
 */
export interface OptionsMoneynessSeriesResponse {
  underlying_code: string;
  spot: Array<{ date: string; close: number | null }>;
  groups: Array<{
    date: string;
    /** Expiry month group (YYYY-MM-01). */
    expiry_month: string;
    /** Real expiry boundary of the group (MIN over its valid rows). */
    expiry_date: string;
    /** Count of IV-valid rows in the group on the date. */
    n_valid: number;
    /** OI-weighted mean moneyness E[M] (null when invalid). */
    e_m: number | null;
    /** Σ(w·K) over the group's valid rows (w = GREATEST(OI, 1), raw units) —
     * with `wsum` reconstructs the date-level aggregate E[M]. */
    wsum_k: number;
    /** Σ(w) over the group's valid rows. */
    wsum: number;
    /** OTM OI shares over the group's ALL active rows (null side = no OI). */
    call_oi: number;
    put_oi: number;
    call_otm_oi: number;
    put_otm_oi: number;
  }>;
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

/** Per-expiry OI stats per (date, expiry month) from the dedicated
 *  per-real-expiry table analysis.options_oi_stats (expiry months with
 *  several expiries — CFFEX weeklies — aggregate via SUM / MAX; the
 *  CALL/PUT duplicated rows are deduped server-side). */
export interface OptionsOiStatsRow {
  date: string;
  expiry_month: string;
  /** Latest exact expiry date in the month group (null when absent). */
  expiry_date: string | null;
  /** Calls+puts OI of the expiry group on the date (contracts). */
  oi_total: number | null;
  /** OI change vs 5 / 20 trading sessions earlier (contracts; NULL when
   *  the group had no row at the offset session). */
  oi_delta_5d: number | null;
  oi_delta_20d: number | null;
  /** Max OI over the trailing 20 sessions incl. the date (contracts). */
  oi_max_20d: number | null;
}

export interface OptionsOiStatsResponse {
  underlying_code: string;
  rows: OptionsOiStatsRow[];
}

/** Options-implied OI-MASS CORRIDOR band per (date) from
 *  analysis.options_bands v2: the bounded region = strikes enclosing the
 *  central 80% of pooled chain OI (corridor_low/high = OI-CDF P10/P90),
 *  spot's position in the mass, a validated direction lean, and
 *  prior-session corridor events. Evidence (2020-2026): close <= P10 =
 *  90% up next 5 sessions (mean +4.5%); close >= P90 = 89% down
 *  (-3.3%); skew flags alone carry no edge (context only). */
export interface OptionsBandsRow {
  date: string;
  underlying_close: number | null;
  /** OI-CDF P10 / P90 strikes, raw units (same scale as
   *  underlying_close — SSE/SZSE ETF venues store 厘: divide by 1000
   *  for yuan, like spot). */
  corridor_low: number | null;
  corridor_high: number | null;
  oi_total: number | null;
  /** OI share at strikes <= spot, [0,1]: <=0.25 = snap-back-up zone. */
  spot_pct: number | null;
  gap_low_pct: number | null;
  gap_high_pct: number | null;
  pos_state: "BELOW" | "INSIDE" | "ABOVE" | null;
  lean: "UP" | "DOWN" | "NEUTRAL" | null;
  /** Fresh corridor cross vs the PRIOR session's corridor. */
  event: "NONE" | "EXIT_LOW" | "EXIT_HIGH" | "REENTER" | null;
  /** Distinct strikes present beyond the chain's max/min strike of 40
   *  sessions earlier — fresh exchange listing expansion as the
   *  underlying moves (null when the 40-session window is incomplete). */
  new_hi_40d: number | null;
  new_lo_40d: number | null;
  /** Edge-significance amplifier: a breached/approached corridor edge
   *  (upper: lean DOWN / EXIT_HIGH; lower: lean UP / EXIT_LOW) whose OWN
   *  side just expanded by >= 2 new strikes in the trailing 40 sessions
   *  — a breach into freshly-listed price territory, not routine
   *  corridor noise (null = window incomplete / not computed). */
  edge_sig: "NONE" | "UP" | "DOWN" | "BOTH" | null;
  n_expiries: number | null;
  /** CONTEXT ONLY (no standalone edge): every expiry group's E[M] > 1 /
   *  < 1 — whole OI mass overhead / underfoot. */
  skew_all_above: 0 | 1 | null;
  skew_all_below: 0 | 1 | null;
  min_em: number | null;
  mean_em: number | null;
  /** 5-session IV expected move in % of spot (NULL when the source chain
   *  ATM IV is below the plausibility floor — ETF greeks miscalibration). */
  em5_pct: number | null;
  ret5_pct: number | null;
  exhausted_5d: 0 | 1 | null;
}

export interface OptionsBandsResponse {
  underlying_code: string;
  rows: OptionsBandsRow[];
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
  /** Deeper |delta|=0.10 wing (null when the strike grid has no near-target contract). */
  iv_call10: number | null;
  iv_put10: number | null;
  risk_reversal_10d: number | null;
  smile_skewness: number | null;
  rr25_ma5: number | null;
  rr25_ma20: number | null;
  rr25_ma60: number | null;
  /** Whole-period (expanding) correlation of rr25 MA vs spot MA. */
  corr_rr25_ma5_vs_spot_ma5: number | null;
  corr_rr25_ma20_vs_spot_ma20: number | null;
  corr_rr25_ma60_vs_spot_ma60: number | null;
}

export interface IvSkewResponse {
  underlying_code: string;
  rows: IvSkewRow[];
}

export interface VolIndexRow {
  date: string;
  underlying_code: string;
  /** Expiry with T <= 30d used in the constant-maturity bracket (null on fallback). */
  near_expiry_date: string | null;
  /** Expiry with T > 30d used in the bracket (null on single-expiry fallback). */
  far_expiry_date: string | null;
  dte_near: number | null;
  dte_far: number | null;
  var_near: number | null;
  var_far: number | null;
  variance_30d: number | null;
  /** 100 * sqrt(variance_30d) — vol points, comparable to VIX. */
  vol_index_30d: number | null;
}

export interface VolIndexResponse {
  /** null when the request was for ALL underlyings. */
  underlying_code: string | null;
  rows: VolIndexRow[];
}

/** Stride-sampled rolling correlation between the 30d model-free vol
 *  index and the underlying spot close (one row every 20th trading
 *  session; windows count only joint-valid daily log-change pairs). */
export interface VolIndexCorrRow {
  date: string;
  underlying_code: string;
  /** Pearson corr of daily spot log-return vs vol-index log-change over
   *  the trailing 20 sessions (null until 20 joint-valid pairs). */
  corr_20d: number | null;
  /** Same over the trailing 60 sessions. */
  corr_60d: number | null;
  /** Same over the trailing 252 sessions. */
  corr_252d: number | null;
}

export interface VolIndexCorrResponse {
  /** null when the request was for ALL underlyings. */
  underlying_code: string | null;
  rows: VolIndexCorrRow[];
}

export interface EtfOhlcvResponse {
  dates: string[];
  code: string;
  rows: Array<{
    date: string;
    // null on non-trading dates (CN holidays for A-share indices) — the
    // chart renders them as gaps, never as zero-price candles
    open: number | null;
    high: number | null;
    low: number | null;
    close: number | null;
    volume: number | null;
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
  /** Dividend price estimation curve value in RAW price units (CNY), from
   *  analysis.dividend_est — cum-dividend fair-value line anchored
   *  on each ex-date's post-drop close. Null before the code's first
   *  ex-dividend date (non-payers have no rows). The chart converts it to
   *  adjusted space (× adj_close/close) to overlay the adjusted OHLC. */
  div_est_price: number | null;
  /** Next ex-dividend date bounding the curve's current segment; null on
   *  the open tail (no next ex-date yet). */
  seg_end_date: string | null;
  /** Per-share dividend going ex on seg_end_date; null on the open tail. */
  seg_next_div: number | null;
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
// Index (v_index_daily view)
// ----------------------------------------------------------------------------
export interface IndexDailyRow {
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
  ma252: number | null;
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

export interface IndexDailyResponse {
  code: string;
  name: string;
  dates: string[];
  rows: IndexDailyRow[];
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
  rows: IndexDailyRow[];
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
// Used by the Index page's "Linked ETFs" expansion beside the
// Composition pie — shows the ETFs tracking the displayed index.
// ----------------------------------------------------------------------------
/** One ETF that tracks the requested index. */
export interface LinkedEtfRow {
  /** ETF code, bare 6-digit (e.g. "510300"). */
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
  /** Number of ETF联接基金 feeder funds targeting this ETF — from
   *  stats.sec_etf_links (COUNT by target_etf_code). Drives the unified
   *  table's expandable rows: > 0 rows expand their feeder sub-rows on
   *  click; 0 rows keep the row-click trend-chart behavior. */
  feeder_count: number;
}

export interface LinkedEtfsResponse {
  /** The requested index code (echoed back, suffix-stripped). */
  index_code: string;
  /** Display name of the requested index from stats.sec_classification
   *  (type='index'). "" when unknown — the unified table shows
   *  "index_code index_name" in its Index column. */
  index_name: string;
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
// Linked Feeder Funds — ETF联接基金 (feeder funds) whose target (master) ETF
// is the requested code. Market design: a feeder invests >=90% of NAV in ONE
// listed target ETF (same manager, same tracking index) and bridges OTC
// channels into the exchange product at NAV. Mirror of the index page's
// LinkedEtfsList (retired — now the Feeder Fund group of the unified
// LinkedEtfsFeedersTable), one hierarchy level further from the index:
//   index ← parent_index_code ← ETF ← sec_etf_links.target_etf_code ← feeder.
// Source: stats.sec_etf_links (built by builds.classification from the
// csindex etf_feeder_map_*.csv — one row per feeder family, no A/C split).
// Latest NAV + 日涨跌 come from stats.etf_basic_stats via v_etf_margin
// (builds.etf.feeder_nav — the huatai-pb fund site publishes close-only
// NAV, loaded with is_ohl_estimated=TRUE).
// ----------------------------------------------------------------------------
export interface LinkedFeederRow {
  /** OTC feeder fund code, bare 6-digit (e.g. "012761"). */
  feeder_code: string;
  feeder_name: string;
  /** TRUE when the feeder name carries 发起式 (initiator fund). */
  is_initiator: boolean;
  /** Target ETF Chinese name. */
  target_etf_name: string;
  /** Target ETF CSIndex type: "ETF" or "增强ETF". */
  target_etf_type: string;
  /** Tracked index code, bare (equals the ETF's parent_index_code). */
  index_code: string;
  index_name: string;
  manager: string;
  /** Feeder AUM in 亿元 (0 for young feeders). */
  aum_yi: number | null;
  /** Feeder inception date (YYYY-MM-DD, "" when unknown). */
  inception_date: string;
  /** Latest NAV date (YYYY-MM-DD, "" — feeder has no NAV series yet). */
  latest_date: string;
  /** Latest 单位净值 (close-only NAV series). */
  latest_nav: number | null;
  /** 日涨跌 in percent on latest_date. */
  day_pct: number | null;
}

export interface LinkedFeedersResponse {
  /** The requested ETF code, bare 6-digit (e.g. "510880"). */
  etf_code: string;
  feeders: LinkedFeederRow[];
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
// Stock (v_stock_daily view — stock_identity + stock_basic_stats)
// Used by the composition pie chart's per-stock OHLC expansion.
// ----------------------------------------------------------------------------
export interface StockDailyRow {
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

export interface StockDailyResponse {
  code: string;
  name: string;
  rows: StockDailyRow[];
  /** Dividend events for this stock (all dates — not windowed). Empty when
   *  the stock has no dividend history (e.g. new listings, non-dividend-paying
   *  stocks). Sourced from stats.stock_dividends. */
  dividends: StockDividend[];
}

// ----------------------------------------------------------------------------
// Stock granular blocks — per-table single-code reads backing the trend
// chart's parallel block loads (core OHLC + margin overlays + dividends).
// Each block is one base table with its own code predicate, so the planner
// always has a PK-indexed path (no v_stock_daily LEFT-JOIN plan trap).
// ----------------------------------------------------------------------------

/** Core daily row — stock_identity ⋈ stock_basic_stats (OHLC + valuation).
 *  `has_intraday_5mins` is deliberately absent: only the INDEX panel reads
 *  it; stock-path consumers never do. */
export interface StockCoreRow {
  date: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  prev_close: number | null;
  pct_change: number | null;
  pe: number | null;
  eps: number | null;
  is_pe_estimated: boolean;
}

/** GET /api/stock/core?code= — the required block; its rows define the
 *  chart's x-axis. Margin/dividend blocks are joined onto these dates. */
export interface StockCoreResponse {
  code: string;
  name: string;
  rows: StockCoreRow[];
}

/** Margin + liquidity row — stats.stock_liquidity_margin only. Dates with
 *  no margin activity are simply absent (the chart maps by date and treats
 *  missing as null → line break). */
export interface StockMarginRow {
  date: string;
  trading_shares: number | null;
  trading_amount: number | null;
  rz_balance: number | null;
  rz_buy: number | null;
  rq_balance_qty: number | null;
  rq_balance_amt: number | null;
  total_balance: number | null;
}

/** GET /api/stock/margin?code= — best-effort overlay block. */
export interface StockMarginResponse {
  code: string;
  rows: StockMarginRow[];
}

/** GET /api/stock/dividends?code= — best-effort event-marker block. */
export interface StockDividendsResponse {
  code: string;
  dividends: StockDividend[];
}

// ----------------------------------------------------------------------------
// Stock Combined (v_stock_daily view + sec_classification)
// Used by the Stock page — paginated stock list filtered by sector +
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
  rows: StockDailyRow[];
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
  /** MAX(date) from stats.v_debt_daily */
  debt: string;
  /** MAX(date) from stats.v_etf_margin */
  etf_margin: string;
  /** MAX(date) from stats.v_index_daily */
  index_daily: string;
  /** MAX(date) from stats.v_options_quote */
  options: string;
  /** MAX(snapshot_date) from stats.sec_composition */
  sec_composition: string;
  /** MAX(date) from stats.v_stock_daily */
  stock_daily: string;
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
//     • 5 price-vs-MA pairs:  ma_short = 0 (price sentinel), ma_long ∈ {5,20,60,120,252}
//     • 4 MA-vs-MA pairs:     ma_short = 5, ma_long ∈ {20,60,120,252}
//   gap_value = (short_value - long_value) / long_value
//
//   The sec_type is supplied as a query param on every endpoint; responses
//   are scoped to that sec_type (no sec_type field in the payload).
// ----------------------------------------------------------------------------
/** Security type discriminator for the MA-spread analysis. "industry" =
 *  the composite industry entities (code = stats.sec_classification.industry_id). */
export type MaSpreadSecType = "etf" | "index" | "stock" | "industry";

/**
 * One row in a pair's date-level series — the TRIMMED per-pair columns the
 * Simple-MA / EMA charts draw (values, derivatives, Bollinger σ, OHLC,
 * trading amount). Per-date tooltip metrics that are IDENTICAL across all
 * pairs (RSI, trading-amt MA slopes / market shares, rolling-window OHLC)
 * live ONE copy in MovAveSpreadChartResponse.shared — the former
 * MovAveSpreadDetailRow duplicated them into every pair's rows, which
 * tripled the payload.
 */
export interface MovAveSpreadPairRow {
  date: string;
  /** Value of the short series on this date (adj_close ?? close for price; MA/EMA value for MA pairs; trading_amount for amt pairs). */
  short_value: number | null;
  /** Value of the long MA/EMA on this date. */
  long_value: number | null;
  /** (short_value - long_value) / long_value — signed fractional gap. */
  gap_value: number | null;
  /**
   * 1st derivative of the short series — the DB-stored DIRECT diff
   * value[t] − value[t−1] (price_slope for the price legs; ma5_slope /
   * ema6_slope / ema12_slope for the short MA/EMA legs). Null on amt rows
   * and where the window has no stored derivative.
   */
  short_slope: number | null;
  /** 2nd derivative of the short series — the slope's diff. See short_slope. */
  short_curvature: number | null;
  /**
   * 1st derivative of the long MA/EMA — the DB-stored direct diff
   * (ma{5,20}_slope / ema{6,12}_slope). Null for the windows without a
   * persisted derivative (SMA 60/120/252; the EMA26 leg).
   */
  long_slope: number | null;
  /** 2nd derivative of the long MA/EMA — see long_slope. */
  long_curvature: number | null;
  /**
   * Rolling population σ (ddof=0) of price over the long window's span —
   * the Bollinger envelope (long_value ± k × long_std). For SMA pairs from
   * analysis.mov_ave_spreads_detail.std_*days; for EMA pairs from
   * analysis.mov_ave_spreads_detail_ema.std_*days (same price σ); for amt
   * pairs the σ of trading_amt_maW (the amt Bollinger band).
   */
  long_std: number | null;
  /** Open price on this date (null on amt rows — the amt view uses high/low only). */
  open: number | null;
  /** High price on this date. */
  high: number | null;
  /** Low price on this date. */
  low: number | null;
  /** Trading amount in yuan on this date. */
  trading_amount: number | null;
}

/**
 * Per-date tooltip metrics shared by every pair series — ONE copy in the
 * chart response, index-aligned with each pair's rows (all pairs share one
 * date axis). These are tooltip-only readouts; the drawn curves come from
 * the pair rows themselves.
 */
export interface MovAveSpreadSharedMetricsRow {
  date: string;
  /** Wilder RSI over 3/6/10/14/20/60 trading days (analysis.mov_ave_rsi). */
  rsi_3days: number | null;
  rsi_6days: number | null;
  rsi_10days: number | null;
  rsi_14days: number | null;
  rsi_20days: number | null;
  rsi_60days: number | null;
  /** The MATERIALIZED MACD(12,26,9) block (the per-sec_type
   *  stats.*_tech_stats columns via the reconstructed daily views):
   *  macd_line = ema12 − ema26 (DIF), macd_signal = the 9-span EMA of
   *  the macd_line series (DEA), macd_hist = line − signal. NULL for
   *  industry composites (no tech_stats table) and any not-yet-settled
   *  column. */
  macd_line: number | null;
  macd_signal: number | null;
  macd_hist: number | null;
  /** Tail-episode population flags per window (LEFT JOINed from
   *  analysis.trend_episode_days — the consolidated trends component's
   *  store; a missing row = out-of-band): 1 = typical (the side's
   *  quality pass — bottom: isolated first-touch capitulation / top:
   *  re-test double top), 0 = the noisy population pass, null = the
   *  day's RSI is outside both 25% band tails. */
  is_typical_3days: number | null;
  is_typical_6days: number | null;
  is_typical_10days: number | null;
  is_typical_14days: number | null;
  is_typical_20days: number | null;
  is_typical_60days: number | null;
  /** Fractional daily change of trading_amt_ma{W} — tooltip readout when
   *  the trading-amt display is on (analysis.mov_ave_spreads_detail). */
  trading_amt_ma5_slope: number | null;
  trading_amt_ma20_slope: number | null;
  trading_amt_ma60_slope: number | null;
  trading_amt_ma120_slope: number | null;
  trading_amt_ma252_slope: number | null;
  /** Trading-amount market-share MA (ratio 0..1) — tooltip percentage. */
  trading_amt_market_share_ma5: number | null;
  trading_amt_market_share_ma20: number | null;
  trading_amt_market_share_ma60: number | null;
  trading_amt_market_share_ma120: number | null;
  trading_amt_market_share_ma252: number | null;
  /** Rolling OHLC of the pair-matching windows (analysis.mov_ave_spreads_roof_and_floor_lines):
   *  open W trading days ago + max high / min low over the window. Shown in
   *  the tooltip of SMA pairs whose ma_long matches the window. */
  open_20d: number | null;
  high_20d: number | null;
  low_20d: number | null;
  open_60d: number | null;
  high_60d: number | null;
  low_60d: number | null;
  open_120d: number | null;
  high_120d: number | null;
  low_120d: number | null;
  open_252d: number | null;
  high_252d: number | null;
  low_252d: number | null;
}

/**
 * One row of an Amt/MA pair series (loaded on demand via /extras): the
 * trimmed pair columns plus the 5 trading-amount MA values the envelope
 * draws. long_std holds trading_amt_stdW (amt Bollinger band, from
 * analysis.mov_ave_trading_amt); slopes / market shares come from the chart
 * response's `shared` rows — not duplicated here.
 */
export interface MovAveSpreadAmtRow extends MovAveSpreadPairRow {
  trading_amt_ma5: number | null;
  trading_amt_ma20: number | null;
  trading_amt_ma60: number | null;
  trading_amt_ma120: number | null;
  trading_amt_ma252: number | null;
}

/**
 * Rolling-window OHLC extrema for one date — one row per date, index-aligned
 * with every pair's rows (all pairs share one date axis, so a single copy
 * serves all 23 pair series). Source:
 * analysis.mov_ave_spreads_roof_and_floor_lines (renamed 2026-10 from
 * mov_ave_spreads_detail_ohlc)
 * — the table is LONG format (one row per (code, sec_type, date, period)
 * with generic *_over_period columns); the API joins it once per period and
 * aliases the columns back to the per-window names below, so the response
 * shape is unchanged.
 *
 * For each window W ∈ {20, 60, 120, 252, 504, 756, 1260} (the table's
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
 *   change_since_last_high_Wd — close MINUS the value of the LAST high
 *                       anchor of the window (the later-dated of the two
 *                       high anchors — the 2nd whenever present, else the
 *                       top; sign encodes direction — negative = below
 *                       the roof anchor, positive = above it)
 *   days_since_last_high_Wd   — trading days between `date` and that
 *                       last high anchor date
 *   change_since_last_low_Wd  — close MINUS the value of the LAST low
 *                       anchor (the mirror rule on the low side;
 *                       positive = above the floor anchor, negative =
 *                       below it)
 *   days_since_last_low_Wd    — trading days between `date` and that
 *                       last low anchor date
 *
 * The (top, 2nd) high pairs determine the "roof" trendline and the (top,
 * 2nd) low pairs determine the "floor" trendline drawn on the MA-Spread
 * chart (two points determining a line).
 */
export interface MovAveSpreadOhlcRow {
  date: string;

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
  change_since_last_high_60d: number | null;
  days_since_last_high_60d: number | null;
  change_since_last_low_60d: number | null;
  days_since_last_low_60d: number | null;

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
  change_since_last_high_120d: number | null;
  days_since_last_high_120d: number | null;
  change_since_last_low_120d: number | null;
  days_since_last_low_120d: number | null;

  open_252d: number | null;
  high_252d: number | null;
  high_date_252d: string | null;
  high_2nd_252d: number | null;
  high_2nd_date_252d: string | null;
  high_line_slope_252d: number | null;
  low_252d: number | null;
  low_date_252d: string | null;
  low_2nd_252d: number | null;
  low_2nd_date_252d: string | null;
  low_line_slope_252d: number | null;
  change_since_last_high_252d: number | null;
  days_since_last_high_252d: number | null;
  change_since_last_low_252d: number | null;
  days_since_last_low_252d: number | null;

  open_504d: number | null;
  high_504d: number | null;
  high_date_504d: string | null;
  high_2nd_504d: number | null;
  high_2nd_date_504d: string | null;
  high_line_slope_504d: number | null;
  low_504d: number | null;
  low_date_504d: string | null;
  low_2nd_504d: number | null;
  low_2nd_date_504d: string | null;
  low_line_slope_504d: number | null;
  change_since_last_high_504d: number | null;
  days_since_last_high_504d: number | null;
  change_since_last_low_504d: number | null;
  days_since_last_low_504d: number | null;

  open_756d: number | null;
  high_756d: number | null;
  high_date_756d: string | null;
  high_2nd_756d: number | null;
  high_2nd_date_756d: string | null;
  high_line_slope_756d: number | null;
  low_756d: number | null;
  low_date_756d: string | null;
  low_2nd_756d: number | null;
  low_2nd_date_756d: string | null;
  low_line_slope_756d: number | null;
  change_since_last_high_756d: number | null;
  days_since_last_high_756d: number | null;
  change_since_last_low_756d: number | null;
  days_since_last_low_756d: number | null;

  open_1260d: number | null;
  high_1260d: number | null;
  high_date_1260d: string | null;
  high_2nd_1260d: number | null;
  high_2nd_date_1260d: string | null;
  high_line_slope_1260d: number | null;
  low_1260d: number | null;
  low_date_1260d: string | null;
  low_2nd_1260d: number | null;
  low_2nd_date_1260d: string | null;
  low_line_slope_1260d: number | null;
  change_since_last_high_1260d: number | null;
  days_since_last_high_1260d: number | null;
  change_since_last_low_1260d: number | null;
  days_since_last_low_1260d: number | null;
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
   * "ema" = the 6 TYPICAL Exponential MA pairs (2026-10-26 rebase:
   *        short=price or ema6 or ema12, long over the typical spans
   *        {6,12,26} — the classic 12/26 spread IS the MACD line).
   *        Rendered like price pairs but using EMA values from
   *        analysis.mov_ave_spreads_detail_ema.
   * Defaults to "price" for backward compatibility.
   */
  kind?: MovAveSpreadPairKind;
  rows: MovAveSpreadPairRow[];
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

/** The market-regime taxonomy (stats.market_regimes — the daily
 *  4-state label built by builds.market_regimes with shift-1 trailing
 *  inputs, no look-ahead): hot = elevated volatility WITH volume
 *  expansion (the retired boolean "hyped"), panic = elevated volatility
 *  WITHOUT volume expansion (exhaustion/unwind — invisible to the old
 *  AND-gated hype boolean), quiet = volume expansion without price
 *  movement, calm = everything else. */
export type MarketRegime = "calm" | "hot" | "panic" | "quiet";


/** One CONTIGUOUS same-regime run from the stats.market_regime_spans
 *  table (materialized by builds.market_regimes over the daily
 *  registry): startDate / endDate bracket the span (inclusive),
 *  spanDays is its trading-date count. The UI shading source that
 *  replaces the retired hype episodes. */
export interface MarketRegimeSpan {
  regime: MarketRegime;
  startDate: string;
  endDate: string;
  spanDays: number;
}

/** Regime spans keyed by regime — one span list per regime; regimes
 *  with no spans are absent from the map. Source:
 *  stats.market_regime_spans. */
export type MarketRegimeSpans = Record<MarketRegime, MarketRegimeSpan[]>;

/** Response for GET /api/analysis/market-regimes?sec_type=…&code=… —
 *  one (sec_type, code)'s contiguous regime spans, keyed by regime.
 *  Serves the shared CodeTrendChart's Regimes toggle and the MA-Spread
 *  panel's regime chip shading from the same endpoint, regardless of
 *  which analysis page owns the chart. Replaces the retired
 *  MarketHypeEpisodesResponse. */
export interface MarketRegimeSpansResponse {
  secType: string;
  code: string;
  spans: Partial<MarketRegimeSpans>;
}

/** One band-BREAK excursion streak from
 *  analysis.mov_ave_high_low_pct_streaks: a maximal run of trading days
 *  whose adjusted close stayed outside the (period, pctType) high/low
 *  percentile band (close > high_val = high leg, close < low_val = low
 *  leg), with in-band gaps of up to 5 trading days bridged inside one
 *  streak (a 6-day gap ends it). date spans are shipped flat for ALL
 *  (period, pctType) combos — the client filters by its nested
 *  period→pct selection. */
export interface MovAveSpreadHighLowStreak {
  /** Band lookback window in trading rows (252/504/756/1260). */
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

/** Price-speed state of one day (the px_vol family's px_speed —
 *  analysis.mov_ave_price_vs_amt). */
export type MovAveSpreadPxVolSpeed =
  | "sharp_up"
  | "slow_up"
  | "flat"
  | "slow_dn"
  | "sharp_dn";

/** Trading-amount state of one day (the px_vol family's vol_state —
 *  heavy = 放量/increasing, normal = flat, shrink = 缩量/decreasing). */
export type MovAveSpreadPxVolVolState = "heavy" | "normal" | "shrink";

/** One per-date Price × Trading-Amount state from
 *  analysis.mov_ave_price_vs_amt (the px_vol family's date-level
 *  source of truth): every state-valid day joins exactly ONE of the 15
 *  speed × vol categories. */
export interface MovAveSpreadPriceVsAmtDay {
  date: string;
  speed: MovAveSpreadPxVolSpeed;
  vol: MovAveSpreadPxVolVolState;
}

/** Which optional metric group the /extras endpoint loads. Each group feeds
 *  one of the MA-Spread panel's on-demand control sections:
 *    amt      — the 5 Trading Amt/MA pair series (Amt/MA chips → envelope view)
 *    ohlc     — rolling-window OHLC extrema (Roof/Floor Trendlines buttons →
 *               high/low envelope + roof/floor trendlines)
 *    rfest    — quantitative roof/floor LEVEL estimates per method (the
 *               Roof/Floor section's Est-levels toggle → the two horizontal
 *               estimated roof/floor lines at the chosen date)
 *    rfaudit  — the roof/floor REBOUND AUDIT events (recorded extremes →
 *               rebound frequency/amplitude + retest hold/break → the
 *               Roof/Floor section's audit captions)
 *    streaks  — band-break streaks (High/Low Streaks buttons' availability)
 *    pxvol    — per-date Price × Trading-Amount states (Px-Vol States shading)
 *    rsiranks — per-date RSI history ranks (RSI Trends' Top-% shades)
 *    momentum — per-date lookback-adaptive momentum (Momentum overlay lines) */
export type MovAveSpreadMetric = "amt" | "ohlc" | "rfest" | "rfaudit" | "streaks" | "pxvol" | "gaps" | "rsiranks" | "momentum" | "alignment";

/** All metric groups, in canonical order (used to normalize the query param). */
export const MOV_AVE_SPREAD_METRICS = ["amt", "ohlc", "rfest", "rfaudit", "streaks", "pxvol", "gaps", "rsiranks", "momentum", "alignment"] as const;

/** The quantitative roof/floor estimate methods (static.label_dim
 *  'roof_floor_method' codes 0..5; analysis.mov_ave_spreads_roof_floor_
 *  estimates). Registry order; "median" is the cross-method consensus of
 *  the five methods before it. */
export const ROOF_FLOOR_ESTIMATE_METHODS = [
  "trendline", "donchian", "quantile", "channel", "boll", "median",
] as const;
export type RoofFloorEstimateMethod = (typeof ROOF_FLOOR_ESTIMATE_METHODS)[number];

/** The estimate windows (trading days) — the roof/floor windows (the 20d
 *  window is not a roof/floor window). */
export const ROOF_FLOOR_ESTIMATE_WINDOWS = [60, 120, 252, 504, 756, 1260] as const;
export type RoofFloorEstimateWindow = (typeof ROOF_FLOOR_ESTIMATE_WINDOWS)[number];

/** One row of the "rfest" extras group: the estimated roof/floor (resistance/
 *  support) LEVEL AT `date` under every method × window — the table is LONG
 *  (one row per (code, sec_type, date, period, method)); the API pivots it
 *  back to one row per date with `roof_{method}_{W}d` / `floor_{method}_{W}d`
 *  fields, index-aligned with the chart response's pair rows.
 *  Methods: trendline (the anchor-line extrapolation of the roof/floor
 *  lines table), donchian (window max high / min low), quantile (rolling
 *  90th/10th close percentile), channel (OLS fit ± 2σ residual), boll
 *  (SMA ± 2 population σ), median (consensus of the five). NULL where a
 *  method has no level for that (date, window).
 *  `breached_{method}_{W}d` is the table's is_recently_breached flag: TRUE
 *  when ALL of the last 5 trading days closed beyond the fixed d−5 line
 *  (roof side above / floor side below); NULL where no row exists. */
export interface MovAveSpreadRoofFloorEstimateRow
  extends Record<
    | `roof_${RoofFloorEstimateMethod}_${RoofFloorEstimateWindow}d`
    | `floor_${RoofFloorEstimateMethod}_${RoofFloorEstimateWindow}d`,
    number | null
  >,
  Record<
    `breached_${RoofFloorEstimateMethod}_${RoofFloorEstimateWindow}d`,
    boolean | null
  > {
  date: string;
}

/** The side of a roof/floor rebound-audit event. */
export type RoofFloorSide = "floor" | "roof";

/** The rebound-audit funnel outcome of a recorded extreme
 *  (static.label_dim 'rf_rebound_outcome'): no_rebound → never_retested
 *  → held / broken / unresolved. */
export type RfReboundOutcome =
  | "no_rebound"
  | "never_retested"
  | "held"
  | "broken"
  | "unresolved";

/** One recorded-extreme event of the "rfaudit" extras group
 *  (analysis.mov_ave_spreads_roof_floor_rebounds): the episode extreme
 *  of fresh period-day extremes (close[t] is the min/max of
 *  close[t-period+1, t+10]; candidates within 40 trading days cluster
 *  into one episode) with its REBOUND (a close at level*(1±5%) within
 *  60 trading days) and RETEST outcome — held (a close 5% back away
 *  before any close 1% beyond the level) / broken (the reverse) /
 *  unresolved (censored). Amplitudes are fractions: reboundPct signed
 *  (+ floor / − roof), bouncePct / breakDepthPct positive. Durability:
 *  cleanBreak (still beyond the level 20d after the break), laterBreak
 *  (a held level eventually exceeded on a later retest), retestEpisodes
 *  (zone-touch runs separated by ≥10 out-of-zone days). */
export interface MovAveSpreadRoofFloorRebound {
  eventDate: string;
  side: RoofFloorSide;
  /** The event scale (trailing-window W in trading days). */
  period: number;
  /** The recorded extreme CLOSE on eventDate. */
  level: number;
  outcome: RfReboundOutcome;
  reboundDate: string | null;
  retestDate: string | null;
  daysToRetest: number | null;
  reboundPct: number | null;
  bouncePct: number | null;
  breakDepthPct: number | null;
  daysToBreak: number | null;
  cleanBreak: boolean | null;
  laterBreak: boolean | null;
  retestEpisodes: number | null;
}

/** One row of the "momentum" extras group: analysis.mov_ave_momentum, one
 *  row per source-price day. momentum[n] = price[t]/price[t-n] - 1 (signed
 *  fractional). The two fixed anchors (5/20 trading days) plus the three
 *  ADAPTIVE terms whose lookback n is chosen per (code, regime) by
 *  volatility — lb_short/lb_mid/lb_long record the day's effective
 *  lookbacks, so the adaptive values must be read together with them.
 *  regime is the decoded market-regime label; vol_regime the sigma the
 *  lookbacks were derived from. NULL momentum = insufficient history at
 *  that lookback. */
export interface MovAveSpreadMomentumDay {
  date: string;
  regime: MarketRegime;
  vol_regime: number | null;
  lb_short: number;
  lb_mid: number;
  lb_long: number;
  momentum_5days: number | null;
  momentum_20days: number | null;
  momentum_short: number | null;
  momentum_mid: number | null;
  momentum_long: number | null;
}

/** One row of the "rsiranks" extras group: the code's per-date RSI history
 *  ranks (analysis.mov_ave_rsi_ranks, one row per mov_ave_rsi row). Per
 *  window, rank 1 = the code's HIGHEST RSI reading within its own full
 *  history (overbought extreme), N = the lowest; ties break by earlier
 *  date; ranks are dense 1..N so the window's count of ranked days is the
 *  max rank over the rows (the client derives its top-% cutoff from it).
 *  NULL wherever that window's RSI is NULL (before min_periods / flat
 *  windows). */
export interface MovAveSpreadRsiRankDay {
  date: string;
  rank_rsi_3days: number | null;
  rank_rsi_6days: number | null;
  rank_rsi_10days: number | null;
  rank_rsi_14days: number | null;
  rank_rsi_20days: number | null;
  rank_rsi_60days: number | null;
}

/**
 * Response for GET /chart — the DEFAULT load: the 18 Simple-MA + EMA pair
 * series with trimmed rows, plus the ONE shared per-date tooltip-metrics
 * array. The Amt/MA pairs, OHLC extrema, streaks, and px-vol states are
 * NOT included — they load on demand via /extras so the panel renders as
 * soon as the pair views are ready.
 */
export interface MovAveSpreadChartResponse {
  code: string;
  name: string;
  /** 18 pair time series (9 Simple MA + 9 EMA). */
  pairs: MovAveSpreadPairSeries[];
  /** Per-date tooltip metrics shared by every pair series — index-aligned
   *  with each pair's rows (all pairs share one date axis). ONE copy instead
   *  of duplicated into every pair's rows. */
  shared: MovAveSpreadSharedMetricsRow[];
}

// ----------------------------------------------------------------------------
// MA-Spread chart BLOCKS — per-table async loads. The default monolithic
// /chart response joins ~6 tables into a ~90-column row and fans it out into
// 18 pair series in ONE request; for a full stock history that is an ~8.5MB
// JSON whose Node-side fan-out + serialization dominates the response time
// (DB is <1s of it). The panel therefore fires three table-aligned blocks in
// PARALLEL and renders as soon as the REQUIRED pairs block lands:
//   • /chart/pairs  — stats price/MA tables + detail gaps → default chart +
//     the 9 Simple-MA chips (REQUIRED; drives the date axis)
//   • /chart/ema    — EMA detail table → the 9 EMA chips (best-effort)
//   • /chart/shared — RSI table + OHLC pivot + amt metrics → tooltip
//     metrics (best-effort; the option builder already tolerates null)
// ----------------------------------------------------------------------------

/** GET /chart/pairs — the REQUIRED block. */
export interface MovAveSpreadChartPairsResponse {
  code: string;
  name: string;
  /** 9 Simple-MA pair series (kind "price"). */
  pairs: MovAveSpreadPairSeries[];
}

/** GET /chart/ema — best-effort block: the 9 EMA pair series. */
export interface MovAveSpreadChartEmaResponse {
  code: string;
  pairs: MovAveSpreadPairSeries[];
}

/** GET /chart/shared — best-effort block: the per-date tooltip metrics. */
export interface MovAveSpreadChartSharedResponse {
  code: string;
  shared: MovAveSpreadSharedMetricsRow[];
}

/** Response for GET /extras?sec_type=…&code=…&metrics=amt,ohlc — only the
 *  requested groups are present (the rest is undefined). */
export interface MovAveSpreadExtrasResponse {
  code: string;
  sec_type: MaSpreadSecType;
  /** The groups served in this response (normalized to canonical order). */
  metrics: MovAveSpreadMetric[];
  /** 5 trading-amount pair series (kind "amt"; rows are MovAveSpreadAmtRow). */
  amtPairs?: MovAveSpreadPairSeries[];
  /** Rolling-window OHLC extrema (all 7 windows), index-aligned with the
   *  chart response's pair rows. */
  ohlc?: MovAveSpreadOhlcRow[];
  /** Quantitative roof/floor level estimates per method × window
   *  (rfest group), index-aligned with the chart response's pair rows. */
  rfEstimates?: MovAveSpreadRoofFloorEstimateRow[];
  /** Roof/floor rebound-audit events (rfaudit group) — the code's
   *  recorded extremes with rebound/retest outcomes, ascending by
   *  eventDate. */
  rfRebounds?: MovAveSpreadRoofFloorRebound[];
  /** High/low band-BREAK excursion streaks, flat across all (period,
   *  pctType) combos — gates the High/Low Streaks buttons. */
  highLowStreaks?: MovAveSpreadHighLowStreak[];
  /** Per-date Price × Trading-Amount state registry rows — drives the
   *  Px-Vol States shading. */
  priceVsAmt?: MovAveSpreadPriceVsAmtDay[];
  /** Per-day OHLC gap anatomy + the top-decile wick flags + the
   *  classified pattern (analysis.mov_ave_spreads_detail_ohlc_gaps) —
   *  drives the Top-10% OHLC Days group. */
  ohlcGaps?: MovAveSpreadOhlcGapDay[];
  /** Per-date RSI history ranks (analysis.mov_ave_rsi_ranks) — drives
   *  the RSI Trends overlay's Top-% vertical shades. */
  rsiRanks?: MovAveSpreadRsiRankDay[];
  /** Per-date lookback-adaptive momentum (analysis.mov_ave_momentum) —
   *  drives the Momentum overlay's fixed + adaptive term lines. */
  momentum?: MovAveSpreadMomentumDay[];
  /** Per-date MA-chain alignment (analysis.mov_ave_spreads_detail's
   *  alignment_score/state) — drives the Alignment section's trajectory
   *  chart + verdict chip. */
  alignment?: MovAveSpreadAlignmentDay[];
}

/** The five-leg MA-chain alignment verdict (static.label_dim
 *  'ma_alignment' codes, decoded server-side): all five legs of
 *  close>ma5>ma20>ma60>ma120>ma252 positive = bullish 多头排列, all
 *  negative = bearish 空头排列, otherwise mixed 纠缠. */
export type MaAlignment = "bearish" | "mixed" | "bullish";

/** One row of the "alignment" extras group: the code's per-date
 *  alignment score (count of positive legs, 0..5) and its decoded
 *  verdict. NULL score = insufficient MA history (the ma252 leg not yet
 *  realized) — never a verdict. */
export interface MovAveSpreadAlignmentDay {
  date: string;
  alignment_score: number | null;
  alignment_state: MaAlignment | null;
}

/** One day's OHLC gap anatomy row (the gaps store's wide row, trimmed
 *  to what the chart group reads). Flags are the wick ≥ the code's
 *  trailing-252d 90th percentile verdicts (NULL = window too short);
 *  pattern ∈ gap_up_advance / gap_up_decline / gap_down_advance /
 *  gap_down_decline / spike_fade / flush_recover / neutral (NULL =
 *  unknown anatomy). */
export interface MovAveSpreadOhlcGapDay {
  date: string;
  gap_open: number | null;
  intra: number | null;
  uw: number | null;
  lw: number | null;
  uw_top10: boolean | null;
  lw_top10: boolean | null;
  pattern: string | null;
}

// ----------------------------------------------------------------------------
//  Analysis Commons — Forecast buckets (analysis_forecasts schema)
//
//    REMOVED 2026-10-05 with the forecast layer (tables dropped, package
//    + SQL files deleted): the signals tier's rsi-pct strategies are
//    computed in-engine and surface via the trading-signals endpoints.
//
// ----------------------------------------------------------------------------
//  Analysis Commons — PE (per-(sec_type, code, date) valuation)
//    analysis.pe / analysis.dividends   — daily raw pe / trailing-12m dividend_yield (split 2026-09)
//    PK (detail): (sec_type, code, date)
//
//    (The former annual pe_stats / dividends_stats snapshot tables were
//    removed 2026-09-28 with their only consumer, the Dividend Stats card.)
//
//    Close price is NOT stored in the analysis tables (it lives in
//    stats: index_basic_stats.close, etf_basic_stats.close,
//    stock_basic_stats.close). The chart endpoint JOINs stats live at
//    request time so the UI always shows the freshest close.
// ----------------------------------------------------------------------------
/** "industry" = the composite industry entities (code = industry_id). */
export type PeSecType = "etf" | "index" | "stock" | "industry";

/** One daily row JOINed from analysis.pe / analysis.dividends + stats close. */
export interface PeChartRow {
  /** Trading date (YYYY-MM-DD). */
  date: string;
  /** Close price from stats (index_basic_stats.close / etf adj_close /
   *  stock_basic_stats.close). NULL when the source has no close on this date. */
  close: number | null;
  /** Raw PE ratio (analysis.pe.pe — all sec_types; NULL on
   *  no-earnings / invalid-PE days). */
  pe: number | null;
  /** Trailing-12m dividend yield (D/P) as a fractional ratio (0.035 = 3.5%). */
  dividend_yield: number | null;
}

/** Response for GET /api/analysis/pe/chart. */
export interface PeChartResponse {
  code: string;
  name: string;
  rows: PeChartRow[];
}

// ----------------------------------------------------------------------------
//  Analysis Commons — Dividend Deliveries (analysis.dividend_deliveries)
//    Rolling 5-year (1-year stride) dividend delivery audit per
//    (sec_type, code, window_end_date); etf + stock (an index has no
//    per-share deliveries). Built by analysis.pe_and_dividends
//    --metrics deliveries; the Dividends page renders it as the
//    "Dividend Deliveries" table under the baseline code-trend chart
//    (where the renamed analysis.dividend_est estimation curve stays as
//    the dashed "Div Est" overlay).
// ----------------------------------------------------------------------------
/** One rolling-window audit row (newest first). */
export interface DividendDeliveryRow {
  /** Window anchor: the calendar year's last data date (current year =
   *  latest close — the open window). */
  window_end_date: string;
  /** window_end_date - 5 calendar years + 1 day. */
  window_start_date: string;
  /** Delivery events (ex-date with D > 0) inside the window. */
  deliveries: number;
  /** Distinct event calendar years inside the window (0..6). */
  years_with_delivery: number;
  /** Mean of the per-event yields D / close(ex-date) (fractional). */
  avg_yield: number | null;
  /** Σ per-event D/close ÷ window span years (~5) — the window's average
   *  ANNUAL cash yield on per-event prices; NULL when no delivery. */
  annualized_yield: number | null;
  /** Σ per-event D/close over the final 365d of the window — the
   *  trailing-1-year yield as of the anchor (0 when the window has
   *  deliveries but none in the last year; NULL when no delivery). */
  latest_year_yield: number | null;
  /** Mean of the per-event eps (close/pe at the ex-date); NULL when the
   *  window has no delivery or no EPS source. */
  avg_eps: number | null;
  /** Deliveries with a same-code delivery within ±20 days of the same
   *  date one year earlier (last-year ±20d repeat rule). */
  stable_events: number;
  /** stable_events / deliveries; NULL when no delivery. */
  stability: number | null;
}

/** Response for GET /api/analysis/dividends/deliveries. */
export interface DividendDeliveriesResponse {
  code: string;
  secType: PeSecType;
  rows: DividendDeliveryRow[];
}

// ----------------------------------------------------------------------------
//  PE band-BREAK excursion streaks (analysis.pe_pct +
//  analysis.pe_pct_streaks) — the high/low streaks pattern of
//  analysis.mov_ave_high_low_pct[_streaks] applied to the pe series. A day
//  breaks out when its pe value is ABOVE its own month-band high_val
//  (high — PE is stretched vs its own trailing history) or BELOW low_val
//  (low — compressed); a streak is the maximal run of same-side break days
//  with up to 5 consecutive in-band trading days bridged (a 6-day gap or a
//  side switch ends it). (2026-09-28: PE-only — the dividend_yield half,
//  analysis.dividends_pct[_streaks], was dropped with its unused consumer
//  path.)
// ----------------------------------------------------------------------------

/** The audited series — always PE (the dividend_yield streak half was
 *  removed 2026-09-28). */
export type PeStreakMetric = "pe";

/** One band-BREAK excursion streak from analysis.pe_pct_streaks. Shipped
 *  flat for ALL (period, pctType) combos — the client filters by its
 *  nested selection. */
export interface PeStreak {
  /** Which valuation series was audited — always "pe". */
  metric: PeStreakMetric;
  /** Band lookback window in observations (252/504/756/1260). */
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

/** Response for GET /api/analysis/pe/streaks. */
export interface PeStreaksResponse {
  code: string;
  name: string;
  /** Flat per-streak array, ascending by metric, period, pctType,
   *  startDate. */
  streaks: PeStreak[];
}

/** One row in the codes list (UNION of analysis.pe / analysis.dividends, latest snapshot). */
export interface ValuationCodeRow {
  code: string;
  name: string;
  first_date: string;
  last_date: string;
  n_dates: number;
  /** Latest snapshot's raw pe (NULL on no-earnings / invalid-PE days). */
  latest_pe: number | null;
  /** Latest snapshot's dividend_yield (fractional ratio). */
  latest_dividend_yield: number | null;
}

/** Response for GET /api/analysis/valuation/codes — the SHARED PE ∪
 *  Dividends universe (both analysis pages' SecCodesPageShell nav + codes). */
export interface ValuationCodesResponse {
  codes: ValuationCodeRow[];
}

// ----------------------------------------------------------------------------
//  Analysis Commons — Market Crowdedness (analysis.market_crowdedness)
//    Per-(code, sec_type, date) crowdedness curve for indices and
//    composite industries, built by analysis.market_crowdedness
//    (database/sql/analysis/29_market_crowdedness.sql). crowdedness_score
//    = 0.5*concentration + 0.5*flow_change computed from the pool's
//    dynamic hot set = top 10% of the day's active pool by trading
//    amount, floored at 5 names. The nav/codes universe is
//    the FULL classification (stats.sec_classification type='index' +
//    stats.industry_identity entities), deliberately NOT restricted to
//    codes with rows; codes outside the pipeline scope (index side =
//    the 10 study indices) plot close + amount only. Chart rows are
//    driven by the stats close source (one row per close day) so the
//    chart always plots the code's close + trading amount; the metric is
//    LEFT JOINed live.
// ----------------------------------------------------------------------------
/** The sec_types the Market Crowdedness page exposes and the pipeline
 *  is scoped to (no etf/stock — the page toggle lists only these two). */
export type MarketCrowdednessSecType = "index" | "industry";

/** One daily chart row: stats close + trading amount with the metric
 *  LEFT JOINed (NULL on dates with no defined crowdedness row). */
export interface MarketCrowdednessChartRow {
  /** Trading date (YYYY-MM-DD). */
  date: string;
  /** Close price read live from stats (index_basic_stats.close /
   *  industry_basic_stats.mean_close, pool-'all' slice). */
  close: number | null;
  /** Trading amount in yuan, read live from stats
   *  (index_basic_stats.trading_amount /
   *  industry_basic_stats.total_trading_amount). NULL when the source
   *  has no amount on the date. */
  trading_amount: number | null;
  /** Market crowdedness (0..1, higher = more crowded) = renormalized
   *  blend 0.30*concentration_pct + 0.30*flow_pct +
   *  0.25*comovement_pct + 0.15*valuation_pct over the pillars defined
   *  on the date; each *_pct reads its pillar against the pool's own
   *  trailing-250-day history (percentile). NULL on dates with no
   *  defined row (percentile warm-up, out-of-scope codes). */
  crowdedness_score: number | null;
  /** RAW level pillar (0..1): the STABLE hot set's (top 10% by
   *  60-active-day MEAN amount, min 5 names) share of the pool's
   *  5-active-day MEAN trading amount. NULL where crowdedness_score is
   *  NULL. */
  concentration: number | null;
  /** RAW flow pillar (0..1): the hot set's share of the pool's gross
   *  increase of the 5-active-day MEAN amount (sustained multi-day
   *  inflow). NULL where crowdedness_score is NULL. */
  flow_change: number | null;
  /** RAW co-movement pillar (−1..1): mean over the hot set of the
   *  trailing-120-active-day correlation between the stock's return
   *  (pct_change, or close/prev_close where unpublished) and the
   *  pool's leave-one-out equal-weight return. NULL when no hot member
   *  has a defined correlation. */
  comovement: number | null;
  /** RAW valuation pillar (0..1, 0.5 = hot names at the pool median):
   *  mean over the hot set of each stock's pe cross-sectional
   *  percentile within the day's active pool. NULL when no hot member
   *  has a valid pe. */
  valuation: number | null;
  /** Turnover-concentration evidence (not a score input): the top 1% of
   *  the active pool by DAY amount (min 1 name) and their share of the
   *  day's pool amount. */
  top1_share: number | null;
  /** Turnover-concentration evidence: top 5% by DAY amount, share of
   *  the day's pool amount. */
  top5_share: number | null;
  /** Turnover-concentration evidence: top 10% by DAY amount, share of
   *  the day's pool amount (the pre-2026-11-01 concentration
   *  definition, kept as evidence). */
  top10_share: number | null;
  /** Turnover-concentration evidence: effective traded-name count
   *  (Σ amt)² / Σ amt² (inverse Herfindahl, 1..active_n) — how many
   *  names the pool's trading is effectively spread over. */
  effective_n: number | null;
  /** concentration vs the pool's own trailing-250-day history
   *  (percentile, 0..1) — a score input. */
  concentration_pct: number | null;
  /** flow_change vs the pool's own trailing-250-day history
   *  (percentile, 0..1) — a score input. */
  flow_pct: number | null;
  /** comovement vs the pool's own trailing-250-day history
   *  (percentile, 0..1) — a score input; NULL where comovement is
   *  NULL (the pillar drops out of the blend with its weight). */
  comovement_pct: number | null;
  /** valuation vs the pool's own trailing-250-day history (percentile,
   *  0..1) — a score input; NULL where valuation is NULL. */
  valuation_pct: number | null;
  /** 5-pool-date-ROW moving average of crowdedness_score — the ONLINE
   *  smoothing layer (raw day-over-day movement is dominated by
   *  single-day pillar noise); on a pinned variant it rolls within the
   *  variant's own series. */
  crowdedness_score_ma5: number | null;
  /** Comma-joined display names of the date's hot set (top 10% of the
   *  active pool by 60-active-day MEAN amount, min 5 names) — tooltip
   *  evidence. */
  top_stocks: string | null;
}

/** Response for GET /api/analysis/market-crowdedness/chart. */
export interface MarketCrowdednessChartResponse {
  code: string;
  name: string;
  rows: MarketCrowdednessChartRow[];
}

/** One row of the News Text States chart (text.news_text_daily — built
 *  by analysis.news_text_daily): the day's news-text state panel for one
 *  scope, with the security's close read live from stats for price
 *  context. Metric fields NULL on dates with no defined row (the
 *  n>=3-scored guard / window warm-up); industry-kind rows exist only
 *  on event days. Validated readings (2026-11-21 study): the level is
 *  state, not a direction call; sent_ema10 is contrarian at the monthly
 *  tail; flow_af is the abnormal-coverage risk flag (negative forward
 *  IC). The sentiment_z/sentiment_ma5/flow_z/kw_tone tail is the RAW
 *  mood layer LEFT JOINed from analysis.text_metrics_daily (built by
 *  analysis.text_metrics — the merged-in Text Sentiment Metrics page's
 *  LLM-only Tetlock z family): '' (broad market) for the market scopes,
 *  the scope's industry_id for industry scopes, always NULL for sector
 *  scopes (no raw rows). */
export interface NewsTextStatesChartRow {
  /** Trading date (YYYY-MM-DD) of the close row. */
  date: string;
  /** Close price read live from stats (SSE Composite for the market
   *  scopes, industry_basic_stats.mean_close pool-'all' slice for
   *  industries). */
  close: number | null;
  /** Articles in scope that day (the coverage basis flow_af reads). */
  n_articles: number | null;
  /** Digested non-fake articles entering the sentiment aggregates. */
  n_scored: number | null;
  /** Lexicon-blended mean sentiment [-5,+5] (0.7*LLM + 0.3*CFSD, a
   *  disagreeing article weighted 0.5) — state, not direction. */
  sent_level: number | null;
  /** (level − trailing-60d mean)/std, min 20 obs. */
  sent_z: number | null;
  /** ewm(sent_z, halflife 3) — the shock line. */
  sent_ema3: number | null;
  /** ewm(sent_z, halflife 10) — the persistent line (validated
   *  contrarian at the monthly tail). */
  sent_ema10: number | null;
  /** Level day-over-day change (the CFSD paper's "change" predictor). */
  sent_delta: number | null;
  /** (n_articles − trailing-63d median)/IQR, min 40 obs — the
   *  abnormal-coverage risk flag (|af| ≥ 1 = abnormal). */
  flow_af: number | null;
  /** Articles blended-scored > +1. */
  flow_pos: number | null;
  /** Articles blended-scored < −1 (a flag, never bucketed). */
  flow_neg: number | null;
  /** Std across per-origin mean scores (≥ 2 origins; NULL otherwise). */
  disp_origin: number | null;
  /** kw_market px-tone keyword z (up-family minus down-family mentions)
   *  — the lexicon cross-check on the LLM curves. */
  lex_tone_z: number | null;
  /** TRUE when n_scored < 3 — no reliable reading that day. */
  low_coverage: boolean;
  /** RAW layer — Tetlock (2007) abnormal sentiment from
   *  analysis.text_metrics_daily: the day's LLM-only mean deviation
   *  from its trailing 60-article-day history, in std units. */
  sentiment_z: number | null;
  /** RAW layer — sentiment_z smoothed over 5 article-days. */
  sentiment_ma5: number | null;
  /** RAW layer — abnormal news volume (article_count vs its trailing
   *  60-article-day distribution; Heston-Sinha flow z). */
  flow_z: number | null;
  /** RAW layer — (bull − bear) / (bull + bear) kw_market tone,
   *  −1..+1 — the deterministic lexicon cross-check; NULL when no
   *  directional mentions. */
  kw_tone: number | null;
}

/** Response for GET /api/analysis/news-text-states/chart. */
export interface NewsTextStatesChartResponse {
  /** The requested scope kind ('news_scope_kind' code). */
  scope_kind: number;
  /** The requested scope code ('000001' market, industry_id otherwise). */
  scope_code: string;
  rows: NewsTextStatesChartRow[];
}

/** One row of the News Text States scope list (the selector source). */
export interface NewsTextStatesScopeRow {
  /** 'news_scope_kind' code (0 market_all / 1 market_recap / 2
   *  industry / 3 sector). */
  scope_kind: number;
  /** Decoded kind label (static.label_dim index). */
  scope_kind_label: string;
  /** '000001' for the market kinds, the industry_id for kind 2, the
   *  L1 sector_id for kind 3. */
  scope_code: string;
  /** Canonical Chinese display name from the classification (the raw
   *  code when unmapped). */
  scope_label: string;
  /** Panel rows present for the scope. */
  rows: number;
  /** Earliest panel date. */
  min_date: string;
  /** Latest panel date. */
  max_date: string;
}

/** Response for GET /api/analysis/news-text-states/scopes. */
export interface NewsTextStatesScopesResponse {
  rows: NewsTextStatesScopeRow[];
}

/** One row of the News Text States industry event days (kind 2 — a row
 *  only on days the industry has scored articles). */
export interface NewsTextStatesEventRow {
  date: string;
  industry_id: string;
  /** Canonical Chinese display name from the classification (the raw
   *  code when unmapped). */
  industry_label: string;
  /** Articles in the industry scope that day. */
  n_articles: number;
  /** Scored articles (low_coverage below 3). */
  n_scored: number;
  /** Blended event-day mean sentiment (NULL never — ≥ 1 scored). */
  sent_level: number | null;
  flow_pos: number;
  flow_neg: number;
  low_coverage: boolean;
}

/** Response for GET /api/analysis/news-text-states/events. */
export interface NewsTextStatesEventsResponse {
  rows: NewsTextStatesEventRow[];
}

/** One day of the lexicon-vs-LLM disagreement series (the ambiguity
 *  flag — text.news_lexicon_scores over the digested corpus). */
export interface NewsTextStatesDisagreementRow {
  date: string;
  /** CFSD-scored articles that day. */
  scored_count: number;
  /** Of them, flagged disagreeing (sign mismatch AND |llm−lex| ≥ 2 —
   *  the flag that halves the article's daily-blend weight). */
  disagree_count: number;
}

/** Response for GET /api/analysis/news-text-states/disagreement. */
export interface NewsTextStatesDisagreementResponse {
  rows: NewsTextStatesDisagreementRow[];
}

/** One strong-negative industry event day (kind 2, blended level
 *  ≤ −1.5 — the study's strong-neg cut; historically followed by
 *  positive 5d industry returns, suggestive 6-13-event evidence). */
export interface NewsTextStatesStrongNegEventRow {
  date: string;
  industry_id: string;
  /** Canonical Chinese display name from the classification (the raw
   *  code when unmapped). */
  industry_label: string;
  n_articles: number;
  n_scored: number;
  sent_level: number;
  flow_pos: number;
  flow_neg: number;
  low_coverage: boolean;
}

/** Response for GET /api/analysis/news-text-states/strong-neg-events. */
export interface NewsTextStatesStrongNegEventsResponse {
  rows: NewsTextStatesStrongNegEventRow[];
}

/** One scope's latest event-day reading in the recent-levels window
 *  (kinds 2 industry / 3 sector — the cross-section the bar overview
 *  plots; a scope with no event-day row in the window is absent). */
export interface NewsTextStatesRecentLevelRow {
  /** 'news_scope_kind' code (2 industry / 3 sector). */
  scope_kind: number;
  /** The industry_id (kind 2) or L1 sector_id (kind 3). */
  scope_code: string;
  /** Canonical Chinese display name from the classification (the raw
   *  code when unmapped). */
  scope_label: string;
  /** The reading's event day. */
  date: string;
  /** Scored articles behind the reading (low_coverage below 3). */
  n_scored: number;
  /** Blended event-day mean sentiment (NULL never — ≥ 1 scored). */
  sent_level: number;
  flow_pos: number;
  flow_neg: number;
  low_coverage: boolean;
}

/** Response for GET /api/analysis/news-text-states/recent-levels. */
export interface NewsTextStatesRecentLevelsResponse {
  rows: NewsTextStatesRecentLevelRow[];
}

/** One row of the Market Crowdedness codes list (full universe). */
export interface MarketCrowdednessCodeRow {
  code: string;
  name: string;
}

/** Response for GET /api/analysis/market-crowdedness/codes. */
export interface MarketCrowdednessCodesResponse {
  codes: MarketCrowdednessCodeRow[];
}

/** Response for GET /api/analysis/market-crowdedness/hot-years — the
 *  year-pinned hot-set variants available for one code (calendar
 *  years, newest first). Picking one re-fetches the chart with
 *  hot_year set, swapping the metric to the pinned variant (the hot
 *  basket frozen to that year's roster, applied across the whole
 *  history). */
export interface MarketCrowdednessHotYearsResponse {
  code: string;
  years: number[];
}

// ----------------------------------------------------------------------------
//  Market Crowdedness hot-tech stats — GET /api/analysis/market-
//  crowdedness/hot-tech. The hot stocks' typical technical profile from
//  analysis.market_crowdedness_hot_tech: per (code, sec_type, date) the
//  equal-weight mean + median over the day's ROLLING hot set (top 10%
//  of the active pool by 60-active-day mean amount, min 5) of eight
//  per-stock metrics, each aggregate also read as a percentile vs the
//  pool's own trailing-250-day history (>= 60 obs). Driven by the stats
//  close source (one row per close day) so the chart always plots the
//  close; the stats are LEFT JOINed live.
// ----------------------------------------------------------------------------

/** The eight hot-tech metrics (the stat-switcher button keys). All are
 *  decimal fractions except macd_hist (price units) and rsi14 (0..100). */
export type MarketCrowdednessHotTechMetric =
  | "ma5_ratio"
  | "px_vs_ma20"
  | "px_vs_ma60"
  | "macd_hist"
  | "rsi14"
  | "roc20"
  | "amt_vs_ma20"
  | "ret";

/** One daily hot-tech chart row: stats close with the hot-tech
 *  aggregates LEFT JOINed (NULL on dates with no defined hot-tech row —
 *  pre-gate days, out-of-scope codes; *_pct additionally NULL during
 *  the pool's percentile warm-up). */
export interface MarketCrowdednessHotTechRow {
  /** Trading date (YYYY-MM-DD). */
  date: string;
  /** Close price read live from stats (index_basic_stats.close /
   *  industry_basic_stats.mean_close, pool-'all' slice). */
  close: number | null;
  /** The day's hot-set size = the members the aggregates cover (top
   *  max(ceil(0.1*active_n), 5) of the active pool by 60-active-day
   *  mean amount). NULL on dates with no hot-tech row. */
  hot_n: number | null;
  /** Mean over the hot set of the precomputed ma5_ratio
   *  (stats.stock_tech_stats: close/ma5 - 1; 0 = at MA5). */
  mean_ma5_ratio: number | null;
  /** mean_ma5_ratio vs the pool's own trailing-250-day history
   *  (percentile, 0..1). */
  mean_ma5_ratio_pct: number | null;
  /** Median over the hot set of ma5_ratio. */
  median_ma5_ratio: number | null;
  /** median_ma5_ratio vs the pool's own trailing-250-day history. */
  median_ma5_ratio_pct: number | null;
  /** Mean over the hot set of close/ma20 - 1. */
  mean_px_vs_ma20: number | null;
  /** mean_px_vs_ma20 vs the pool's own trailing-250-day history. */
  mean_px_vs_ma20_pct: number | null;
  /** Median over the hot set of px_vs_ma20. */
  median_px_vs_ma20: number | null;
  /** median_px_vs_ma20 vs the pool's own trailing-250-day history. */
  median_px_vs_ma20_pct: number | null;
  /** Mean over the hot set of close/ma60 - 1. */
  mean_px_vs_ma60: number | null;
  /** mean_px_vs_ma60 vs the pool's own trailing-250-day history. */
  mean_px_vs_ma60_pct: number | null;
  /** Median over the hot set of px_vs_ma60. */
  median_px_vs_ma60: number | null;
  /** median_px_vs_ma60 vs the pool's own trailing-250-day history. */
  median_px_vs_ma60_pct: number | null;
  /** Mean over the hot set of the precomputed MACD histogram (price
   *  units; sign = momentum direction). */
  mean_macd_hist: number | null;
  /** mean_macd_hist vs the pool's own trailing-250-day history. */
  mean_macd_hist_pct: number | null;
  /** Median over the hot set of macd_hist. */
  median_macd_hist: number | null;
  /** median_macd_hist vs the pool's own trailing-250-day history. */
  median_macd_hist_pct: number | null;
  /** Mean over the hot set of RSI(14), SMA-smoothed (Cutler form,
   *  0..100; the GPU-native rolling-mean formulation, not Wilder's). */
  mean_rsi14: number | null;
  /** mean_rsi14 vs the pool's own trailing-250-day history. */
  mean_rsi14_pct: number | null;
  /** Median over the hot set of rsi14. */
  median_rsi14: number | null;
  /** median_rsi14 vs the pool's own trailing-250-day history. */
  median_rsi14_pct: number | null;
  /** Mean over the hot set of the 20-active-row rate of change
   *  (close/close[-20] - 1). */
  mean_roc20: number | null;
  /** mean_roc20 vs the pool's own trailing-250-day history. */
  mean_roc20_pct: number | null;
  /** Median over the hot set of roc20. */
  median_roc20: number | null;
  /** median_roc20 vs the pool's own trailing-250-day history. */
  median_roc20_pct: number | null;
  /** Mean over the hot set of amount/MA20(amount) - 1 — the per-stock
   *  放量/缩量 gauge (positive = participation above its own MA20). */
  mean_amt_vs_ma20: number | null;
  /** mean_amt_vs_ma20 vs the pool's own trailing-250-day history. */
  mean_amt_vs_ma20_pct: number | null;
  /** Median over the hot set of amt_vs_ma20. */
  median_amt_vs_ma20: number | null;
  /** median_amt_vs_ma20 vs the pool's own trailing-250-day history. */
  median_amt_vs_ma20_pct: number | null;
  /** Mean over the hot set of the day's return (decimal). */
  mean_ret: number | null;
  /** mean_ret vs the pool's own trailing-250-day history. */
  mean_ret_pct: number | null;
  /** Median over the hot set of the day's return. */
  median_ret: number | null;
  /** median_ret vs the pool's own trailing-250-day history. */
  median_ret_pct: number | null;
}

/** Response for GET /api/analysis/market-crowdedness/hot-tech. */
export interface MarketCrowdednessHotTechResponse {
  code: string;
  name: string;
  rows: MarketCrowdednessHotTechRow[];
}

// ----------------------------------------------------------------------------
//  Volatility — GET /api/analysis/volatility/*. The per-(sec_type,
//  code, date) realized-volatility panel from analysis.volatility,
//  built by analysis.volatility (structured for all securities; run
//  scope = index only for now — the 10 study indices). All *vol*
//  fields are PERCENT ANNUALIZED (daily vol × √252 × 100 — 23.45 means
//  23.45%/year) except atr_14 (price level, index points) and
//  vol_ratio_20_252 (a ratio). Estimator warm-ups leave early rows NULL
//  (hv_W from row W+1 on; percentiles/z from ~60 obs); the 5-min
//  realized-vol leg is NULL outside the intraday-bar retention window
//  (~90 days).

/** One daily volatility row (analysis.volatility + the joined raw
 *  close from the sec_type's bars source). */
export interface VolatilityRow {
  /** Trading date (YYYY-MM-DD). */
  date: string;
  /** Raw session close (index points / price level — feeds the UI's
   *  price overlay line on every vol chart). */
  close: number | null;
  /** Close-to-close historical vol, std of daily log returns (ddof=1)
   *  over the trailing W rows, % annualized. */
  hv_5: number | null;
  hv_10: number | null;
  hv_20: number | null;
  hv_60: number | null;
  hv_120: number | null;
  hv_252: number | null;
  /** RiskMetrics EWMA vol (λ=0.94, contemporaneous), % annualized. */
  ewma_vol: number | null;
  /** Parkinson estimator (20-row mean of daily ln(H/L)²/(4·ln2)),
   *  % annualized. */
  parkinson_vol_20: number | null;
  /** Garman-Klass estimator (OHLC), % annualized. */
  garman_klass_vol_20: number | null;
  /** Rogers-Satchell estimator (drift-robust), % annualized. */
  rogers_satchell_vol_20: number | null;
  /** Yang-Zhang estimator (gap + drift robust), % annualized. */
  yang_zhang_vol_20: number | null;
  /** Overnight-gap vol (std of ln(O/C_prev), 20 rows), % annualized. */
  overnight_vol_20: number | null;
  /** Open-to-close intraday vol (std of ln(C/O), 20 rows), %
   *  annualized. */
  intraday_vol_20: number | null;
  /** Wilder ATR(14) — price level (index points), NOT annualized. */
  atr_14: number | null;
  /** NATR(14) = ATR/close × 100 (percent of level). */
  natr_14: number | null;
  /** Bollinger band width: 4·σ_price20 / ma20 × 100 (price-level σ). */
  boll_bw_20: number | null;
  /** Vol-of-vol: std of the hv_20 series over 20 rows (vol points). */
  vol_of_vol_20: number | null;
  /** Short/long vol ratio hv_20 / hv_252 (> 1 expanding). */
  vol_ratio_20_252: number | null;
  /** Trailing-252-row percentile of hv_20 (0..1, >= 60 obs). */
  hv_20_pct_rank_252: number | null;
  /** Trailing-252-row percentile of ewma_vol (0..1). */
  ewma_vol_pct_rank_252: number | null;
  /** Trailing-252-row percentile of yang_zhang_vol_20 (0..1). */
  yang_zhang_vol_20_pct_rank_252: number | null;
  /** Trailing-252-row percentile of natr_14 (0..1). */
  natr_14_pct_rank_252: number | null;
  /** z-score of hv_20 vs its trailing-252 moments (shifted 1 row). */
  hv_20_z_252: number | null;
  /** z-score of ewma_vol vs its trailing-252 moments (shifted 1 row). */
  ewma_vol_z_252: number | null;
  /** 5-minute realized vol (Σ intra-session r² × 252, sqrt), %
   *  annualized; NULL where stats.index_intraday_5min has no bars. */
  rv_5min: number | null;
  /** Realized-variance jump gauge max(0, RV/BPV − 1) (bipower
   *  variation); > 0 flags jump-dominated sessions. */
  jump_ratio_5min: number | null;
  /** The day's 5-min bar count (quality gauge, ~48/session). */
  rv_bar_count: number | null;
}

/** Response for GET /api/analysis/volatility/series — one security's
 *  full history (the UI slices via dataZoom). */
export interface VolatilitySeriesResponse {
  code: string;
  name: string;
  rows: VolatilityRow[];
}

/** One row of the Volatility codes list (the codes with vol rows —
 *  the 10 study indices for now). */
export interface VolatilityCodeRow {
  code: string;
  name: string;
}

/** Response for GET /api/analysis/volatility/codes. */
export interface VolatilityCodesResponse {
  codes: VolatilityCodeRow[];
}

// ----------------------------------------------------------------------------
//  Oracle — GET /api/analysis/oracle/*. The hindsight ORACLE tier from
//  analysis.oracle_signals (DECOUPLED from the rule tier; run scope =
//  index only for now — the 10 study indices): v2 pivot grammar — one
//  row per ORDER-5 local extreme: buy at every local minimum, sell at
//  every local maximum (close[date] is the min/max of the +-5-row
//  window; definition study 2026-11-19). Each row's ext_* legs carry
//  the next OPPOSITE pivot — the hindsight swing target (NULL at the
//  frontier, where that opposite pivot is not confirmed yet). The
//  trail ends 5 bars before the data frontier.
// ----------------------------------------------------------------------------

/** The oracle action (static.label_dim 'oracle_action'): v2 emits buy
 *  (a local min) / sell (a local max); no_action was the v1 era's
 *  stand-down row, decode-only. */
export type OracleAction = "buy" | "sell" | "no_action";

/** One oracle pivot row (analysis.oracle_signals + the joined raw
 *  close of the pivot date, for the UI's price overlay + marker
 *  placement). */
export interface OracleRow {
  /** The PIVOT date (YYYY-MM-DD) — the local extreme itself; the
   *  action executes AT it. */
  date: string;
  /** The hindsight action (decoded 'oracle_action' label): buy at a
   *  local min, sell at a local max. */
  action: OracleAction;
  /** The pivot's confirmation window (the order K — close[date] is
   *  the extreme of the +-K-row window; 5). */
  window_days: number | null;
  /** The NEXT OPPOSITE pivot — the row's hindsight swing target (a
   *  buy's target = the next local max, where the hindsight holder
   *  sells); NULL at the frontier. */
  ext_date: string | null;
  /** Traded-row distance pivot -> swing target (>= 1); NULL at the
   *  frontier. */
  ext_offset: number | null;
  /** The swing target's close (the hindsight exit / re-entry price);
   *  NULL at the frontier. */
  ext_price: number | null;
  /** The v4 forward-trend grade 0-100: the linear ramp of the
   *  pivot's forward-60d favorable move (buy: biggest rise / sell:
   *  biggest drop; 2% no-action floor / 10% full — below the floor
   *  the pivot is not in the table at all). NULL while the window is
   *  unrealized (frontier pivots). */
  confidence: number | null;
  /** Raw session close of the pivot date (the UI's price overlay +
   *  the marker's y anchor). */
  close: number | null;
}

/** One point of the oracle page's FULL daily close series (every
 *  session of the bars source — the price-overlay line runs on the
 *  whole history; the hindsight markers land on extremum dates between
 *  decision dates). */
export interface OracleCloseRow {
  date: string;
  close: number | null;
}

/** Response for GET /api/analysis/oracle/series — one security's full
 *  decision history (the UI slices via dataZoom) + the full daily
 *  close series. */
export interface OracleSeriesResponse {
  code: string;
  name: string;
  rows: OracleRow[];
  closes: OracleCloseRow[];
}

/** One row of the Oracle codes list (the codes with decisions — the
 *  10 study indices for now). */
export interface OracleCodeRow {
  code: string;
  name: string;
}

/** Response for GET /api/analysis/oracle/codes. */
export interface OracleCodesResponse {
  codes: OracleCodeRow[];
}

// ----------------------------------------------------------------------------
//  Security baseline bundle — GET /api/sec-baseline?sec_type=&code=
//  The ONE shared endpoint behind the shared baseline-plot component
//  (useSecBaselineBundle → IndexPanel / EtfMarginPanel / StockPanel). The
//  bundle is whichever of the three shapes matches sec_type.
// ----------------------------------------------------------------------------
export interface SecBaselineResponse {
  sec_type: "index" | "etf" | "stock";
  /** Suffix-normalized code as requested. */
  code: string;
  /** The security's bundle — IndexBundle (index) | EtfBundle (etf) |
   *  StockBundle (stock). NULL when the code has no rows. */
  bundle: IndexBundle | EtfBundle | StockBundle | null;
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
export type MarginAttributionType = "index" | "etf" | "industry" | "exchange";

/** One security (index or ETF code) available in an industry for plotting. */
export interface MarginSecurity {
  /** Bare 6-digit index or ETF code. */
  code: string;
  /** Display name from stats.sec_classification.name. */
  label: string;
}

/** One broad-market index offered as the 'exchange' attribution's mapped
 *  underlying (stats.sec_index_tags.is_broad_market = TRUE). */
export interface MarginUnderlyingEntry {
  /** Bare 6-digit index code. */
  code: string;
  /** Display name (stats.sec_classification.name; falls back to the code). */
  name: string;
}

/** One daily margin data point for one security. */
export interface MarginSeriesRow {
  code: string;
  date: string;
  /** RONGZI outstanding balance (融资余额, yuan). */
  balance: number | null;
  /** RONGZI buy amount (融资买入额, yuan, FLOW). */
  buy: number | null;
  /** Underlying security close price (from index/etf_basic_stats; exchange
   *  rows read the mapped broad-market underlying). */
  close: number | null;
  /** Underlying security turnover (成交金额, yuan). Index rows read
   *  stats.index_basic_stats.trading_amount (whole-index turnover); ETF rows
   *  read stats.etf_liquidity_margin.trading_amount; exchange rows read the
   *  mapped broad-market underlying's turnover (default 沪深300 000300). With
   *  `buy` this yields the margin-buy share of turnover (买入占比) — the
   *  margin-intensity regime variable. NOTE for index/exchange attributions
   *  the share is scale-offset (weighted-AVG constituent rz_buy or
   *  WHOLE-exchange rz_buy vs the underlying index's turnover), which is
   *  fine for RANK-based (tercile) regime bucketing. */
  trading_amount: number | null;
  /** The day's canonical market-regime label (stats.market_regimes:
   *  calm/hot/panic/quiet — the vol_z × amt_z cross, trailing shift-1)
   *  for the security's own sec_type; null when no regime row exists.
   *  Feeds the regime×tercile amplification split of the companion
   *  chart. */
  regime: MarketRegime | null;
  /** 'industry' rows only: the number of member securities (non-null
   *  non-zero rz_* contributors) behind the per-side averages on this
   *  date, derived exactly as SUM/AVG per side. Null on dates where no
   *  member contributed. Drives the member-count change dots + the
   *  tooltip's "N members" note (both modes). */
  n_members?: number | null;
}

/** Response for GET /api/analysis/margin-trends/industry-series.
 *  Per-security daily margin series for ONE industry + ONE attribution.
 *  attribution='index' reads analysis.margin_index_series (weighted-avg
 *  constituent-stock margin); attribution='etf' reads
 *  stats.etf_liquidity_margin for the industry's ETFs; attribution=
 *  'industry' reads analysis.margin_industry_stats — the industry's OWN
 *  margin curve as ONE series (code = industry_id): raw (default) is the
 *  SUM(stock+etf) curve, normalized (?normalize=true) is the SUM of the
 *  per-side member AVERAGES over non-null non-zero members (the *_norm
 *  cols) — with close from stats.industry_basic_stats.mean_close and
 *  trading_amount from total_trading_amount (pool_size='all' slice). */
export interface MarginIndustrySeriesResponse {
  industry_id: string;
  industry_label: string;
  /** True when the rows carry the normalized curve (SUM of the per-side
   *  member averages) — only ever true for the 'industry' attribution;
   *  the amplification analytics stay raw-based regardless. */
  normalized: boolean;
  /** Mapped underlying label, when the attribution borrows another
   *  instrument's price/turnover for the daily-return + hyped-vs-drained
   *  views. Set ONLY for the 'exchange' attribution (= the user-selected
   *  broad-market underlying; exchange rows carry no composite of their
   *  own). The chart title shows both:
   *  "<industry_label> · <underlying_label>". */
  underlying_label?: string;
  /** The broad-market index code the 'exchange' attribution mapped as the
   *  underlying (echoes the ?underlying= request param, or the 沪深300
   *  default when the param was absent / not a broad-market code). */
  underlying_code?: string;
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
//    Stored per-row: code_sec_shared_weight, benchmark_sec_shared_weight,
//    corr_{20,60,252}d. The ETF-market liquidity fields below (amounts,
//    ratio, MA5) are NOT stored — the API derives them at read time from
//    stats.index_exts.total_etf_trading_amount.
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
  /** benchmark_etf_trading_amount / code_etf_trading_amount (derived at read
   *  time by the API from stats.index_exts). A LIQUIDITY ratio
   *  (≥1 means benchmark ETF-market turnover exceeds subject's). Its inverse
   *  (1/ratio) is the subject's SHARE of the benchmark ETF market. NULL when
   *  either amount is NULL/0 (e.g. benchmark has no tracking ETF). */
  etf_trading_amount_ratio: number | null;
  /** Aggregate ETF turnover (yuan) tracking benchmark_code on this date
   *  (stats.index_exts.total_etf_trading_amount for benchmark_code, joined at
   *  read time). NULL when no ETF tracks the benchmark (e.g. 000001 上证指数). */
  benchmark_etf_trading_amount: number | null;
  /** Subject's ETF turnover (yuan) (stats.index_exts.total_etf_trading_amount
   *  for the subject code, joined at read time; NULL for ETF subjects —
   *  index_exts is keyed on index codes).
   *  NULL for indices with no tracking ETF. */
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
  /** benchmark_etf_trading_amount / code_etf_trading_amount (derived at read
   *  time by the API from stats.index_exts). LIQUIDITY ratio,
   *  NOT a price-attribution proportion. NULL when either amount is NULL/0
   *  or |ratio| >= 1e6 (the former NUMERIC(10,4) build cap). */
  etf_trading_amount_ratio: number | null;
  /** 5-trading-day moving average of etf_trading_amount_ratio (computed at
   *  read time by the API via a 5-row trailing window; NULLs skipped —
   *  parity with the former pandas rolling(5, min_periods=1) build-time
   *  computation). */
  etf_trading_amount_ratio_ma5: number | null;
  /** Aggregate ETF turnover (yuan) tracking benchmark_code on this date
   *  (stats.index_exts, joined at read time). */
  benchmark_etf_trading_amount: number | null;
  /** Subject's ETF turnover (yuan) on this date (stats.index_exts, joined at
   *  read time; NULL for ETF subjects — index_exts is keyed on index codes). */
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
  corr_252d: number | null;
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
  /** ETF code, bare 6-digit (e.g. "510300"). */
  code: string;
  /** ETF display name (e.g. "沪深300ETF"). */
  name: string;
}

// ----------------------------------------------------------------------------
//  Analysis Commons — Market Beta
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
//    the BROWSER (MarketBetaPage.tsx) from raw daily closes.
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
export interface MarketBetaIndexRow {
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
export interface MarketBetaIndex {
  code: string;
  name: string;
  /** Exchange code from stats.sec_classification.exchange. 'HK' for Hong
   *  Kong-linked indices, NULL/empty for mainland. Used by the frontend's
   *  show/hide HK toggle. */
  exchange: string | null;
  rows: MarketBetaIndexRow[];
}

/** One per-date aggregation row for a pool_size slice. mean_close and
 *  var_price are computed across rebased-to-100 close values of member
 *  indices in this pool_size slice on this date (anchored at history start).
 *  mean_pe and total_trading_amount are computed on RAW values (no rebasing). */
export interface MarketBetaAggRow {
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

/** Response for GET /api/analysis/market-beta/chart?industry_id=...
 *  Returns one entry per member index in the industry (raw close series for
 *  the multi-line plot) PLUS the precomputed mean/var aggregation rows per
 *  pool_size slice (for the overlay) PLUS a set of broad-market benchmark
 *  close series (CSI 300, SSE 50, CSI 1000) for the optional benchmark
 *  dropdown overlay. */
export interface MarketBetaChartResponse {
  industry_id: string;
  industry_label: string;
  /** All indices classified into this industry (stats.sec_classification
   *  type='index' AND industry_id = $1). Indices with no index_basic_stats
   *  rows are omitted (nothing to plot). */
  indices: MarketBetaIndex[];
  /** Precomputed mean/var aggregation rows for this industry, one per
   *  (date, pool_size) — across all 4 pool_size slices. The frontend filters
   *  to the user-selected pool_size for the overlay. Empty when the analysis
   *  hasn't been run yet. */
  aggregation: MarketBetaAggRow[];
  /** Broad-market benchmark close series — CSI 300 (000300), SSE 50
   *  (000016), CSI 1000 (000852). The frontend renders a multi-select
   *  dropdown so the user can tick any subset to overlay (each rebased to
   *  100 at the visible window start, same as member indices). Benchmarks
   *  with no close data are omitted. Empty when no benchmarks have data. */
  benchmarks: MarketBetaIndex[];
}

// ----------------------------------------------------------------------------
//  Industry Correlations — windowed pairwise Pearson correlation between
//  two industries' MA curves of mean_close (stats.industry_basic_stats.
//  mean_close). Drives the expandable Correlation chart on the
//  MarketBeta page (multi-industry mode only — Correlation button
//  is disabled when fewer than 2 industries are selected).
//
//  Source: analysis.industry_correlations (built by the correlations step
//  of analysis.market_beta). One row per (start_date, pair,
//  pool_size, interval) with corr_ma20_20d / corr_ma60_60d /
//  corr_ma252_252d: corr_ma{W}_{W}d is the Pearson correlation between
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
  /** Pearson correlation between the two industries' MA252 curves over
   *  the 252 trading days starting on start_date. NULL when the window is
   *  not full. */
  corr_ma252_252d: number | null;
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
//  20/60/252 trading-day window:
//    overall_corr_ma{W}_{W}d    — RAW pairwise correlation of the MA-W
//      curves (same semantics as IndustryCorrelationRow.corr_ma{W}_{W}d).
//    offset_sub_corr_ma{W}_{W}d — correlation of the benchmark-offset
//      SUBTRACTED recomputed prices (common market factor removed).
//      (The table's derived opposite_score_ma{W}_{W}d = (1 − offset)/2 is
//      DB-side audit storage only — not exposed; trivially derivable.)
//
//  Source: analysis_composites.industry_corr_benchmark_offsets (built by
//  python -m analysis.analysis_composites; default benchmark 000300).
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
  /** END date of each window on the trading calendar (start + W − 1
   *  trading days; NULL when that window is not full) — the date the
   *  audit value was knowable through, and the chart's x key. */
  end_ma20_20d: string | null;
  end_ma60_60d: string | null;
  end_ma252_252d: string | null;
  /** Stride in trading days between consecutive window starts (default
   *  20). */
  interval: number;
  /** Pool_size slice (same for both industries). */
  pool_size: "small" | "mid" | "large" | "all";
  /** RAW pairwise Pearson correlation of the MA20 curves over the 20d
   *  window. NULL when the window is not full. */
  overall_corr_ma20_20d: number | null;
  overall_corr_ma60_60d: number | null;
  overall_corr_ma252_252d: number | null;
  /** Correlation of the benchmark-offset SUBTRACTED recomputed prices
   *  (industry MA − rebased benchmark MA). NULL when the window is not
   *  full or the benchmark MA is undefined on a window date. */
  offset_sub_corr_ma20_20d: number | null;
  offset_sub_corr_ma60_60d: number | null;
  offset_sub_corr_ma252_252d: number | null;
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
//  News Audit (composite analysis) — the 5-day window audits of the news
//  corpus built by `python -m analysis.news_audit`:
//    text.news_provider_audit — one row per window per ORIGIN (source +
//      author; author "" is the provider-level row), with items /
//      digested / fake share / avg sentiment / avg word count / industry
//      spread, joined with the text.news_providers registry row
//      (is_blocked + seen-date span).
//    text.news_content_audit — the content twin per INDUSTRY
//      ("" = untagged): the same window metrics plus the keyword rollup
//      (distinct keywords hit, total mentions, articles with any hit).
//
//  Windows are 5-calendar-day non-overlapping tiles (stride 5d, anchored
//  2000-01-03); the newest window is PARTIAL (is_complete false) and
//  recomputes on every audit run. Sentiment/fake verdicts exist only on
//  digested rows — their denominators are num_digested, while num_items
//  counts every article.
//
//  Drives the Composites → "News Audit" page (Provider ↔ Content toggle).
// ----------------------------------------------------------------------------
/** One provider-audit row — a 5-day window's metrics for one news origin. */
export interface NewsProviderAuditRow {
  /** Window start on the 5-day grid (YYYY-MM-DD). */
  window_start: string;
  /** Window end = window_start + 4 days (YYYY-MM-DD). */
  window_end: string;
  /** Provider (= text.news.source: gov, zhihu, ndrc, pboc_*, ai_daily). */
  source: string;
  /** Author when captured (zhihu AuthorName, gov 来源); "" = the
   *  provider-level row. */
  author: string;
  /** False for the trailing partial window (recomputed every run). */
  is_complete: boolean;
  /** ALL of the origin's articles dated in the window. */
  num_items: number;
  /** Articles with an LLM digestion row (sentiment/fake denominator). */
  num_digested: number;
  /** Digested articles flagged fake (disingenuous tomorrow-question). */
  num_fake: number;
  /** num_fake / num_digested as a 0..1 fraction; null when nothing was
   *  digested. */
  fake_pct: number | null;
  /** Average digestion sentiment_level in [-5, +5]; null when none. */
  avg_sentiment: number | null;
  /** Average word_count over all items; null when no counts. */
  avg_word_count: number | null;
  /** COUNT(DISTINCT industry_id) over the window's items. */
  num_industries: number | null;
  /** Registry media type (text.news_providers.news_type) — label_dim
   *  'news_type' code: 0 news_article | 1 financial_report |
   *  2 social_media | 3 ai_digest | 99 other. */
  news_type_code: number;
  /** The decoded media-type label ("other" when the registry row is
   *  missing). */
  news_type: string;
  /** Registry blocklist flag (text.news_providers.is_blocked). */
  is_blocked: boolean;
  /** Registry seen-date span (text.news_providers). */
  first_seen_date: string | null;
  last_seen_date: string | null;
  /** When this row was computed. */
  computed_at: string;
}

/** Response for GET /api/analysis/news-audit/providers?limit=&source= */
export interface NewsProviderAuditResponse {
  /** Clamped number of most recent distinct windows returned. */
  limit_windows: number;
  /** Provider filter applied ("" = all). */
  source: string;
  /** Backend arg for the shared ExpandedTable header filters. */
  enable_filters: boolean;
  rows: NewsProviderAuditRow[];
}

/** One content-audit row — a 5-day window's metrics for one industry. */
export interface NewsContentAuditRow {
  window_start: string;
  window_end: string;
  /** Industry tag; "" = untagged articles (broad-market recaps, gov). */
  industry_id: string;
  /** Display label (from stats.sec_classification; falls back to the id). */
  industry_label: string;
  is_complete: boolean;
  num_items: number;
  num_digested: number;
  num_fake: number;
  fake_pct: number | null;
  avg_sentiment: number | null;
  /** Different keywords hit across the window's content (0 = none). */
  distinct_keywords: number | null;
  /** Total affirmative keyword mentions (negated readings never count). */
  keyword_mentions: number | null;
  /** Articles that hit at least one keyword. */
  articles_with_keywords: number | null;
  computed_at: string;
}

/** Response for GET /api/analysis/news-audit/content?limit=&industry_id= */
export interface NewsContentAuditResponse {
  limit_windows: number;
  /** Industry filter applied ("" = all). */
  industry_id: string;
  enable_filters: boolean;
  rows: NewsContentAuditRow[];
}

/** One coverage-audit row — ONE TRADING DAY's coverage counts for one
 *  industry (text.news_coverage_audit, built by analysis.news_audit's
 *  CoverageAuditStep; the 2026-11-22 coverage migration's daily grain).
 *  industry_id '000001' = the WHOLE-MARKET rollup row; "" = untagged. */
export interface NewsCoverageAuditRow {
  /** Trading day (YYYY-MM-DD; weekend articles rolled forward). */
  date: string;
  /** '000001' = the whole-market rollup; "" = untagged; else the id. */
  industry_id: string;
  /** Display label ('(whole market)' / '(untagged)' / the taxonomy name). */
  industry_label: string;
  /** Every text.news row in scope (digested or not — the raw coverage
   *  basis the daily panel's flow_af reads). */
  n_articles: number;
  /** Rows with a text.news_digestions row. */
  n_digested: number;
  /** Rows whose digestion flagged is_fake_news. */
  n_fake: number;
  /** Rollup row only: distinct industry buckets covered that day. */
  n_industries: number | null;
}

/** Response for GET /api/analysis/news-audit/coverage?limit_days= */
export interface NewsCoverageAuditResponse {
  limit_days: number;
  enable_filters: boolean;
  rows: NewsCoverageAuditRow[];
}


// ----------------------------------------------------------------------------
//  Pair Pct-Change Ratios — the subject's daily pct change as a multiple
//  of the benchmark's (ratio = pct_change_code / pct_change_benchmark on
//  the pair's common trading dates), smoothed by trailing 5/20/60/252-
//  common-day rolling means and materialized ONLY on stride-grid dates
//  of the global index calendar (default stride 20 — the corr pattern).
//  Ratio > 1 = subject outmoves the benchmark, (0,1) = undermoves, < 0 =
//  opposite direction. The RATIO counterpart of the stats.cross_stats
//  point-offset primitive.
//
//  TWO grains (static.label_dim 'pair_grain'): 'index' = per-index pair
//  rows (subjects = ALL active indexes); 'industry' = industry_id rows,
//  the AVG over the industry's member-index rows per (benchmark, grid
//  date) — the cross_stats industry-grain pattern.
//
//  Source: analysis.pair_pct_change_ratios (built by
//  python -m analysis.pair_pct_change_ratios; subjects = ALL active
//  indexes + industry aggregates, benchmarks = ALL active indexes).
//  Drives the pair selector + ratio chart on the Composites → "Opposite
//  Industry Correlations" page.
// ----------------------------------------------------------------------------
/** Subject grain of a pair-ratio row (static.label_dim 'pair_grain'). */
export type PairPctRatioGrain = "index" | "industry";

/** One side of the pair selector universe (a subject code or a benchmark
 *  code with materialized rows). */
export interface PairPctRatioUniverseEntry {
  /** Index code, or industry_id when grain = 'industry'. */
  code: string;
  /** Display label (from stats.sec_classification; falls back to the
   *  code). */
  label: string;
  /** Subject grain ('index' for every benchmark-side entry — benchmarks
   *  are always indexes). */
  grain: PairPctRatioGrain;
  /** Distinct counterpart codes this side has pair rows with. */
  n_counterparts: number;
  /** First / last grid dates materialized. */
  first_date: string;
  last_date: string;
}

/** Response for GET /api/analysis/pair-pct-ratios/universe */
export interface PairPctRatioUniverseResponse {
  /** Selectable subjects — industry aggregates first, then per-index
   *  rows. */
  codes: PairPctRatioUniverseEntry[];
  benchmarks: PairPctRatioUniverseEntry[];
  /** Grid stride the universe listing is scoped to (default 20). */
  interval: number;
  /** Industry_ids the universe was limited to (industry entries + member
   *  indexes on both sides; empty = unfiltered full universe). */
  industry_ids: string[];
}

/** One stride-grid sample of the four ratio rolling means. */
export interface PairPctRatioRow {
  /** Stride-grid date (YYYY-MM-DD). */
  date: string;
  /** Minimum-variance hedge ratio h* = -Cov(A,B)/Var(A), RAW mode
   *  (A,B = industry aggregate pct changes; NULL when the window's
   *  pairwise-complete null share exceeds 10%, Var(A) is at the floor,
   *  or beyond the NUMERIC(10,6) bound). */
  hedge_raw_ma5d: number | null;
  hedge_raw_ma20d: number | null;
  hedge_raw_ma60d: number | null;
  hedge_raw_ma252d: number | null;
  /** Same ratio on market-offset series A' = A - M, B' = B - M (M =
   *  benchmark_market index pct — the market factor cancelled; the
   *  opposite-moves regime where the buy-to-hedge answer lives). */
  hedge_offset_ma5d: number | null;
  hedge_offset_ma20d: number | null;
  hedge_offset_ma60d: number | null;
  hedge_offset_ma252d: number | null;
}

/** Response for GET /api/analysis/pair-pct-ratios?code=&benchmark= */
export interface PairPctRatiosResponse {
  code: string;
  benchmark_code: string;
  interval: number;
  /** Subject grain of the returned series (industry = the AVG over the
   *  industry's member-index rows). */
  grain: PairPctRatioGrain;
  /** Grid-sampled rows, ordered by date ascending. */
  rows: PairPctRatioRow[];
  /** The offset-market benchmark index code (e.g. '000300'). */
  benchmark_market: string;
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
//  Market Beta page in "Benchmark Attribution" mode. The 1st plot is
//  the benchmark price chart (clickable to pick a date); each subsequent plot
//  shows the attribution bars for ONE selected industry at the clicked date.
//
//  Source: analysis.industry_attributions (built by
//  analysis.market_beta.attributions — truncate-then-recompute).
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
//  date) in "Benchmark Attribution" mode on the Market Beta page.
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
  /** Same as above over the trailing 252-trading-day window (~1 year). */
  non_this_industry_rolling_252days_price: number | null;
  /** Same as above over the trailing 504-trading-day window (~2 years). */
  non_this_industry_rolling_504days_price: number | null;
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
//  on the Market Beta page.
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

/** One date row in an industry's daily series. `rolling` is the industry's
 *  SHARED PORTFOLIO trailing-N-day return factor (100-based), compounded by
 *  the API from the daily decomposition identity — the same math as the
 *  build's hype metric. The frontend plots it directly as the industry
 *  curve (rebased to 100 at the window start, like the benchmark's own
 *  N-day rebasing); hype = rolling/100 - 1 - benchmark_return_nd. */
export interface HypesDrainsIndustrySeriesRow {
  date: string;
  /** Shared-portfolio trailing-N-day 100-based factor (null before the
   *  window fills). */
  rolling: number | null;
  /** benchmark_shared_weight (percent 0-100) on this date. */
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
//  industry identity joined from stats.sec_classification and aggregates
//  computed at query time.
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
//  with trading-amount weights (stats.index_basic_stats, prev day) and
//  composition-overlap shared weights (stats.cross_stats) joined at read
//  time. Drives the "By Trading Amt / Equal" toggle on the Intraday
//  Attribution panel of the Market Movements page
//  (GET /api/live-data/sec-alloc-live/attribution).
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
//  Market Beta page. Mirrors "Benchmark Attribution" but replaces
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
//  Industry Stock Attribution — per-industry daily pct-change bucket
//  distribution (the audit view on the Market Beta page: "how many stocks
//  in industry X rose >= 7.5% today"). Source:
//  analysis.industry_stock_change_dist (bucket = the
//  'stock_change_bucket' static.label_dim code, 12 codes drop tail ->
//  rise tail).
// ----------------------------------------------------------------------------

/** One bucket of the per-industry pct-change distribution. */
export interface IndustryStockAttributionBucket {
  /** The 'stock_change_bucket' label_dim code (0 '<-17.5%' .. 11 '>=17.5%'). */
  bucket: number;
  /** Decoded bucket label, e.g. '7.5~12.5%'. */
  bucket_label: string;
  /** Number of member stocks whose pct_change falls in this bucket. */
  stock_count: number;
  /** stock_count / total_stocks (fraction 0-1). Null when total_stocks is 0. */
  pct_of_industry: number | null;
}

/** Response for GET /api/analysis/industry-stock-attribution
 *  ?industry_id=BANKS&date=YYYY-MM-DD (date optional → latest). */
export interface IndustryStockAttributionResponse {
  industry_id: string;
  industry_label: string;
  /** As-of date (latest available when no `date` was requested). */
  date: string;
  /** Total mapped stocks with non-NULL pct_change on the date
   *  (= SUM of the buckets' stock_count). */
  total_stocks: number;
  /** All 12 buckets in code order (drop tail -> rise tail). Empty when the
   *  industry has no distribution data at all. */
  buckets: IndustryStockAttributionBucket[];
  /** The 90 most recent available dates (DESC) — feeds the view's date
   *  dropdown. */
  available_dates: string[];
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
// Live Options — intraday OI-weighted moneyness skewness
//  GET /api/live-options/* — spot bars from the underlying's 5-min table +
//  the computed series in live.options_intraday_skewness (written on
//  demand by python -m live.options_intraday_skewness; the per-bar OI is
//  the estimator prev-day daily OI + day-cumulative traded volume).
// ----------------------------------------------------------------------------

/** Target type of an option underlying: ETF venues (SZSE/SSE) vs index
 *  venue (CFFEX). Routes which 5-min spot table backs the chart. */
export type LiveOptionsTargetType = "ETF" | "INDEX";

/** Sentinel expiry_date of the MEAN series row (all active expiry groups
 *  blended) — real rows carry their group's actual expiry date. */
export const LIVE_OPTIONS_MEAN_EXPIRY = "9998-12-31";

/** One 5-min bar of the underlying's spot (close only — the chart's solid
 *  line; time is the 5-min bar END time 'HH:MM'). */
export interface LiveOptionsSpotBar {
  time: string;
  close: number;
}

/** One computed skewness point: a (time × expiry group) pair, or the MEAN
 *  row (expiry_date = LIVE_OPTIONS_MEAN_EXPIRY). Semantics mirror
 *  analysis.options_skewness_stats skew_type='oi_moneyness': skew_price =
 *  S(t) × E[M], E[M] = Σ(OI·K/S(t)) / Σ(OI) with OI per bar estimated as
 *  prev-day daily OI + day-cumulative traded volume. */
export interface LiveOptionsSkewPoint {
  time: string;
  expiry_date: string;
  /** Underlying 5-min close the point was computed at (yuan / index pts). */
  spot: number | null;
  skew_price: number | null;
  skew_pct: number | null;
  /** Group's total OI weight (contracts; drives the line-width encoding). */
  oi_total: number | null;
  otm_call_share: number | null;
  otm_put_share: number | null;
  skew_type: SkewType;
}

/** Response for GET /api/live-options/oi-skew-intraday. */
export interface LiveOptionsOiSkewResponse {
  underlying_code: string;
  /** Trading day the bars belong to (resolved to the latest when omitted). */
  date: string;
  /** Date of the prev-trading-day options snapshot the OI base came from
   *  (null when no snapshot exists before the date). */
  snapshot_date: string | null;
  bars: LiveOptionsSpotBar[];
  series: LiveOptionsSkewPoint[];
  /** True when this request spawned the on-demand compute (rows were
   *  missing or stale — the response may still predate the run finishing). */
  computing: boolean;
}

/** Response for GET /api/live-options/dates — distinct trading days with
 *  spot bars for the underlying, descending. */
export interface LiveOptionsDatesResponse {
  underlying_code: string;
  dates: string[];
}

/** Response for GET /api/live-options/run/status — process-id tag → running. */
export interface LiveOptionsRunStatusResponse {
  status: Record<string, boolean>;
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
 *  / max(mean_holding_days, 1)) × 252. 0 when total_qty = 0 or
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
   *  normalized_mean_buy_price) / max(mean_holding_days, 1)) × 252. 0 when
   *  total_qty = 0 (no capital at risk) or mean_holding_days <= 0. */
  return_rate: number;
  /** Annualized Sharpe ratio (×√252, rf=0) of daily Δtotal_pnl over ALL
   *  history up to this trade_date. 0 when < 2 deltas or σ = 0. */
  sharpe_ratio: number;
  /** Annualized Sharpe ratio over a rolling 252-trading-day window (~1 year). */
  sharpe_ratio_252d: number;
  /** Annualized Sharpe ratio over a rolling 504-trading-day window (~2 years). */
  sharpe_ratio_504d: number;
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
  /** The displayed run's strategy_identity.seq_id (null = no run found).
   *  A non-latest value means the run-history selector picked this run. */
  seq_id: number | null;
  /** Display counter of the displayed run (null = no run found). */
  seq_no: number | null;
  /** The displayed run's stored config (params JSONB: trading keys + the
   *  comb block + params_version on portfolio runs). null = no run found. */
  params: Record<string, unknown> | null;
}

// ----------------------------------------------------------------------------
//  Strategy — run history (GET /api/strategy/singleton/run-history)
//  Recent strategy_identity runs for one (sec_type, code, strategy_name),
//  newest first. Old runs are retained with is_active=FALSE, so each row
//  is a selectable past run with its stored params config.
// ----------------------------------------------------------------------------
/** One past run in the history list. */
export interface RunHistoryItem {
  seq_id: number;
  seq_no: number;
  strategy_name: string;
  sec_type: MaSpreadSecType;
  code: string;
  start_date: string | null;
  end_date: string | null;
  /** The run's stored config (params JSONB: trading keys + the comb block
   *  + params_version on portfolio runs). */
  params: Record<string, unknown>;
  scenario: string | null;
  status: string | null;
  fault_tolerance: number;
  is_active: boolean;
  created_at: string | null;
}

export interface RunHistoryResponse {
  code: string;
  sec_type: MaSpreadSecType;
  strategy_name: string;
  runs: RunHistoryItem[];
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
//  Futures Baseline — CFFEX futures curves (v_futures_daily view)
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

/** One daily row of a futures contract (from v_futures_daily). */
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

// ----------------------------------------------------------------------------
// News (text schema — text.news / text.news_keywords, loaded by builds.text)
// One raw article per (title, source, date); industry_id joins the canonical
// (sector_id, industry_id) classification model (UPPERCASE ids: BANKS, SEMI,
// BROAD_SSE, …) so news shares the same nav tree as the rest of the app.
// ----------------------------------------------------------------------------

/** One news article row (item-list shape — content truncated to a snippet). */
export interface NewsItem {
  news_id: number;
  title: string;
  source: string | null;
  date: string;
  url: string | null;
  author: string | null;
  industry_id: string | null;
  /** Parent sector of industry_id in the canonical taxonomy (denormalized;
   *  set iff industry_id is set) — lets an L1-only scope filter at sector
   *  granularity and sector-only items surface under their sector chip. */
  sector_id: string | null;
  /** Upvotes when the source provides them (zhihu); the list is ordered by
   *  this first. NULL for sources without votes — those sort after by date. */
  votes: number | null;
  /** Stored comments for this article (0 when none loaded yet). */
  comment_count: number;
  /** True when the mapped industry had a big market move on this date
   *  (builds.text big_market_mv: |day change| >= 3% AND RSI(3) pinned,
   *  5-td cooldown per industry). */
  is_big_fluctuation: boolean;
  /** LLM digestion sentiment in [-5, 5] (text.news_digestions; jev or the
   *  chat digestion pipeline). NULL while the article is not digested yet. */
  sentiment_level: number | null;
  /** Discourse category of the digested article (text.news_digestions.
   *  category, decoded server-side from the news_category label codes):
   *  discussion | analysis | opinion | statement | other. NULL while the
   *  article is not digested yet; "other" = the detector's read was not
   *  confident enough to classify. */
  category: string | null;
  /** The disingenuous tomorrow-question verdict (text.news_digestions.
   *  is_fake_news): the title asks about the next day's move while the
   *  same origin's earlier items already made explicit directional
   *  statements — opinions/sentiments never trigger it. NULL while the
   *  article is not digested yet. */
  is_fake_news: boolean | null;
  /** First ~200 chars of the article body (or null when not crawled). */
  snippet: string | null;
}

/** Full article row for the feed's click-to-expand card. */
export interface NewsItemDetail extends Omit<NewsItem, "snippet"> {
  /** Complete article body (untruncated). */
  content: string | null;
  author: string | null;
}

/** One comment in the threaded tree (replies nested under their root). */
export interface NewsComment {
  comment_id: number;
  parent_comment_id: number | null;
  author: string | null;
  content: string | null;
  date: string | null;
  votes: number | null;
  is_reply: boolean;
  replies: NewsComment[];
}

/** Response for GET /api/news/comments — roots carry nested replies. */
export interface NewsCommentsResponse {
  total: number;
  comments: NewsComment[];
}

/** Response for GET /api/news/items. */
export interface NewsItemsResponse {
  total: number;
  items: NewsItem[];
}

/** One date bucket — count of news matching the current industry/search. */
export interface NewsDayCount {
  date: string;
  count: number;
}

/** Response for GET /api/news/calendar. */
export interface NewsCalendarResponse {
  /** Days WITH at least one matching article (zero-count days omitted). */
  days: NewsDayCount[];
  /** Inclusive coverage of the calendar data, so the date bar can lay out
   *  its window even at the edges of the corpus. */
  min_date: string | null;
  max_date: string | null;
}

/** Response for GET /api/news/themes — same SectorNode tree as the security
 *  classification nav, but counts = number of news articles per industry and
 *  items[] always empty (news has no L3 security level). */
export type NewsThemesResponse = SectorNode[];

// ----------------------------------------------------------------------------
// News question search (POST /api/news/search) — live zhihu content search
// driven by the News page question bar; items are raw API payloads, not
// text.news rows (those appear only after the next builds.text run).
// ----------------------------------------------------------------------------

/** One raw zhihu search item (downloader artifact schema, snake keys). */
export interface NewsSearchItem {
  Title: string;
  ContentType: string;
  ContentID: string;
  ContentText: string | null;
  Url: string | null;
  CommentCount?: string;
  VoteUpCount?: string;
  AuthorName: string | null;
  AuthorAvatar?: string | null;
  AuthorBadgeText?: string | null;
  /** Unix-seconds publish/update timestamp (string) — the item's date. */
  EditTime: string | null;
  AuthorityLevel?: string;
  RankingScore?: string;
}

/** Response for POST /api/news/search. */
export interface NewsSearchResponse {
  /** True iff the run exited 0 AND its envelope reported success. */
  success: boolean;
  /** True when NO run was started because one for the same source is
   *  already in flight (process-id-tag dedupe). */
  already_running?: boolean;
  question: string;
  source: string;
  author: string | null;
  /** Items kept (after the optional author filter). */
  total: number;
  /** Items the API returned before any filtering. */
  total_returned: number;
  /** Artifact path the response was stored under (temps/zhihu_news/…). */
  out_file: string | null;
  items: NewsSearchItem[];
  /** Diagnostics tails (surfaced on failure). */
  stdout_tail?: string;
  stderr_tail?: string;
}

// ----------------------------------------------------------------------------
// AI (text schema — llm.llm_qa / llm.llm_qa_refs / text.news_digestions,
// written by llm_agents). The DataViz > AI tab renders the Q&A knowledge
// base with the same shape conventions as the News endpoints: the QA "date"
// is qa_date (the question's own data date) bucketed to Asia/Shanghai days,
// industry scope follows the
// canonical (sector_id, industry_id) classification model.
// ----------------------------------------------------------------------------

/** One Q&A row (item-list shape — answer truncated to a 240-char snippet).
 *  Inactive rows (is_active = false) are filtered out server-side and never
 *  appear in any response. */
export interface AiQaItem {
  qa_id: number;
  question: string;
  answer_snippet: string | null;
  industry_id: string | null;
  /** Parent sector of industry_id in the canonical taxonomy (denormalized;
   *  set iff industry_id is set) — lets an L1-only scope filter at sector
   *  granularity and sector-only items surface under their sector chip. */
  sector_id: string | null;
  category: string | null;
  llm_model: string | null;
  language: string | null;
  /** qa_date (the question's own data date) as a YYYY-MM-DD Asia/Shanghai
   *  day, mapped to the latest trading day on/before it (the calendar key). */
  date: string;
  /** updated_at as an Asia/Shanghai timestamp string. */
  updated_at: string;
  /** Articles in the Q&A's news-group provenance (0 when none). */
  n_sources: number;
  /** True when the mapped industry had a big market move on the row's
   *  Shanghai qa_date day (builds.text big_market_mv: |day change| >= 3%
   *  AND RSI(3) pinned, 5-td cooldown per industry). */
  is_big_fluctuation: boolean;
}

/** One reference resolution of a Q&A answer, joined to its article. */
export interface AiQaRefItem {
  /** Representative citation tag of the article (ref_1, …) — the
   *  smallest tag the answer cites among the refs resolving to it. */
  ref: string;
  /** 'exact' (came with the summary response) | 'relevant' (found
   *  after the summary by title search — corpus match or ddgs). */
  ref_type: string;
  /** Provenance behind ref_type: 'summary' | 'corpus' | 'ddgs' (null
   *  on rows stored before the column existed). */
  resolved_via: string | null;
  resolved_url: string | null;
  /** Whether the answer cites the article (any of its tags) — search
   *  results the summarizer did not cite load too, unused. */
  is_used: boolean;
  /** The reference's publish time per the search response (timestamptz str). */
  ref_time: string | null;
  news_id: number;
  title: string;
  source: string | null;
  /** The ref's ACTUAL publish date (llm.llm_qa_refs.ref_time's
   *  Asia/Shanghai day — populated by the Python writers: the search
   *  response's date, else the URL-embedded date). null = unknown —
   *  nothing renders (never a now()/qa-date fallback). Refs whose known
   *  date falls outside the QA's interest window [interest day − 20d,
   *  interest day] are not stored at all. */
  date: string | null;
  url: string | null;
  /** Article content preview for the ref expansion (first 4000 chars,
   *  server-truncated; null for placeholder / content-less rows). */
  content: string | null;
}

/** Full Q&A row for the feed card's click-to-expand fetch. */
export interface AiQaDetail extends Omit<AiQaItem, "answer_snippet"> {
  /** Complete answer (untruncated). */
  answer: string;
  /** Optional supporting passage stored with the Q&A. */
  context: string | null;
  /** Average text.news_digestions sentiment across the Q&A's refs that
   *  have one (null when no digested refs — the digestion agent hasn't
   *  covered them yet). */
  avg_ref_sentiment: number | null;
  refs: AiQaRefItem[];
}

/** Response for GET /api/ai/items. */
export interface AiQaItemsResponse {
  total: number;
  items: AiQaItem[];
}

/** One date bucket — count of Q&A rows matching the current industry/search. */
export type AiDayCount = NewsDayCount;

/** Response for GET /api/ai/calendar (same shape as the news calendar). */
export type AiCalendarResponse = NewsCalendarResponse;

// ----------------------------------------------------------------------------
// AI ask history (llm.llm_qa_by_ask family + multi_media.src_images, written
// by llm_agents.llm_ask.storage — every data_viz "AI Ask" modal submit). The
// AiPage "QA by Ask" feed renders it with the same shape conventions as the
// knowledge-base endpoints above; the ask "date" is ask_date mapped to the
// latest trading day on/before it.
// ----------------------------------------------------------------------------

/** One persisted ask (item-list shape — answer truncated to a 240-char
 *  snippet; failed asks carry the error tail there instead). */
export interface AiAskHistoryItem {
  ask_id: number;
  question: string;
  answer_snippet: string | null;
  /** 'answered' | 'failed' — failed asks surface with their error tail. */
  status: string;
  online_search: boolean;
  /** The ask's primary instrument code (first scope instrument). */
  code: string | null;
  /** Product identity tag (spec product id, else the submitting page path). */
  product: string | null;
  llm_model: string | null;
  language: string | null;
  /** ask_date as a YYYY-MM-DD Asia/Shanghai day, mapped to the latest
   *  trading day on/before it (the calendar key). */
  date: string;
  /** updated_at as an Asia/Shanghai timestamp string. */
  updated_at: string;
  /** Screenshots linked through llm.llm_qa_ask_context_images. */
  n_images: number;
  /** Derived keyword rows (llm.llm_qa_keywords_by_ask). */
  n_keywords: number;
}

/** One keyword row of an ask (derived at persist time from the payload). */
export interface AiAskHistoryKeyword {
  keyword: string;
  /** 'code' | 'name' | 'product' | 'item' | 'series' — where the keyword
   *  came from (see llm.llm_qa_keywords_by_ask.kind). */
  kind: string;
}

/** One screenshot of an ask (metadata only — bytes come from
 *  GET /api/ai/ask-image?image_id=). */
export interface AiAskHistoryImage {
  image_id: number;
  /** Screenshot slot order within the ask. */
  position: number;
  mime_type: string;
  byte_size: number;
  width: number | null;
  height: number | null;
  /** Producer hint, e.g. 'ai-ask screenshot 2/4'. */
  label: string | null;
}

/** Full ask row for the feed card's click-to-expand fetch. */
export interface AiAskHistoryDetail extends Omit<AiAskHistoryItem, "answer_snippet"> {
  /** Complete answer (null when status = 'failed'). */
  answer: string | null;
  /** Failure detail when status = 'failed'. */
  error_tail: string | null;
  search_query: string | null;
  provider: string | null;
  // Context summary (from llm.llm_qa_ask_context; the full plotInfo JSONB
  // stays server-side — these are the display/searchable fields).
  chart_kind: string | null;
  chart_title: string | null;
  /** Route path the ask was submitted from. */
  page: string | null;
  industry_id: string | null;
  sector_id: string | null;
  window_start: string | null;
  window_end: string | null;
  keywords: AiAskHistoryKeyword[];
  images: AiAskHistoryImage[];
}

/** Response for GET /api/ai/ask-items. */
export interface AiAskHistoryItemsResponse {
  total: number;
  items: AiAskHistoryItem[];
}

// ----------------------------------------------------------------------------
// AI research Q&A (llm.llm_qa_by_research, written by
// llm_agents.llm_qa_by_research's SIX-request generation flow: five
// evaluation-dimension requests — intro / optimistic_forecast /
// pessimistic_forecast / risks / capitalization — then a 6th
// summary+label request). The AiPage "Research" feed renders it;
// answer is the 6th request's SUMMARY (the display content), the five
// dimensions are the expandable detail, and the row's news group is the
// refs mapping (no per-ref table — llm.llm_qa_refs FKs llm.llm_qa only).
// ----------------------------------------------------------------------------

/** One research Q&A row (item-list shape — the summary truncated to a
 *  240-char snippet). */
export interface AiQaResearchItem {
  qa_id: number;
  question: string;
  answer_snippet: string | null;
  industry_id: string | null;
  sector_id: string | null;
  category: string | null;
  /** Dominant investing logic, decoded from the static.label_dim
   *  research_logic code at the API boundary: growth | value | dividend |
   *  commodity | cyclical | defensive | quality | theme | other. */
  logic_label: string;
  llm_model: string | null;
  language: string | null;
  /** qa_date mapped to the latest trading day on/before it (the calendar
   *  key — same contract as AiQaItem.date). */
  date: string;
  updated_at: string;
  /** Articles in the row's merged news-group provenance (0 when none). */
  n_sources: number;
}

/** One source article of a research Q&A row (the news group expanded —
 *  the by-research refs mapping). */
export interface AiQaResearchSource {
  news_id: number;
  title: string;
  source: string | null;
  url: string | null;
  /** Article publish date (text.news.date's day). */
  date: string | null;
  /** Content preview (first 4000 chars, server-truncated). */
  content: string | null;
}

/** Per-dimension reference resolutions of a research Q&A row — each
 *  dimension's [来源：ref_N] citation tags resolved to their articles
 *  (the AiQaRefItem shape, from the row's dim_refs column joined to
 *  text.news). All research refs are exact / summary provenance: they
 *  are the search echoes of the dimension's own request. */
export interface AiQaResearchDimensionRefs {
  intro: AiQaRefItem[];
  optimistic_forecast: AiQaRefItem[];
  pessimistic_forecast: AiQaRefItem[];
  risks: AiQaRefItem[];
  capitalization: AiQaRefItem[];
}

/** Full research Q&A row for the card's click-to-expand fetch. */
export interface AiQaResearchDetail extends Omit<AiQaResearchItem, "answer_snippet"> {
  /** The 5th request's SUMMARY (complete, untruncated) — the card body. */
  answer: string;
  /** The per-dimension reference blocks (【维度】+ formatted hits), kept so
   *  each dimension answer's inline [来源：ref_N] citations stay aligned. */
  context: string | null;
  /** Dimension 1 — general investors' logic (null when its request failed). */
  intro: string | null;
  /** Dimension 2 — the optimistic-scenario 6-12m forecast. */
  optimistic_forecast: string | null;
  /** Dimension 3 — the pessimistic-scenario 6-12m forecast. */
  pessimistic_forecast: string | null;
  /** Dimension 4 — risks. */
  risks: string | null;
  /** Dimension 5 — current market-capital interests. */
  capitalization: string | null;
  /** Average text.news_digestions sentiment across the row's source
   *  articles that have one (null when none digested yet). */
  avg_ref_sentiment: number | null;
  /** Per-dimension expandable refs (dim_refs joined to text.news); null
   *  on rows stored before dim_refs existed — texts render plain. */
  dimension_refs: AiQaResearchDimensionRefs | null;
  sources: AiQaResearchSource[];
}

/** Response for GET /api/ai/research-items. */
export interface AiQaResearchItemsResponse {
  total: number;
  items: AiQaResearchItem[];
}

// ---------------------------------------------------------------------------
//  Sec board map (stats.sec_board_map) — the code-trend header's board tag
// ---------------------------------------------------------------------------

/** One listing-board entry. Stocks carry exactly ONE entry (their own
 *  listing board, pct=100); ETFs/indices carry one entry per board
 *  present in their latest composition snapshot, pct = composition-weight
 *  share. board: "MAIN" | "STAR" | "GEM" | "BSE". */
export interface SecBoardTag {
  board: string;
  pct: number;
  n_stocks: number;
}

/** Response for GET /api/sec-board?sec_type=…&code=… */
export interface SecBoardMapResponse {
  code: string;
  sec_type: "etf" | "index" | "stock";
  /** Sorted pct DESC; empty when the security has no board data. */
  boards: SecBoardTag[];
  /** Composition snapshot the mix was computed from (null for stocks). */
  snapshot_date: string | null;
}

// ---------------------------------------------------------------------------
//  Forecast next day (strategy.forecast_next_day) — the Strategy → Forecast
//  page's σ-ladder scenario register
// ---------------------------------------------------------------------------

/** One strategy.forecast_next_day row (labels decoded SQL-side through
 *  static.label_dim). trigger_no 0 is the scenario SUMMARY row (no
 *  signal fired — the signal columns null); 1..n carry one fired rule
 *  cell each, with the scenario constants denormalized on every row. */
export interface ForecastNextDayRow {
  /** The real bar date the forecast is anchored on. */
  anchor_date: string;
  code: string;
  /** Display name from the sec_type's stats identity table (latest row
   *  per code); null when no identity row is available. */
  code_name: string | null;
  /** "down" | "up" — the scenario's side of the σ move. */
  direction: string;
  /** The σ multiplier k ∈ {1, 2, 3}. */
  sigma_mult: number;
  trigger_no: number;
  base_close: number;
  /** Population std of the trailing 20 daily returns, PERCENT. */
  sigma_20d_pct: number;
  forecast_close: number;
  forecast_change_pct: number;
  /** The fired cell's family (null on the summary row). */
  signal_type: string | null;
  signal_sub_type: string | null;
  /** "buy" | "sell" — the fired cell's own action. */
  action: string | null;
  signal_value: number | null;
  signal_threshold: number | null;
  signal_excess_pct: number | null;
  /** The projected confidence (the live tier's breach formula), bp. */
  confidence_bp: number | null;
  trend_state: string | null;
  regime_state: string | null;
  /** The strategy pickup reading's inputs (sell executes only with a
   *  position open past min_holding_period). */
  position_open: boolean | null;
  would_execute: boolean | null;
  /** The strategy the reading applies to (the nightly portfolio). */
  strategy_name: string;
  /** The forecast run's write time (the writer stamps it on every
   *  registration — the page's "run at" display). */
  updated_at: string;
}

/** Response for GET /api/strategy/forecast. */
export interface ForecastNextDayResponse {
  sec_type: string;
  /** The code filter (null = all codes served, latest anchor each). */
  code: string | null;
  /** The explicit anchor filter when given (null = latest per code). */
  anchor_date: string | null;
  rows: ForecastNextDayRow[];
}

// ============================================================================
//  Signal Rules Audit (composite analysis) — the per-family census
//  (analysis_signals.signal_type_audit) and the pairwise family overlap
//  (analysis_signals.signal_overlap_audit), built by
//  `python -m analysis.signal_rules_audit` over the standalone trading
//  tier of analysis_signals.history_signals. Periods are calendar years
//  (counts additive; "all" = the API-side SUM re-aggregation).
// ============================================================================

/** One meta row — a YEAR period present in the audit at the scope. */
export interface SignalAuditPeriodRow {
  /** Calendar year (e.g. 2024). */
  period: number;
  period_start: string;
  period_end: string;
  /** The period's DISTINCT (code, date) days with any family signal —
   *  the chance denominator of the overlap audit's expected_pct. */
  signal_universe: number;
  n_families: number;
  n_events: number;
  computed_at: string;
}

/** Response for GET /api/analysis/signals-audit/meta?sec_type= */
export interface SignalAuditMetaResponse {
  sec_type: string;
  /** Descending by period (the UI's period selector). */
  rows: SignalAuditPeriodRow[];
}

/** One per-family census row (one YEAR period, or the all-history sum
 *  with period = 0). */
export interface SignalTypeAuditRow {
  /** Calendar year; 0 = the all-history sum. */
  period: number;
  period_start: string;
  period_end: string;
  /** signal_type label code (static.label_dim 'signal_type'). */
  signal_type: number;
  /** Decoded family name (label_dim), null when unmapped. */
  signal_type_label: string | null;
  /** DISTINCT (code, date) days the family fired. */
  n_events: number;
  n_buy: number;
  n_sell: number;
  n_codes: number;
  n_days: number;
  signal_universe: number;
  first_date: string;
  last_date: string;
}

/** Response for GET /api/analysis/signals-audit/summary?sec_type=&period= */
export interface SignalTypeAuditResponse {
  sec_type: string;
  /** The calendar year, or "all" for the all-history sum. */
  period: number | "all";
  /** Backend arg for the shared ExpandedTable header filters. */
  enable_filters: boolean;
  rows: SignalTypeAuditRow[];
}

/** One ordered-pair overlap row — A = base family (matrix ROW),
 *  B = other family (matrix COLUMN); overlap_pct is asymmetric by
 *  design: of A's trigger days, the fraction B also fired on. */
export interface SignalOverlapAuditRow {
  /** Calendar year; 0 = the all-history sum. */
  period: number;
  period_start: string;
  period_end: string;
  /** Base family A (static.label_dim 'signal_type' code). */
  signal_type: number;
  signal_type_label: string | null;
  /** Co-occurring family B (matrix column). */
  other_signal_type: number;
  other_signal_type_label: string | null;
  n_base: number;
  n_other: number;
  n_universe: number;
  n_overlap: number;
  /** Overlap days with at least one matching side (buy↔buy / sell↔sell). */
  n_same_action: number;
  /** Overlap days with at least one opposite side (buy↔sell — conflicts). */
  n_opposite_action: number;
  /** n_overlap / n_base, 0..1. */
  overlap_pct: number;
  /** n_other / n_universe — chance co-occurrence. */
  expected_pct: number;
  /** n_overlap / (n_base + n_other - n_overlap), 0..1. */
  jaccard: number;
  /** overlap_pct / expected_pct — > 1 = enrichment beyond chance. */
  lift: number;
}

/** Response for GET /api/analysis/signals-audit/overlap?sec_type=&period= */
export interface SignalOverlapAuditResponse {
  sec_type: string;
  /** The calendar year, or "all" for the all-history sum. */
  period: number | "all";
  /** Backend arg for the shared ExpandedTable header filters. */
  enable_filters: boolean;
  rows: SignalOverlapAuditRow[];
}
