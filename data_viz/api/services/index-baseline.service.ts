/**
 * Index Baseline service — queries stats.v_index_baseline view + stats.sec_classification.
 *
 * Returns the list of available indices and their daily OHLCV + PE + MA data.
 * Index classification (L1 sector + L2 industry) is read from precomputed
 * columns in stats.sec_classification (type='index', populated by
 * build_classification.py via keyword rules).
 * No classification logic lives in TS.
 */
import { queryRows, toDateParam, formatDate, toNum } from "../lib/db.js";
import type { QueryResultRow } from "pg";
import { matchesExchange } from "../lib/classify-etf.js";
import { listClassificationMetaRows } from "./classification-cache.js";
import type {
  IndexInfo,
  IndexBaselineResponse,
  IndexBaselineRow,
  IndexIntraday5minResponse,
  IndexIntraday5minRow,
  IndexBundle,
  IndexCombinedResponse,
  SectorNode,
  IndustryNode,
  StrategyNode,
  ThemeNode,
} from "../../shared/types.js";

interface DbIndexMetaRow extends QueryResultRow {
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
  is_industry_not_strategy: boolean;
  exchange: string;
  is_dummy: boolean;
}

interface DbIndexRow extends QueryResultRow {
  code: string;
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
  has_intraday_5mins: boolean | null;
}

interface DbIntradayRow extends QueryResultRow {
  time: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  change: number | null;
  change_pct: number | null;
}

function transformRow(r: DbIndexRow): IndexBaselineRow {
  return {
    date: formatDate(r.date),
    open: toNum(r.open),
    high: toNum(r.high),
    low: toNum(r.low),
    close: toNum(r.close),
    trading_shares: toNum(r.trading_shares),
    trading_amount: toNum(r.trading_amount),
    change_pct: toNum(r.change_pct),
    pe: toNum(r.pe),
    cons_number: toNum(r.cons_number),
    ma5: toNum(r.ma5),
    ma20: toNum(r.ma20),
    ma60: toNum(r.ma60),
    ma120: toNum(r.ma120),
    ma255: toNum(r.ma255),
    has_intraday_5mins: r.has_intraday_5mins === true,
  };
}

/** List all available indices with their date coverage.
 *  Filters out indices with fewer than 40 trading days (insufficient for
 *  visualization — matches the "Insufficient data" alert threshold). */
export async function listIndices(): Promise<IndexInfo[]> {
  const rows = await queryRows<DbIndexMetaRow>(`
    SELECT code,
           MAX(name) AS name,
           COUNT(*)   AS n_days,
           MIN(date)::text AS first_date,
           MAX(date)::text AS last_date
      FROM stats.index_identity
     GROUP BY code
    HAVING COUNT(*) >= 40
     ORDER BY n_days DESC, code
  `);
  return rows.map((r) => ({
    code: r.code,
    name: r.name ?? "",
    n_days: parseInt(String(r.n_days), 10) || 0,
    first_date: r.first_date ?? "",
    last_date: r.last_date ?? "",
  }));
}

