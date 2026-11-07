/**
 * Performance Attribution paired history — getPerfAttrChart, one (subject,
 * benchmark) pair's daily series: correlation windows, shared weights'
 * liquidity ratio (MA5 window), closes, and the ETFs linked to each side.
 * Split out of the former monolithic perf-attribution.ts.
 */
import { formatDate, toNum } from "@api/lib/db";
import { querySqlView, sqlFrag } from "@api/lib/sql-views";
import type { QueryResultRow } from "pg";
import { codeVariants } from "@api/lib/classify-etf";
import { stripped } from "@api/services/analysis/_shared";
import type {
  PerfAttrSecType,
  PerfAttrChartResponse,
} from "@shared/types";
import { PERF_ATTR_NAME_TABLE } from "./_shared.js";

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
 *  above but keyed on the perf-attr table alias `a` (not `d`). Passed to the
 *  paired_history sql view as fragment args. */
export const PERF_ATTR_SUBJECT_SOURCE: Record<PerfAttrSecType, {
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
    // The ETF liquidity columns are derived at READ time from
    // stats.index_exts.total_etf_trading_amount (cross_stats no longer
    // stores them — consolidated away 2026-09-07): ratio = bench/code,
    // NULL when either amount is NULL/0 or |ratio| >= 1e6 (the former
    // NUMERIC(10,4) build cap); MA5 = 5-trading-day trailing mean of the
    // ratio (NULLs skipped — pandas rolling(5, min_periods=1) parity from
    // the former build-time computation). The SELECT lives in the sql view
    // data_viz/api/services/analysis/perf-attribution/chart/paired_history.
    querySqlView<DbPerfAttrChartRow>(
      "data_viz/api/services/analysis/perf-attribution/chart/paired_history",
      {
        sec_type: secType,
        codes: codeVariants(target),
        benchmark_code: benchmarkCode,
        subject_price_expr: sqlFrag(subjSrc.priceExpr),
        subject_join_clause: sqlFrag(subjSrc.joinClause),
      },
    ),
    querySqlView<{ name: string | null }>(
      "data_viz/api/services/analysis/perf-attribution/chart/subject_name",
      { codes: codeVariants(target), name_table: sqlFrag(nameTable) },
    ),
    querySqlView<{ name: string | null }>(
      "data_viz/api/services/analysis/perf-attribution/chart/benchmark_name",
      { code: benchmarkCode },
    ),
    // ETFs tracking the benchmark index (parent_index_code = benchmark_code).
    querySqlView<{ code: string; name: string | null }>(
      "data_viz/api/services/analysis/perf-attribution/chart/linked_etfs",
      { parent_index_code: benchmarkCode },
    ),
    // ETFs tracking the subject index (parent_index_code = code). For ETF
    // subjects (sec_type='etf') this returns nothing — the subject IS the ETF.
    querySqlView<{ code: string; name: string | null }>(
      "data_viz/api/services/analysis/perf-attribution/chart/linked_etfs",
      { parent_index_code: target },
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
