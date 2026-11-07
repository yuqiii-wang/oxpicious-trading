/**
 * Check if a strategy run already exists for the natural key.
 * Used by the UI to prompt the user before forcing a re-run.
 */
import { querySqlView } from "@api/lib/sql-views";
import type { MaSpreadSecType } from "../../../shared/types.js";
import { DEFAULT_STRATEGY_NAME } from "./_shared.js";

interface CheckExistingRow {
  seq_id: number;
  seq_no: number;
  start_date: string;
  end_date: string | null;
  scenario: string | null;
  status: string;
  fault_tolerance: string | number | null;
}

// The SELECT lives in the sql view
// data_viz/api/services/strategy/check-existing/check_existing.

/**
 * Check if a strategy_identity row already exists for the given
 * (strategy_name, sec_type, code) natural key. Returns the existing row
 * metadata if found, or null if no run exists yet.
 *
 * Used by the UI to decide whether to show a "force re-run" confirmation
 * modal before spawning the Python backtest.
 */
export async function checkExistingStrategy(
  rawCode: string,
  rawSecType: string | undefined | null,
  strategyName: string = DEFAULT_STRATEGY_NAME,
): Promise<CheckExistingRow | null> {
  const secType = (rawSecType as MaSpreadSecType) ?? "index";
  const code = rawCode.trim();
  const rows = await querySqlView<CheckExistingRow>(
    "data_viz/api/services/strategy/check-existing/check_existing",
    { strategy_name: strategyName, sec_type: secType, code },
  );
  return rows.length > 0 ? rows[0] : null;
}
