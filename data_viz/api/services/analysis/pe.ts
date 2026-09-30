/**
 * PE & Dividend Yield (valuation) analysis service.
 *
 * The UNIVERSE-level functions (listValuationCodes / listValuationThemes /
 * listValuationStrategyThemes) serve the SHARED /api/analysis/valuation/*
 * routes used by BOTH the PE and Dividends pages' SecCodesPageShell — the
 * universe is the UNION of analysis.pe and analysis.dividends, so the
 * endpoints are named for the universe, not for one page. The PE-specific
 * chart / streaks functions keep the pe/ prefix (/api/analysis/pe/chart,
 * /pe/streaks — the Dividends page has no streaks card).
 *
 * Reads from:
 *   analysis.pe               — daily raw pe
 *   analysis.dividends        — daily trailing-12m dividend_yield
 *   (2026-09 per-metric splits of the former combined
 *   analysis.pe_and_dividends table; the former annual pe_stats /
 *   dividends_stats snapshot tables were removed 2026-09-28 with their
 *   only consumer, the UI's Dividend Stats card)
 *
 * Close price is NOT stored in the analysis tables (it lives in stats:
 * index_basic_stats.close, etf_basic_stats.close /
 * etf_adjustment.adj_close, stock_basic_stats.close). The chart endpoint
 * JOINs stats live at request time so the UI always shows the freshest
 * close alongside the stored pe + dividend_yield.
 *
 * Mirrors the mov-ave-spreads service shape (codes + chart + themes +
 * strategy-themes) so the page can reuse SecClassificationNav verbatim.
 */
import { queryRows, formatDate, toNum } from "../../lib/db.js";
import type { QueryResultRow } from "pg";
import { stripExchangeSuffix, matchesExchange, codeVariants } from "../../lib/classify-etf.js";
import { cachedRows } from "../classification-cache.js";
import { stripped } from "./_shared.js";
import { buildStrategyThemesFromRows, matchesClassification } from "../_shared.js";
import type {
  PeSecType,
  ValuationCodeRow,
  ValuationCodesResponse,
  PeChartResponse,
  PeChartRow,
  PeStreak,
  PeStreaksResponse,
  SectorNode,
  IndustryNode,
  StrategyNode,
} from "../../../shared/types.js";

// ----------------------------------------------------------------------------
//  Per-sec_type source-table config
//  Mirrors mov-ave-spreads SEC_SOURCES but only needs close (raw pe and
//  dividend_yield come from analysis.pe / analysis.dividends).
// ----------------------------------------------------------------------------
interface SecSource {
  /** Schema-qualified identity table for the asset name lookup. */
  identityTable: string;
  /** FROM clause for the chart query — the code's close source (the
   *  analysis.pe / analysis.dividends LEFT JOINs are appended). */
  chartFromClause: string;
  /** SQL expression for the per-row close column. */
  closeExpr: string;
}

const SEC_SOURCES: Record<PeSecType, SecSource> = {
  etf: {
    identityTable: "stats.etf_identity",
    // a.code = ANY($1) pushes the code predicate into the adjustment join
    // (see buildChartSql's note — unpinned LEFT JOIN sides full-scan).
    chartFromClause:
      "FROM stats.etf_basic_stats b\n" +
      "  LEFT JOIN stats.etf_adjustment a ON a.date = b.date AND a.code = b.code AND a.code = ANY($1::text[])",
    closeExpr: "COALESCE(a.adj_close, b.close)",
  },
  index: {
    identityTable: "stats.index_identity",
    chartFromClause:
      "FROM stats.index_basic_stats b",
    closeExpr: "b.close",
  },
  stock: {
    identityTable: "stats.stock_identity",
    chartFromClause:
      "FROM stats.stock_basic_stats b",
    closeExpr: "b.close",
  },
};

const VALID_SEC_TYPES: ReadonlySet<PeSecType> = new Set(["etf", "index", "stock"]);

function normalizeSecType(raw: string | undefined | null): PeSecType {
  const v = (raw ?? "").trim().toLowerCase();
  if (!VALID_SEC_TYPES.has(v as PeSecType)) {
    throw new Error(`Invalid sec_type: ${raw!}. Expected 'etf', 'index', or 'stock'.`);
  }
  return v as PeSecType;
}

// ----------------------------------------------------------------------------
//  DB row types
// ----------------------------------------------------------------------------
interface DbCodeRow extends QueryResultRow {
  code: string;
  name: string;
  first_date: Date | string;
  last_date: Date | string;
  n_dates: number;
  latest_pe: number | null;
  latest_dividend_yield: number | null;
}

interface DbChartRow extends QueryResultRow {
  date: Date | string;
  close: number | null;
  pe: number | null;
  dividend_yield: number | null;
}

