/**
 * Stock Baseline service — queries stats.v_stock_baseline view
 * (stock_identity + stock_basic_stats JOIN) plus stats.sec_classification
 * (type='stock') for L1/L2 classification.
 *
 *   • getStockBaseline() — single-code OHLC + pct_change + PE, used by the
 *     composition pie chart's per-stock OHLC expansion.
 *   • listStockThemes() — two-level L1 sector → L2 industry tree (mirrors
 *     listIndexThemes()).
 *   • getStocksCombined() — paginated stock list filtered by sector +
 *     industry + (optionally) exact code search.
 *
 * Stock classification (L1 sector + L2 industry) is read from precomputed
 * columns in stats.sec_classification (type='stock', populated by
 * build_classification.py via index-inheritance).  Labels + slug are
 * DENORMALIZED onto sec_classification — no JOIN to a catalog table.
 *
 * Suffix convention (project-wide, see project_memory.md):
 *   • .SZ  = Shenzhen Stock Exchange  (深圳证券交易所)
 *   • .SS  = Shanghai Stock Exchange  (上海证券交易所, Yahoo Finance
 *             convention — NOT Tushare's .SH)
 *   • .BJ  = Beijing Stock Exchange     (北京证券交易所)
 *
 * Both stock_identity.code AND sec_classification.code (type='stock') store
 * codes WITH the exchange suffix (e.g. "000001.SZ" = Ping An Bank, a
 * Shenzhen stock; "600000.SS" = 浦发银行, a Shanghai stock).  The JOIN is
 * therefore a direct equality — no suffix stripping.
 *
 * Note: bare "000001" is ambiguous — it could be Ping An Bank (000001.SZ) or
 * the Shanghai Composite Index (000001.SS, an INDEX stored in index_identity,
 * NOT in this table).  The suffix disambiguates correctly.
 *
 * Rows with NULL OHLC are filtered out (v_stock_baseline is a LEFT JOIN of
 * stock_identity + stock_basic_stats — identity-only rows like the historical
 * "test"/"test2" rows on 2026-07-23/24 have NULL OHLC and must be skipped so
 * they don't override the real stock name in MAX(name) resolution).
 */
import { queryRows, toDateParam, formatDate, toNum } from "../lib/db.js";
import type { QueryResultRow } from "pg";
import { matchesExchange, stripExchangeSuffix, codeVariants } from "../lib/classify-etf.js";
import { buildStrategyThemesFromRows, matchesClassification } from "./_shared.js";
import { listClassificationMetaRows } from "./classification-cache.js";
import type {
  StockBaselineResponse,
  StockBaselineRow,
  StockBundle,
  StockCombinedResponse,
  SectorNode,
  IndustryNode,
  StockDividend,
  StrategyNode,
} from "../../shared/types.js";

interface DbStockRow extends QueryResultRow {
  date: string;
  code: string;
  name: string | null;
  prev_close: number | null;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  pct_change: number | null;
  pe: number | null;
  eps: number | null;
  is_pe_estimated: boolean | null;
  has_intraday_5mins: boolean | null;
  // Liquidity + margin columns (from stock_liquidity_margin via v_stock_baseline)
  trading_shares: number | null;
  trading_amount: number | null;
  rz_balance: number | null;
  rz_buy: number | null;
  rq_balance_qty: number | null;
  rq_balance_amt: number | null;
  total_balance: number | null;
}

interface DbStockMetaRow extends QueryResultRow {
  // Suffixed code (e.g. "000001.SZ") — the canonical key used everywhere
  // (stock_identity, sec_classification type='stock', v_stock_baseline).
  code: string;
  name: string;
  n_days: number;
  first_date: string;
  last_date: string;
  sector_id: string;
  sector_label: string;
  industry_id: string;
  industry_label: string;
  industry_slug: string;
  /** When TRUE, sector_id/industry_id hold INDUSTRY classification (industry-
   *  primary row). When FALSE, they hold STRATEGY classification (strategy-
   *  primary row). Used by the parallel strategy/theme selector. */
  is_industry_not_strategy: boolean;
  exchange: string;
}

