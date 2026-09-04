/**
 * PE & Dividend Yield analysis service.
 *
 * Reads from:
 *   analysis.pe_and_dividends       — daily pe_ma20 + dividend_yield
 *   analysis.pe_and_dividend_stats  — monthly 5y rolling stats snapshot
 *
 * Close price and raw PE ratio are NOT stored in analysis.pe_and_dividends
 * (they live in stats: index_basic_stats.close, index_valuation.pe,
 * etf_basic_stats.close / etf_adjustment.adj_close, stock_basic_stats.close).
 * The chart endpoint JOINs stats live at request time so the UI always shows
 * the freshest close/PE alongside the derived analytics.
 *
 * Mirrors the mov-ave-spreads service shape (codes + chart + themes +
 * strategy-themes) so the page can reuse SecClassificationNav verbatim.
 */
import { queryRows, formatDate, toNum } from "../../lib/db.js";
import type { QueryResultRow } from "pg";
import { stripExchangeSuffix, matchesExchange, codeVariants } from "../../lib/classify-etf.js";
import { stripped } from "./_shared.js";
import { buildStrategyThemesFromRows, matchesClassification } from "../_shared.js";
import type {
  PeAndDividendSecType,
  PeAndDividendCodeRow,
  PeAndDividendCodesResponse,
  PeAndDividendChartResponse,
  PeAndDividendChartRow,
  PeAndDividendStatsResponse,
  PeAndDividendStatsRow,
  SectorNode,
  IndustryNode,
  StrategyNode,
} from "../../../shared/types.js";

// ----------------------------------------------------------------------------
//  Per-sec_type source-table config
//  Mirrors mov-ave-spreads SEC_SOURCES but only needs close + (index-only) PE.
// ----------------------------------------------------------------------------
interface SecSource {
  /** Schema-qualified identity table for the asset name lookup. */
  identityTable: string;
  /** FROM clause for the chart query — recovers close (and PE for index). */
  chartFromClause: string;
  /** SQL expression for the per-row close column. */
  closeExpr: string;
  /** SQL expression for the per-row PE column (NULL for etf/stock). */
  peExpr: string;
}

const SEC_SOURCES: Record<PeAndDividendSecType, SecSource> = {
  etf: {
    identityTable: "stats.etf_identity",
    chartFromClause:
      "FROM analysis.pe_and_dividends d\n" +
      "  JOIN stats.etf_basic_stats   b ON b.date = d.date AND b.code = d.code\n" +
      "  LEFT JOIN stats.etf_adjustment a ON a.date = d.date AND a.code = d.code",
    closeExpr: "COALESCE(a.adj_close, b.close)",
    peExpr: "NULL::numeric",
  },
  index: {
    identityTable: "stats.index_identity",
    chartFromClause:
      "FROM analysis.pe_and_dividends d\n" +
      "  JOIN stats.index_basic_stats b ON b.date = d.date AND b.code = d.code\n" +
      "  LEFT JOIN stats.index_valuation v ON v.date = d.date AND v.code = b.code",
    closeExpr: "b.close",
    peExpr: "v.pe",
  },
  stock: {
    identityTable: "stats.stock_identity",
    chartFromClause:
      "FROM analysis.pe_and_dividends d\n" +
      "  JOIN stats.stock_basic_stats b ON b.date = d.date AND b.code = d.code",
    closeExpr: "b.close",
    peExpr: "NULL::numeric",
  },
};

const VALID_SEC_TYPES: ReadonlySet<PeAndDividendSecType> = new Set(["etf", "index", "stock"]);

function normalizeSecType(raw: string | undefined | null): PeAndDividendSecType {
  const v = (raw ?? "").trim().toLowerCase();
  if (!VALID_SEC_TYPES.has(v as PeAndDividendSecType)) {
    throw new Error(`Invalid sec_type: ${raw!}. Expected 'etf', 'index', or 'stock'.`);
  }
  return v as PeAndDividendSecType;
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
  latest_pe_ma20: number | null;
  latest_dividend_yield: number | null;
}

interface DbChartRow extends QueryResultRow {
  date: Date | string;
  close: number | null;
  pe: number | null;
  pe_ma20: number | null;
  dividend_yield: number | null;
}

