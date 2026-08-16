/**
 * Margin Trends analysis service — INDUSTRY-LEVEL, SECURITY-PAIR.
 *
 * Two endpoints feed the 2-plot single-industry page:
 *   • themes / strategy-themes — L1 sector → L2 industry tree from
 *     analysis.margin_industry_stats (industries WITH margin data only)
 *   • industry-series — per-(security, date) margin series for ONE
 *     industry + attribution. 'index' reads the margin_index_series
 *     VIEW (weighted-avg constituent-stock margin); 'etf' reads
 *     stats.etf_liquidity_margin for the industry's ETFs.
 *   • industry-correlation — precomputed pairwise rolling Pearson
 *     correlation from analysis.margin_industry_correlation, filtered
 *     to the user-selected security codes.
 *
 * Sources:
 *   analysis.margin_industry_stats        — industry tree (themes)
 *   analysis.margin_index_series (VIEW)   — 'index' series
 *   stats.etf_liquidity_margin            — 'etf' series
 *   analysis.margin_industry_correlation  — pairwise corr (precomputed)
 *
 * RONGZI (融资 / cash-borrow) only — RONQIN (融券 / sec borrow) EXCLUDED.
 */
import { queryRows, formatDate, toNum } from "../../lib/db.js";
import type { QueryResultRow } from "pg";
import { buildStrategyThemesFromRows } from "../_shared.js";
import type {
  MarginIndustrySeriesResponse,
  MarginIndustryCorrelationResponse,
  MarginTrendsShadeResponse,
  MarginSeriesRow,
  MarginSecurity,
  MarginCorrPair,
  MarginCorrRow,
  MarginAttributionType,
  SectorNode,
  IndustryNode,
  StrategyNode,
} from "../../../shared/types.js";

// ----------------------------------------------------------------------------
//  DB row types
// ----------------------------------------------------------------------------
interface DbMetaRow extends QueryResultRow {
  industry_id: string;
  industry_label: string;
  sector_id: string;
  sector_label: string;
  industry_slug: string;
  is_industry_not_strategy: boolean;
}

interface DbIndexSeriesRow extends QueryResultRow {
  index_code: string;
  date: Date | string;
  index_margin_balance: string | number | null;
  index_margin_buy: string | number | null;
  close: string | number | null;
}

interface DbEtfSeriesRow extends QueryResultRow {
  code: string;
  date: Date | string;
  rz_balance: string | number | null;
  rz_buy: string | number | null;
  close: string | number | null;
}

interface DbSecurityRow extends QueryResultRow {
  industry_id: string;
  code: string;
  name: string;
}

interface DbCorrRow extends QueryResultRow {
  date: Date | string;
  security_code: string;
  benchmark_code: string;
  corr: string | number | null;
}

// ----------------------------------------------------------------------------
//  META_SQL — one row per industry that has rows in
//  analysis.margin_industry_stats, JOINed with stats.sec_classification
//  (type='index') to recover sector_id / sector_label / industry_slug /
//  is_industry_not_strategy.
// ----------------------------------------------------------------------------
const META_SQL = `
  WITH margin_industries AS (
    SELECT DISTINCT industry_id, industry_label
    FROM analysis.margin_industry_stats
  )
  SELECT
    mi.industry_id,
    COALESCE(NULLIF(mi.industry_label, ''), mi.industry_id) AS industry_label,
    COALESCE(sc.sector_id,              'OTHER')  AS sector_id,
    COALESCE(sc.sector_label,           '其他')   AS sector_label,
    COALESCE(sc.industry_slug,          LOWER(mi.industry_id)) AS industry_slug,
    COALESCE(sc.is_industry_not_strategy, TRUE)  AS is_industry_not_strategy
  FROM margin_industries mi
  LEFT JOIN LATERAL (
    SELECT sector_id, sector_label, industry_slug, is_industry_not_strategy
    FROM stats.sec_classification
    WHERE industry_id = mi.industry_id
      AND type = 'index'
      AND is_active = TRUE
    LIMIT 1
  ) sc ON TRUE
`;

