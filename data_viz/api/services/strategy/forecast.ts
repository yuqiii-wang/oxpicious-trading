/**
 * The Strategy → Forecast service — the σ-ladder scenario register's
 * read + run.
 *
 * GET side: strategy.forecast_next_day rows (labels decoded SQL-side
 * through static.label_dim), bounded to one code / one anchor or the
 * LATEST anchor per code — the page's default view.
 *
 * RUN side: submits `python -m strategy.forecast_next_day` to the
 * server-cluster STRATEGIST (strategy.* module prefix → 127.0.0.1:8772)
 * via the shared py-runner service — no local spawn; resolves at the
 * cluster accept, the outcome is polled via the shared
 * /singleton/process-status + /singleton/process-result endpoints
 * under the same process-id-tag (the Run Strategy / Train Model
 * pattern).
 */
import { querySqlView, sqlFrag } from "../../lib/sql-views.js";
import { labelDecodeSql, LABEL_CODE } from "../infra/label-codes.js";
import {
  submitPythonModule,
  type SubmitScriptResult,
} from "../infra/py-runner.service.js";
import type {
  ForecastNextDayResponse,
  ForecastNextDayRow,
  MaSpreadSecType,
} from "../../../shared/types.js";

// The SELECT lives in the sql view
// strategy/forecast/forecast.

/**
 * Fetch the forecast register rows for one sec_type — one code's
 * ladder when `code` is given, else every code's latest anchor.
 */
export async function fetchForecast(
  secType: MaSpreadSecType,
  code: string | null,
  anchorDate: string | null,
): Promise<ForecastNextDayResponse> {
  // the stored vocabularies are SMALLINT label codes — decode SQL-side
  // through static.label_dim (never CASE), the filter carries the code
  const rows = await querySqlView<ForecastNextDayRow>(
    "strategy/forecast/forecast",
    {
      direction_decode: sqlFrag(
        labelDecodeSql("forecast_direction", "f.direction")),
      signal_type_decode: sqlFrag(
        labelDecodeSql("signal_type", "f.signal_type")),
      sub_type_decode: sqlFrag(
        labelDecodeSql("signal_sub_type", "f.signal_sub_type")),
      action_decode: sqlFrag(labelDecodeSql("action", "f.action")),
      trend_decode: sqlFrag(labelDecodeSql("trend", "f.trend_state")),
      regime_decode: sqlFrag(labelDecodeSql("regime", "f.regime_state")),
      sec_type: sqlFrag(String(LABEL_CODE.sec_type[secType])),
      code,
      anchor_date: anchorDate,
    },
  );
  return {
    sec_type: secType,
    code,
    anchor_date: anchorDate,
    rows,
  };
}

/**
 * Run Forecast — submit the forecast pipeline
 * (`python -m strategy.forecast_next_day`) to the cluster strategist.
 * The module is incremental (codes whose registered anchor matches the
 * OHLCV horizon are skipped) and AUTO-LINKS to the strategy run (it
 * spawns the nightly portfolio CLI itself when the latest active run
 * is behind the bar horizon). Returns at the cluster accept; poll
 * process-status under the returned tag.
 */
export async function runForecastScript(
  secType: MaSpreadSecType,
  processIdTag?: string,
): Promise<SubmitScriptResult> {
  return submitPythonModule(
    "strategy.forecast_next_day",
    ["--sec-type", secType],
    {
      processIdTag:
        processIdTag?.trim() || `forecast-run:${secType}`,
    },
  );
}
