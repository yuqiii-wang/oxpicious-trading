/**
 * Run history — lists the recent strategy_identity runs for one
 * (sec_type, code, strategy_name), newest first. Old runs are retained
 * with is_active=FALSE, so each row is a selectable past run; `params`
 * carries that run's config (incl. the comb block on portfolio runs).
 * Serves GET /api/strategy/singleton/run-history.
 */
import { toNum } from "../infra/db.service.js";
import { querySqlView } from "@api/lib/sql-views";
import type { MaSpreadSecType } from "../../../shared/types.js";
import { DEFAULT_STRATEGY_NAME } from "./_shared.js";

export interface RunHistoryItem {
  seq_id: number;
  seq_no: number;
  strategy_name: string;
  sec_type: MaSpreadSecType;
  code: string;
  start_date: string | null;
  end_date: string | null;
  /** The run's stored config (params JSONB): trading keys + the comb
   *  block + params_version on portfolio runs. */
  params: Record<string, unknown>;
  scenario: string | null;
  status: string | null;
  fault_tolerance: number;
  is_active: boolean;
  created_at: string | null;
}

export interface RunHistoryResponse {
  code: string;
  sec_type: MaSpreadSecType;
  strategy_name: string;
  runs: RunHistoryItem[];
}

interface RunHistoryRow {
  seq_id: number;
  seq_no: number;
  strategy_name: string;
  sec_type: string;
  code: string;
  start_date: string | Date | null;
  end_date: string | Date | null;
  params: unknown;
  scenario: string | null;
  status: string | null;
  fault_tolerance: string | number;
  is_active: boolean;
  created_at: string | Date | null;
}

// JSONB cells arrive as JSON strings (no pg codec registered in this app).
function parseJsonObj(v: unknown): Record<string, unknown> | null {
  if (v === null || v === undefined) return null;
  const parsed = typeof v === "string" ? JSON.parse(v) : v;
  return parsed && typeof parsed === "object" ? (parsed as Record<string, unknown>) : null;
}

function fmtDate(v: unknown): string | null {
  if (v === null || v === undefined) return null;
  const d = new Date(String(v));
  return Number.isNaN(d.getTime()) ? String(v) : d.toISOString().slice(0, 10);
}

function fmtTs(v: unknown): string | null {
  if (v === null || v === undefined) return null;
  const d = v instanceof Date ? v : new Date(String(v));
  return Number.isNaN(d.getTime()) ? String(v) : d.toISOString();
}

function mapRun(r: RunHistoryRow): RunHistoryItem {
  return {
    seq_id: r.seq_id,
    seq_no: r.seq_no,
    strategy_name: r.strategy_name,
    sec_type: r.sec_type as MaSpreadSecType,
    code: r.code,
    start_date: fmtDate(r.start_date),
    end_date: fmtDate(r.end_date),
    params: parseJsonObj(r.params) ?? {},
    scenario: r.scenario,
    status: r.status,
    fault_tolerance: toNum(r.fault_tolerance) ?? 0,
    is_active: r.is_active,
    created_at: fmtTs(r.created_at),
  };
}

export async function fetchRunHistory(
  rawCode: string,
  rawSecType: string | undefined | null,
  strategyName: string = DEFAULT_STRATEGY_NAME,
): Promise<RunHistoryResponse> {
  const secType = (rawSecType as MaSpreadSecType) ?? "index";
  const rows = await querySqlView<RunHistoryRow>(
    "strategy/run-history/runs",
    { sec_type: secType, code: rawCode, strategy_name: strategyName },
  );
  return {
    code: rawCode,
    sec_type: secType,
    strategy_name: strategyName,
    runs: rows.map(mapRun),
  };
}
