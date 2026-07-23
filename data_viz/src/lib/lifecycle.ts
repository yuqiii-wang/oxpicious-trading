/**
 * PBoC outright repo / MLF lifecycle computation.
 * Ported from compute_outright_repo_lifecycle() in plot_debt_baseline.py.
 *
 * Given the rows from debt_baseline.csv, compute the start/end injection legs
 * for each outright repo + MLF tender (using tenor_days), then group by date
 * and cumsum to get a cumulative balance curve.
 */
import type { DebtBaselineRow } from "../../shared/types";
import { addDays } from "./series";

export interface RepoLeg {
  date: string;
  outright_start: number;
  outright_end: number;
  mlf_start: number;
  mlf_end: number;
}

export interface LifecycleRow extends RepoLeg {
  outright_net: number;
  outright_cumulative: number;
}

/**
 * Compute outright repo + MLF lifecycle. `rows` must be sorted by date ascending
 * and should span the full history (not just the filtered range), otherwise the
 * cumulative will be wrong.
 */
export function computeOutrightRepoLifecycle(rows: DebtBaselineRow[]): LifecycleRow[] {
  const legs: RepoLeg[] = [];

  // Outright repo tenders
  for (const r of rows) {
    if (r.outright_repo_marker !== 1) continue;
    const qty = r.outright_repo_quantity;
    const tenorDays = r.outright_repo_tenor_days;
    if (qty != null && tenorDays != null && qty > 0) {
      const startDate = r.date;
      const endDate = addDays(startDate, Math.round(tenorDays));
      legs.push({
        date: startDate,
        outright_start: qty,
        outright_end: 0,
        mlf_start: 0,
        mlf_end: 0,
      });
      legs.push({
        date: endDate,
        outright_start: 0,
        outright_end: -qty,
        mlf_start: 0,
        mlf_end: 0,
      });
    }
  }

  // MLF operations
  for (const r of rows) {
    if (r.mlf_marker !== 1) continue;
    const qty = r.mlf_quantity;
    const tenorDays = r.mlf_tenor_days;
    if (qty != null && tenorDays != null && qty > 0) {
      const startDate = r.date;
      const endDate = addDays(startDate, Math.round(tenorDays));
      legs.push({
        date: startDate,
        outright_start: 0,
        outright_end: 0,
        mlf_start: qty,
        mlf_end: 0,
      });
      legs.push({
        date: endDate,
        outright_start: 0,
        outright_end: 0,
        mlf_start: 0,
        mlf_end: -qty,
      });
    }
  }

  // Build per-date aggregation
  const byDate = new Map<string, RepoLeg>();
  for (const leg of legs) {
    const existing = byDate.get(leg.date) ?? {
      date: leg.date,
      outright_start: 0,
      outright_end: 0,
      mlf_start: 0,
      mlf_end: 0,
    };
    existing.outright_start += leg.outright_start;
    existing.outright_end += leg.outright_end;
    existing.mlf_start += leg.mlf_start;
    existing.mlf_end += leg.mlf_end;
    byDate.set(leg.date, existing);
  }

  // Merge onto the full date range from rows
  const out: LifecycleRow[] = [];
  let cumulative = 0;
  for (const r of rows) {
    const leg = byDate.get(r.date);
    const outright_start = leg?.outright_start ?? 0;
    const outright_end = leg?.outright_end ?? 0;
    const mlf_start = leg?.mlf_start ?? 0;
    const mlf_end = leg?.mlf_end ?? 0;
    const net = outright_start + outright_end + mlf_start + mlf_end;
    cumulative += net;
    out.push({
      date: r.date,
      outright_start,
      outright_end,
      mlf_start,
      mlf_end,
      outright_net: net,
      outright_cumulative: cumulative,
    });
  }
  return out;
}

/**
 * Extract outright-repo + MLF marker events for vertical line annotations.
 */
export interface MarkerEvent {
  date: string;
  quantity: number;
  tenor_label: string;
  tenor_days: number | null;
  serial: string;
  type: "outright_repo" | "MLF";
}

export function getRepoMarkers(rows: DebtBaselineRow[]): MarkerEvent[] {
  const out: MarkerEvent[] = [];
  for (const r of rows) {
    if (r.outright_repo_marker === 1) {
      out.push({
        date: r.date,
        quantity: r.outright_repo_quantity ?? 0,
        tenor_label: r.outright_repo_tenor_label,
        tenor_days: r.outright_repo_tenor_days,
        serial: r.outright_repo_serial,
        type: "outright_repo",
      });
    }
    if (r.mlf_marker === 1) {
      out.push({
        date: r.date,
        quantity: r.mlf_quantity ?? 0,
        tenor_label: r.mlf_tenor_label,
        tenor_days: r.mlf_tenor_days,
        serial: r.mlf_serial,
        type: "MLF",
      });
    }
  }
  return out.sort((a, b) => a.date.localeCompare(b.date));
}
