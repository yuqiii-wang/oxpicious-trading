/**
 * Performance Attribution codes list — listPerfAttrCodes, one row per
 * subject code with first/last date, n_dates and the benchmark list.
 * Split out of the former monolithic perf-attribution.ts.
 */
import { formatDate } from "@api/lib/db";
import { querySqlView, sqlFrag } from "@api/lib/sql-views";
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
  fetchPerfAttrMetaRows,
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
  // historical two-CTE full-table aggregate (fallback only). The two SELECT
  // shapes live in the sql views data_viz/api/services/analysis/
  // perf-attribution/codes/codes_summary and .../codes/codes_live.
  const codesView = codeSource === "summary"
    ? "data_viz/api/services/analysis/perf-attribution/codes/codes_summary"
    : "data_viz/api/services/analysis/perf-attribution/codes/codes_live";
  const rows = await querySqlView<DbPerfAttrCodeRow>(codesView, {
    sec_type: secType,
    name_table: sqlFrag(nameTable),
  });

  // When a classification filter is active, fetch the meta rows (same query
  // as listPerfAttrThemes) and build a code → classification map so
  // matchesClassification() can decide which codes to include. Industry and
  // strategy filters are mutually exclusive (handled by matchesClassification).
  let classMap: Map<string, DbPerfAttrMetaRow> | null = null;
  if (hasClassFilter) {
    const metaTable = PERF_ATTR_META_TABLE[secType] ?? PERF_ATTR_META_TABLE.etf;
    const metaType = PERF_ATTR_META_TYPE[secType] ?? PERF_ATTR_META_TYPE.etf;
    const metaRows = await fetchPerfAttrMetaRows(metaTable, codeSource, secType, metaType);
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
