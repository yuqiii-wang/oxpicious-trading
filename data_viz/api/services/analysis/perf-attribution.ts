/**
 * Performance Attribution - ETF/Index subjects x Index benchmarks.
 * Extracted from the former analysis.service.ts.
 */
import { queryRows, formatDate, toNum } from "../../lib/db.js";
import type { QueryResultRow } from "pg";
import { stripExchangeSuffix, codeVariants } from "../../lib/classify-etf.js";
import { stripped } from "./_shared.js";
import { buildStrategyThemesFromRows, matchesClassification } from "../_shared.js";
import type {
  PerfAttrSecType,
  PerfAttrCodeRow,
  PerfAttrCodesResponse,
  PerfAttrAttributionResponse,
  PerfAttrBenchmarkRow,
  PerfAttrChartResponse,
  SectorNode,
  IndustryNode,
  StrategyNode,
} from "../../../shared/types.js";

// ============================================================================
//  Performance Attribution — ETF/Index subjects × Index benchmarks
//    stats.cross_stats (sec_type='index' pair grain; former
//    analysis.sec_alloc_perf_attribution, migrated 2026-09-04)
//    PK: (code, benchmark_code, date, sec_type)
// ============================================================================

/** Whitelisted sec_type → name table mapping (safe for string interpolation). */
const PERF_ATTR_NAME_TABLE: Record<string, string> = {
  etf: "stats.etf_identity",
  index: "stats.index_identity",
};

interface DbPerfAttrCodeRow extends QueryResultRow {
  code: string;
  name: string;
  first_date: Date | string;
  last_date: Date | string;
  n_dates: number;
  benchmarks: string[];
}

/** Which source the per-code aggregates read for this sec_type:
 *  - 'summary': stats.cross_stats_code_summary (per-(sec_type, code)
 *    rollup maintained by builds.cross_stats — millisecond reads; a live
 *    GROUP BY over the 70M+ row main table costs ~30s per request).
 *  - 'live': on-the-fly aggregate over stats.cross_stats — only used while
 *    the rollup has no rows for the sec_type yet (fresh DB before the
 *    first build).
 *  - 'empty': stats.cross_stats has no rows for the sec_type at all
 *    (e.g. sec_type='etf' — the ETF pair grain is reserved/unused) → the
 *    caller returns an empty result without any table scan. */
type PerfAttrCodeSource = "summary" | "live" | "empty";

async function resolvePerfAttrCodeSource(
  secType: PerfAttrSecType,
): Promise<PerfAttrCodeSource> {
  const summary = await queryRows(
    `SELECT 1 AS ok FROM stats.cross_stats_code_summary WHERE sec_type = $1::text LIMIT 1`,
    [secType],
  );
  if (summary.length) return "summary";
  const source = await queryRows(
    `SELECT 1 AS ok FROM stats.cross_stats WHERE sec_type = $1::text LIMIT 1`,
    [secType],
  );
  return source.length ? "live" : "empty";
}

