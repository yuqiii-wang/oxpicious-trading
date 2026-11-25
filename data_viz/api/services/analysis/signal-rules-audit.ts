/**
 * Signal Rules Audit (composite analysis) — the per-family census
 * (analysis_signals.signal_type_audit) and the pairwise family overlap
 * (analysis_signals.signal_overlap_audit) over the standalone trading
 * tier of analysis_signals.history_signals.
 *
 * Source: built by `python -m analysis.signal_rules_audit`; the UI
 * refresh runs it via POST /api/analysis/signals-audit/run. Periods are
 * calendar years — the per-year views read the stored rows, the "all"
 * views SUM-re-aggregate (counts are additive across the disjoint year
 * buckets; ratios re-derive from the sums).
 */
import { formatDate, toNum } from "../../lib/db.js";
import { querySqlView, sqlFrag } from "@api/lib/sql-views";
import { encodeLabelParam, labelDecodeSql } from "../infra/label-codes.js";
import type { QueryResultRow } from "pg";
import type {
  SignalAuditMetaResponse,
  SignalAuditPeriodRow,
  SignalOverlapAuditResponse,
  SignalOverlapAuditRow,
  SignalTypeAuditResponse,
  SignalTypeAuditRow,
} from "../../../shared/types.js";

/** The audit's supported security scopes (the pipeline's --sec-type). */
const SEC_TYPE_CHOICES = ["index", "etf", "stock"] as const;

function resolveSecType(raw: string | undefined): string {
  const s = (raw ?? "").trim();
  return (SEC_TYPE_CHOICES as readonly string[]).includes(s) ? s : "index";
}

function secTypeCode(raw: string | undefined): number {
  const code = encodeLabelParam("sec_type", resolveSecType(raw));
  return code ?? 1; // index
}

/** `period` request param → the calendar year, or "all". */
function resolvePeriod(raw: string | undefined): number | "all" {
  const s = (raw ?? "").trim().toLowerCase();
  if (s === "" || s === "all") return "all";
  const n = Number.parseInt(s, 10);
  return Number.isFinite(n) && n >= 1970 && n <= 2999 ? n : "all";
}

function overlapViewName(period: number | "all"): string {
  return period === "all"
    ? "data_viz/api/services/analysis/signal-rules-audit/overlap-all"
    : "data_viz/api/services/analysis/signal-rules-audit/overlap-year";
}

function summaryViewName(period: number | "all"): string {
  return period === "all"
    ? "data_viz/api/services/analysis/signal-rules-audit/summary-all"
    : "data_viz/api/services/analysis/signal-rules-audit/summary-year";
}

// ----------------------------------------------------------------------------
//  GET meta — the period selector basis: every YEAR period present in the
//  census at the scope, with bounds / universe / family + event totals.
// ----------------------------------------------------------------------------
interface DbMetaRow extends QueryResultRow {
  period: number;
  period_start: Date | string;
  period_end: Date | string;
  signal_universe: string | number;
  n_families: number;
  n_events: string | number;
  computed_at: Date | string;
}

export async function getSignalAuditMeta(
  rawSecType: string | undefined,
): Promise<SignalAuditMetaResponse> {
  const secType = resolveSecType(rawSecType);
  const rows = await querySqlView<DbMetaRow>(
    "data_viz/api/services/analysis/signal-rules-audit/meta",
    { sec_type: secTypeCode(rawSecType) },
  );

  const periods: SignalAuditPeriodRow[] = rows.map((r) => ({
    period: r.period,
    period_start: formatDate(r.period_start),
    period_end: formatDate(r.period_end),
    signal_universe: toNum(r.signal_universe) ?? 0,
    n_families: r.n_families,
    n_events: toNum(r.n_events) ?? 0,
    computed_at: new Date(r.computed_at).toISOString(),
  }));

  return { sec_type: secType, rows: periods };
}

// ----------------------------------------------------------------------------
//  GET summary — one row per family per period (or the all-history sum).
// ----------------------------------------------------------------------------
interface DbSummaryRow extends QueryResultRow {
  period?: number;
  period_start: Date | string;
  period_end: Date | string;
  signal_type: number;
  signal_type_label: string | null;
  n_events: string | number;
  n_buy: string | number;
  n_sell: string | number;
  n_codes: string | number;
  n_days: string | number;
  signal_universe: string | number;
  first_date: Date | string;
  last_date: Date | string;
}

