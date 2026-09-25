/**
 * PE & Dividend Yield analysis service.
 *
 * Reads from:
 *   analysis.pe                    — daily raw pe
 *   analysis.dividends             — daily trailing-12m dividend_yield
 *   analysis.pe_and_dividend_stats — annual 10y rolling stats snapshot
 *   (2026-09: pe and dividends SPLIT from the former combined
 *   analysis.pe_and_dividends table)
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
  PeAndDividendStreakMetric,
  PeAndDividendStreak,
  PeAndDividendStreaksResponse,
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

const SEC_SOURCES: Record<PeAndDividendSecType, SecSource> = {
  etf: {
    identityTable: "stats.etf_identity",
    chartFromClause:
      "FROM stats.etf_basic_stats b\n" +
      "  LEFT JOIN stats.etf_adjustment a ON a.date = b.date AND a.code = b.code",
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
  latest_pe: number | null;
  latest_dividend_yield: number | null;
}

interface DbChartRow extends QueryResultRow {
  date: Date | string;
  close: number | null;
  pe: number | null;
  dividend_yield: number | null;
}

interface DbStatsRow extends QueryResultRow {
  date: Date | string;
  is_active: boolean;
  min_pe_10y: number | null;
  max_pe_10y: number | null;
  dividend_var_10y: number | null;
  dividend_stability_10y: number | null;
  last_dividend_per_share: number | null;
  dividend_issued_this_year: boolean;
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
//  and the latest snapshot's pe + dividend_yield (for sparkline / sort).
//  Mirrors listMovAveSpreadCodes but draws from analysis.pe +
//  analysis.dividends (a code's date axis = the UNION of its rows in the
//  two split tables).
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
      FROM (
        SELECT code, date FROM analysis.pe WHERE sec_type = $1
        UNION
        SELECT code, date FROM analysis.dividends WHERE sec_type = $1
      ) u
      GROUP BY code
    ),
    latest_pe_row AS (
      SELECT DISTINCT ON (code) code, pe
      FROM analysis.pe
      WHERE sec_type = $1
      ORDER BY code, date DESC
    ),
    latest_dy_row AS (
      SELECT DISTINCT ON (code) code, dividend_yield
      FROM analysis.dividends
      WHERE sec_type = $1
      ORDER BY code, date DESC
    )
    SELECT
      cd.code,
      COALESCE(n.name, '')        AS name,
      cd.first_date,
      cd.last_date,
      cd.n_dates,
      lp.pe                       AS latest_pe,
      ld.dividend_yield           AS latest_dividend_yield
    FROM code_dates cd
    LEFT JOIN latest_name n  ON n.code  = cd.code
    LEFT JOIN latest_pe_row lp ON lp.code = cd.code
    LEFT JOIN latest_dy_row ld ON ld.code = cd.code
    ORDER BY cd.code
  `;
}

const META_TYPE: Record<PeAndDividendSecType, string> = {
  etf: "etf",
  index: "index",
  stock: "stock",
};

/** Meta SQL shared by listPeAndDividendThemes() and listPeAndDividendStrategyThemes().
 *  Returns one row per code present in analysis.pe / analysis.dividends
 *  (filtered by sec_type) with its precomputed L1/L2 classification from
 *  stats.sec_classification. */
const META_SQL = `
  WITH pd_codes AS (
    SELECT DISTINCT code FROM analysis.pe WHERE sec_type = $1::text
    UNION
    SELECT DISTINCT code FROM analysis.dividends WHERE sec_type = $1::text
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
      latest_pe: toNum(r.latest_pe),
      latest_dividend_yield: toNum(r.latest_dividend_yield),
    });
  }
  return { codes };
}

// ----------------------------------------------------------------------------
//  getPeAndDividendChart — daily time series for one asset.
//
//  Drives from the asset-appropriate close source (etf_basic_stats +
//  etf_adjustment for ETFs; index_basic_stats for indices;
//  stock_basic_stats for stocks) and LEFT JOINs the two split metric
//  tables — analysis.pe and analysis.dividends — so the response keeps
//  one row per close day with nulls where a metric is undefined.
// ----------------------------------------------------------------------------
function buildChartSql(secType: PeAndDividendSecType): string {
  const src = SEC_SOURCES[secType];
  return `
    SELECT
      b.date,
      ${src.closeExpr} AS close,
      p.pe,
      dy.dividend_yield
    ${src.chartFromClause}
    LEFT JOIN analysis.pe p
      ON p.code = b.code AND p.date = b.date
    LEFT JOIN analysis.dividends dy
      ON dy.code = b.code AND dy.date = b.date
    WHERE b.close IS NOT NULL
      AND b.code = ANY($1::text[])
    ORDER BY b.date ASC
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
    queryRows<DbChartRow>(buildChartSql(secType), [codeVariants(target)]),
    queryRows<{ name: string | null }>(buildNameSql(secType), [codeVariants(target)]),
  ]);

  const name = nameRows[0]?.name ?? "";

  const rows: PeAndDividendChartRow[] = chartRows.map((r) => ({
    date: formatDate(r.date),
    close: toNum(r.close),
    pe: toNum(r.pe),
    dividend_yield: toNum(r.dividend_yield),
  }));

  return { code: target, name, rows };
}

