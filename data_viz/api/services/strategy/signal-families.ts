/**
 * The Singleton Strategy SIGNAL TAGS menu — the analysis signal families
 * with recorded standalone-tier events for a sec_type.
 *
 * Signals-picked-up era (2026-11-07): the strategy tier never detects —
 * it picks up `analysis_signals.history_signals` events by family. The
 * tag menu is therefore DATA-DRIVEN from those events (a family appears
 * once its events exist), not a hardcoded algo list.
 */
import { querySqlView, sqlFrag } from "../../lib/sql-views.js";
import { labelDecodeSql, LABEL_CODE } from "../infra/label-codes.js";
import type { MaSpreadSecType } from "../../../shared/types.js";

/** One signal family tag: name + evidence footprint for the menu. */
export interface SignalFamilyRow {
  /** The static.label_dim 'signal_type' label — also the Python algo
   *  name (registry.get_algo resolves every family) and the DB
   *  strategy_name for a single-family run. */
  name: string;
  /** Recorded standalone-tier events for this sec_type. */
  n_events: number;
  /** Distinct codes with events. */
  n_codes: number;
  /** Newest event date (the data horizon). */
  latest_date: string | null;
}

// The SELECT lives in the sql view
// data_viz/api/services/strategy/signal-families/signal_families.

/**
 * Fetch the selectable signal families for one sec_type — the families
 * with at least one recorded standalone-tier event (wait rows and the
 * hedge_pairs portfolio family excluded in SQL).
 */
export async function fetchSignalFamilies(
  secType: MaSpreadSecType,
): Promise<SignalFamilyRow[]> {
  // the stored columns are SMALLINT label codes (the label-code
  // contract) — the filter carries the codes, the response decodes the
  // family name back to the label the UI / Python algo names speak.
  const rows = await querySqlView<SignalFamilyRow>(
    "data_viz/api/services/strategy/signal-families/signal_families",
    {
      signal_type_decode: sqlFrag(
        labelDecodeSql("signal_type", "h.signal_type")),
      sec_type_code: sqlFrag(String(LABEL_CODE.sec_type[secType])),
      wait_code: sqlFrag(String(LABEL_CODE.action.wait)),
      hedge_pairs_code: sqlFrag(String(LABEL_CODE.signal_type.hedge_pairs)),
    },
  );
  return rows;
}