export async function listPerfAttrCodes(
  secType: PerfAttrSecType,
  sector?: string | null,
  industry?: string | null,
  strategy?: string | null,
  theme?: string | null,
): Promise<PerfAttrCodesResponse> {
  const nameTable = PERF_ATTR_NAME_TABLE[secType] ?? PERF_ATTR_NAME_TABLE.etf;
  const sectorFilter = (sector ?? "").trim();
  const industryFilter = (industry ?? "").trim();
  const strategyFilter = (strategy ?? "").trim();
  const themeFilter = (theme ?? "").trim();
  const hasClassFilter = !!(sectorFilter || industryFilter || strategyFilter || themeFilter);
  const codeSource = await resolvePerfAttrCodeSource(secType);
  if (codeSource === "empty") {
    return { sec_type: secType, codes: [] };
  }
  // Summary variant: one PK-grain read of the rollup (first/last/n_dates/
  // benchmarks precomputed by builds.cross_stats). Live variant: the
  // historical two-CTE full-table aggregate (fallback only).
  const statsSql =
    codeSource === "summary"
      ? `
    SELECT
      cs.code,
      cs.first_date,
      cs.last_date,
      cs.n_dates,
      cs.benchmarks
    FROM stats.cross_stats_code_summary cs
    WHERE cs.sec_type = $1::text`
      : `
    WITH code_stats AS (
      SELECT
        code,
        MIN(date) AS first_date,
        MAX(date) AS last_date,
        COUNT(DISTINCT date) AS n_dates
      FROM stats.cross_stats
      WHERE sec_type = $1::text
      GROUP BY code
    ),
    bench_list AS (
      SELECT code, ARRAY_AGG(DISTINCT benchmark_code ORDER BY benchmark_code) AS benchmarks
      FROM stats.cross_stats
      WHERE sec_type = $1::text
      GROUP BY code
    )
    SELECT
      cs.code,
      cs.first_date,
      cs.last_date,
      cs.n_dates,
      COALESCE(bl.benchmarks, '{}') AS benchmarks
    FROM code_stats cs
    LEFT JOIN bench_list bl ON bl.code = cs.code`;
  const sql = `
    WITH latest_name AS (
      SELECT DISTINCT ON (code) code, name
      FROM ${nameTable}
      ORDER BY code, date DESC
    )
    SELECT
      s.code,
      COALESCE(n.name, '') AS name,
      s.first_date,
      s.last_date,
      s.n_dates,
      s.benchmarks
    FROM (${statsSql}) s
    LEFT JOIN latest_name n ON n.code = s.code
    ORDER BY s.n_dates DESC NULLS LAST, s.code
  `;
  const rows = await queryRows<DbPerfAttrCodeRow>(sql, [secType]);

  // When a classification filter is active, fetch the meta rows (same query as
  // listPerfAttrThemes) and build a code → classification map so
  // matchesClassification() can decide which codes to include. Industry and
  // strategy filters are mutually exclusive (handled by matchesClassification).
  let classMap: Map<string, DbPerfAttrMetaRow> | null = null;
  if (hasClassFilter) {
    const metaTable = PERF_ATTR_META_TABLE[secType] ?? PERF_ATTR_META_TABLE.etf;
    const metaType = PERF_ATTR_META_TYPE[secType] ?? PERF_ATTR_META_TYPE.etf;
    const metaRows = await queryRows<DbPerfAttrMetaRow>(
      buildPerfAttrMetaSql(metaTable, codeSource),
      [secType, metaType],
    );
    classMap = new Map<string, DbPerfAttrMetaRow>();
    for (const m of metaRows) {
      const code = stripExchangeSuffix(m.code);
      if (!code) continue;
      classMap.set(code, m);
    }
  }

  const codes: PerfAttrCodeRow[] = [];
  for (const r of rows) {
    const code = stripped(r.code);
    if (classMap) {
      const meta = classMap.get(code);
      if (!meta || !matchesClassification(meta, sectorFilter, industryFilter, strategyFilter, themeFilter)) {
        continue;
      }
    }
    codes.push({
      code,
      name: r.name ?? "",
      first_date: formatDate(r.first_date),
      last_date: formatDate(r.last_date),
      n_dates: Number(r.n_dates) || 0,
      benchmarks: Array.isArray(r.benchmarks) ? r.benchmarks : [],
    });
  }
  return { sec_type: secType, codes };
}

// ----------------------------------------------------------------------------

interface DbPerfAttrAttributionRow extends QueryResultRow {
  benchmark_code: string;
  benchmark_name: string | null;
  date: Date | string;
  code_sec_shared_weight: number | null;
  benchmark_sec_shared_weight: number | null;
  etf_trading_amount_ratio: number | null;
  benchmark_etf_trading_amount: number | null;
  code_etf_trading_amount: number | null;
  is_broad_market: boolean | null;
  benchmark_return: number | null;
  subject_return: number | null;
}