/** Fetch daily index data for a single index code within a date range. */
export async function getIndexBaseline(
  code: string,
  startDate?: string,
  endDate?: string,
): Promise<IndexBaselineResponse> {
  const params: unknown[] = [code];
  let paramIdx = 2;
  const whereParts: string[] = [`code = $1`];
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
    SELECT date, open, high, low, close, trading_shares, trading_amount, change_pct,
           pe, cons_number, ma5, ma20, ma60, ma120, ma255, has_intraday_5mins
      FROM stats.v_index_baseline
     WHERE ${whereParts.join(" AND ")}
     ORDER BY date ASC
  `;
  const rows = await queryRows<DbIndexRow>(sql, params);

  // Fetch the index name
  const metaRows = await queryRows<DbIndexMetaRow>(
    `SELECT code, MAX(name) AS name FROM stats.index_identity WHERE code = $1 GROUP BY code`,
    [code],
  );
  const name = metaRows.length > 0 ? (metaRows[0].name ?? "") : "";

  return {
    code,
    name,
    dates: rows.map((r) => formatDate(r.date)),
    rows: rows.map(transformRow),
  };
}

// ----------------------------------------------------------------------------
//  Meta query — fetch all indices with precomputed L1/L2 classification from
//  stats.sec_classification (type='index'), ordered by n_days DESC (most data first).
//
//  Filter: n_days >= 40 — indices with fewer than 40 trading days of history
//  are suppressed from the data-viz list (matches the "Insufficient data"
//  alert threshold in IndexPanel).
//
//  sector_id/industry_id carry EITHER industry OR strategy classification,
//  depending on is_industry_not_strategy:
//    TRUE  → industry (LEFT column: sector/industry tree)
//    FALSE → strategy (RIGHT column: strategy/theme tree)
//  Labels (sector_label, industry_label, industry_slug) are DENORMALIZED
//  onto sec_classification by build_classification.py.
// ----------------------------------------------------------------------------
const INDEX_MIN_DAYS = 40;

async function getIndexMetaRows(): Promise<DbIndexMetaRow[]> {
  // Cached (10-min TTL, shared with other services) — the classification
  // only changes on the nightly build_classification.py run.
  const rows = await listClassificationMetaRows("index");
  return rows.filter((r) => r.n_days >= INDEX_MIN_DAYS || r.is_dummy === true);
}

// ----------------------------------------------------------------------------
//  Index themes — build the two-level L1 sector → L2 industry → indices tree
//  from the precomputed classification columns in stats.sec_classification.
//
//  Only includes industry-PRIMARY indices (is_industry_not_strategy = TRUE) —
//  these are indices whose industry classification matched (e.g. 中证银行 →
//  FIN/BANKS).  Strategy-primary indices (沪深300 → BROAD/BROAD_CSI) appear
//  in the parallel strategy tree from listStrategyThemes().
// ----------------------------------------------------------------------------
export async function listIndexThemes(exchange?: string | null): Promise<SectorNode[]> {
  const rows = await getIndexMetaRows();
  const exFilter = (exchange ?? "").trim() || null;

  const sectorMap = new Map<string, {
    sector_label: string;
    industries: Map<string, IndustryNode>;
  }>();

  for (const r of rows) {
    // LEFT column: only industry-primary securities.
    if (!r.is_industry_not_strategy) continue;
    // Apply exchange filter so the nav tree respects the selected exchange
    // (e.g. HK indices like 港股通50/恒生 are excluded when "All (primary)"
    // is selected).
    if (exFilter && !matchesExchange(r.exchange, exFilter)) continue;
    const item = { code: r.code, name: r.name ?? "", is_dummy: r.is_dummy };
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
//  Strategy themes — build the parallel L1 sector → L2 industry → indices tree
//  restricted to strategy-PRIMARY indices (is_industry_not_strategy = FALSE).
//  For these, sector_id/industry_id carry the STRATEGY classification
//  (e.g. 沪深300 → BROAD/BROAD_CSI, 中证红利 → DIV/DIV_GENERAL).
//  Industry-primary indices appear in listIndexThemes().
//
//  There is no separate strategy_id/theme_id column — strategy IS a sector
//  and a theme IS an industry in the unified column model. The returned
//  tree uses the SAME field names (sector_id/industry_id) as the industry
//  tree; the difference is only the row filter (is_industry_not_strategy).
// ----------------------------------------------------------------------------
export async function listStrategyThemes(exchange?: string | null): Promise<StrategyNode[]> {
  const rows = await getIndexMetaRows();
  const exFilter = (exchange ?? "").trim() || null;

  const sectorMap = new Map<string, {
    sector_label: string;
    industries: Map<string, ThemeNode>;
  }>();

  for (const r of rows) {
    // RIGHT column: only strategy-primary securities.
    if (r.is_industry_not_strategy) continue;
    if (exFilter && !matchesExchange(r.exchange, exFilter)) continue;
    const item = { code: r.code, name: r.name ?? "", is_dummy: r.is_dummy };
    // sector_id/industry_id carry strategy/theme when is_ind=FALSE
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

  const strategies: StrategyNode[] = [];
  for (const [sector_id, sector] of sectorMap) {
    const industries = Array.from(sector.industries.values()).sort((a, b) => {
      if (a.industry_id === "OTHER") return 1;
      if (b.industry_id === "OTHER") return -1;
      return b.count - a.count;
    });
    strategies.push({
      sector_id,
      sector_label: sector.sector_label,
      count: industries.reduce((sum, t) => sum + t.count, 0),
      industries,
    });
  }
  strategies.sort((a, b) => {
    if (a.sector_id === "OTHER") return 1;
    if (b.sector_id === "OTHER") return -1;
    return b.count - a.count;
  });
  return strategies;
}

// ----------------------------------------------------------------------------
//  Combined index data with sector/industry OR strategy/theme filter + pagination
// ----------------------------------------------------------------------------
export interface IndexCombinedQuery {
  sector?: string;
  industry?: string;
  /** Strategy filter (RIGHT column). When set, filters by sector_id on rows
   *  where is_industry_not_strategy=FALSE (strategy-primary).
   *  Mutually exclusive with sector/industry — if both are set, sector wins. */
  strategy?: string;
  /** Theme filter (RIGHT column). When set, filters by industry_id or
   *  industry_slug on strategy-primary rows. */
  theme?: string;
  /** Exact index code (e.g. "000300", "H30007"). When set, all filters and
   *  pagination are bypassed — only the matching index is returned. */
  code?: string;
  /** Exchange filter: 'SS' (SSE+STAR), 'SZ' (SZSE+GEM), 'BJ' (BSE). */
  exchange?: string;
  start_date?: string;
  end_date?: string;
  page?: number;
  page_size?: number;
}

export async function getIndicesCombined(
  q: IndexCombinedQuery,
): Promise<IndexCombinedResponse> {
  const sectorFilter = (q.sector ?? "").trim();
  const industryFilter = (q.industry ?? "").trim();
  const strategyFilter = (q.strategy ?? "").trim();
  const themeFilter = (q.theme ?? "").trim();
  const exchangeFilter = (q.exchange ?? "").trim() || null;
  // When a code filter is provided, all filters and pagination are bypassed.
  const codeFilter = (q.code ?? "").trim().toUpperCase();

  // 1. Fetch all indices with classification, ordered by n_days DESC (cached).
  const metaRows = await getIndexMetaRows();

  // 2. Filter by sector+industry OR strategy+theme + exchange (or by exact code).
  //    When sectorFilter is set, industry filtering applies (LEFT column).
  //    When strategyFilter is set (and no sectorFilter), theme filtering applies
  //    (RIGHT column).
  const useStrategyFilter = !sectorFilter && !!strategyFilter;
  const meta = new Map<string, {
    name: string;
    sector_id: string;
    sector_label: string;
    industry_id: string;
    industry_label: string;
    is_industry_not_strategy: boolean;
    is_dummy: boolean;
  }>();
  const wantedCodes: string[] = [];
  for (const r of metaRows) {
    meta.set(r.code, {
      name: r.name ?? "",
      sector_id: r.sector_id,
      sector_label: r.sector_label,
      industry_id: r.industry_id,
      industry_label: r.industry_label,
      is_industry_not_strategy: r.is_industry_not_strategy,
      is_dummy: r.is_dummy,
    });
    if (codeFilter) {
      // Exact code search — ignore all filters.
      if (r.code.toUpperCase() === codeFilter) wantedCodes.push(r.code);
      continue;
    }
    const exchangeOk = matchesExchange(r.exchange, exchangeFilter);
    if (useStrategyFilter) {
      // RIGHT column filter: strategy + theme.
      // Only include strategy-PRIMARY indices (is_industry_not_strategy=FALSE)
      // to maintain mutual exclusivity with the LEFT column.
      // sector_id/industry_id carry strategy/theme when is_ind=FALSE.
      if (r.is_industry_not_strategy) continue;
      const stratOk = r.sector_id === strategyFilter;
      const themeOk = !themeFilter || r.industry_slug === themeFilter || r.industry_id === themeFilter;
      if (stratOk && themeOk && exchangeOk) wantedCodes.push(r.code);
    } else {
      // LEFT column filter: sector + industry (default).
      // Only include industry-PRIMARY indices (is_industry_not_strategy=TRUE)
      // to maintain mutual exclusivity with the RIGHT column.
      if (!r.is_industry_not_strategy) continue;
      const sectorOk = !sectorFilter || r.sector_id === sectorFilter;
      const industryOk = !industryFilter || r.industry_slug === industryFilter || r.industry_id === industryFilter;
      if (sectorOk && industryOk && exchangeOk) wantedCodes.push(r.code);
    }
  }

  const totalIndices = wantedCodes.length;
  const pageSize = q.page_size && q.page_size > 0 ? q.page_size : 2;
  const totalPages = Math.max(1, Math.ceil(totalIndices / pageSize));
  const page = q.page && q.page > 0 ? Math.min(q.page, totalPages) : 1;
  const pageCodes = wantedCodes.slice((page - 1) * pageSize, page * pageSize);

  if (pageCodes.length === 0) {
    return {
      sector_id: sectorFilter,
      industry_id: industryFilter,
      dates: [],
      indices: [],
      total_indices: 0,
      total_pages: 1,
      page: 1,
      page_size: pageSize,
    };
  }

  // 3. Fetch row data for the wanted indices (with optional date filtering)
  const params: unknown[] = [];
  let paramIdx = 1;
  params.push(pageCodes);
  const whereParts: string[] = [`code = ANY($${paramIdx++}::text[])`];
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
    SELECT code, date, open, high, low, close, trading_shares, trading_amount, change_pct,
           pe, cons_number, ma5, ma20, ma60, ma120, ma255, has_intraday_5mins
      FROM stats.v_index_baseline
     WHERE ${whereParts.join(" AND ")}
     ORDER BY code, date ASC
  `;
  const dbRows = await queryRows<DbIndexRow>(sql, params);

  // Group rows by code
  const byCode = new Map<string, IndexBaselineRow[]>();
  for (const r of dbRows) {
    if (!byCode.has(r.code)) byCode.set(r.code, []);
    byCode.get(r.code)!.push(transformRow(r));
  }

  // 4. Build bundles — sector_id/industry_id carry the PRIMARY classification
  //    (industry when is_ind=TRUE, strategy when is_ind=FALSE).
  //    Dummy indices (is_dummy=true) are included even with 0 rows so they
  //    appear in the selector; IndexPanel shows "No data" for them.
  const indices: IndexBundle[] = [];
  for (const code of pageCodes) {
    const m = meta.get(code);
    if (!m) continue;
    const rows = byCode.get(code) ?? [];
    if (rows.length === 0 && !m.is_dummy) continue;
    indices.push({
      code,
      name: m.name,
      sector_id: m.sector_id,
      sector_label: m.sector_label,
      industry_id: m.industry_id,
      industry_label: m.industry_label,
      is_industry_not_strategy: m.is_industry_not_strategy,
      is_dummy: m.is_dummy,
      rows,
    });
  }

  // Union of all dates across selected indices (sorted)
  const dateSet = new Set<string>();
  for (const idx of indices) for (const r of idx.rows) dateSet.add(r.date);
  const dates = Array.from(dateSet).sort();

  return {
    sector_id: sectorFilter,
    industry_id: industryFilter,
    dates,
    indices,
    total_indices: totalIndices,
    total_pages: totalPages,
    page,
    page_size: pageSize,
  };
}

