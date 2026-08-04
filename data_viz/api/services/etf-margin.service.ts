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
import type {
  EtfMarginRow,
  EtfBundle,
  EtfMarginCombinedResponse,
  SectorNode,
  IndustryNode,
} from "../../shared/types.js";

export interface EtfMarginQuery {
  /** L1 sector id (e.g. "FIN", "TECH", "BROAD"). */
  sector?: string;
  /** L2 industry slug (e.g. "banks", "semi", "broad_csi300"). */
  industry?: string;
  /** Exact ETF code (6-digit, suffix-stripped). When set, sector/industry
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
}

// ----------------------------------------------------------------------------
//  DB row types
// ----------------------------------------------------------------------------
interface DbEtfMetaRow extends QueryResultRow {
  code: string;
  name: string;
  score: number;
  sector_id: string;
  sector_label: string;
  industry_id: string;
  industry_label: string;
  industry_slug: string;
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
  adj_open: number | null;
  adj_high: number | null;
  adj_low: number | null;
  adj_close: number | null;
  adj_prev_close: number | null;
  is_split_event_day: number | null;
  action_type: string | null;
  implied_dividend_per_share: number | null;
  cum_split_factor: number | null;
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
    prev_close: toNum(r.prev_close) ?? 0,
    open: toNum(r.open) ?? 0,
    high: toNum(r.high) ?? 0,
    low: toNum(r.low) ?? 0,
    close: toNum(r.close) ?? 0,
    adj_open: toNum(r.adj_open),
    adj_high: toNum(r.adj_high),
    adj_low: toNum(r.adj_low),
    adj_close: toNum(r.adj_close),
    adj_prev_close: toNum(r.adj_prev_close),
    is_split_event_day: Number(r.is_split_event_day) | 0,
    action_type: r.action_type ?? null,
    implied_dividend_per_share: toNum(r.implied_dividend_per_share),
    cum_split_factor: toNum(r.cum_split_factor),
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

const ETF_MARGIN_COLUMNS = `
  date, prev_close, open, high, low, close,
  adj_open, adj_high, adj_low, adj_close, adj_prev_close,
  is_split_event_day, action_type, implied_dividend_per_share, cum_split_factor,
  trading_shares, trading_amount,
  rz_balance, rq_balance_qty, rq_balance_amt, total_balance
`;

// ----------------------------------------------------------------------------
//  Meta query — fetch all ETFs with precomputed L1/L2 classification from
//  stats.sec_classification (type='etf').
//
//  Ordering (high → low priority):
//    1. has_margin DESC   — ETFs with margin data (融资融券) come first
//    2. n_days DESC       — longer date range (more trading-day history) first
//    3. score DESC        — selectivity_rank_score as a tiebreaker
//    4. v.code            — stable final tiebreaker
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
         COALESCE(MAX(m.selectivity_rank_score), 0) AS score,
         COALESCE(MAX(m.sector_id),       'OTHER')  AS sector_id,
         COALESCE(MAX(m.sector_label),    '其他')   AS sector_label,
         COALESCE(MAX(m.industry_id),     'OTHER')  AS industry_id,
         COALESCE(MAX(m.industry_label),  '未分类')  AS industry_label,
         COALESCE(MAX(m.industry_slug),   'other')  AS industry_slug,
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
   ORDER BY has_margin DESC, n_days DESC, score DESC, v.code
`;

// ----------------------------------------------------------------------------
//  Themes — build the two-level L1 sector → L2 industry → ETFs tree from
//  the precomputed classification columns in stats.sec_classification.
// ----------------------------------------------------------------------------
export async function listThemes(): Promise<SectorNode[]> {
  const rows = await queryRows<DbEtfMetaRow>(META_SQL);

  // Group ETFs by (sector_id) → (industry_id)
  const sectorMap = new Map<string, {
    sector_label: string;
    industries: Map<string, IndustryNode>;
  }>();

  for (const r of rows) {
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
//  Combined ETF + margin data with sector/industry filter + pagination
// ----------------------------------------------------------------------------
export async function getEtfMarginCombined(
  q: EtfMarginQuery,
): Promise<EtfMarginCombinedResponse> {
  const sectorFilter = (q.sector ?? "").trim();
  const industryFilter = (q.industry ?? "").trim();
  const exchangeFilter = (q.exchange ?? "").trim() || null;
  // When a code filter is provided, sector/industry/pagination are bypassed.
  const codeFilter = stripExchangeSuffix((q.code ?? "").trim());

  // 1. Fetch all distinct (code, name) + classification, ordered by has_margin
  //    DESC, n_days DESC, score DESC (see META_SQL).
  const metaRows = await queryRows<DbEtfMetaRow>(META_SQL);

  // 2. Filter by sector + industry + exchange (or by exact code when codeFilter is set).
  //    metaRows are already ordered by has_margin DESC, n_days DESC, score DESC
  //    (see META_SQL); preserve that order so pagination returns ETFs with
  //    margin data + longer history first.
  const meta = new Map<string, { name: string; sector_id: string; sector_label: string; industry_id: string; industry_label: string; index_code: string; index_name: string }>();
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
      index_code: r.index_code ?? "",
      index_name: r.index_name ?? "",
    });
    if (codeFilter) {
      // Exact code search — ignore sector/industry/exchange filters.
      if (code.toUpperCase() === codeFilter.toUpperCase()) wantedCodes.push(code);
      continue;
    }
    const sectorOk = !sectorFilter || r.sector_id === sectorFilter;
    // industry filter matches either the industry_slug (URL-friendly) or the
    // industry_id (canonical).  Both are unique per industry.
    const industryOk = !industryFilter || r.industry_slug === industryFilter || r.industry_id === industryFilter;
    const exchangeOk = matchesExchange(r.exchange, exchangeFilter);
    if (sectorOk && industryOk && exchangeOk) wantedCodes.push(code);
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
  const params: unknown[] = [];
  let paramIdx = 1;

  params.push(pageCodes);
  const whereParts: string[] = [`REGEXP_REPLACE(code, '\\.(SZ|SS|SH)$', '') = ANY($${paramIdx++}::text[])`];
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
    SELECT code, name, ${ETF_MARGIN_COLUMNS}
    FROM stats.v_etf_margin
    WHERE ${whereParts.join(" AND ")}
    ORDER BY code, date ASC
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

