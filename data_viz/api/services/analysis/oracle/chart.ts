/**
 * Oracle chart service — listOracleCodes + getOracleSeries.
 *
 * The hindsight ORACLE tier behind the Signals → Oracle page
 * (/analysis/signals/oracle): v7 grammar — local extremes of the
 * 5-bar window scanned daily with one action PER SIDE per 5-day
 * window (a buy and a sell may sit within the same 5 rows), then the
 * GENUINE-EXTREME gate on both sides (a BUY passing the
 * genuine-deep-min test: the drawdown into the pivot from the prior
 * 20 rows' high is 5%+ — a genuine deep min always buys, even when
 * a deeper local min follows within 20 days; a shallower min is a
 * small fluctuation that omits only when a materially deeper level
 * is imminent within those 20 rows; a SELL only at a
 * genuine high: no 5%+ fall from the prior 20 rows' ceiling — a weak
 * bounce is not a high — and no close 5%+ above within the next 20
 * rows — a higher exit is imminent), then the favorable-move
 * grading: each surviving pivot graded 0-100 by its favorable move
 * on EITHER side — the weighted sum 0.3 x the backward-60-row move
 * into the pivot + 0.7 x the forward-20-row move
 * after it (user-directed 2026-11-20 "let future look only be 20d
 * not 60d"; each leg ramped 2% no-action floor / 10% full — a zero
 * weighted grade means the pivot is not in the table at all), from
 * analysis.oracle_signals (built by analysis.oracle_signals;
 * DECOUPLED from the analysis_signals rule tier — its own table, its
 * own pipeline; run scope = index only for now, the 10 study
 * indices): buy at a local minimum, sell at a local maximum, each row
 * carrying the next opposite PLACED action as its hindsight swing
 * target. The action decodes through the label
 * index ('oracle_action' domain) at the SQL boundary — no CASE.
 */
import { formatDate, toNum } from "@api/lib/db";
import { querySqlView, sqlFrag } from "@api/lib/sql-views";
import type { QueryResultRow } from "pg";
import { codeVariants } from "@api/lib/classify-etf";
import { HttpError } from "@api/lib/http";
import { stripped } from "@api/services/analysis/_shared";
import {
  LABEL_CODE,
  labelDecodeSql,
} from "@api/services/infra/label-codes";
import type {
  OracleAction,
  OracleCloseRow,
  OracleCodeRow,
  OracleCodesResponse,
  OracleRow,
  OracleSeriesResponse,
} from "@shared/types";

/** The sec_types the endpoints accept (the pipeline covers the
 *  security sec_types; the run scope is index only for now, but the
 *  endpoints accept the full set so widening needs no API change). */
const ORACLE_SEC_TYPES = ["etf", "index", "stock"] as const;
export type OracleSecType = (typeof ORACLE_SEC_TYPES)[number];

/** Parse + validate the sec_type query param (default "index"). */
export function normalizeOracleSecType(
  raw: string | undefined | null,
): OracleSecType {
  const v = (raw ?? "index").trim().toLowerCase();
  if ((ORACLE_SEC_TYPES as readonly string[]).includes(v)) {
    return v as OracleSecType;
  }
  throw HttpError.invalidParam(
    "sec_type", `expected one of ${ORACLE_SEC_TYPES.join("|")}, got '${raw}'`);
}

/** Per-sec_type identity source for the display-name lookup. */
const IDENTITY_TABLE: Record<OracleSecType, string> = {
  etf: "stats.etf_identity",
  index: "stats.index_identity",
  stock: "stats.stock_identity",
};

/** Per-sec_type OHLC bars source for the close overlay (the pipeline's
 *  own bars tables — read-only join, no new storage). */
const BARS_TABLE: Record<OracleSecType, string> = {
  etf: "stats.etf_basic_stats",
  index: "stats.index_basic_stats",
  stock: "stats.stock_basic_stats",
};

/** The forward-extreme + shape evidence columns are RETIRED with the
 *  v2 pivot grammar — the row now carries only the action, the pivot
 *  order and the swing-target legs (see @shared/types OracleRow). */

interface DbCodeRow extends QueryResultRow {
  code: string;
  name: string | null;
}

type DbSeriesRow = QueryResultRow & {
  date: Date | string;
  ext_date: Date | string | null;
  window_days: number | string | null;
  ext_offset: number | string | null;
  ext_price: number | string | null;
  confidence: number | string | null;
  close: number | string | null;
};

interface DbCloseRow extends QueryResultRow {
  date: Date | string;
  close: number | string | null;
}

export async function listOracleCodes(
  rawSecType: string | undefined | null,
): Promise<OracleCodesResponse> {
  const secType = normalizeOracleSecType(rawSecType);
  const rows = await querySqlView<DbCodeRow>(
    "data_viz/api/services/analysis/oracle/codes",
    { sec_type: LABEL_CODE.sec_type[secType] },
  );
  const codes: OracleCodeRow[] = rows
    .map((r) => ({ code: r.code, name: r.name ?? "" }))
    .sort((a, b) => a.code.localeCompare(b.code));
  return { codes };
}

export async function getOracleSeries(
  rawCode: string,
  rawSecType: string | undefined | null,
): Promise<OracleSeriesResponse> {
  const secType = normalizeOracleSecType(rawSecType);
  const target = stripped(rawCode);
  const codes = codeVariants(target);
  const [seriesRows, closeRows, nameRows] = await Promise.all([
    querySqlView<DbSeriesRow>(
      "data_viz/api/services/analysis/oracle/series",
      {
        codes,
        sec_type: LABEL_CODE.sec_type[secType],
        bars_table: sqlFrag(BARS_TABLE[secType]),
        action_decode: sqlFrag(labelDecodeSql("oracle_action", "v.action")),
      },
    ),
    querySqlView<DbCloseRow>(
      "data_viz/api/services/analysis/oracle/close_series",
      {
        codes,
        bars_table: sqlFrag(BARS_TABLE[secType]),
      },
    ),
    querySqlView<DbCodeRow>(
      "data_viz/api/services/analysis/volatility/security_name",
      { codes, identity_table: sqlFrag(IDENTITY_TABLE[secType]) },
    ),
  ]);
  const name = nameRows[0]?.name ?? "";

  const rows: OracleRow[] = seriesRows.map((r) => ({
    date: formatDate(r.date),
    action: r.action as OracleAction,
    window_days: toNum(r.window_days),
    ext_date: r.ext_date != null ? formatDate(r.ext_date) : null,
    ext_offset: toNum(r.ext_offset),
    ext_price: toNum(r.ext_price),
    confidence: toNum(r.confidence),
    close: toNum(r.close),
  }));

  const closes: OracleCloseRow[] = closeRows.map((r) => ({
    date: formatDate(r.date),
    close: toNum(r.close),
  }));

  return { code: target, name, rows, closes };
}
