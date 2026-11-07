/**
 * Pair Pct-Change Ratios — INDUSTRY x INDUSTRY excess pct change
 * (mean daily difference), stride-grid values, from
 * analysis.pair_pct_change_ratios (grain 1 only,
 * static.label_dim 'pair_grain'; the per-index grain was dropped in the
 * 2026-10-17 industry-vs-industry rework).
 *
 * Both pair sides are INDUSTRY AGGREGATE pct curves ("pct vs pct"):
 * ind_pct(i, d) is the null-skipped mean of the industry's member
 * indexes' own-calendar daily pct changes (missing member days are
 * skipped, never zero-filled). The daily measure is the DIFFERENCE
 * diff(d) = ind_pct(code, d) − ind_pct(benchmark_code, d) (excess pct
 * change; e.g. A −1%, −2%, +3% vs B +1%, +1%, +1.5% → −2%, −3%,
 * +1.5%), and the stored value is the trailing 5/20/60/255-day MEAN
 * of the daily differences — no division anywhere (quotient forms
 * were numerically fragile on near-flat denominators). A day missing
 * on either side is null data — skipped by the mean, never
 * zero-filled; a window with >10% null days emits NULL. Values
 * materialize ONLY on stride-grid dates of the global index calendar
 * (default 20 — the corr materialization pattern). Units: pct-change
 * points — >0 = the subject industry outmoves the benchmark upward
 * over the window, <0 = undermoves it or moves opposite.
 *
 * Subjects = benchmarks = ALL industries with active type='index'
 * members carrying closes.
 *
 * Source: analysis.pair_pct_change_ratios (built by
 * `python -m analysis.pair_pct_change_ratios`).
 */
import { formatDate, toNum } from "../../lib/db.js";
import { querySqlView, sqlFrag } from "@api/lib/sql-views";
import type { QueryResultRow } from "pg";
import type {
  PairPctRatioGrain,
  PairPctRatioRow,
  PairPctRatioUniverseEntry,
  PairPctRatioUniverseResponse,
  PairPctRatiosResponse,
} from "../../../shared/types.js";
import { encodeLabelParam } from "../label-codes.js";

const DEFAULT_INTERVAL = 20;
const DEFAULT_GRAIN: PairPctRatioGrain = "industry";
const DEFAULT_MARKET = "000300";

// Industry_id label: the classification's industry_label over that
// industry's member rows, falling back to the id. (Fragment builder —
// passed into the universe sql views as {{subject_label}} /
// {{benchmark_label}}.)
const INDUSTRY_LABEL_SQL = (
  codeExpr: string,
) => `(
  SELECT COALESCE(NULLIF(MAX(sc2.industry_label), ''), ${codeExpr})
  FROM stats.sec_classification sc2
  WHERE sc2.type = 'index' AND sc2.industry_id = ${codeExpr}
) AS label`;

interface DbUniverseRow extends QueryResultRow {
  code: string;
  label: string | null;
  n_counterparts: string | number;
  first_date: Date | string;
  last_date: Date | string;
}