// ----------------------------------------------------------------------------
//  Dividend fetch helper — reads stats.stock_dividends for one or many codes.
//
//  Source: stats.stock_dividends (PK: code, ex_dividend_date) populated by
//  builds.stock.dividends from {code}_dividend.csv files (SSE
//  commonQuery.do, sqlId COMMON_SSE_CP_GPJCTPZ_GPLB_LRFP_FH_L).
//
//  Uses idx_stock_dividends_code_exdate when filtering by single code (code=$1)
//  and idx_stock_dividends_exdate otherwise. NULL/empty result is normal —
//  most stocks have only a handful of dividend events; some have none.
// ----------------------------------------------------------------------------
interface DbStockDividendRow extends QueryResultRow {
  code: string;
  ex_dividend_date: string;
  record_date: string | null;
  dividend_per_share_pre_tax: number | null;
  dividend_per_share_post_tax: number | null;
  total_dividend_wan: number | null;
  pre_close_price: number | null;
  open_price: number | null;
}

const DIVIDEND_SELECT_SQL = `
  SELECT code, ex_dividend_date, record_date,
         dividend_per_share_pre_tax, dividend_per_share_post_tax,
         total_dividend_wan, pre_close_price, open_price
    FROM stats.stock_dividends
`;

function mapDividendRow(r: DbStockDividendRow): StockDividend {
  return {
    ex_dividend_date: formatDate(r.ex_dividend_date),
    record_date: r.record_date ? formatDate(r.record_date) : null,
    dividend_per_share_pre_tax: toNum(r.dividend_per_share_pre_tax),
    dividend_per_share_post_tax: toNum(r.dividend_per_share_post_tax),
    total_dividend_wan: toNum(r.total_dividend_wan),
    pre_close_price: toNum(r.pre_close_price),
    open_price: toNum(r.open_price),
  };
}

/** Fetch dividends for a single code (accepts both suffixed and bare forms,
 *  mirroring getStockBaseline's convention). Returns [] when no dividends
 *  exist or the table has no row for this code. */
export async function getStockDividends(codeParam: string): Promise<StockDividend[]> {
  const code = codeParam.trim();
  if (!code) return [];
  // code = ANY(variants) keeps idx_stock_dividends_code_exdate usable for
  // both suffixed and bare inputs (bare 6-digit codes would otherwise fall
  // back to a REGEXP_REPLACE full scan).
  const sql = `${DIVIDEND_SELECT_SQL} WHERE code = ANY($1::text[]) ORDER BY ex_dividend_date ASC`;
  const rows = await queryRows<DbStockDividendRow>(sql, [codeVariants(code)]);
  return rows.map(mapDividendRow);
}

/** Fetch dividends for many codes at once (used by getStocksCombined).
 *  Returns a Map keyed by suffixed code (e.g. "600008.SS"). Codes with no
 *  dividends are absent from the map (caller should default to []). */
export async function getStockDividendsBatch(codes: string[]): Promise<Map<string, StockDividend[]>> {
  const out = new Map<string, StockDividend[]>();
  if (codes.length === 0) return out;
  const sql = `${DIVIDEND_SELECT_SQL} WHERE code = ANY($1::text[]) ORDER BY code, ex_dividend_date ASC`;
  const rows = await queryRows<DbStockDividendRow>(sql, [codes]);
  for (const r of rows) {
    const code = r.code;
    if (!out.has(code)) out.set(code, []);
    out.get(code)!.push(mapDividendRow(r));
  }
  return out;
}

