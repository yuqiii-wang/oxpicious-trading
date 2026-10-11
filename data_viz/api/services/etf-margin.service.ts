/**
 * ETF + Margin service — queries stats.v_etf_margin view and stats.sec_classification.
 *
 * ETF classification (L1 sector + L2 industry) + tracking index are read from
 * precomputed columns in stats.sec_classification (type='etf', populated by
 * build_classification.py via CSV + index inheritance).  No classification
 * logic lives in TS — the Python build_classification.py is the single
 * source of truth.
 *
 * All row data is fetched from the database with index-driven WHERE clauses
 * on (code, date).
 */
import { queryRows, toDateParam, formatDate, toNum } from "../lib/db.js";
import type { QueryResultRow } from "pg";
import { stripExchangeSuffix, matchesExchange } from "../lib/classify-etf.js";
import { buildStrategyThemesFromRows, matchesClassification } from "./_shared.js";
import { cachedRows } from "./classification-cache.js";
import { LABEL_CODE } from "./label-codes.js";
import type {
  EtfMarginRow,
  EtfBundle,
  EtfMarginCombinedResponse,
  SectorNode,
  IndustryNode,
  StrategyNode,
} from "../../shared/types.js";

export interface EtfMarginQuery {
  /** L1 sector id (e.g. "FIN", "TECH", "BROAD"). */
  sector?: string;
  /** L2 industry slug (e.g. "banks", "semi", "broad_csi300"). */
  industry?: string;
  /** Exact ETF code (bare 6-digit). When set, sector/industry
   *  filters and pagination are bypassed — only the matching ETF is returned. */
  code?: string;
  /** Exchange filter: 'SS' (SSE+STAR), 'SZ' (SZSE+GEM), 'BJ' (BSE). */
  exchange?: string;
  start_date?: string;
  end_date?: string;
  /** Optional cap on number of ETFs returned per theme (for dev / fast preview). */
  limit_per_theme?: number;
  /** 1-based page number for pagination. */
  page?: number;
  /** Number of ETFs per page (default 2). */
  page_size?: number;
  /** L1 strategy id (parallel to sector, e.g. "BROAD", "DIV") — when set,
   *  only strategy-primary ETFs are returned. */
  strategy?: string;
  /** L2 theme slug (parallel to industry, e.g. "broad_csi300") — paired
   *  with `strategy`. */
  theme?: string;
}

// ----------------------------------------------------------------------------
//  DB row types
// ----------------------------------------------------------------------------
interface DbEtfMetaRow extends QueryResultRow {
  code: string;
  name: string;
  sector_id: string;
  sector_label: string;
  industry_id: string;
  industry_label: string;
  industry_slug: string;
  /** When TRUE, sector_id/industry_id hold INDUSTRY classification (industry-
   *  primary row). When FALSE, they hold STRATEGY classification (strategy-
   *  primary row). Used by the parallel strategy/theme selector. */
  is_industry_not_strategy: boolean;
  index_code: string;
  index_name: string;
  exchange: string;
  has_margin: boolean;
  n_days: number;
}

interface DbEtfMarginRow extends QueryResultRow {
  date: Date | string;
  prev_close: number | null;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  eps: number | null;
  adj_open: number | null;
  adj_high: number | null;
  adj_low: number | null;
  adj_close: number | null;
  adj_prev_close: number | null;
  is_split_event_day: number | null;
  action_type: string | null;
  implied_dividend_per_share: number | null;
  cum_split_factor: number | null;
  // Dividend price estimation curve (analysis.dividend_est — raw
  // price units; the UI converts to adjusted space via adj_close/close).
  div_est_price: number | null;
  seg_end_date: Date | string | null;
  seg_next_div: number | null;
  trading_shares: number | null;
  trading_amount: number | null;
  rz_balance: number | null;
  rq_balance_qty: number | null;
  rq_balance_amt: number | null;
  total_balance: number | null;
}

