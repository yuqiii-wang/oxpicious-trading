/**
 * ETF + Margin service — queries stats.v_etf_margin view and stats.etf_meta.
 *
 * ETF classification (L1 sector + L2 industry) is read from precomputed
 * columns in stats.etf_meta (populated by build_etf_classification.py via
 * _classification.classify_etf_full()).  No classification logic lives in TS —
 * the Python _classification.py is the single source of truth.
 *
 * All row data is fetched from the database with index-driven WHERE clauses
 * on (code, date).
 */
import { queryRows, toDateParam, formatDate, toNum } from "../lib/db.js";
import type { QueryResultRow } from "pg";
import { stripExchangeSuffix } from "../lib/classify-etf.js";
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
  volume_wan: number | null;
  amount_wan: number | null;
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
    volume_wan: toNum(r.volume_wan) ?? 0,
    amount_wan: toNum(r.amount_wan) ?? 0,
    rz_balance: toNum(r.rz_balance) ?? 0,
    rq_balance_qty: toNum(r.rq_balance_qty) ?? 0,
    rq_balance_amt: toNum(r.rq_balance_amt) ?? 0,
    total_balance: toNum(r.total_balance) ?? 0,
  };
}

const ETF_MARGIN_COLUMNS = `
  date, prev_close, open, high, low, close,
  adj_open, adj_high, adj_low, adj_close, adj_prev_close,
  is_split_event_day, action_type, implied_dividend_per_share, cum_split_factor,
  volume_wan, amount_wan,
  rz_balance, rq_balance_qty, rq_balance_amt, total_balance
`;

// ----------------------------------------------------------------------------
//  Meta query — fetch all ETFs with precomputed L1/L2 classification from
//  stats.etf_meta, ordered by data_quality_score DESC.
// ----------------------------------------------------------------------------
const META_SQL = `
  SELECT v.code,
         MAX(v.name) AS name,
         COALESCE(MAX(m.data_quality_score), 0) AS score,
         COALESCE(MAX(m.sector_id),     'OTHER')     AS sector_id,
         COALESCE(MAX(m.sector_label),  '其他')       AS sector_label,
         COALESCE(MAX(m.industry_id),   'OTHER')     AS industry_id,
         COALESCE(MAX(m.industry_label),'未分类')     AS industry_label,
         COALESCE(MAX(m.industry_slug), 'other')     AS industry_slug,
         COALESCE(MAX(eim.index_code), '')            AS index_code,
         COALESCE(MAX(eim.index_name), '')            AS index_name
    FROM stats.v_etf_margin v
    LEFT JOIN stats.etf_meta m ON v.code = m.code
    LEFT JOIN stats.etf_index_map eim ON v.code = eim.etf_code
   GROUP BY v.code
   ORDER BY score DESC, v.code
`;

// ----------------------------------------------------------------------------
//  Themes — build the two-level L1 sector → L2 industry → ETFs tree from
//  the precomputed classification columns in stats.etf_meta.
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
  // When a code filter is provided, sector/industry/pagination are bypassed.
  const codeFilter = stripExchangeSuffix((q.code ?? "").trim());

  // 1. Fetch all distinct (code, name) + classification, ordered by score DESC.
  const metaRows = await queryRows<DbEtfMetaRow>(META_SQL);

  // 2. Filter by sector + industry (or by exact code when codeFilter is set).
  //    metaRows are already ordered by score DESC; preserve that order so
  //    pagination returns the highest-quality ETFs first.
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
      // Exact code search — ignore sector/industry filters.
      if (code.toUpperCase() === codeFilter.toUpperCase()) wantedCodes.push(code);
      continue;
    }
    const sectorOk = !sectorFilter || r.sector_id === sectorFilter;
    // industry filter matches either the industry_slug (URL-friendly) or the
    // industry_id (canonical).  Both are unique per industry.
    const industryOk = !industryFilter || r.industry_slug === industryFilter || r.industry_id === industryFilter;
    if (sectorOk && industryOk) wantedCodes.push(code);
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