interface DbStatsRow extends QueryResultRow {
  date: Date | string;
  is_active: boolean;
  min_pe_5y: number | null;
  max_pe_5y: number | null;
  dividend_var_5y: number | null;
  dividend_stability_5y: number | null;
  last_dividend_per_share: number | null;
  dividend_issued_this_month: boolean;
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
//  listPeAndDividendCodes — one row per code with first/last date, n_dates,
//  and the latest snapshot's pe_ma20 + dividend_yield (for sparkline / sort).
//  Mirrors listMovAveSpreadCodes but draws from analysis.pe_and_dividends.
// ----------------------------------------------------------------------------
function buildCodesSql(secType: PeAndDividendSecType): string {
  return `
    WITH latest_name AS (
      SELECT DISTINCT ON (code) code, name
      FROM ${SEC_SOURCES[secType].identityTable}
      ORDER BY code, date DESC
    ),
    code_dates AS (
      SELECT
        code,
        MIN(date) AS first_date,
        MAX(date) AS last_date,
        COUNT(DISTINCT date) AS n_dates
      FROM analysis.pe_and_dividends
      WHERE sec_type = $1
      GROUP BY code
    ),
    latest_row AS (
      SELECT DISTINCT ON (code) code, pe_ma20, dividend_yield
      FROM analysis.pe_and_dividends
      WHERE sec_type = $1
      ORDER BY code, date DESC
    )
    SELECT
      cd.code,
      COALESCE(n.name, '')        AS name,
      cd.first_date,
      cd.last_date,
      cd.n_dates,
      lr.pe_ma20                  AS latest_pe_ma20,
      lr.dividend_yield           AS latest_dividend_yield
    FROM code_dates cd
    LEFT JOIN latest_name n  ON n.code  = cd.code
    LEFT JOIN latest_row lr  ON lr.code = cd.code
    ORDER BY cd.code
  `;
}

const META_TYPE: Record<PeAndDividendSecType, string> = {
  etf: "etf",
  index: "index",
  stock: "stock",
};

/** Meta SQL shared by listPeAndDividendThemes() and listPeAndDividendStrategyThemes().
 *  Returns one row per code in analysis.pe_and_dividends (filtered by sec_type)
 *  with its precomputed L1/L2 classification from stats.sec_classification. */
const META_SQL = `
  WITH pd_codes AS (
    SELECT DISTINCT code
    FROM analysis.pe_and_dividends
    WHERE sec_type = $1::text
  )
  SELECT
    sc.code,
    COALESCE(m.name, '')             AS name,
    COALESCE(m.sector_id,       'OTHER')  AS sector_id,
    COALESCE(m.sector_label,    '其他')   AS sector_label,
    COALESCE(m.industry_id,     'OTHER')  AS industry_id,
    COALESCE(m.industry_label,  '未分类') AS industry_label,
    COALESCE(m.industry_slug,   'other')  AS industry_slug,
    COALESCE(m.is_industry_not_strategy, TRUE) AS is_industry_not_strategy,
    COALESCE(m.exchange, '')               AS exchange
  FROM pd_codes sc
  LEFT JOIN stats.sec_classification m ON m.code = sc.code AND m.type = $2::text
  WHERE COALESCE(m.is_active, TRUE) = TRUE
`;

export async function listPeAndDividendCodes(
  rawSecType: string | undefined | null,
  sector?: string | null,
  industry?: string | null,
  strategy?: string | null,
  theme?: string | null,
  rawExchange?: string | null,
): Promise<PeAndDividendCodesResponse> {
  const secType = normalizeSecType(rawSecType);
  const sectorFilter = (sector ?? "").trim();
  const industryFilter = (industry ?? "").trim();
  const strategyFilter = (strategy ?? "").trim();
  const themeFilter = (theme ?? "").trim();
  const hasClassFilter = !!(sectorFilter || industryFilter || strategyFilter || themeFilter);
  const exFilter = (rawExchange ?? "").trim() || null;
  const needMeta = hasClassFilter || !!exFilter;

  const rows = await queryRows<DbCodeRow>(buildCodesSql(secType), [secType]);

  let classMap: Map<string, DbMetaRow> | null = null;
  if (needMeta) {
    const metaType = META_TYPE[secType];
    const metaRows = await queryRows<DbMetaRow>(META_SQL, [secType, metaType]);
    classMap = new Map<string, DbMetaRow>();
    for (const m of metaRows) {
      const code = stripExchangeSuffix(m.code);
      if (!code) continue;
      classMap.set(code, m);
    }
  }

  const codes: PeAndDividendCodeRow[] = [];
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
      latest_pe_ma20: toNum(r.latest_pe_ma20),
      latest_dividend_yield: toNum(r.latest_dividend_yield),
    });
  }
  return { codes };
}

// ----------------------------------------------------------------------------
//  getPeAndDividendChart — daily time series for one asset.
//
//  JOINs analysis.pe_and_dividends with the asset-appropriate source tables
//  (etf_basic_stats + etf_adjustment for ETFs; index_basic_stats +
//  index_valuation for indices; stock_basic_stats for stocks) to recover
//  close + pe alongside the derived pe_ma20 + dividend_yield.
// ----------------------------------------------------------------------------
function buildChartSql(secType: PeAndDividendSecType): string {
  const src = SEC_SOURCES[secType];
  return `
    SELECT
      d.date,
      ${src.closeExpr} AS close,
      ${src.peExpr}     AS pe,
      d.pe_ma20,
      d.dividend_yield
    ${src.chartFromClause}
    WHERE d.sec_type = $2
      AND d.code = ANY($1::text[])
    ORDER BY d.date ASC
  `;
}