// ----------------------------------------------------------------------------
//  Row transformer
// ----------------------------------------------------------------------------
function transformEtfRow(r: DbEtfMarginRow): EtfMarginRow {
  return {
    date: formatDate(r.date),
    // prev_close/OHLC preserve NULL (no data) — close-only series (feeder
    // NAV via builds.etf.feeder_nav) leave the FIRST row's open/prev_close
    // NULL by design; coercing to 0 dragged the rebased y-axis to -100%
    // for that bar. Only close keeps the defensive 0 (core series).
    prev_close: toNum(r.prev_close),
    open: toNum(r.open),
    high: toNum(r.high),
    low: toNum(r.low),
    close: toNum(r.close) ?? 0,
    eps: toNum(r.eps),
    adj_open: toNum(r.adj_open),
    adj_high: toNum(r.adj_high),
    adj_low: toNum(r.adj_low),
    adj_close: toNum(r.adj_close),
    adj_prev_close: toNum(r.adj_prev_close),
    is_split_event_day: Number(r.is_split_event_day) | 0,
    action_type: r.action_type ?? null,
    implied_dividend_per_share: toNum(r.implied_dividend_per_share),
    cum_split_factor: toNum(r.cum_split_factor),
    div_est_price: toNum(r.div_est_price),
    seg_end_date: r.seg_end_date != null ? formatDate(r.seg_end_date) : null,
    seg_next_div: toNum(r.seg_next_div),
    trading_shares: toNum(r.trading_shares) ?? 0,
    trading_amount: toNum(r.trading_amount) ?? 0,
    // Margin fields preserve NULL (no data) so the chart can break the line
    // instead of interpolating across missing/zero samples.
    rz_balance: toNum(r.rz_balance),
    rq_balance_qty: toNum(r.rq_balance_qty),
    rq_balance_amt: toNum(r.rq_balance_amt),
    total_balance: toNum(r.total_balance),
  };
}

// (The row-fetch column list lives inline in getEtfMarginCombined's
// base-tables SQL — see step 5 there for why it no longer selects through
// stats.v_etf_margin.)

// ----------------------------------------------------------------------------
//  Meta query — fetch all ETFs with precomputed L1/L2 classification from
//  stats.sec_classification (type='etf').
//
//  Ordering (high → low priority):
//    1. has_margin DESC   — ETFs with margin data (融资融券) come first
//    2. n_days DESC       — longer date range (more trading-day history) first
//    3. v.code            — stable final tiebreaker
//
//  Filter: HAVING COUNT(v.date) >= 40 — ETFs with fewer than 40 trading days
//  of actual rows in v_etf_margin are suppressed from the data-viz list
//  (matches the "Insufficient data" alert threshold in EtfMarginPanel).
//  Uses the live row count from the view (not the precomputed sec_classification
//  .n_days column, which may be stale).
//
//  Labels are DENORMALIZED onto sec_classification by build_classification.py
//  — no JOIN to a catalog table is needed.
// ----------------------------------------------------------------------------
const META_SQL = `
  SELECT v.code,
         MAX(v.name) AS name,
         COALESCE(MAX(m.sector_id),       'OTHER')  AS sector_id,
         COALESCE(MAX(m.sector_label),    '其他')   AS sector_label,
         COALESCE(MAX(m.industry_id),     'OTHER')  AS industry_id,
         COALESCE(MAX(m.industry_label),  '未分类')  AS industry_label,
         COALESCE(MAX(m.industry_slug),   'other')  AS industry_slug,
         COALESCE(BOOL_OR(m.is_industry_not_strategy), TRUE) AS is_industry_not_strategy,
         COALESCE(MAX(m.parent_index_code), '')     AS index_code,
         COALESCE(MAX(mi.name), '')                  AS index_name,
         COALESCE(MAX(m.exchange), '')               AS exchange,
         COALESCE(BOOL_OR(m.has_margin), FALSE)      AS has_margin,
         COUNT(v.date)                               AS n_days
    FROM stats.v_etf_margin v
    LEFT JOIN stats.sec_classification m ON v.code = m.code AND m.type = 'etf'
    LEFT JOIN stats.sec_classification mi ON mi.code = m.parent_index_code AND mi.type = 'index'
   GROUP BY v.code
  HAVING COUNT(v.date) >= 40
     AND COALESCE(BOOL_OR(m.is_active), TRUE)
   ORDER BY has_margin DESC, n_days DESC, v.code
`;