interface DbMetaRow extends QueryResultRow {
  code: string;
  name: string;
  sector_id: string;
  sector_label: string;
  industry_id: string;
  industry_label: string;
  industry_slug: string;
  is_industry_not_strategy: boolean;
  exchange: string;
}

// ----------------------------------------------------------------------------
//  listValuationCodes — one row per code with first/last date, n_dates,
//  and the latest snapshot's pe + dividend_yield (for sparkline / sort).
//  Reads the PRECOMPUTED per-code summary analysis.pe_codes (maintained by
//  analyze.pe_and_dividends' refresh_pe_codes_summary after every detail
//  write pass). The former live aggregate — UNION + sort + GROUP BY over
//  analysis.pe ∪ analysis.dividends (1.3M rows index / 7.1M stock) plus
//  DISTINCT-ON latest-row and identity-name lookups — took 4.7s index /
//  35s stock per request and gated the PE / Dividends pages' first open;
//  this is the same result from a small indexed table (~10ms), the
//  mov_ave_spreads_codes pattern.
// ----------------------------------------------------------------------------
function buildCodesSql(): string {
  return `
    SELECT
      c.code,
      c.name,
      c.first_date,
      c.last_date,
      c.n_dates,
      c.latest_pe,
      c.latest_dividend_yield
    FROM analysis.pe_codes c
    WHERE c.sec_type = $1
    ORDER BY c.code
  `;
}

const META_TYPE: Record<PeSecType, string> = {
  etf: "etf",
  index: "index",
  stock: "stock",
};

/** Meta rows (the analysis.pe ∪ analysis.dividends code universe joined with
 *  classification), cached in-process for the shared 10-min TTL. Sourced from
 *  the precomputed analysis.pe_codes summary — the former live DISTINCT over
 *  both tables' partitions cost ~0.65s on the stock universe even with the
 *  (sec_type, code) index-only scans, and every nav fetch of the PE page
 *  paid it on each cold TTL window. Exchange + classification filters are
 *  applied client-side, so the rows are exchange-independent and safely
 *  cacheable per sec_type. */
async function listPeMetaRows(secType: PeSecType): Promise<DbMetaRow[]> {
  return cachedRows(`pe_meta:${secType}`, () =>
    queryRows<DbMetaRow>(META_SQL, [secType, META_TYPE[secType]]));
}

/** Meta SQL shared by listValuationThemes() and listValuationStrategyThemes().
 *  Returns one row per classification row of each code present in
 *  analysis.pe / analysis.dividends (filtered by sec_type) with its
 *  precomputed L1/L2 classification from stats.sec_classification (a
 *  multi-parent stock yields one row per parent — the tree builders
 *  consolidate). */
const META_SQL = `
  SELECT
    c.code,
    COALESCE(c.name, '')             AS name,
    COALESCE(m.sector_id,       'OTHER')  AS sector_id,
    COALESCE(m.sector_label,    '其他')   AS sector_label,
    COALESCE(m.industry_id,     'OTHER')  AS industry_id,
    COALESCE(m.industry_label,  '未分类') AS industry_label,
    COALESCE(m.industry_slug,   'other')  AS industry_slug,
    COALESCE(m.is_industry_not_strategy, TRUE) AS is_industry_not_strategy,
    COALESCE(m.exchange, '')               AS exchange
  FROM analysis.pe_codes c
  LEFT JOIN stats.sec_classification m ON m.code = c.code AND m.type = $2::text
  WHERE c.sec_type = $1::text
    AND COALESCE(m.is_active, TRUE) = TRUE
`;

export async function listValuationCodes(
  rawSecType: string | undefined | null,
  sector?: string | null,
  industry?: string | null,
  strategy?: string | null,
  theme?: string | null,
  rawExchange?: string | null,
): Promise<ValuationCodesResponse> {
  const secType = normalizeSecType(rawSecType);
  const sectorFilter = (sector ?? "").trim();
  const industryFilter = (industry ?? "").trim();
  const strategyFilter = (strategy ?? "").trim();
  const themeFilter = (theme ?? "").trim();
  const hasClassFilter = !!(sectorFilter || industryFilter || strategyFilter || themeFilter);
  const exFilter = (rawExchange ?? "").trim() || null;
  const needMeta = hasClassFilter || !!exFilter;

  const rows = await queryRows<DbCodeRow>(buildCodesSql(), [secType]);

  let classMap: Map<string, DbMetaRow> | null = null;
  if (needMeta) {
    const metaRows = await listPeMetaRows(secType);
    classMap = new Map<string, DbMetaRow>();
    for (const m of metaRows) {
      const code = stripExchangeSuffix(m.code);
      if (!code) continue;
      classMap.set(code, m);
    }
  }

  const codes: ValuationCodeRow[] = [];
  for (const r of rows) {
    const code = stripped(r.code);
    if (classMap) {
      const meta = classMap.get(code);
      if (hasClassFilter && (!meta || !matchesClassification(meta, sectorFilter, industryFilter, strategyFilter, themeFilter))) {
        continue;
      }
      if (exFilter && (!meta || !matchesExchange(meta.exchange, exFilter))) {
        continue;
      }
    }
    codes.push({
      code,
      name: r.name ?? "",
      first_date: formatDate(r.first_date),
      last_date: formatDate(r.last_date),
      n_dates: Number(r.n_dates) || 0,
      latest_pe: toNum(r.latest_pe),
      latest_dividend_yield: toNum(r.latest_dividend_yield),
    });
  }
  return { codes };
}