// ----------------------------------------------------------------------------
//  ITEMS_SQL — securities per industry, parametrized by attribution.
//    attribution='index' → distinct index_code from margin_index_series VIEW
//    attribution='etf'   → ETF codes from sec_classification
//  Returns (industry_id, code, name) for populating the L3 items[] in each
//  IndustryNode, so SecClassificationNav shows non-zero counts + clickable
//  security chips when an industry is selected.
// ----------------------------------------------------------------------------
const INDEX_ITEMS_SQL = `
  SELECT DISTINCT s.industry_id,
         s.index_code AS code,
         COALESCE(NULLIF(sc.name, ''), s.index_code) AS name
  FROM analysis.margin_index_series s
  LEFT JOIN LATERAL (
    SELECT name FROM stats.sec_classification
    WHERE code = s.index_code AND type = 'index' AND is_active = TRUE
    LIMIT 1
  ) sc ON TRUE
  WHERE s.industry_id IS NOT NULL AND s.industry_id <> ''
  ORDER BY s.index_code
`;

const ETF_ITEMS_SQL = `
  SELECT sc.industry_id,
         sc.code,
         COALESCE(NULLIF(sc.name, ''), sc.code) AS name
  FROM stats.sec_classification sc
  WHERE sc.type = 'etf'
    AND sc.parent_index_is_primary = TRUE
    AND sc.is_active = TRUE
    AND sc.industry_id IS NOT NULL AND sc.industry_id <> ''
  ORDER BY sc.code
`;

// ----------------------------------------------------------------------------
//  listMarginTrendThemes — L1 sector → L2 industry tree, restricted to
//  industries that have rows in analysis.margin_industry_stats. Each industry
//  node is populated with items[] (securities) based on attribution, so the
//  L3 column shows indices or ETFs and the count is non-zero.
// ----------------------------------------------------------------------------
export async function listMarginTrendThemes(
  rawAttribution?: string,
): Promise<SectorNode[]> {
  const attribution: MarginAttributionType =
    rawAttribution === "etf" ? "etf" : "index";

  const [rows, itemRows] = await Promise.all([
    queryRows<DbMetaRow>(META_SQL, []),
    queryRows<DbSecurityRow>(
      attribution === "etf" ? ETF_ITEMS_SQL : INDEX_ITEMS_SQL,
      [],
    ),
  ]);

  // Group items by industry_id.
  const itemsByIndustry = new Map<string, Array<{ code: string; name: string }>>();
  for (const r of itemRows) {
    if (!itemsByIndustry.has(r.industry_id)) {
      itemsByIndustry.set(r.industry_id, []);
    }
    itemsByIndustry.get(r.industry_id)!.push({ code: r.code, name: r.name });
  }

  const sectorMap = new Map<string, {
    sector_label: string;
    industries: Map<string, IndustryNode>;
  }>();

  for (const r of rows) {
    // ALL classification types (industries, broad-markets, strategies)
    // are included — no is_industry_not_strategy filter. The LEFT column
    // shows the full universe; the RIGHT column (strategy-themes) still
    // filters to is_industry_not_strategy=FALSE for strategies only.
    if (!sectorMap.has(r.sector_id)) {
      sectorMap.set(r.sector_id, {
        sector_label: r.sector_label,
        industries: new Map(),
      });
    }
    const sector = sectorMap.get(r.sector_id)!;
    if (!sector.industries.has(r.industry_id)) {
      const items = itemsByIndustry.get(r.industry_id) ?? [];
      sector.industries.set(r.industry_id, {
        industry_id: r.industry_id,
        industry_label: r.industry_label,
        industry_slug: r.industry_slug,
        count: items.length,
        items,
      });
    }
  }

  const sectors: SectorNode[] = [];
  for (const [sector_id, sector] of sectorMap) {
    const industries = Array.from(sector.industries.values()).sort((a, b) => {
      if (a.industry_id === "OTHER") return 1;
      if (b.industry_id === "OTHER") return -1;
      return a.industry_label.localeCompare(b.industry_label);
    });
    sectors.push({
      sector_id,
      sector_label: sector.sector_label,
      count: industries.length,
      industries,
    });
  }
  sectors.sort((a, b) => {
    if (a.sector_id === "OTHER") return 1;
    if (b.sector_id === "OTHER") return -1;
    return a.sector_label.localeCompare(b.sector_label);
  });
  return sectors;
}