export async function getStockBaseline(
  codeParam: string,
  startDate?: string,
  endDate?: string,
): Promise<StockBaselineResponse> {
  const code = codeParam.trim();
  if (!code) {
    return { code: codeParam, name: "", dates: [], rows: [], dividends: [] };
  }

  // code = ANY(variants) keeps the (code, date) index usable for both
  // suffixed ("600519.SS") and bare ("600519") inputs.
  const params: unknown[] = [codeVariants(code)];
  let paramIdx = 2;
  const whereParts: string[] = ["code = ANY($1::text[])"];
  // Skip rows with NULL OHLC (identity-only rows like the historical
  // "test"/"test2" entries — they have no matching stock_basic_stats row).
  whereParts.push("close IS NOT NULL");
  const sd = toDateParam(startDate);
  const ed = toDateParam(endDate);
  if (sd) {
    whereParts.push(`date >= $${paramIdx++}::date`);
    params.push(sd);
  }
  if (ed) {
    whereParts.push(`date <= $${paramIdx++}::date`);
    params.push(ed);
  }

  const sql = `
    SELECT date, code, name, prev_close, open, high, low, close,
           pct_change, pe, eps, is_pe_estimated, has_intraday_5mins,
           trading_shares, trading_amount,
           rz_balance, rz_buy, rq_balance_qty, rq_balance_amt, total_balance
      FROM stats.v_stock_baseline
     WHERE ${whereParts.join(" AND ")}
     ORDER BY date ASC
  `;
  const rows = await queryRows<DbStockRow>(sql, params);

  // Resolve the display name via mode (most frequent non-empty name) —
  // robust against a few bogus "test" rows that would otherwise shadow
  // the real name via latest-by-date resolution.
  const nameTally = new Map<string, number>();
  for (const r of rows) {
    if (r.name) nameTally.set(r.name, (nameTally.get(r.name) ?? 0) + 1);
  }
  let name = "";
  let bestCnt = -1;
  for (const [nm, cnt] of nameTally) {
    if (cnt > bestCnt || (cnt === bestCnt && nm < name)) {
      name = nm;
      bestCnt = cnt;
    }
  }

  return {
    code: rows.length > 0 ? rows[0].code : code,
    name,
    dates: rows.map((r) => formatDate(r.date)),
    rows: rows.map<StockBaselineRow>((r) => ({
      date: formatDate(r.date),
      open: toNum(r.open),
      high: toNum(r.high),
      low: toNum(r.low),
      close: toNum(r.close),
      prev_close: toNum(r.prev_close),
      pct_change: toNum(r.pct_change),
      pe: toNum(r.pe),
      eps: toNum(r.eps),
      is_pe_estimated: r.is_pe_estimated === true,
      has_intraday_5mins: r.has_intraday_5mins === true,
      trading_shares: toNum(r.trading_shares) ?? 0,
      trading_amount: toNum(r.trading_amount) ?? 0,
      // Margin fields preserve NULL (no data) so the chart can break the line
      // instead of interpolating across missing/zero samples.
      rz_balance: toNum(r.rz_balance),
      rz_buy: toNum(r.rz_buy),
      rq_balance_qty: toNum(r.rq_balance_qty),
      rq_balance_amt: toNum(r.rq_balance_amt),
      total_balance: toNum(r.total_balance),
    })),
    // Dividend events are not windowed by start/end_date — they are stock-
    // lifetime events and may predate the OHLC window (e.g. dividend from
    // 2022 when the OHLC view starts in 2024). The frontend filters to the
    // visible window when rendering markers.
    dividends: await getStockDividends(code),
  };
}

// ----------------------------------------------------------------------------
//  Meta query — fetch all stocks with precomputed L1/L2 classification from
//  stats.sec_classification (type='stock'), ordered by n_days DESC (most data first).
//
//  Labels (sector_label, industry_label, industry_slug) are DENORMALIZED
//  onto sec_classification by build_classification.py — no JOIN to a
//  catalog table is needed.
//
//  Key points:
//    • stock_identity.code and sec_classification.code (type='stock') BOTH
//      store the code WITH exchange suffix (e.g. "000001.SZ") — JOIN is a
//      direct equality, no suffix stripping.
//    • A stock may have MULTIPLE sec_classification rows (one per qualifying
//      parent index). The LATERAL subquery picks the highest-weight row so
//      each stock gets exactly one (sector_id, industry_id) pair.
//  OPTIMIZATION (was: STOCK_META_SQL aggregating stock_identity ×
//  stock_basic_stats with COUNT/MIN/MAX/mode() per code on EVERY call —
//  millions of daily rows scanned per nav fetch).  The precomputed
//  sec_classification columns (name, n_days, first_date, last_date) already
//  carry the same information, populated by build_classification.py.  Rows
//  are read once per 10-min TTL window (see classification-cache.ts) and
//  shared by listStockThemes(), listStrategyThemes() and getStocksCombined().
//
//  NOTE: n_days is the build-time count from stock_identity rather than a
//  live COUNT — equivalent for the nav/selector use case.  DISTINCT ON
//  (code) picks the primary/highest-weight parent row for stocks with
//  multiple sec_classification rows.
// ----------------------------------------------------------------------------
const MIN_DAYS = 40;

async function getStockMetaRows(): Promise<DbStockMetaRow[]> {
  const rows = await listClassificationMetaRows("stock");
  return rows.filter((r) => r.n_days >= MIN_DAYS);
}

