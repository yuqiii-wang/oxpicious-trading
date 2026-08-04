/**
 * Live Data service — queries stats.{index,stock}_intraday_5min tables joined
 * with stats.sec_classification to provide per-(date, code) intraday bars
 * filtered by L1 sector + L2 industry + exchange.
 *
 * Two security types are supported via the `type` query param:
 *   • 'index' → stats.index_intraday_5min  (no volume column)
 *   • 'stock' → stats.stock_intraday_5min  (has volume column, in shares)
 * ETF is currently unsupported (no stats.etf_intraday_5min table) — the
 * frontend renders an empty placeholder until that table is added.
 *
 * The available-dates endpoint returns the union of dates present in the
 * intraday table for the requested type (descending — most recent first).
 */
import { queryRows, toDateParam, formatDate, toNum } from "../lib/db.js";
import type { QueryResultRow } from "pg";
import { matchesExchange } from "../lib/classify-etf.js";
import type {
  LiveDataIntradayBar,
  LiveDataBundle,
  LiveDataCombinedResponse,
  LiveDataDatesResponse,
  SectorNode,
  IndustryNode,
} from "../../shared/types.js";

// ----------------------------------------------------------------------------
//  Row types
// ----------------------------------------------------------------------------
interface DbLiveMetaRow extends QueryResultRow {
  code: string;
  name: string;
  sector_id: string;
  sector_label: string;
  industry_id: string;
  industry_label: string;
  industry_slug: string;
  exchange: string;
  /** Number of distinct dates with intraday bars (for sorting / display). */
  n_dates: number;
  first_date: string;
  last_date: string;
}

interface DbIntradayBarRow extends QueryResultRow {
  time: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  volume: number | null;
  change: number | null;
  change_pct: number | null;
}

// ----------------------------------------------------------------------------
//  Type discriminator + table routing
// ----------------------------------------------------------------------------
export type LiveDataSecType = "index" | "stock";

interface LiveDataTableConfig {
  /** Schema-qualified intraday table name. */
  table: string;
  /** Whether the table has a `volume` column (only stocks do). */
  hasVolume: boolean;
}

const TABLES: Record<LiveDataSecType, LiveDataTableConfig> = {
  index: { table: "stats.index_intraday_5min", hasVolume: false },
  stock: { table: "stats.stock_intraday_5min", hasVolume: true },
};

// ----------------------------------------------------------------------------
//  Meta SQL — fetch all codes of the requested type that have at least one
//  intraday bar, joined to stats.sec_classification for the L1/L2 taxonomy.
//  Mirrors STOCK_META_SQL in stock-baseline.service.ts but with an EXISTS
//  filter on the intraday table so only codes with intraday data are returned.
//
//  Deduplication: a stock may have MULTIPLE sec_classification rows (one per
//  qualifying parent index with weight > 2%, see builds/classification/
//  __main__.py — PK is (code, parent_index_code)). A naive JOIN would emit
//  one row per parent index, surfacing duplicates like "000063.SZ · 中兴通讯"
//  12 times. The LATERAL subquery picks exactly ONE row per code: the row
//  with parent_index_is_primary = TRUE, falling back to the highest
//  parent_index_weight. Indices (parent_index_code = '') and ETFs (one-to-one
//  ETF → tracking index) already have a single row per code, so the LATERAL
//  LIMIT 1 is a no-op for them.
// ----------------------------------------------------------------------------
function buildMetaSql(secType: LiveDataSecType): string {
  const cfg = TABLES[secType];
  return `
    SELECT sc.code,
           COALESCE(sc.name, '') AS name,
           COALESCE(sc.sector_id,       'OTHER')  AS sector_id,
           COALESCE(sc.sector_label,    '其他')   AS sector_label,
           COALESCE(sc.industry_id,     'OTHER')  AS industry_id,
           COALESCE(sc.industry_label,  '未分类')  AS industry_label,
           COALESCE(sc.industry_slug,   'other')  AS industry_slug,
           COALESCE(sc.exchange, '')               AS exchange,
           x.n_dates,
           x.first_date,
           x.last_date
      FROM (
        SELECT code,
               COUNT(DISTINCT date) AS n_dates,
               MIN(date)::text       AS first_date,
               MAX(date)::text       AS last_date
          FROM ${cfg.table}
         GROUP BY code
      ) x
      JOIN LATERAL (
        SELECT sc.code, sc.name, sc.sector_id, sc.sector_label,
               sc.industry_id, sc.industry_label, sc.industry_slug, sc.exchange
          FROM stats.sec_classification sc
         WHERE sc.code = x.code AND sc.type = $1
         ORDER BY sc.parent_index_is_primary DESC NULLS LAST,
                  sc.parent_index_weight DESC NULLS LAST,
                  sc.parent_index_code
         LIMIT 1
      ) sc ON true
     ORDER BY x.n_dates DESC, sc.code
  `;
}