// ----------------------------------------------------------------------------
//  Code-lookup query — used for exact-code search (the CodeSearchBar).
//
//  Unlike META_SQL, this does NOT apply the `HAVING COUNT(v.date) >= 40`
//  threshold. Newly-listed ETFs (e.g. 159066 with ~20 trading days) must be
//  findable by exact code even before they accumulate 40 days of history.
//  The EtfMarginPanel still shows an "Insufficient data" info alert for < 40
//  rows, so the browse-list threshold (META_SQL) and the search path are
//  intentionally decoupled.
// ----------------------------------------------------------------------------
const META_SQL_FOR_CODE = `
  SELECT v.code,
         MAX(v.name) AS name,
         COALESCE(MAX(m.sector_id),       'OTHER')  AS sector_id,
         COALESCE(MAX(m.sector_label),    '其他')   AS sector_label,
         COALESCE(MAX(m.industry_id),     'OTHER')  AS industry_id,
         COALESCE(MAX(m.industry_label),  '未分类')  AS industry_label,
         COALESCE(MAX(m.industry_slug),   'other')  AS industry_slug,
         COALESCE(BOOL_OR(m.is_industry_not_strategy), TRUE) AS is_industry_not_strategy,
         COALESCE(MAX(m.parent_index_code), '')     AS index_code,
         COALESCE(MAX(mi.name), '')                  AS index_name,
         COALESCE(MAX(m.exchange), '')               AS exchange,
         COALESCE(BOOL_OR(m.has_margin), FALSE)      AS has_margin,
         COUNT(v.date)                               AS n_days
    FROM stats.v_etf_margin v
    LEFT JOIN stats.sec_classification m ON v.code = m.code AND m.type = 'etf'
    LEFT JOIN stats.sec_classification mi ON mi.code = m.parent_index_code AND mi.type = 'index'
   WHERE v.code = ANY($1::text[])
   GROUP BY v.code
`;

// Cached meta rows — META_SQL aggregates the v_etf_margin view (expensive);
// the classification/content only changes on the nightly builds, so the rows
// are read once per 10-min TTL window and shared by listThemes(),
// listStrategyThemes() and getEtfsCombined(). META_SQL_FOR_CODE (exact-code
// search) stays uncached.
async function getEtfMetaRows(): Promise<DbEtfMetaRow[]> {
  return cachedRows("etf-margin:meta", () => queryRows<DbEtfMetaRow>(META_SQL));
}