// ----------------------------------------------------------------------------
//  listMarginTrendStrategyThemes — parallel L1 strategy → L2 theme tree.
// ----------------------------------------------------------------------------
export async function listMarginTrendStrategyThemes(): Promise<StrategyNode[]> {
  const rows = await queryRows<DbMetaRow>(META_SQL, []);

  const filteredRows = rows
    .filter((r) => !r.is_industry_not_strategy)
    .map((r) => ({
      code: r.industry_id,
      name: r.industry_label,
      sector_id: r.sector_id,
      sector_label: r.sector_label,
      industry_id: r.industry_id,
      industry_label: r.industry_label,
      industry_slug: r.industry_slug,
      is_industry_not_strategy: r.is_industry_not_strategy,
    }));

  return buildStrategyThemesFromRows(filteredRows);
}

// ----------------------------------------------------------------------------
//  getMarginIndustrySeries — per-(security, date) margin series for ONE
//  industry + ONE attribution. Returns the securities list (codes + labels)
//  and the full daily series, so the 1st plot can render one line per
//  security and the 2nd plot can offer a security multi-select.
//
//  attribution='index' → analysis.margin_index_series VIEW (weighted-avg
//    constituent-stock margin per index_code). Securities = distinct
//    index_code values with their sec_classification.name label.
//  attribution='etf'   → stats.etf_liquidity_margin for the industry's
//    ETFs (via sec_classification parent_index_is_primary). Securities =
//    the industry's ETF codes with their name labels.
// ----------------------------------------------------------------------------
const INDEX_SERIES_SQL = `
  SELECT s.index_code, s.date, s.index_margin_balance, s.index_margin_buy,
         ib.close
  FROM analysis.margin_index_series s
  LEFT JOIN stats.index_basic_stats ib
    ON ib.code = s.index_code AND ib.date = s.date
  WHERE s.industry_id = $1::text
  ORDER BY s.index_code, s.date
`;

const INDEX_SECURITIES_SQL = `
  SELECT DISTINCT s.index_code AS code,
         COALESCE(NULLIF(sc.name, ''), s.index_code) AS name
  FROM analysis.margin_index_series s
  LEFT JOIN LATERAL (
    SELECT name FROM stats.sec_classification
    WHERE code = s.index_code AND type = 'index' AND is_active = TRUE
    LIMIT 1
  ) sc ON TRUE
  WHERE s.industry_id = $1::text
  ORDER BY code
`;

const ETF_SECURITIES_SQL = `
  SELECT sc.code, COALESCE(NULLIF(sc.name, ''), sc.code) AS name
  FROM stats.sec_classification sc
  WHERE sc.type = 'etf'
    AND sc.industry_id = $1::text
    AND sc.parent_index_is_primary = TRUE
    AND sc.is_active = TRUE
  ORDER BY sc.code
`;

const ETF_SERIES_SQL = `
  SELECT e.code, e.date, e.rz_balance, e.rz_buy, eb.close
  FROM stats.etf_liquidity_margin e
  LEFT JOIN stats.etf_basic_stats eb
    ON eb.code = e.code AND eb.date = e.date
  WHERE e.code IN (
    SELECT sc.code FROM stats.sec_classification sc
    WHERE sc.type = 'etf'
      AND sc.industry_id = $1::text
      AND sc.parent_index_is_primary = TRUE
      AND sc.is_active = TRUE
  )
  ORDER BY e.code, e.date
`;

