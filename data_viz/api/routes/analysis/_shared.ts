/**
 * Shared query-param parsers for the analysis route subfiles.
 * Full route docs: see index.ts.
 */

import { type Request } from "express";

/** Parse the `sec_type` query param — required on every endpoint. */
export function parseSecType(req: Request): string | undefined {
  const v = req.query.sec_type;
  return typeof v === "string" ? v : undefined;
}


/** Parse the `code` query param — required on chart / summary endpoints. */
export function parseCode(req: Request): string | null {
  const v = req.query.code;
  return typeof v === "string" && v.length > 0 ? v : null;
}

/** Parse the optional `exchange` query param (PRIMARY/SS/SZ/BJ/HK/OVERSEAS). */
export function parseExchange(req: Request): string | null {
  const v = req.query.exchange;
  return typeof v === "string" && v.length > 0 ? v : null;
}