// ----------------------------------------------------------------------------
//  GET universe — both sides of the pair selector, BOTH industries
//  (the pair is industry x industry; grain 1 rows only): subject
//  industries and benchmark industries, each with counterpart counts +
//  grid date bounds. Optional industry_ids scopes BOTH sides to those
//  industry_ids — the page scopes the selector to its already-selected
//  industries.
// ----------------------------------------------------------------------------
export async function listPairPctRatioUniverse(
  rawInterval: string,
  rawIndustryIds: string[],
): Promise<PairPctRatioUniverseResponse> {
  const interval = Number.parseInt(rawInterval, 10);
  const stride = Number.isFinite(interval) && interval >= 1 ? interval : DEFAULT_INTERVAL;
  const industryIds = Array.from(
    new Set(
      (rawIndustryIds ?? [])
        .map((s) => (s ?? "").trim())
        .filter((s) => s.length > 0),
    ),
  );

  // Inner GROUP BYs aggregate the ratio table once per side; the UNION
  // ALL stitches the result shapes — no pairwise fan-out (empty filter →
  // no scope fragments, full industry universe). The SELECT lives in the
  // sql views data_viz/api/services/analysis/pair-pct-ratios/universe
  // (unscoped) and .../universe_scoped (an industry_ids filter present).
  const strideArg = { stride };
  const labelArgs = {
    subject_label: sqlFrag(INDUSTRY_LABEL_SQL("s.code")),
    benchmark_label: sqlFrag(INDUSTRY_LABEL_SQL("b.code")),
  };
  const rows = await (industryIds.length > 0
    ? querySqlView<DbUniverseRow & { kind: "subject" | "benchmark" }>(
        "data_viz/api/services/analysis/pair-pct-ratios/universe_scoped",
        { ...strideArg, industry_ids: industryIds, ...labelArgs },
      )
    : querySqlView<DbUniverseRow & { kind: "subject" | "benchmark" }>(
        "data_viz/api/services/analysis/pair-pct-ratios/universe",
        { ...strideArg, ...labelArgs },
      ));

  const toEntry = (r: DbUniverseRow): PairPctRatioUniverseEntry => ({
    code: r.code,
    label: r.label ?? r.code,
    grain: "industry",
    n_counterparts: Number(r.n_counterparts ?? 0),
    first_date: formatDate(r.first_date),
    last_date: formatDate(r.last_date),
  });
  const of = (kind: DbUniverseRow["kind"]) => rows.filter((r) => r.kind === kind);
  const byCode = (a: PairPctRatioUniverseEntry, b: PairPctRatioUniverseEntry) =>
    a.code.localeCompare(b.code);
  return {
    codes: of("subject").map(toEntry).sort(byCode),
    benchmarks: of("benchmark").map(toEntry).sort(byCode),
    interval: stride,
    industry_ids: industryIds,
  };
}

// ----------------------------------------------------------------------------
//  GET rows — the hedge ratios (both modes, four windows each) for ONE
//  industry x industry pair against ONE offset market (PK-prefix scan:
//  code, benchmark_code, benchmark_market, interval, grain).
// ----------------------------------------------------------------------------
interface DbRatioRow extends QueryResultRow {
  date: Date | string;
  hedge_raw_ma5d: string | number | null;
  hedge_raw_ma20d: string | number | null;
  hedge_raw_ma60d: string | number | null;
  hedge_raw_ma255d: string | number | null;
  hedge_offset_ma5d: string | number | null;
  hedge_offset_ma20d: string | number | null;
  hedge_offset_ma60d: string | number | null;
  hedge_offset_ma255d: string | number | null;
}

export async function getPairPctRatios(
  rawCode: string,
  rawBenchmark: string,
  rawInterval: string,
  rawGrain: string,
  rawMarket: string,
): Promise<PairPctRatiosResponse> {
  const code = (rawCode ?? "").trim();
  const benchmark = (rawBenchmark ?? "").trim();
  if (!code || !benchmark) {
    throw new Error("Both 'code' and 'benchmark' are required.");
  }
  if (code === benchmark) {
    throw new Error("'code' and 'benchmark' must differ (self-pairs excluded).");
  }
  const interval = Number.parseInt(rawInterval, 10);
  const stride = Number.isFinite(interval) && interval >= 1 ? interval : DEFAULT_INTERVAL;
  const grain = encodeLabelParam("pair_grain", rawGrain) === null
    ? DEFAULT_GRAIN
    : (rawGrain as PairPctRatioGrain);
  const grainCode = encodeLabelParam("pair_grain", grain);
  const market = (rawMarket ?? "").trim() || DEFAULT_MARKET;

  const rows = await querySqlView<DbRatioRow>(
    "data_viz/api/services/analysis/pair-pct-ratios/pair_rows",
    {
      code,
      benchmark,
      market,
      stride,
      grain: grainCode,
    },
  );

  const out: PairPctRatioRow[] = rows.map((r) => ({
    date: formatDate(r.date),
    hedge_raw_ma5d: toNum(r.hedge_raw_ma5d),
    hedge_raw_ma20d: toNum(r.hedge_raw_ma20d),
    hedge_raw_ma60d: toNum(r.hedge_raw_ma60d),
    hedge_raw_ma255d: toNum(r.hedge_raw_ma255d),
    hedge_offset_ma5d: toNum(r.hedge_offset_ma5d),
    hedge_offset_ma20d: toNum(r.hedge_offset_ma20d),
    hedge_offset_ma60d: toNum(r.hedge_offset_ma60d),
    hedge_offset_ma255d: toNum(r.hedge_offset_ma255d),
  }));
  return {
    code, benchmark_code: benchmark, benchmark_market: market,
    interval: stride, grain, rows: out,
  };
}