// ----------------------------------------------------------------------------
//  Stock themes — build the two-level L1 sector → L2 industry → stocks tree
//  from the precomputed classification columns in sec_classification.
// ----------------------------------------------------------------------------
export async function listStockThemes(exchange?: string | null): Promise<SectorNode[]> {
  const rows = await getStockMetaRows();
  const exFilter = (exchange ?? "").trim() || null;

  const sectorMap = new Map<string, {
    sector_label: string;
    industries: Map<string, IndustryNode>;
  }>();

  for (const r of rows) {
    // LEFT column: only industry-primary securities. Strategy-primary rows
    // (is_industry_not_strategy=FALSE) carry strategy/theme in
    // sector_id/industry_id and belong in the RIGHT column only.
    if (!r.is_industry_not_strategy) continue;
    if (exFilter && !matchesExchange(r.exchange, exFilter)) continue;
    // Use the SUFFIXED code (e.g. "000001.SZ") as the canonical identifier
    // — this matches what stock_identity, sec_classification, and
    // v_stock_baseline all use.  CodeSearchBar uses partial matching, so
    // searching "000001" still finds "000001.SZ".
    const item = { code: r.code, name: r.name ?? "" };
    if (!sectorMap.has(r.sector_id)) {
      sectorMap.set(r.sector_id, { sector_label: r.sector_label, industries: new Map() });
    }
    const sector = sectorMap.get(r.sector_id)!;
    if (!sector.industries.has(r.industry_id)) {
      sector.industries.set(r.industry_id, {
        industry_id: r.industry_id,
        industry_label: r.industry_label,
        industry_slug: r.industry_slug,
        count: 0,
        items: [],
      });
    }
    const ind = sector.industries.get(r.industry_id)!;
    ind.items.push(item);
    ind.count++;
  }

  const sectors: SectorNode[] = [];
  for (const [sector_id, sector] of sectorMap) {
    const industries = Array.from(sector.industries.values()).sort((a, b) => {
      if (a.industry_id === "OTHER") return 1;
      if (b.industry_id === "OTHER") return -1;
      return b.count - a.count;
    });
    sectors.push({
      sector_id,
      sector_label: sector.sector_label,
      count: industries.reduce((sum, i) => sum + i.count, 0),
      industries,
    });
  }
  sectors.sort((a, b) => {
    if (a.sector_id === "OTHER") return 1;
    if (b.sector_id === "OTHER") return -1;
    return b.count - a.count;
  });
  return sectors;
}

// ----------------------------------------------------------------------------
//  Strategy themes — parallel L1 strategy → L2 theme → stocks tree built from
//  the same STOCK_META_SQL but using the strategy-primary rows
//  (is_industry_not_strategy=FALSE).  sector_id/industry_id on those rows
//  carry the strategy/theme classification.  Tree-building is delegated to
//  the shared buildStrategyThemesFromRows helper to avoid duplicating the
//  grouping/sorting logic across services.
//
//  Note: codes here are stripped of their exchange suffix (e.g. "000001.SZ"
//  → "000001") so the helper's tree keys match what the strategy selector
//  uses.  This differs from listStockThemes() which uses the suffixed code
//  as the canonical identifier.
// ----------------------------------------------------------------------------
export async function listStrategyThemes(exchange?: string | null): Promise<StrategyNode[]> {
  const rows = await getStockMetaRows();
  const exFilter = (exchange ?? "").trim() || null;

  const mappedRows = rows
    .filter((r) => !exFilter || matchesExchange(r.exchange, exFilter))
    .map((r) => ({
      code: stripExchangeSuffix(r.code),
      name: r.name,
      sector_id: r.sector_id,
      sector_label: r.sector_label,
      industry_id: r.industry_id,
      industry_label: r.industry_label,
      industry_slug: r.industry_slug,
      is_industry_not_strategy: r.is_industry_not_strategy,
    }));

  return buildStrategyThemesFromRows(mappedRows);
}