function buildNameSql(secType: PeAndDividendSecType): string {
  const src = SEC_SOURCES[secType];
  return `
    SELECT DISTINCT ON (code) code, name
    FROM ${src.identityTable}
    WHERE code = ANY($1::text[])
    ORDER BY code, date DESC
  `;
}

export async function getPeAndDividendChart(
  rawCode: string,
  rawSecType: string | undefined | null,
): Promise<PeAndDividendChartResponse> {
  const secType = normalizeSecType(rawSecType);
  const target = stripped(rawCode);

  const [chartRows, nameRows] = await Promise.all([
    queryRows<DbChartRow>(buildChartSql(secType), [codeVariants(target), secType]),
    queryRows<{ name: string | null }>(buildNameSql(secType), [codeVariants(target)]),
  ]);

  const name = nameRows[0]?.name ?? "";

  const rows: PeAndDividendChartRow[] = chartRows.map((r) => ({
    date: formatDate(r.date),
    close: toNum(r.close),
    pe: toNum(r.pe),
    pe_ma20: toNum(r.pe_ma20),
    dividend_yield: toNum(r.dividend_yield),
  }));

  return { code: target, name, rows };
}

// ----------------------------------------------------------------------------
//  listPeAndDividendStats — monthly 5y rolling stats snapshot rows for one
//  code from analysis.pe_and_dividend_stats. Returns ALL monthly snapshots
//  (most recent first) so the UI can render the full history table; the
//  is_active flag marks the latest row for highlighting.
// ----------------------------------------------------------------------------
function buildStatsSql(): string {
  return `
    SELECT
      date,
      is_active,
      min_pe_5y,
      max_pe_5y,
      dividend_var_5y,
      dividend_stability_5y,
      last_dividend_per_share,
      dividend_issued_this_month
    FROM analysis.pe_and_dividend_stats
    WHERE sec_type = $2
      AND code = ANY($1::text[])
    ORDER BY date DESC
  `;
}

export async function listPeAndDividendStats(
  rawCode: string,
  rawSecType: string | undefined | null,
): Promise<PeAndDividendStatsResponse> {
  const secType = normalizeSecType(rawSecType);
  const target = stripped(rawCode);

  const [statsRows, nameRows] = await Promise.all([
    queryRows<DbStatsRow>(buildStatsSql(), [codeVariants(target), secType]),
    queryRows<{ name: string | null }>(buildNameSql(secType), [codeVariants(target)]),
  ]);

  const name = nameRows[0]?.name ?? "";

  const rows: PeAndDividendStatsRow[] = statsRows.map((r) => ({
    date: formatDate(r.date),
    is_active: r.is_active === true,
    min_pe_5y: toNum(r.min_pe_5y),
    max_pe_5y: toNum(r.max_pe_5y),
    dividend_var_5y: toNum(r.dividend_var_5y),
    dividend_stability_5y: toNum(r.dividend_stability_5y),
    last_dividend_per_share: toNum(r.last_dividend_per_share),
    dividend_issued_this_month: r.dividend_issued_this_month === true,
  }));

  return { code: target, name, rows };
}

// ----------------------------------------------------------------------------
//  listPeAndDividendThemes — L1 sector → L2 industry → items tree, restricted
//  to codes that have rows in analysis.pe_and_dividends for the requested
//  sec_type. Mirrors listMovAveSpreadThemes().
// ----------------------------------------------------------------------------
export async function listPeAndDividendThemes(
  rawSecType: string | undefined | null,
  rawExchange?: string | null,
): Promise<SectorNode[]> {
  const secType = normalizeSecType(rawSecType);
  const exFilter = (rawExchange ?? "").trim() || null;
  const metaType = META_TYPE[secType];
  const rows = await queryRows<DbMetaRow>(META_SQL, [secType, metaType]);

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
//  listPeAndDividendStrategyThemes — parallel L1 strategy → L2 theme → items
//  tree from the strategy-primary rows (is_industry_not_strategy=FALSE).
// ----------------------------------------------------------------------------
export async function listPeAndDividendStrategyThemes(
  rawSecType: string | undefined | null,
  rawExchange?: string | null,
): Promise<StrategyNode[]> {
  const secType = normalizeSecType(rawSecType);
  const exFilter = (rawExchange ?? "").trim() || null;
  const metaType = META_TYPE[secType];
  const rows = await queryRows<DbMetaRow>(META_SQL, [secType, metaType]);

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