// ----------------------------------------------------------------------------
//  getPeChart — daily time series for one asset.
//
//  Drives from the asset-appropriate close source (etf_basic_stats +
//  etf_adjustment for ETFs; index_basic_stats for indices;
//  stock_basic_stats for stocks) and LEFT JOINs the two split metric
//  tables — analysis.pe and analysis.dividends — so the response keeps
//  one row per close day with nulls where a metric is undefined.
// ----------------------------------------------------------------------------
function buildChartSql(secType: PeSecType): string {
  const src = SEC_SOURCES[secType];
  // The code predicate is repeated inside BOTH LEFT JOIN ON clauses on
  // purpose: without it the planner merge-joins by sorting the ENTIRE
  // analysis.pe / analysis.dividends tables (the join condition alone
  // defeats the ANY-array pushdown) and degenerates into a near-cartesian
  // product — measured 2.67M rows / 30s for ONE index code (2026-09-29).
  // Semantically identical (b.code ∈ ANY($1) already implies p.code ∈
  // ANY($1)); each side becomes an index probe (~60ms total).
  return `
    SELECT
      b.date,
      ${src.closeExpr} AS close,
      p.pe,
      dy.dividend_yield
    ${src.chartFromClause}
    LEFT JOIN analysis.pe p
      ON p.code = b.code AND p.date = b.date AND p.code = ANY($1::text[])
    LEFT JOIN analysis.dividends dy
      ON dy.code = b.code AND dy.date = b.date AND dy.code = ANY($1::text[])
    WHERE b.close IS NOT NULL
      AND b.code = ANY($1::text[])
    ORDER BY b.date ASC
  `;
}

function buildNameSql(secType: PeSecType): string {
  const src = SEC_SOURCES[secType];
  return `
    SELECT DISTINCT ON (code) code, name
    FROM ${src.identityTable}
    WHERE code = ANY($1::text[])
    ORDER BY code, date DESC
  `;
}

export async function getPeChart(
  rawCode: string,
  rawSecType: string | undefined | null,
): Promise<PeChartResponse> {
  const secType = normalizeSecType(rawSecType);
  const target = stripped(rawCode);

  const [chartRows, nameRows] = await Promise.all([
    queryRows<DbChartRow>(buildChartSql(secType), [codeVariants(target)]),
    queryRows<{ name: string | null }>(buildNameSql(secType), [codeVariants(target)]),
  ]);

  const name = nameRows[0]?.name ?? "";

  const rows: PeChartRow[] = chartRows.map((r) => ({
    date: formatDate(r.date),
    close: toNum(r.close),
    pe: toNum(r.pe),
    dividend_yield: toNum(r.dividend_yield),
  }));

  return { code: target, name, rows };
}

// ----------------------------------------------------------------------------
//  listPeStreaks — band-BREAK excursion streaks of one code's pe series,
//  straight from analysis.pe_pct_streaks joined with its band table
//  (analysis.pe_pct) — the mov-ave-spreads buildStreaksSql pattern.
//  (2026-09-28: PE-only — the dividend_yield streak half,
//  analysis.dividends_pct_streaks, was dropped with its unused consumer
//  path.)
//
//  The streak SIDE (high leg vs low leg) is not stored — it is derived here
//  by joining the band row of the streak's END month
//  (date_trunc('month', s.end_date)) and comparing the streak's end_value
//  against that band: each streak day is tested against its OWN month's
//  band (so the end-month band row is guaranteed to exist) and a streak
//  never switches sides, so the end day's own-month band decides exactly.
//  Shipped flat for ALL (period, pct_type) combos — the client filters by
//  its nested period→pct selection.
// ----------------------------------------------------------------------------
function buildStreaksSql(): string {
  return `
    SELECT
      'pe' AS metric,
      s.period,
      s.pct_type,
      s.start_date,
      s.end_date,
      s.start_value,
      s.end_value,
      s.max_value,
      s.min_value,
      b.high_val AS band_high,
      b.low_val  AS band_low,
      s.day_count,
      s.std_dev,
      CASE
        WHEN s.end_value > b.high_val THEN 'high'
        WHEN s.end_value < b.low_val  THEN 'low'
      END AS side
    FROM analysis.pe_pct_streaks s
    JOIN analysis.pe_pct b
      ON b.sec_type = s.sec_type
      AND b.code = s.code
      AND b.date_year_month = date_trunc('month', s.end_date)::date
      AND b.period = s.period
      AND b.pct_type = s.pct_type
    WHERE s.sec_type = $1
      AND s.code = ANY($2::text[])
    ORDER BY period, pct_type, start_date
  `;
}