export async function getSignalTypeSummary(
  rawSecType: string | undefined,
  rawPeriod: string | undefined,
): Promise<SignalTypeAuditResponse> {
  const secType = resolveSecType(rawSecType);
  const period = resolvePeriod(rawPeriod);
  const secCode = secTypeCode(rawSecType);

  const rows = await querySqlView<DbSummaryRow>(
    summaryViewName(period),
    period === "all"
      ? {
          sec_type: secCode,
          signal_type_decode: sqlFrag(
            labelDecodeSql("signal_type", "p.signal_type"),
          ),
        }
      : {
          sec_type: secCode,
          period,
          signal_type_decode: sqlFrag(
            labelDecodeSql("signal_type", "t.signal_type"),
          ),
        },
  );

  const summary: SignalTypeAuditRow[] = rows.map((r) => ({
    period: period === "all" ? 0 : (r.period ?? period),
    period_start: formatDate(r.period_start),
    period_end: formatDate(r.period_end),
    signal_type: r.signal_type,
    signal_type_label: r.signal_type_label,
    n_events: toNum(r.n_events) ?? 0,
    n_buy: toNum(r.n_buy) ?? 0,
    n_sell: toNum(r.n_sell) ?? 0,
    n_codes: toNum(r.n_codes) ?? 0,
    n_days: toNum(r.n_days) ?? 0,
    signal_universe: toNum(r.signal_universe) ?? 0,
    first_date: formatDate(r.first_date),
    last_date: formatDate(r.last_date),
  }));

  return { sec_type: secType, period, enable_filters: true, rows: summary };
}

// ----------------------------------------------------------------------------
//  GET overlap — one row per ordered pair per period (or the all-history
//  sum with re-derived ratios).
// ----------------------------------------------------------------------------
interface DbOverlapRow extends QueryResultRow {
  period?: number;
  period_start: Date | string;
  period_end: Date | string;
  signal_type: number;
  signal_type_label: string | null;
  other_signal_type: number;
  other_signal_type_label: string | null;
  n_base: string | number;
  n_other: string | number;
  n_universe: string | number;
  n_overlap: string | number;
  n_same_action: string | number;
  n_opposite_action: string | number;
  overlap_pct: string | number | null;
  expected_pct: string | number | null;
  jaccard: string | number | null;
  lift: string | number | null;
}

export async function getSignalOverlap(
  rawSecType: string | undefined,
  rawPeriod: string | undefined,
): Promise<SignalOverlapAuditResponse> {
  const secType = resolveSecType(rawSecType);
  const period = resolvePeriod(rawPeriod);
  const secCode = secTypeCode(rawSecType);

  const decodeFragments =
    period === "all"
      ? {
          sec_type: secCode,
          signal_type_decode: sqlFrag(
            labelDecodeSql("signal_type", "o.signal_type"),
          ),
          other_signal_type_decode: sqlFrag(
            labelDecodeSql("signal_type", "o.other_signal_type"),
          ),
        }
      : {
          sec_type: secCode,
          period,
          signal_type_decode: sqlFrag(
            labelDecodeSql("signal_type", "o.signal_type"),
          ),
          other_signal_type_decode: sqlFrag(
            labelDecodeSql("signal_type", "o.other_signal_type"),
          ),
        };

  const rows = await querySqlView<DbOverlapRow>(
    overlapViewName(period),
    decodeFragments,
  );

  const pairs: SignalOverlapAuditRow[] = rows.map((r) => ({
    period: period === "all" ? 0 : (r.period ?? period),
    period_start: formatDate(r.period_start),
    period_end: formatDate(r.period_end),
    signal_type: r.signal_type,
    signal_type_label: r.signal_type_label,
    other_signal_type: r.other_signal_type,
    other_signal_type_label: r.other_signal_type_label,
    n_base: toNum(r.n_base) ?? 0,
    n_other: toNum(r.n_other) ?? 0,
    n_universe: toNum(r.n_universe) ?? 0,
    n_overlap: toNum(r.n_overlap) ?? 0,
    n_same_action: toNum(r.n_same_action) ?? 0,
    n_opposite_action: toNum(r.n_opposite_action) ?? 0,
    overlap_pct: toNum(r.overlap_pct) ?? 0,
    expected_pct: toNum(r.expected_pct) ?? 0,
    jaccard: toNum(r.jaccard) ?? 0,
    lift: toNum(r.lift) ?? 0,
  }));

  return { sec_type: secType, period, enable_filters: true, rows: pairs };
}