export async function getMarginIndustrySeries(
  rawIndustryId: string,
  rawAttribution: string,
): Promise<MarginIndustrySeriesResponse> {
  const industryId = (rawIndustryId ?? "").trim();
  if (!industryId) {
    throw new Error("industry_id is required.");
  }
  const attribution: MarginAttributionType =
    rawAttribution === "etf" ? "etf" : "index";

  let securities: MarginSecurity[] = [];
  let seriesRows: MarginSeriesRow[] = [];
  let industryLabel = industryId;

  if (attribution === "index") {
    const secRows = await queryRows<DbSecurityRow>(INDEX_SECURITIES_SQL, [industryId]);
    securities = secRows.map((r) => ({ code: r.code, label: r.name }));

    const rows = await queryRows<DbIndexSeriesRow>(INDEX_SERIES_SQL, [industryId]);
    seriesRows = rows.map((r) => ({
      code: r.index_code,
      date: formatDate(r.date),
      balance: toNum(r.index_margin_balance),
      buy: toNum(r.index_margin_buy),
      close: toNum(r.close),
    }));

    // Recover industry_label from the first index's sec_classification row.
    if (securities.length > 0) {
      const labelRow = await queryRows<{ industry_label: string }>(
        `SELECT industry_label FROM stats.sec_classification
         WHERE code = $1 AND type = 'index' AND is_active = TRUE LIMIT 1`,
        [securities[0].code],
      );
      if (labelRow.length > 0 && labelRow[0].industry_label) {
        industryLabel = labelRow[0].industry_label;
      }
    }
  } else {
    const secRows = await queryRows<DbSecurityRow>(ETF_SECURITIES_SQL, [industryId]);
    securities = secRows.map((r) => ({ code: r.code, label: r.name }));

    const rows = await queryRows<DbEtfSeriesRow>(ETF_SERIES_SQL, [industryId]);
    seriesRows = rows.map((r) => ({
      code: r.code,
      date: formatDate(r.date),
      balance: toNum(r.rz_balance),
      buy: toNum(r.rz_buy),
      close: toNum(r.close),
    }));

    if (securities.length > 0) {
      const labelRow = await queryRows<{ industry_label: string }>(
        `SELECT industry_label FROM stats.sec_classification
         WHERE code = $1 AND type = 'etf' AND is_active = TRUE LIMIT 1`,
        [securities[0].code],
      );
      if (labelRow.length > 0 && labelRow[0].industry_label) {
        industryLabel = labelRow[0].industry_label;
      }
    }
  }

  return {
    industry_id: industryId,
    industry_label: industryLabel,
    attribution,
    securities,
    rows: seriesRows,
  };
}

// ----------------------------------------------------------------------------
//  getMarginIndustryCorrelation — precomputed pairwise rolling Pearson
//  correlation from analysis.margin_industry_correlation, filtered to the
//  user-selected security codes. Returns all pairs among the selected
//  codes (security_code < benchmark_code) and their per-date corr values
//  for the chosen series + window.
//
//  The corr column name is built from validated series + window values
//  (corr_balance_60d / corr_buy_255d / …) — safe to interpolate.
// ----------------------------------------------------------------------------
const VALID_WINDOWS = new Set([5, 20, 60, 120, 255]);