// ----------------------------------------------------------------------------
//  Combined stock data with sector/industry filter + pagination
// ----------------------------------------------------------------------------
export interface StockCombinedQuery {
  sector?: string;
  industry?: string;
  /** Exact stock code.  Accepts both suffixed ("000001.SZ") and bare
   *  ("000001") forms — the bare form is matched against the stripped code.
   *  When set, sector/industry/pagination are bypassed — only the matching
   *  stock is returned. */
  code?: string;
  /** Exchange filter: 'SS' (SSE+STAR), 'SZ' (SZSE+GEM), 'BJ' (BSE). */
  exchange?: string;
  start_date?: string;
  end_date?: string;
  /** L1 strategy id (parallel to sector, e.g. "BROAD", "DIV") — when set,
   *  only strategy-primary stocks are returned. */
  strategy?: string;
  /** L2 theme slug (parallel to industry, e.g. "broad_csi300") — paired
   *  with `strategy`. */
  theme?: string;
  page?: number;
  page_size?: number;
}

export async function getStocksCombined(
  q: StockCombinedQuery,
): Promise<StockCombinedResponse> {
  const sectorFilter = (q.sector ?? "").trim();
  const industryFilter = (q.industry ?? "").trim();
  const strategyFilter = (q.strategy ?? "").trim();
  const themeFilter = (q.theme ?? "").trim();
  const exchangeFilter = (q.exchange ?? "").trim() || null;
  // Normalize the code search: strip any suffix so we can match against both
  // the suffixed ("000001.SZ") and stripped ("000001") forms in metaRows.
  const rawCodeFilter = (q.code ?? "").trim().toUpperCase();
  // Strip optional suffix (.SS/.SZ/.SH/.BJ — accept .SH as an alias for .SS
  // since some users type Tushare convention).
  const codeFilterBare = rawCodeFilter.replace(/\.(SS|SZ|SH|BJ)$/i, "");
  const codeFilterHasSuffix = codeFilterBare !== rawCodeFilter;

  // 1. Fetch all stocks with classification, ordered by n_days DESC (cached).
  const metaRows = await getStockMetaRows();

  // 2. Filter by sector/industry/strategy/theme + exchange (or by exact
  //    code when codeFilter is set).  metaRows are already ordered by
  //    n_days DESC; preserve that order so pagination returns the most-
  //    liquid stocks first.
  const meta = new Map<string, {
    name: string;
    sector_id: string;
    sector_label: string;
    industry_id: string;
    industry_label: string;
    industry_slug: string;
    is_industry_not_strategy: boolean;
  }>();
  const wantedCodes: string[] = [];
  for (const r of metaRows) {
    meta.set(r.code, {
      name: r.name ?? "",
      sector_id: r.sector_id,
      sector_label: r.sector_label,
      industry_id: r.industry_id,
      industry_label: r.industry_label,
      industry_slug: r.industry_slug,
      is_industry_not_strategy: r.is_industry_not_strategy,
    });
    if (codeFilterBare) {
      // Exact code search — ignore sector/industry/exchange filters.
      // Match either the full suffixed code (e.g. "000001.SZ") or the
      // stripped 6-digit form ("000001").
      const stripped = r.code.replace(/\.(SZ|SS|BJ)$/, "").toUpperCase();
      const matches = codeFilterHasSuffix
        ? r.code.toUpperCase() === rawCodeFilter
        : stripped === codeFilterBare;
      if (matches) wantedCodes.push(r.code);
      continue;
    }
    // Classification filter (sector/industry OR strategy/theme — mutually
    // exclusive, handled by matchesClassification) + exchange filter.
    const classOk = matchesClassification(
      r,
      sectorFilter,
      industryFilter,
      strategyFilter,
      themeFilter,
    );
    const exchangeOk = matchesExchange(r.exchange, exchangeFilter);
    if (classOk && exchangeOk) wantedCodes.push(r.code);
  }

  const totalStocks = wantedCodes.length;
  const pageSize = q.page_size && q.page_size > 0 ? q.page_size : 2;
  const totalPages = Math.max(1, Math.ceil(totalStocks / pageSize));
  const page = q.page && q.page > 0 ? Math.min(q.page, totalPages) : 1;
  const pageCodes = wantedCodes.slice((page - 1) * pageSize, page * pageSize);

  if (pageCodes.length === 0) {
    return {
      sector_id: sectorFilter,
      industry_id: industryFilter,
      dates: [],
      stocks: [],
      total_stocks: 0,
      total_pages: 1,
      page: 1,
      page_size: pageSize,
    };
  }

  // 3. Fetch row data for the wanted stocks (with optional date filtering)
  //    pageCodes already carries the suffix (e.g. "000001.SZ"), so we match
  //    directly: code = ANY($1).  Skip rows with NULL close (bogus identity-
  //    only entries like "test"/"test2").
  const params: unknown[] = [];
  let paramIdx = 1;
  params.push(pageCodes);
  const whereParts: string[] = [
    `code = ANY($${paramIdx++}::text[])`,
    "close IS NOT NULL",
  ];
  const startDate = toDateParam(q.start_date);
  const endDate = toDateParam(q.end_date);
  if (startDate) {
    whereParts.push(`date >= $${paramIdx++}::date`);
    params.push(startDate);
  }
  if (endDate) {
    whereParts.push(`date <= $${paramIdx++}::date`);
    params.push(endDate);
  }

  const sql = `
    SELECT code, date, name, prev_close, open, high, low, close,
           pct_change, pe, eps, is_pe_estimated, has_intraday_5mins,
           trading_shares, trading_amount,
           rz_balance, rz_buy, rq_balance_qty, rq_balance_amt, total_balance
      FROM stats.v_stock_baseline
     WHERE ${whereParts.join(" AND ")}
     ORDER BY code, date ASC
  `;
  const dbRows = await queryRows<DbStockRow>(sql, params);

  // Group rows by suffixed code (no stripping — code is the canonical key)
  const byCode = new Map<string, StockBaselineRow[]>();
  // Tally name frequencies per code so we can pick the mode (most frequent)
  // — robust against a few bogus "test" rows that would otherwise shadow
  // the real name via latest-by-date resolution.
  const nameCounts = new Map<string, Map<string, number>>();
  for (const r of dbRows) {
    if (!byCode.has(r.code)) byCode.set(r.code, []);
    byCode.get(r.code)!.push({
      date: formatDate(r.date),
      open: toNum(r.open),
      high: toNum(r.high),
      low: toNum(r.low),
      close: toNum(r.close),
      prev_close: toNum(r.prev_close),
      pct_change: toNum(r.pct_change),
      pe: toNum(r.pe),
      eps: toNum(r.eps),
      is_pe_estimated: r.is_pe_estimated === true,
      has_intraday_5mins: r.has_intraday_5mins === true,
      trading_shares: toNum(r.trading_shares) ?? 0,
      trading_amount: toNum(r.trading_amount) ?? 0,
      rz_balance: toNum(r.rz_balance),
      rz_buy: toNum(r.rz_buy),
      rq_balance_qty: toNum(r.rq_balance_qty),
      rq_balance_amt: toNum(r.rq_balance_amt),
      total_balance: toNum(r.total_balance),
    });
    if (r.name) {
      if (!nameCounts.has(r.code)) nameCounts.set(r.code, new Map());
      const counts = nameCounts.get(r.code)!;
      counts.set(r.name, (counts.get(r.name) ?? 0) + 1);
    }
  }
  // Resolve display name = mode (most frequent name) per code
  const nameByCode = new Map<string, string>();
  for (const [code, counts] of nameCounts) {
    let best = "";
    let bestCnt = -1;
    for (const [nm, cnt] of counts) {
      if (cnt > bestCnt || (cnt === bestCnt && nm < best)) {
        best = nm;
        bestCnt = cnt;
      }
    }
    if (best) nameByCode.set(code, best);
  }

  // 4. Build bundles — use the suffixed code as the canonical identifier.
  //    Batch-fetch dividends for all pageCodes in one query (small N — usually
  //    1-2 codes per page — so this is essentially free).
  const dividendsByCode = await getStockDividendsBatch(pageCodes);

  const stocks: StockBundle[] = [];
  for (const code of pageCodes) {
    const rows = byCode.get(code) ?? [];
    if (rows.length === 0) continue;
    const m = meta.get(code)!;
    stocks.push({
      code,
      name: nameByCode.get(code) ?? m.name,
      sector_id: m.sector_id,
      sector_label: m.sector_label,
      industry_id: m.industry_id,
      industry_label: m.industry_label,
      rows,
      dividends: dividendsByCode.get(code) ?? [],
    });
  }

  // Union of all dates across selected stocks (sorted)
  const dateSet = new Set<string>();
  for (const s of stocks) for (const r of s.rows) dateSet.add(r.date);
  const dates = Array.from(dateSet).sort();

  return {
    sector_id: sectorFilter,
    industry_id: industryFilter,
    dates,
    stocks,
    total_stocks: totalStocks,
    total_pages: totalPages,
    page,
    page_size: pageSize,
  };
}