/** One streak row from analysis.pe_pct_streaks joined with its end-month
 *  band (see buildStreaksSql). side is NULL only if the end value fell
 *  exactly on a band boundary — never expected. */
interface DbStreakRow extends QueryResultRow {
  metric: string;
  period: number;
  pct_type: number;
  start_date: Date | string;
  end_date: Date | string;
  start_value: number | string | null;
  end_value: number | string | null;
  max_value: number | string | null;
  min_value: number | string | null;
  band_high: number | string | null;
  band_low: number | string | null;
  day_count: number;
  std_dev: number | string | null;
  side: string | null;
}

/** Map the streak rows into the response's FLAT per-streak array
 *  (ascending by period, pctType, startDate; rows with an unusable side
 *  are dropped). */
function toStreaks(rows: DbStreakRow[]): PeStreak[] {
  const out: PeStreak[] = [];
  for (const r of rows) {
    if (r.metric !== "pe") continue;
    if (r.side !== "high" && r.side !== "low") continue;
    out.push({
      metric: "pe",
      period: r.period,
      pctType: r.pct_type,
      startDate: formatDate(r.start_date),
      endDate: formatDate(r.end_date),
      side: r.side,
      startValue: toNum(r.start_value) ?? 0,
      endValue: toNum(r.end_value) ?? 0,
      maxValue: toNum(r.max_value) ?? 0,
      minValue: toNum(r.min_value) ?? 0,
      bandHigh: toNum(r.band_high) ?? 0,
      bandLow: toNum(r.band_low) ?? 0,
      dayCount: r.day_count,
      stdDev: toNum(r.std_dev) ?? 0,
    });
  }
  return out;
}

export async function listPeStreaks(
  rawCode: string,
  rawSecType: string | undefined | null,
): Promise<PeStreaksResponse> {
  const secType = normalizeSecType(rawSecType);
  const target = stripped(rawCode);

  const [streakRows, nameRows] = await Promise.all([
    queryRows<DbStreakRow>(buildStreaksSql(), [secType, codeVariants(target)]),
    queryRows<{ name: string | null }>(buildNameSql(secType), [codeVariants(target)]),
  ]);

  const name = nameRows[0]?.name ?? "";

  return { code: target, name, streaks: toStreaks(streakRows) };
}

// ----------------------------------------------------------------------------
//  listValuationThemes — L1 sector → L2 industry → items tree, restricted
//  to codes that have rows in analysis.pe / analysis.dividends for the requested
//  sec_type. Mirrors listMovAveSpreadThemes().
// ----------------------------------------------------------------------------
export async function listValuationThemes(
  rawSecType: string | undefined | null,
  rawExchange?: string | null,
): Promise<SectorNode[]> {
  const secType = normalizeSecType(rawSecType);
  const exFilter = (rawExchange ?? "").trim() || null;
  const rows = await listPeMetaRows(secType);

  const sectorMap = new Map<string, {
    sector_label: string;
    industries: Map<string, IndustryNode>;
  }>();

  for (const r of rows) {
    if (!r.is_industry_not_strategy) continue;
    if (exFilter && !matchesExchange(r.exchange, exFilter)) continue;
    const code = stripExchangeSuffix(r.code);
    if (!code) continue;
    const item = { code, name: r.name ?? "" };

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
//  listValuationStrategyThemes — parallel L1 strategy → L2 theme → items
//  tree from the strategy-primary rows (is_industry_not_strategy=FALSE).
// ----------------------------------------------------------------------------
export async function listValuationStrategyThemes(
  rawSecType: string | undefined | null,
  rawExchange?: string | null,
): Promise<StrategyNode[]> {
  const secType = normalizeSecType(rawSecType);
  const exFilter = (rawExchange ?? "").trim() || null;
  const rows = await listPeMetaRows(secType);

  const filteredRows = exFilter
    ? rows.filter((r) => matchesExchange(r.exchange, exFilter))
    : rows;

  const mappedRows = filteredRows.map((r) => ({
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