export async function getMarginIndustryCorrelation(
  rawIndustryId: string,
  rawAttribution: string,
  rawCodes: string[],
  rawSeries: string,
  rawWindow: string | number,
): Promise<MarginIndustryCorrelationResponse> {
  const industryId = (rawIndustryId ?? "").trim();
  if (!industryId) {
    throw new Error("industry_id is required.");
  }
  const attribution: MarginAttributionType =
    rawAttribution === "etf" ? "etf" : "index";
  const series: "balance" | "buy" = rawSeries === "buy" ? "buy" : "balance";
  const window = Number(rawWindow);
  if (!VALID_WINDOWS.has(window)) {
    throw new Error(`Invalid window: ${rawWindow}. Must be one of 5,20,60,120,255.`);
  }
  const codes = (rawCodes ?? []).map((c) => c.trim()).filter(Boolean);
  if (codes.length < 2) {
    return {
      industry_id: industryId,
      attribution,
      series,
      window,
      pairs: [],
      rows: [],
    };
  }

  const corrCol = `corr_${series}_${window}d`;
  const sql = `
    SELECT date, security_code, benchmark_code, ${corrCol} AS corr
    FROM analysis.margin_industry_correlation
    WHERE industry_id = $1::text
      AND attribution_type = $2::text
      AND security_code = ANY($3::text[])
      AND benchmark_code = ANY($3::text[])
    ORDER BY date, security_code, benchmark_code
  `;
  const rows = await queryRows<DbCorrRow>(sql, [industryId, attribution, codes]);

  const corrRows: MarginCorrRow[] = rows.map((r) => ({
    date: formatDate(r.date),
    security_code: r.security_code,
    benchmark_code: r.benchmark_code,
    corr: toNum(r.corr),
  }));

  // Distinct pairs (preserve first-seen order).
  const seen = new Set<string>();
  const pairs: MarginCorrPair[] = [];
  for (const r of rows) {
    const key = `${r.security_code}|${r.benchmark_code}`;
    if (!seen.has(key)) {
      seen.add(key);
      pairs.push({
        security_code: r.security_code,
        benchmark_code: r.benchmark_code,
      });
    }
  }

  return {
    industry_id: industryId,
    attribution,
    series,
    window,
    pairs,
    rows: corrRows,
  };
}

// ----------------------------------------------------------------------------
//  getMarginTrends — sustained UP/DOWN TREND EPISODES for the securities in
//  ONE industry + ONE attribution, from analysis.margin_changes. Returns
//  (code, start_date, end_date, is_trend_up_not_down) per episode so the
//  1st plot can render a light shade (markArea) over each trend window.
//
//  attribution='index' → sec_type='index', codes from margin_index_series
//  attribution='etf'   → sec_type='etf', codes from sec_classification
// ----------------------------------------------------------------------------
interface DbTrendRow extends QueryResultRow {
  code: string;
  start_date: Date | string;
  end_date: Date | string;
  is_trend_up_not_down: boolean;
}

const INDEX_TRENDS_SQL = `
  WITH industry_codes AS (
    SELECT DISTINCT index_code AS code
    FROM analysis.margin_index_series
    WHERE industry_id = $1::text
  )
  SELECT t.code, t.start_date, t.end_date, t.is_trend_up_not_down
  FROM analysis.margin_changes t
  JOIN industry_codes ic ON ic.code = t.code
  WHERE t.sec_type = 'index'
  ORDER BY t.start_date
`;

const ETF_TRENDS_SQL = `
  WITH industry_codes AS (
    SELECT sc.code
    FROM stats.sec_classification sc
    WHERE sc.type = 'etf'
      AND sc.industry_id = $1::text
      AND sc.parent_index_is_primary = TRUE
      AND sc.is_active = TRUE
  )
  SELECT t.code, t.start_date, t.end_date, t.is_trend_up_not_down
  FROM analysis.margin_changes t
  JOIN industry_codes ic ON ic.code = t.code
  WHERE t.sec_type = 'etf'
  ORDER BY t.start_date
`;

export async function getMarginTrends(
  rawIndustryId: string,
  rawAttribution: string,
): Promise<MarginTrendsShadeResponse> {
  const industryId = (rawIndustryId ?? "").trim();
  if (!industryId) {
    throw new Error("industry_id is required.");
  }
  const attribution: MarginAttributionType =
    rawAttribution === "etf" ? "etf" : "index";

  const sql = attribution === "etf" ? ETF_TRENDS_SQL : INDEX_TRENDS_SQL;
  const rows = await queryRows<DbTrendRow>(sql, [industryId]);

  const episodes = rows.map((r) => ({
    code: r.code,
    start_date: formatDate(r.start_date),
    end_date: formatDate(r.end_date),
    is_trend_up_not_down: r.is_trend_up_not_down,
  }));

  return {
    industry_id: industryId,
    attribution,
    episodes,
  };
}