// ----------------------------------------------------------------------------
//  listPeAndDividendStats — annual 10y rolling stats snapshot rows for one
//  code from analysis.pe_and_dividend_stats. Returns ALL annual snapshots
//  (most recent first) so the UI can render the full history table; the
//  is_active flag marks the latest row for highlighting.
// ----------------------------------------------------------------------------
function buildStatsSql(): string {
  return `
    SELECT
      date,
      is_active,
      min_pe_10y,
      max_pe_10y,
      dividend_var_10y,
      dividend_stability_10y,
      last_dividend_per_share,
      dividend_issued_this_year
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
    min_pe_10y: toNum(r.min_pe_10y),
    max_pe_10y: toNum(r.max_pe_10y),
    dividend_var_10y: toNum(r.dividend_var_10y),
    dividend_stability_10y: toNum(r.dividend_stability_10y),
    last_dividend_per_share: toNum(r.last_dividend_per_share),
    dividend_issued_this_year: r.dividend_issued_this_year === true,
  }));

  return { code: target, name, rows };
}

// ----------------------------------------------------------------------------
//  listPeAndDividendStreaks — band-BREAK excursion streaks of one code's
//  pe / dividend_yield series, straight from
//  analysis.pe_and_dividend_pct_streaks joined with its bands table
//  (analysis.pe_and_dividend_pct) — the mov-ave-spreads buildStreaksSql
//  pattern.
//
//  The streak SIDE (high leg vs low leg) is not stored — it is derived here
//  by joining the band row of the streak's END month
//  (date_trunc('month', s.end_date)) and comparing the streak's end_value
//  against that band: each streak day is tested against its OWN month's
//  band (so the end-month band row is guaranteed to exist) and a streak
//  never switches sides, so the end day's own-month band decides exactly.
//  Shipped flat for ALL (metric, period, pct_type) combos — the client
//  filters by its nested metric→period→pct selection.
// ----------------------------------------------------------------------------
function buildStreaksSql(): string {
  return `
    SELECT
      s.metric,
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
    FROM analysis.pe_and_dividend_pct_streaks s
    JOIN analysis.pe_and_dividend_pct b
      ON b.sec_type = s.sec_type
      AND b.code = s.code
      AND b.metric = s.metric
      AND b.date_year_month = date_trunc('month', s.end_date)::date
      AND b.period = s.period
      AND b.pct_type = s.pct_type
    WHERE s.sec_type = $1
      AND s.code = ANY($2::text[])
    ORDER BY s.metric, s.period, s.pct_type, s.start_date
  `;
}

/** One streak row from analysis.pe_and_dividend_pct_streaks joined with
 *  its end-month band (see buildStreaksSql). side is NULL only if the end
 *  value fell exactly on a band boundary — never expected. */
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
 *  (ascending by metric, period, pctType, startDate; rows with an unusable
 *  side are dropped). */
function toStreaks(rows: DbStreakRow[]): PeAndDividendStreak[] {
  const out: PeAndDividendStreak[] = [];
  for (const r of rows) {
    if (r.metric !== "pe" && r.metric !== "dividend_yield") continue;
    if (r.side !== "high" && r.side !== "low") continue;
    out.push({
      metric: r.metric as PeAndDividendStreakMetric,
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

export async function listPeAndDividendStreaks(
  rawCode: string,
  rawSecType: string | undefined | null,
): Promise<PeAndDividendStreaksResponse> {
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
//  listPeAndDividendThemes — L1 sector → L2 industry → items tree, restricted
//  to codes that have rows in analysis.pe / analysis.dividends for the requested
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