export async function getPerfAttrAttribution(
  rawCode: string,
  secType: PerfAttrSecType,
  date?: string | null,
): Promise<PerfAttrAttributionResponse> {
  const target = stripped(rawCode);
  const nameTable = PERF_ATTR_NAME_TABLE[secType] ?? PERF_ATTR_NAME_TABLE.etf;
  // When a specific date is requested ($3), use it directly; otherwise fall
  // back to MAX(date) (latest trading day). COALESCE keeps a single SQL shape
  // so the prepared-statement cache stays effective.
  //
  // Fractional returns (benchmark_return, subject_return) are computed
  // on-the-fly via LATERAL joins to stats.index_basic_stats — they are NOT
  // stored as DB columns. The return = (close_t - close_{t-1}) / close_{t-1}.
  // Subject return uses index_basic_stats (correct for sec_type='index';
  // returns NULL for ETF subjects since their codes carry exchange suffixes
  // that don't match index_basic_stats.code).
  const sql = `
    WITH target_date AS (
      SELECT COALESCE(
        $3::date,
        (SELECT MAX(date) FROM stats.cross_stats
         WHERE sec_type = $1::text
           AND code = ANY($2::text[]))
      ) AS max_date
    ),
    subject_name AS (
      SELECT DISTINCT ON (code) code, name
      FROM ${nameTable}
      WHERE code = ANY($2::text[])
      ORDER BY code, date DESC
    )
    SELECT
      a.benchmark_code,
      bi.name AS benchmark_name,
      a.date,
      a.code_sec_shared_weight,
      a.benchmark_sec_shared_weight,
      a.etf_trading_amount_ratio_benchmark_to_code AS etf_trading_amount_ratio,
      a.benchmark_etf_trading_amount,
      a.code_etf_trading_amount,
      bm.is_broad_market,
      CASE
        WHEN ib.close IS NOT NULL AND pb.close IS NOT NULL AND pb.close != 0
        THEN (ib.close - pb.close) / pb.close
        ELSE NULL
      END AS benchmark_return,
      CASE
        WHEN sb.close IS NOT NULL AND ps.close IS NOT NULL AND ps.close != 0
        THEN (sb.close - ps.close) / ps.close
        ELSE NULL
      END AS subject_return
    FROM stats.cross_stats a
    CROSS JOIN target_date ld
    LEFT JOIN LATERAL (
      SELECT DISTINCT ON (code) code, name
      FROM stats.index_identity
      WHERE code = a.benchmark_code
      ORDER BY code, date DESC
    ) bi ON true
    LEFT JOIN LATERAL (
      SELECT close FROM stats.index_basic_stats
      WHERE code = a.benchmark_code AND date = a.date
    ) ib ON true
    LEFT JOIN LATERAL (
      SELECT close FROM stats.index_basic_stats
      WHERE code = a.benchmark_code AND date < a.date
      ORDER BY date DESC LIMIT 1
    ) pb ON true
    LEFT JOIN LATERAL (
      SELECT close FROM stats.index_basic_stats
      WHERE code = a.code AND date = a.date
    ) sb ON true
    LEFT JOIN LATERAL (
      SELECT close FROM stats.index_basic_stats
      WHERE code = a.code AND date < a.date
      ORDER BY date DESC LIMIT 1
    ) ps ON true
    LEFT JOIN LATERAL (
      SELECT BOOL_OR(is_broad_market) AS is_broad_market
      FROM stats.sec_index_tags
      WHERE code = a.benchmark_code
    ) bm ON true
    WHERE a.sec_type = $1::text
      AND a.code = ANY($2::text[])
      AND a.date = ld.max_date
    ORDER BY a.benchmark_code
  `;
  const [attrRows, nameRows] = await Promise.all([
    queryRows<DbPerfAttrAttributionRow>(sql, [secType, codeVariants(target), date ?? null]),
    queryRows<{ name: string | null }>(
      `SELECT DISTINCT ON (code) code, name FROM ${nameTable} WHERE code = ANY($1::text[]) ORDER BY code, date DESC`,
      [codeVariants(target)],
    ),
  ]);

  const benchmarks: PerfAttrBenchmarkRow[] = attrRows.map((r) => {
    const br = toNum(r.benchmark_return);
    const sr = toNum(r.subject_return);
    return {
      benchmark_code: r.benchmark_code,
      benchmark_name: r.benchmark_name ?? "",
      date: formatDate(r.date),
      code_sec_shared_weight: toNum(r.code_sec_shared_weight),
      benchmark_sec_shared_weight: toNum(r.benchmark_sec_shared_weight),
      etf_trading_amount_ratio: toNum(r.etf_trading_amount_ratio),
      benchmark_etf_trading_amount: toNum(r.benchmark_etf_trading_amount),
      code_etf_trading_amount: toNum(r.code_etf_trading_amount),
      is_broad_market: r.is_broad_market === null ? null : Boolean(r.is_broad_market),
      benchmark_return: br,
      subject_return: sr,
      active_return: br == null || sr == null ? null : sr - br,
    };
  });

  return {
    code: target,
    name: nameRows[0]?.name ?? "",
    sec_type: secType,
    latest_date: benchmarks[0]?.date ?? "",
    benchmarks,
  };
}

