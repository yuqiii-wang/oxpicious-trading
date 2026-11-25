/**
 * API client for the Signal Rules Audit composite — reads the per-family
 * census (analysis_signals.signal_type_audit) and the pairwise family
 * overlap (analysis_signals.signal_overlap_audit).
 */
import { fetchJson } from "./_cache";
import type { AnalysisRunResponse } from "./analysis-run";
import type {
  SignalAuditMetaResponse,
  SignalOverlapAuditResponse,
  SignalTypeAuditResponse,
} from "@shared/types";

/** `period` values accepted by the summary/overlap endpoints: a calendar
 *  year, or "all" (the all-history SUM re-aggregation). */
export type SignalAuditPeriod = number | "all";

/** Meta — the YEAR periods present at the scope (the period selector). */
export function fetchSignalAuditMeta(
  secType = "index",
): Promise<SignalAuditMetaResponse> {
  const params = new URLSearchParams();
  params.set("sec_type", secType);
  const qs = params.toString();
  return fetchJson<SignalAuditMetaResponse>(
    `/api/analysis/signals-audit/meta?${qs}`,
  );
}

/** Per-family census rows for one period (year or "all"). */
export function fetchSignalTypeSummary(
  period: SignalAuditPeriod,
  secType = "index",
): Promise<SignalTypeAuditResponse> {
  const params = new URLSearchParams();
  params.set("sec_type", secType);
  params.set("period", String(period));
  const qs = params.toString();
  return fetchJson<SignalTypeAuditResponse>(
    `/api/analysis/signals-audit/summary?${qs}`,
  );
}

/** Ordered-pair overlap rows for one period (year or "all"). */
export function fetchSignalOverlap(
  period: SignalAuditPeriod,
  secType = "index",
): Promise<SignalOverlapAuditResponse> {
  const params = new URLSearchParams();
  params.set("sec_type", secType);
  params.set("period", String(period));
  const qs = params.toString();
  return fetchJson<SignalOverlapAuditResponse>(
    `/api/analysis/signals-audit/overlap?${qs}`,
  );
}

// ---------------------------------------------------------------------------
//  Refresh trigger.
//
//  POST /api/analysis/signals-audit/run spawns
//  `python -m analysis.signal_rules_audit` (incremental: missing +
//  trailing + grace years) via the shared py-runner and waits for it to
//  finish. Drives the refresh button on the Composites → Signal Rules
//  Audit page. Deduped by a fixed process-id-tag — poll
//  fetchAnalysisRunStatus([SIGNAL_RULES_AUDIT_RUN_TAG]) for the spinner.
//  Never throws; failures surface as { success: false, stderr_tail }.
// ---------------------------------------------------------------------------
/** Fixed process-id-tag for UI-triggered signal-rules-audit runs — must
 *  match the server-side SIGNAL_RULES_AUDIT_RUN_TAG. */
export const SIGNAL_RULES_AUDIT_RUN_TAG = "analysis-run:signal_rules_audit";

/** Trigger an incremental signal-rules-audit run. Resolves when the run
 *  finishes (or is deduped — already_running). */
export async function runSignalRulesAuditRefresh(): Promise<AnalysisRunResponse> {
  try {
    const res = await fetch("/api/analysis/signals-audit/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    if (!res.ok) {
      return { success: false, stderr_tail: `HTTP ${res.status}` };
    }
    return (await res.json()) as AnalysisRunResponse;
  } catch (e) {
    return { success: false, stderr_tail: String(e) };
  }
}
