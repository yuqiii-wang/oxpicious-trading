/**
 * Sec board map service — stats.sec_board_map behind the code-trend
 * header's board tag.
 *
 *   stock rows        — ONE per code (pct=100): the stock's own listing
 *                       board (MAIN / STAR / GEM / BSE).
 *   etf / index rows  — one per board present in the LATEST composition
 *                       snapshot's constituents, pct = composition-weight
 *                       share (SSE ETFs inherit their tracking index's
 *                       mix — see builds/board_map/runner.py).
 *
 * The table is small (≈25k rows) and only changes on the nightly build,
 * so the whole per-sec_type slice is cached in-process via the shared
 * cachedRows() TTL (same pattern as classification-cache) and filtered
 * per code in JS.
 */
import { queryRows } from "../lib/db.js";
import type { QueryResultRow } from "pg";
import { cachedRows } from "./classification-cache.js";

export type SecBoardSecType = "index" | "etf" | "stock";

export interface SecBoardRow extends QueryResultRow {
  code: string;
  board: string;
  pct: string | number;
  n_stocks: number;
  snapshot_date: string | null;
}

/** All board rows for one sec_type, pct DESC (cached per type). */
async function listBoardRows(secType: SecBoardSecType): Promise<SecBoardRow[]> {
  return cachedRows(`sec_board_map:${secType}`, () =>
    queryRows<SecBoardRow>(
      `
      SELECT code,
             board,
             pct,
             n_stocks,
             snapshot_date::text AS snapshot_date
        FROM stats.sec_board_map
       WHERE sec_type = $1
       ORDER BY code, pct DESC
      `,
      [secType],
    ),
  );
}

export interface SecBoardMapResponse {
  code: string;
  sec_type: SecBoardSecType;
  /** Board tags sorted pct DESC — one entry for stocks, up to four for
   *  etf/index. Empty when the security has no board data (e.g. an ETF
   *  tracking an index without A-share composition). */
  boards: Array<{ board: string; pct: number; n_stocks: number }>;
  snapshot_date: string | null;
}

/** Board tag payload for one code. */
export async function getSecBoardMap(
  secType: SecBoardSecType,
  code: string,
): Promise<SecBoardMapResponse> {
  const rows = await listBoardRows(secType);
  const own = rows.filter((r) => r.code === code);
  return {
    code,
    sec_type: secType,
    boards: own.map((r) => ({
      board: r.board,
      pct: Number(r.pct),
      n_stocks: Number(r.n_stocks),
    })),
    snapshot_date: own[0]?.snapshot_date ?? null,
  };
}