// ----------------------------------------------------------------------------
//  listPerfAttrThemes — two-level L1 sector → L2 industry → items tree for the
//  Perf Attribution page's ThemeSelector. Mirrors listThemes() in
//  etf-margin.service.ts and listIndexThemes() in index-baseline.service.ts,
//  but only includes codes that have rows in stats.cross_stats
//  (sec_type='index') for the requested sec_type.
// ----------------------------------------------------------------------------

/** Whitelisted sec_type → meta-table mapping (safe for string interpolation).
 *  Both ETF and index classification now live in the unified stats.sec_classification
 *  table, discriminated by the `type` column. */
const PERF_ATTR_META_TABLE: Record<string, string> = {
  etf: "stats.sec_classification",
  index: "stats.sec_classification",
};

/** Whitelisted sec_type → type discriminator for sec_classification WHERE clause. */
const PERF_ATTR_META_TYPE: Record<string, string> = {
  etf: "etf",
  index: "index",
};

interface DbPerfAttrMetaRow extends QueryResultRow {
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
}

/** Meta SQL shared by listPerfAttrThemes() and listPerfAttrStrategyThemes().
 *  Returns one row per code in stats.cross_stats (filtered
 *  by sec_type) with its precomputed L1/L2 classification from
 *  stats.sec_classification. is_industry_not_strategy distinguishes
 *  industry-primary (TRUE) from strategy-primary (FALSE) rows.
 *  `codeSource` picks the membership source: the summary rollup's PK
 *  scan ('summary') or the historical DISTINCT full-table scan ('live'). */
function buildPerfAttrMetaSql(
  metaTable: string,
  codeSource: PerfAttrCodeSource,
): string {
  const perfCodes =
    codeSource === "summary"
      ? `
      SELECT code
      FROM stats.cross_stats_code_summary
      WHERE sec_type = $1::text`
      : `
      SELECT DISTINCT code
      FROM stats.cross_stats
      WHERE sec_type = $1::text`;
  return `
    WITH perf_codes AS (${perfCodes})
    SELECT
      pc.code,
      COALESCE(m.name, '')             AS name,
      COALESCE(m.sector_id,       'OTHER')  AS sector_id,
      COALESCE(m.sector_label,    '其他')   AS sector_label,
      COALESCE(m.industry_id,     'OTHER')  AS industry_id,
      COALESCE(m.industry_label,  '未分类') AS industry_label,
      COALESCE(m.industry_slug,   'other')  AS industry_slug,
      COALESCE(m.is_industry_not_strategy, TRUE) AS is_industry_not_strategy
    FROM perf_codes pc
    LEFT JOIN ${metaTable} m ON m.code = pc.code AND m.type = $2::text
  `;
}

