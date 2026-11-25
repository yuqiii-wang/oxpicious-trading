/**
 * Check if a strategy run already exists for the natural key.
 * Also reports staleness — the run's end_date vs the code's analysis
 * horizon — which the UI uses to force-rerun stale runs WITHOUT the
 * confirmation modal (user-directed 2026-11-25).
 */
import { querySqlView } from "@api/lib/sql-views";
import type { MaSpreadSecType } from "../../../shared/types.js";
import { LABEL_CODE } from "../infra/label-codes.js";
import { DEFAULT_STRATEGY_NAME } from "./_shared.js";

interface CheckExistingRow {
  seq_id: number;
  seq_no: number;
  start_date: string;
  end_date: string | null;
  scenario: string | null;
  status: string;
  fault_tolerance: string | number | null;
  /** The code's latest analysis row date (mov_ave_spreads_detail —
   *  the same horizon the strategy runner trims to). */
  analysis_latest_date: string | null;
  /** True when the run's end_date ends before the analysis data does —
   *  a stale run; the UI force-reruns it without the confirm modal. */
  stale: boolean | null;
}

// The SELECT lives in the sql view
// strategy/check-existing/check_existing.

/**
 * Check if a strategy_identity row already exists for the given
 * (strategy_name, sec_type, code) natural key. Returns the existing row
 * metadata if found, or null if no run exists yet.
 *
 * Used by the UI to decide between a direct force re-run (stale run —
 * its rows end before the code's analysis horizon) and the "force
 * re-run" confirmation modal (fresh run).
 */
export async function checkExistingStrategy(
  rawCode: string,
  rawSecType: string | undefined | null,
  strategyName: string = DEFAULT_STRATEGY_NAME,
): Promise<CheckExistingRow | null> {
  const secType = (rawSecType as MaSpreadSecType) ?? "index";
  const code = rawCode.trim();
  const secTypeCode = LABEL_CODE.sec_type[secType] ?? LABEL_CODE.sec_type.index;
  const rows = await querySqlView<CheckExistingRow>(
    "strategy/check-existing/check_existing",
    {
      strategy_name: strategyName,
      sec_type: secType,
      code,
      sec_type_code: secTypeCode,
    },
  );
  return rows.length > 0 ? rows[0] : null;
}