// ----------------------------------------------------------------------------
//  Available dates — returns the union of dates with at least one intraday
//  bar for the requested security type, descending (most recent first).
// ----------------------------------------------------------------------------
export async function listLiveDataDates(
  secType: LiveDataSecType,
): Promise<LiveDataDatesResponse> {
  const cfg = TABLES[secType];
  const rows = await queryRows<{ date: string }>(
    `SELECT DISTINCT date::text AS date
       FROM ${cfg.table}
      ORDER BY date DESC`,
  );
  return {
    type: secType,
    dates: rows.map((r) => formatDate(r.date)),
  };
}

// ----------------------------------------------------------------------------
//  Themes tree — build the L1 sector → L2 industry → codes tree restricted
//  to codes with intraday data (same shape as listIndexThemes).
// ----------------------------------------------------------------------------
export async function listLiveDataThemes(
  secType: LiveDataSecType,
): Promise<SectorNode[]> {
  const rows = await queryRows<DbLiveMetaRow>(buildMetaSql(secType), [
    secType,
  ]);

  const sectorMap = new Map<string, {
    sector_label: string;
    industries: Map<string, IndustryNode>;
  }>();

  for (const r of rows) {
    const item = { code: r.code, name: r.name ?? "" };
    if (!sectorMap.has(r.sector_id)) {
      sectorMap.set(r.sector_id, {
        sector_label: r.sector_label,
        industries: new Map(),
      });
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
//  Combined intraday data — paginated list of (code) intraday bars for ONE
//  selected date, filtered by L1 sector + L2 industry + exchange.
//  Mirrors getIndicesCombined but reads the intraday_5min tables instead of
//  the daily baseline view.
// ----------------------------------------------------------------------------
export interface LiveDataCombinedQuery {
  sector?: string;
  industry?: string;
  /** Exact code (e.g. "000300" or "000001.SZ"). Bypasses sector/industry/
   *  exchange/pagination — returns the matching code with bars for the date. */
  code?: string;
  exchange?: string;
  /** Trading day (YYYY-MM-DD). When omitted, uses the latest available date. */
  date?: string;
  page?: number;
  page_size?: number;
}

export async function getLiveDataCombined(
  secType: LiveDataSecType,
  q: LiveDataCombinedQuery,
): Promise<LiveDataCombinedResponse> {
  const cfg = TABLES[secType];
  const sectorFilter = (q.sector ?? "").trim();
  const industryFilter = (q.industry ?? "").trim();
  const exchangeFilter = (q.exchange ?? "").trim() || null;
  const codeFilter = (q.code ?? "").trim().toUpperCase();

  // 1. Fetch all meta rows (codes with intraday data + classification).
  const metaRows = await queryRows<DbLiveMetaRow>(buildMetaSql(secType), [
    secType,
  ]);

  // 2. Resolve the requested date — explicit param wins; otherwise use the
  //    latest available date across ALL codes of this type (so the page
  //    shows data by default even before the user picks a date).
  let dateParam = toDateParam(q.date);
  if (!dateParam) {
    const dateRows = await queryRows<{ date: string }>(
      `SELECT MAX(date)::text AS date FROM ${cfg.table}`,
    );
    dateParam = dateRows.length > 0 ? dateRows[0].date : null;
  }

  // 3. Filter meta by sector/industry/exchange (or by exact code when set).
  const meta = new Map<string, { name: string; sector_id: string; sector_label: string; industry_id: string; industry_label: string }>();
  const wantedCodes: string[] = [];
  for (const r of metaRows) {
    meta.set(r.code, {
      name: r.name ?? "",
      sector_id: r.sector_id,
      sector_label: r.sector_label,
      industry_id: r.industry_id,
      industry_label: r.industry_label,
    });
    if (codeFilter) {
      // Match with or without exchange suffix (e.g. "000001.SZ" or "000001").
      const stripped = r.code.toUpperCase().replace(/\.(SS|SZ|SH|BJ|HK)$/i, "");
      if (r.code.toUpperCase() === codeFilter || stripped === codeFilter) {
        wantedCodes.push(r.code);
      }
      continue;
    }
    const sectorOk = !sectorFilter || r.sector_id === sectorFilter;
    const industryOk =
      !industryFilter ||
      r.industry_slug === industryFilter ||
      r.industry_id === industryFilter;
    const exchangeOk = matchesExchange(r.exchange, exchangeFilter);
    if (sectorOk && industryOk && exchangeOk) wantedCodes.push(r.code);
  }

  const totalCodes = wantedCodes.length;
  const pageSize = q.page_size && q.page_size > 0 ? q.page_size : 2;
  const totalPages = Math.max(1, Math.ceil(totalCodes / pageSize));
  const page = q.page && q.page > 0 ? Math.min(q.page, totalPages) : 1;
  const pageCodes = wantedCodes.slice((page - 1) * pageSize, page * pageSize);

  if (pageCodes.length === 0 || !dateParam) {
    return {
      type: secType,
      date: dateParam ?? "",
      sector_id: sectorFilter,
      industry_id: industryFilter,
      codes: [],
      total_codes: 0,
      total_pages: 1,
      page: 1,
      page_size: pageSize,
    };
  }

  // 4. Fetch intraday bars for the page's codes on the requested date.
  //    The volume column is only present on the stock table — emit NULL
  //    for indices so the bar type stays uniform. `code` is selected so rows
  //    can be grouped by code (the page lists multiple codes per page).
  const volExpr = cfg.hasVolume ? "trading_shares AS volume" : "NULL::numeric AS volume";
  const dbRows = await queryRows<DbIntradayBarRow & { code: string }>(
    `SELECT code, to_char(time, 'HH24:MI:SS') AS time,
            open, high, low, close, ${volExpr}, change, change_pct
       FROM ${cfg.table}
      WHERE code = ANY($1::text[]) AND date = $2::date
      ORDER BY code, time ASC`,
    [pageCodes, dateParam],
  );

  // Group bars by code (preserving pageCodes order).
  const barsByCode = new Map<string, LiveDataIntradayBar[]>();
  for (const r of dbRows) {
    if (!barsByCode.has(r.code)) barsByCode.set(r.code, []);
    barsByCode.get(r.code)!.push({
      time: r.time,
      open: toNum(r.open),
      high: toNum(r.high),
      low: toNum(r.low),
      close: toNum(r.close),
      volume: toNum(r.volume),
      change: toNum(r.change),
      change_pct: toNum(r.change_pct),
    });
  }

  // 5. Build bundles (skip codes with no bars on this date — e.g. the date
  //    predates the code's first intraday sample).
  const codes: LiveDataBundle[] = [];
  for (const code of pageCodes) {
    const bars = barsByCode.get(code) ?? [];
    if (bars.length === 0) continue;
    const m = meta.get(code)!;
    codes.push({
      code,
      name: m.name,
      sector_id: m.sector_id,
      sector_label: m.sector_label,
      industry_id: m.industry_id,
      industry_label: m.industry_label,
      bars,
    });
  }

  return {
    type: secType,
    date: dateParam,
    sector_id: sectorFilter,
    industry_id: industryFilter,
    codes,
    total_codes: totalCodes,
    total_pages: totalPages,
    page,
    page_size: pageSize,
  };
}