export async function listPerfAttrThemes(
  secType: PerfAttrSecType,
): Promise<SectorNode[]> {
  const metaTable = PERF_ATTR_META_TABLE[secType] ?? PERF_ATTR_META_TABLE.etf;
  const metaType = PERF_ATTR_META_TYPE[secType] ?? PERF_ATTR_META_TYPE.etf;
  const codeSource = await resolvePerfAttrCodeSource(secType);
  if (codeSource === "empty") return [];
  const rows = await queryRows<DbPerfAttrMetaRow>(
    buildPerfAttrMetaSql(metaTable, codeSource),
    [secType, metaType],
  );

  const sectorMap = new Map<string, {
    sector_label: string;
    industries: Map<string, IndustryNode>;
  }>();

  for (const r of rows) {
    // LEFT column: only industry-primary securities. Strategy-primary rows
    // (is_industry_not_strategy=FALSE) carry strategy/theme in
    // sector_id/industry_id and belong in the RIGHT column only.
    if (!r.is_industry_not_strategy) continue;
    // Strip exchange suffix so the items[] codes match the codes returned by
    // listPerfAttrCodes (which also strips the suffix).
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
//  listPerfAttrStrategyThemes — parallel L1 strategy → L2 theme → items tree
//  built from the same meta SQL but using the strategy-primary rows
//  (is_industry_not_strategy=FALSE). sector_id/industry_id on those rows
//  carry the strategy/theme classification. Tree-building is delegated to the
//  shared buildStrategyThemesFromRows helper to avoid duplicating the
//  grouping/sorting logic.
// ----------------------------------------------------------------------------
export async function listPerfAttrStrategyThemes(
  secType: PerfAttrSecType,
): Promise<StrategyNode[]> {
  const metaTable = PERF_ATTR_META_TABLE[secType] ?? PERF_ATTR_META_TABLE.etf;
  const metaType = PERF_ATTR_META_TYPE[secType] ?? PERF_ATTR_META_TYPE.etf;
  const codeSource = await resolvePerfAttrCodeSource(secType);
  if (codeSource === "empty") return [];
  const rows = await queryRows<DbPerfAttrMetaRow>(
    buildPerfAttrMetaSql(metaTable, codeSource),
    [secType, metaType],
  );

  const mappedRows = rows.map((r) => ({
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

interface DbPerfAttrChartRow extends QueryResultRow {
  date: Date | string;
  etf_trading_amount_ratio: number | null;
  etf_trading_amount_ratio_ma5: number | null;
  benchmark_etf_trading_amount: number | null;
  code_etf_trading_amount: number | null;
  benchmark_etf_num: number | null;
  code_etf_num: number | null;
  subject_close: number | null;
  benchmark_close: number | null;
  corr_20d: number | null;
  corr_60d: number | null;
  corr_255d: number | null;
}

/** Per-sec_type subject source-table JOIN + price expression for the
 *  close-price lookup in getPerfAttrChart. Mirrors the SEC_SOURCES pattern
 *  above but keyed on the perf-attr table alias `a` (not `d`). */
const PERF_ATTR_SUBJECT_SOURCE: Record<PerfAttrSecType, {
  joinClause: string;
  priceExpr: string;
}> = {
  etf: {
    joinClause:
      "LEFT JOIN stats.etf_basic_stats   sb ON sb.date = a.date AND sb.code = a.code\n" +
      "LEFT JOIN stats.etf_adjustment    sa ON sa.date = a.date AND sa.code = a.code",
    priceExpr: "COALESCE(sa.adj_close, sb.close)",
  },
  index: {
    joinClause:
      "LEFT JOIN stats.index_basic_stats  sb ON sb.date = a.date AND sb.code = a.code",
    priceExpr: "sb.close",
  },
};

export async function getPerfAttrChart(
  rawCode: string,
  rawBenchmarkCode: string,
  secType: PerfAttrSecType,
): Promise<PerfAttrChartResponse> {
  const target = stripped(rawCode);
  const benchmarkCode = rawBenchmarkCode.trim();
  const nameTable = PERF_ATTR_NAME_TABLE[secType] ?? PERF_ATTR_NAME_TABLE.etf;
  const subjSrc = PERF_ATTR_SUBJECT_SOURCE[secType] ?? PERF_ATTR_SUBJECT_SOURCE.etf;

  const [chartRows, nameRows, benchNameRows, benchLinkedEtfs, codeLinkedEtfs] = await Promise.all([
    queryRows<DbPerfAttrChartRow>(
      `SELECT a.date,
              a.etf_trading_amount_ratio_benchmark_to_code AS etf_trading_amount_ratio,
              a.etf_trading_amount_ratio_benchmark_to_code_ma5 AS etf_trading_amount_ratio_ma5,
              a.benchmark_etf_trading_amount,
              a.code_etf_trading_amount,
              ieb.etf_num AS benchmark_etf_num,
              iec.etf_num AS code_etf_num,
              a.corr_20d,
              a.corr_60d,
              a.corr_255d,
              ${subjSrc.priceExpr} AS subject_close,
              ib.close AS benchmark_close
       FROM stats.cross_stats a
       ${subjSrc.joinClause}
       LEFT JOIN stats.index_basic_stats ib ON ib.date = a.date AND ib.code = a.benchmark_code
       LEFT JOIN stats.index_exts ieb ON ieb.date = a.date AND ieb.code = a.benchmark_code
       LEFT JOIN stats.index_exts iec ON iec.date = a.date AND iec.code = a.code
       WHERE a.sec_type = $1::text
         AND a.code = ANY($2::text[])
         AND a.benchmark_code = $3::text
       ORDER BY a.date ASC`,
      [secType, codeVariants(target), benchmarkCode],
    ),
    queryRows<{ name: string | null }>(
      `SELECT DISTINCT ON (code) code, name FROM ${nameTable} WHERE code = ANY($1::text[]) ORDER BY code, date DESC`,
      [codeVariants(target)],
    ),
    queryRows<{ name: string | null }>(
      `SELECT DISTINCT ON (code) code, name FROM stats.index_identity WHERE code = $1::text ORDER BY code, date DESC`,
      [benchmarkCode],
    ),
    // ETFs tracking the benchmark index (parent_index_code = benchmark_code).
    queryRows<{ code: string; name: string | null }>(
      `SELECT code, name FROM stats.sec_classification
       WHERE type = 'etf' AND is_active = TRUE AND parent_index_code = $1::text
       ORDER BY name NULLS LAST, code`,
      [benchmarkCode],
    ),
    // ETFs tracking the subject index (parent_index_code = code). For ETF
    // subjects (sec_type='etf') this returns nothing — the subject IS the ETF.
    queryRows<{ code: string; name: string | null }>(
      `SELECT code, name FROM stats.sec_classification
       WHERE type = 'etf' AND is_active = TRUE AND parent_index_code = $1::text
       ORDER BY name NULLS LAST, code`,
      [target],
    ),
  ]);

  return {
    code: target,
    name: nameRows[0]?.name ?? "",
    benchmark_code: benchmarkCode,
    benchmark_name: benchNameRows[0]?.name ?? "",
    rows: chartRows.map((r) => ({
      date: formatDate(r.date),
      etf_trading_amount_ratio: toNum(r.etf_trading_amount_ratio),
      etf_trading_amount_ratio_ma5: toNum(r.etf_trading_amount_ratio_ma5),
      benchmark_etf_trading_amount: toNum(r.benchmark_etf_trading_amount),
      code_etf_trading_amount: toNum(r.code_etf_trading_amount),
      benchmark_etf_num: r.benchmark_etf_num == null ? null : Number(r.benchmark_etf_num),
      code_etf_num: r.code_etf_num == null ? null : Number(r.code_etf_num),
      subject_close: toNum(r.subject_close),
      benchmark_close: toNum(r.benchmark_close),
      corr_20d: toNum(r.corr_20d),
      corr_60d: toNum(r.corr_60d),
      corr_255d: toNum(r.corr_255d),
    })),
    benchmark_linked_etfs: benchLinkedEtfs.map((r) => ({
      code: r.code,
      name: r.name ?? r.code,
    })),
    code_linked_etfs: codeLinkedEtfs.map((r) => ({
      code: r.code,
      name: r.name ?? r.code,
    })),
  };
}
