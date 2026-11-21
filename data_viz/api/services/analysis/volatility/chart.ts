/**
 * Volatility chart service — listVolatilityCodes + getVolatilitySeries.
 *
 * listVolatilityCodes backs the SecCodesPageShell codes fetcher: the
 * codes that HAVE vol rows for the sec_type (index for now — the
 * pipeline's 10 study indices; widening the run scope fills the other
 * security sec_types with no API change), each with its latest
 * identity display name.
 *
 * getVolatilitySeries returns one security's FULL daily series (the UI
 * slices via dataZoom): every analysis.volatility metric column,
 * NULL-gated by estimator warm-up (hv_W from row W+1 on, percentiles/z
 * from ~60 obs) and by the intraday-bar retention window for the rv
 * leg.
 */
import { formatDate, toNum } from "@api/lib/db";
import { querySqlView, sqlFrag } from "@api/lib/sql-views";
import type { QueryResultRow } from "pg";
import { codeVariants } from "@api/lib/classify-etf";
import { HttpError } from "@api/lib/http";
import { stripped } from "@api/services/analysis/_shared";
import { LABEL_CODE } from "@api/services/infra/label-codes";
import type {
  VolatilityCodeRow,
  VolatilityCodesResponse,
  VolatilityRow,
  VolatilitySeriesResponse,
} from "@shared/types";

/** The FLOAT8/NUMERIC metric columns the series view returns (the row
 *  mapper walks them so the DB contract and the TS type stay paired).
 *  `close` is the raw price level, joined from the sec_type's bars
 *  source table (the pipeline's own OHLC source) for the UI's price
 *  overlay line. */
const METRIC_COLUMNS = [
  "close",
  "hv_5", "hv_10", "hv_20", "hv_60", "hv_120", "hv_252",
  "ewma_vol",
  "parkinson_vol_20", "garman_klass_vol_20", "rogers_satchell_vol_20",
  "yang_zhang_vol_20",
  "overnight_vol_20", "intraday_vol_20",
  "atr_14", "natr_14",
  "boll_bw_20", "vol_of_vol_20", "vol_ratio_20_252",
  "hv_20_pct_rank_252", "ewma_vol_pct_rank_252",
  "yang_zhang_vol_20_pct_rank_252", "natr_14_pct_rank_252",
  "hv_20_z_252", "ewma_vol_z_252",
  "rv_5min", "jump_ratio_5min",
] as const;

/** The sec_types the pipeline covers (industry stays out — pool grain).
 *  The run scope is index only for now, but the endpoints accept the
 *  full security set so widening the pipeline needs no API change. */
const VOL_SEC_TYPES = ["etf", "index", "stock"] as const;
export type VolatilitySecType = (typeof VOL_SEC_TYPES)[number];

/** Parse + validate the sec_type query param (default "index"). */
export function normalizeVolSecType(
  raw: string | undefined | null,
): VolatilitySecType {
  const v = (raw ?? "index").trim().toLowerCase();
  if ((VOL_SEC_TYPES as readonly string[]).includes(v)) {
    return v as VolatilitySecType;
  }
  throw HttpError.invalidParam(
    "sec_type", `expected one of ${VOL_SEC_TYPES.join("|")}, got '${raw}'`);
}

/** Per-sec_type identity source for the display-name lookup. */
const IDENTITY_TABLE: Record<VolatilitySecType, string> = {
  etf: "stats.etf_identity",
  index: "stats.index_identity",
  stock: "stats.stock_identity",
};

/** Per-sec_type OHLC bars source for the close overlay (the pipeline's
 *  own SEC_TYPE_SOURCES bars tables — read-only join, no new storage). */
const BARS_TABLE: Record<VolatilitySecType, string> = {
  etf: "stats.etf_basic_stats",
  index: "stats.index_basic_stats",
  stock: "stats.stock_basic_stats",
};

interface DbCodeRow extends QueryResultRow {
  code: string;
  name: string | null;
}

type DbSeriesRow = QueryResultRow & {
  date: Date | string;
  rv_bar_count: number | string | null;
} & Record<(typeof METRIC_COLUMNS)[number], number | string | null>;

export async function listVolatilityCodes(
  rawSecType: string | undefined | null,
): Promise<VolatilityCodesResponse> {
  const secType = normalizeVolSecType(rawSecType);
  const rows = await querySqlView<DbCodeRow>(
    "data_viz/api/services/analysis/volatility/codes",
    { sec_type: LABEL_CODE.sec_type[secType] },
  );
  const codes: VolatilityCodeRow[] = rows
    .map((r) => ({ code: r.code, name: r.name ?? "" }))
    .sort((a, b) => a.code.localeCompare(b.code));
  return { codes };
}

export async function getVolatilitySeries(
  rawCode: string,
  rawSecType: string | undefined | null,
): Promise<VolatilitySeriesResponse> {
  const secType = normalizeVolSecType(rawSecType);
  const target = stripped(rawCode);
  const codes = codeVariants(target);
  const [seriesRows, nameRows] = await Promise.all([
    querySqlView<DbSeriesRow>(
      "data_viz/api/services/analysis/volatility/series",
      {
        codes,
        sec_type: LABEL_CODE.sec_type[secType],
        bars_table: sqlFrag(BARS_TABLE[secType]),
      },
    ),
    querySqlView<DbCodeRow>(
      "data_viz/api/services/analysis/volatility/security_name",
      { codes, identity_table: sqlFrag(IDENTITY_TABLE[secType]) },
    ),
  ]);
  const name = nameRows[0]?.name ?? "";

  const rows: VolatilityRow[] = seriesRows.map((r) => {
    const row: Record<string, unknown> = { date: formatDate(r.date) };
    for (const col of METRIC_COLUMNS) {
      row[col] = toNum(r[col]);
    }
    row.rv_bar_count = toNum(r.rv_bar_count);
    return row as unknown as VolatilityRow;
  });

  return { code: target, name, rows };
}
