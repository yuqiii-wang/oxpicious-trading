/**
 * Performance Attribution codes list — listPerfAttrCodes, one row per
 * subject code with first/last date, n_dates and the benchmark list.
 * Split out of the former monolithic perf-attribution.ts.
 */
import { queryRows, formatDate } from "@api/lib/db";
import type { QueryResultRow } from "pg";
import { stripExchangeSuffix } from "@api/lib/classify-etf";
import { stripped } from "@api/services/analysis/_shared";
import { matchesClassification } from "@api/services/_shared";
import type {
  PerfAttrSecType,
  PerfAttrCodeRow,
  PerfAttrCodesResponse,
} from "@shared/types";
import {
  PERF_ATTR_NAME_TABLE,
  PERF_ATTR_META_TABLE,
  PERF_ATTR_META_TYPE,
  resolvePerfAttrCodeSource,
  buildPerfAttrMetaSql,
  type DbPerfAttrMetaRow,
} from "./_shared.js";

interface DbPerfAttrCodeRow extends QueryResultRow {
  code: string;
  name: string;
  first_date: Date | string;
  last_date: Date | string;
  n_dates: number;
  benchmarks: string[];
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