/**
 * Fetch 5-minute intraday bars for a single (code, date) from
 * stats.index_intraday_5min. Returns bars ordered by time ascending.
 */
export async function getIndexIntraday5min(
  code: string,
  date: string,
): Promise<IndexIntraday5minResponse> {
  const d = toDateParam(date);
  const bars = await queryRows<DbIntradayRow>(
    `SELECT to_char(time, 'HH24:MI:SS') AS time,
            open, high, low, close, change, change_pct
       FROM stats.index_intraday_5min
      WHERE code = $1 AND date = $2::date
      ORDER BY time ASC`,
    [code, d],
  );
  // Resolve the index name for display (falls back to "" if unknown).
  const metaRows = await queryRows<DbIndexMetaRow>(
    `SELECT code, MAX(name) AS name FROM stats.index_identity WHERE code = $1 GROUP BY code`,
    [code],
  );
  const name = metaRows.length > 0 ? (metaRows[0].name ?? "") : "";

  const out: IndexIntraday5minResponse = {
    code,
    date: d ?? date,
    name,
    bars: bars.map<IndexIntraday5minRow>((r) => ({
      time: r.time,
      open: toNum(r.open),
      high: toNum(r.high),
      low: toNum(r.low),
      close: toNum(r.close),
      change: toNum(r.change),
      change_pct: toNum(r.change_pct),
    })),
  };
  return out;
}
