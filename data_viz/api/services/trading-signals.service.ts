// ---------------------------------------------------------------------------
//  Trading Signals service — DB reads behind the Live Data → Trading Signals
//  page (analysis scheme only for now).
//
//  • activeConfigs(sec_type) — the signal-type / sub-type menu: DISTINCT
//    (signal_type, signal_sub_type) from analysis_signals.signals where
//    is_active (the current threshold set).
//  • triggered(sec_type, date) — one day's rows from live.live_signals,
//    ordered by confidence DESC (the page's main list).
//  • availableDates(sec_type) — dates present in live_signals (roster,
//    newest first) for the date selector.
// ---------------------------------------------------------------------------
import { queryRows, formatDate } from "./db.service.js";
import type { QueryResultRow } from "pg";

/** One menu entry: a signal family + window (e.g. mov_rsi / rsi14). */
export interface TradingSignalConfigRow {
  signal_type: string;
  signal_sub_type: string;
  n_configs: number;
}

/** One triggered breach record (live.live_signals row). */
export interface TradingSignalRow {
  code: string;
  /** Display name from stats.<sec>_identity (latest row per code);
   *  null when no identity row is available. */
  code_name: string | null;
  sec_type: string;
  signal_type: string;
  signal_sub_type: string;
  date: string;
  time: string;
  action: string;
  /** signal - signal_threshold; sign = breach direction
   *  (positive = upward/above, negative = downward/below). */
  signal_excess: number;
  /** Unitless breach depth in %: signal_excess / |signal_threshold| * 100.
   *  null when signal_threshold = 0 (guarded by NULLIF). */
  signal_excess_pct: number | null;
  signal: number;
  signal_threshold: number;
  confidence: number;
  is_day_close_trigger: boolean;
}

const VALID_SEC_TYPES = new Set(["index", "etf", "stock"]);

function assertSecType(secType: string | null | undefined): string {
  const st = (secType ?? "").trim();
  if (!VALID_SEC_TYPES.has(st)) {
    throw Object.assign(
      new Error(`invalid sec_type: ${secType} (expected index|etf|stock)`),
      { status: 400 },
    );
  }
  return st;
}

/** DISTINCT (signal_type, signal_sub_type) of the ACTIVE configs for a
 *  sec_type — the page's signal menu (default = all). */
export async function fetchTradingSignalConfigs(
  secType: string | null | undefined,
): Promise<TradingSignalConfigRow[]> {
  const st = assertSecType(secType);
  const rows = await queryRows<TradingSignalConfigRow & QueryResultRow>(
    `SELECT signal_type, signal_sub_type, count(*)::int AS n_configs
     FROM analysis_signals.signals
     WHERE sec_type = $1 AND is_active
     GROUP BY signal_type, signal_sub_type
     ORDER BY signal_type, signal_sub_type`,
    [st],
  );
  return rows;
}

/** sec_type → stats identity table that carries (code, date, name).
 *  Matches the per-sec lookup pattern used by index/etf/stock baseline
 *  services (e.g. `stats.index_identity (code, date DESC) INCLUDE (name)`). */
const IDENTITY_TABLE: Record<string, string> = {
  index: "stats.index_identity",
  etf: "stats.etf_identity",
  stock: "stats.stock_identity",
};

/** One day's triggered signals for a sec_type, confidence DESC then time
 *  DESC. `date` is 'YYYY-MM-DD' (the UI pre-resolves biz today).
 *  Joins the sec_type's identity table (latest row per code) to resolve
 *  the display name; falls back to NULL when no identity row exists. */
export async function fetchTriggeredSignals(
  secType: string | null | undefined,
  date: string,
): Promise<TradingSignalRow[]> {
  const st = assertSecType(secType);
  const idt = IDENTITY_TABLE[st]!;
  const rows = await queryRows<TradingSignalRow & QueryResultRow>(
    `SELECT s.code,
            n.name                       AS code_name,
            s.sec_type,
            s.signal_type,
            s.signal_sub_type,
            to_char(s.date, 'YYYY-MM-DD') AS date,
            to_char(s.time, 'HH24:MI')    AS time,
            s.action,
            s.signal_excess::float8       AS signal_excess,
            s.signal_excess_pct::float8    AS signal_excess_pct,
            s.signal::float8              AS signal,
            s.signal_threshold::float8     AS signal_threshold,
            s.confidence,
            s.is_day_close_trigger
     FROM live.live_signals s
     LEFT JOIN LATERAL (
       SELECT i.name FROM ${idt} i
       WHERE i.code = s.code
       ORDER BY i.date DESC LIMIT 1
     ) n ON TRUE
     WHERE s.sec_type = $1 AND s.date = $2::date
     ORDER BY s.confidence DESC, s.time DESC, s.code, s.signal_type,
              s.signal_sub_type`,
    [st, date],
  );
  return rows;
}

/** Dates present in live_signals for a sec_type (roster, newest first). */
export async function fetchTradingSignalDates(
  secType: string | null | undefined,
): Promise<string[]> {
  const st = assertSecType(secType);
  const rows = await queryRows<{ date: string } & QueryResultRow>(
    `SELECT DISTINCT date FROM live.live_signals
     WHERE sec_type = $1
     ORDER BY date DESC
     LIMIT 120`,
    [st],
  );
  return rows.map((r) => formatDate(r.date));
}