// ----------------------------------------------------------------------------
//  Themes — build the two-level L1 sector → L2 industry → ETFs tree from
//  the precomputed classification columns in stats.sec_classification.
// ----------------------------------------------------------------------------
export async function listThemes(exchange?: string | null): Promise<SectorNode[]> {
  const rows = await getEtfMetaRows();
  const exFilter = (exchange ?? "").trim() || null;

  // Group ETFs by (sector_id) → (industry_id)
  const sectorMap = new Map<string, {
    sector_label: string;
    industries: Map<string, IndustryNode>;
  }>();

  for (const r of rows) {
    // LEFT column: only industry-primary ETFs. Strategy-primary rows
    // (is_industry_not_strategy=FALSE) carry strategy/theme in
    // sector_id/industry_id and belong in the RIGHT column only.
    if (!r.is_industry_not_strategy) continue;
    // Apply exchange filter so the nav tree (sector chips + L3 item chips)
    // respects the selected exchange. Without this, cross-border ETFs
    // (HK/OVERSEAS) would appear in the tree even when "All (primary)" is
    // selected — e.g. 161128 标普科技 (OVERSEAS) leaking into TECH sector.
    if (exFilter && !matchesExchange(r.exchange, exFilter)) continue;
    const code = stripExchangeSuffix(r.code);
    if (!code) continue;
    const etf = { code, name: r.name ?? "" };

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
    ind.items.push(etf);
    ind.count++;
  }

  // Build the output tree, sorted by count DESC (OTHER last)
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
//  Strategy themes — parallel L1 strategy → L2 theme → ETFs tree built from
//  the same META_SQL but using the strategy-primary rows
//  (is_industry_not_strategy=FALSE).  sector_id/industry_id on those rows
//  carry the strategy/theme classification.  Tree-building is delegated to
//  the shared buildStrategyThemesFromRows helper to avoid duplicating the
//  grouping/sorting logic across services.
// ----------------------------------------------------------------------------
export async function listStrategyThemes(exchange?: string | null): Promise<StrategyNode[]> {
  const rows = await getEtfMetaRows();
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
//  Combined ETF + margin data with sector/industry filter + pagination
// ----------------------------------------------------------------------------
export async function getEtfMarginCombined(
  q: EtfMarginQuery,
): Promise<EtfMarginCombinedResponse> {
  const sectorFilter = (q.sector ?? "").trim();
  const industryFilter = (q.industry ?? "").trim();
  const strategyFilter = (q.strategy ?? "").trim();
  const themeFilter = (q.theme ?? "").trim();
  const exchangeFilter = (q.exchange ?? "").trim() || null;
  // When a code filter is provided, sector/industry/pagination are bypassed.
  const codeFilter = stripExchangeSuffix((q.code ?? "").trim());

  // 1. Fetch all distinct (code, name) + classification, ordered by has_margin
  //    DESC, n_days DESC (see META_SQL).
  //
  //    For exact-code search, use META_SQL_FOR_CODE instead — it fetches only
  //    the requested code WITHOUT the `HAVING COUNT(v.date) >= 40` threshold
  //    so newly-listed ETFs (< 40 trading days) are still searchable. The
  //    browse-list threshold remains in place for listThemes() to keep the
  //    dropdown free of one-day / delisted entries.
  const metaRows = codeFilter
    ? await queryRows<DbEtfMetaRow>(META_SQL_FOR_CODE, [[codeFilter]])
    : await getEtfMetaRows();

  // 2. Filter by sector + industry + exchange (or by exact code when codeFilter is set).
  //    metaRows are already ordered by has_margin DESC, n_days DESC
  //    (see META_SQL); preserve that order so pagination returns ETFs with
  //    margin data + longer history first.
  const meta = new Map<string, {
    name: string;
    sector_id: string;
    sector_label: string;
    industry_id: string;
    industry_label: string;
    industry_slug: string;
    is_industry_not_strategy: boolean;
    index_code: string;
    index_name: string;
  }>();
  const wantedCodes: string[] = [];
  for (const r of metaRows) {
    const code = stripExchangeSuffix(r.code);
    if (!code) continue;
    meta.set(code, {
      name: r.name ?? "",
      sector_id: r.sector_id,
      sector_label: r.sector_label,
      industry_id: r.industry_id,
      industry_label: r.industry_label,
      industry_slug: r.industry_slug,
      is_industry_not_strategy: r.is_industry_not_strategy,
      index_code: r.index_code ?? "",
      index_name: r.index_name ?? "",
    });
    if (codeFilter) {
      // Exact code search — ignore sector/industry/exchange filters.
      if (code.toUpperCase() === codeFilter.toUpperCase()) wantedCodes.push(code);
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
    if (classOk && exchangeOk) wantedCodes.push(code);
  }

  // 3. Optional cap per theme (legacy dev param)
  let wantedList = wantedCodes;
  if (q.limit_per_theme && q.limit_per_theme > 0) {
    wantedList = wantedList.slice(0, q.limit_per_theme);
  }
  const totalEtfs = wantedList.length;

  // 4. Pagination
  const pageSize = q.page_size && q.page_size > 0 ? q.page_size : 2;
  const totalPages = Math.max(1, Math.ceil(totalEtfs / pageSize));
  const page = q.page && q.page > 0 ? Math.min(q.page, totalPages) : 1;
  const pageCodes = wantedList.slice((page - 1) * pageSize, page * pageSize);

  if (pageCodes.length === 0) {
    return {
      theme_slug: industryFilter || sectorFilter || "",
      sector_id: sectorFilter,
      industry_id: industryFilter,
      dates: [],
      etfs: [],
      total_etfs: 0,
      total_pages: 1,
      page: 1,
      page_size: pageSize,
    };
  }

  // 5. Fetch row data for the wanted ETFs (with date filtering)
  // ETF codes are BARE in the DB (2026-09-30) — pageCodes pass through
  // as-is so code = ANY(...) drives the (code, date) index instead of a
  // REGEXP_REPLACE full scan.
  //
  // BASE TABLES + per-table code predicates, NOT stats.v_etf_margin: the
  // view's LEFT JOIN chain defeats ANY-array pushdown — the planner
  // hash-joins FULL seq scans of the 1M-row etf_adjustment /
  // etf_liquidity_margin partitions to return one code's ~5k rows (measured
  // 2.4-3.8s per exact-code fetch, 2026-09-29). Putting the code predicate
  // on EVERY table makes each side an index probe (~5ms). Same rationale as
  // getStocksCombined / getStockDaily; INNER JOIN basic_stats + close IS
  // NOT NULL also skips bogus identity-only entries (incomplete same-day
  // rows), matching the stock service. analysis.dividend_est is
  // LEFT JOINed for the dividend estimation curve (code predicate on it
  // too, same rule); etf_tech_stats columns are not
  // selected by the row list, so that table needs no join.
  const params: unknown[] = [pageCodes]; // $1 = the code array
  let paramIdx = 2; // date bounds start at $2
  const codePred = `= ANY($1::text[])`;
  const whereParts: string[] = [`i.code ${codePred}`, "o.close IS NOT NULL"];
  const startDate = toDateParam(q.start_date);
  const endDate = toDateParam(q.end_date);
  if (startDate) {
    whereParts.push(`i.date >= $${paramIdx++}::date`);
    params.push(startDate);
  }
  if (endDate) {
    whereParts.push(`i.date <= $${paramIdx++}::date`);
    params.push(endDate);
  }

  const sql = `
    SELECT i.code, i.name, i.date,
           o.prev_close, o.open, o.high, o.low, o.close, o.eps,
           a.adj_open, a.adj_high, a.adj_low, a.adj_close, a.adj_prev_close,
           a.is_split_event_day, a.action_type, a.implied_dividend_per_share, a.cum_split_factor,
           dt.div_est_price, dt.seg_end_date, dt.seg_next_div,
           lm.trading_shares, lm.trading_amount,
           lm.rz_balance, lm.rq_balance_qty, lm.rq_balance_amt, lm.total_balance
      FROM stats.etf_identity i
      JOIN stats.etf_basic_stats o
        ON o.date = i.date AND o.code = i.code AND o.code ${codePred}
      LEFT JOIN stats.etf_adjustment a
        ON a.date = i.date AND a.code = i.code AND a.code ${codePred}
      LEFT JOIN analysis.dividend_est dt
        ON dt.date = i.date AND dt.code = i.code
        AND dt.sec_type = ${LABEL_CODE.sec_type.etf} -- label_dim code: etf
       AND dt.code ${codePred}
      LEFT JOIN stats.etf_liquidity_margin lm
        ON lm.date = i.date AND lm.code = i.code AND lm.code ${codePred}
     WHERE ${whereParts.join(" AND ")}
     ORDER BY i.code, i.date ASC
  `;
  const dbRows = await queryRows<DbEtfMarginRow & { code: string; name: string }>(sql, params);

  // Group rows by stripped code
  const byCode = new Map<string, EtfMarginRow[]>();
  const nameByCode = new Map<string, string>();
  for (const r of dbRows) {
    const stripped = stripExchangeSuffix(r.code);
    if (!byCode.has(stripped)) byCode.set(stripped, []);
    byCode.get(stripped)!.push(transformEtfRow(r));
    nameByCode.set(stripped, r.name ?? "");
  }

  // 6. Build bundles
  const etfs: EtfBundle[] = [];
  for (const code of pageCodes) {
    const rows = byCode.get(code) ?? [];
    if (rows.length === 0) continue;
    const m = meta.get(code)!;
    etfs.push({
      code,
      name: nameByCode.get(code) ?? m.name,
      is_bond: m.sector_id === "BOND",
      rows,
      sector_id: m.sector_id,
      sector_label: m.sector_label,
      industry_id: m.industry_id,
      industry_label: m.industry_label,
      index_code: m.index_code,
      index_name: m.index_name,
    });
  }

  // Union of all dates across selected ETFs (sorted)
  const dateSet = new Set<string>();
  for (const e of etfs) for (const r of e.rows) dateSet.add(r.date);
  const dates = Array.from(dateSet).sort();

  return {
    theme_slug: industryFilter || sectorFilter || "",
    sector_id: sectorFilter,
    industry_id: industryFilter,
    dates,
    etfs,
    total_etfs: totalEtfs,
    total_pages: totalPages,
    page,
    page_size: pageSize,
  };
}

