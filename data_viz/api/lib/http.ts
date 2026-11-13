/**
 * HTTP contract helpers for the data_viz API — the single owner of the
 * error envelope, async-handler safety, and query-param parsing.
 *
 * Wire contract (full spec: data_viz/api/CONTRACTS.md):
 * - 2xx: the body IS the payload, typed in shared/types.ts. Command
 *   endpoints additionally carry `success: true` + named outcome fields; a
 *   python-run command may answer 200 with `success: false` + `stderr_tail`
 *   — that outcome is DATA, never an HTTP error.
 * - non-2xx: the body is ALWAYS ApiErrorBody ({ success: false, error,
 *   code?, details? }). Thrown HttpErrors and forwarded rejections reach
 *   the app-level handler (app.ts), which answers via sendThrowable.
 * - wrapAsync: Express 4 does not catch async route-handler rejections (a
 *   rejected handler promise would hang the response forever — see the
 *   unhandledRejection note in server.ts), so every async route handler is
 *   wrapped and its rejection reaches the error middleware instead.
 */
import type { NextFunction, Request, RequestHandler, Response } from "express";
import type { ApiErrorCode } from "@shared/types";

// ---------------------------------------------------------------------------
// Error throwable
// ---------------------------------------------------------------------------

/** Thrown by route handlers (and param parsers) — carries the HTTP status,
 *  the machine-readable ApiErrorCode, and optional per-field details. The
 *  app-level error handler turns it into the standard ApiErrorBody. */
export class HttpError extends Error {
  readonly status: number;
  readonly code: ApiErrorCode;
  readonly details?: Record<string, string>;

  constructor(
    status: number,
    code: ApiErrorCode,
    message: string,
    details?: Record<string, string>,
  ) {
    super(message);
    this.name = "HttpError";
    this.status = status;
    this.code = code;
    this.details = details;
  }

  /** 400 — a required query/body param is absent (or blank). */
  static missingParam(name: string): HttpError {
    return new HttpError(400, "MISSING_PARAM", `Missing '${name}' parameter`, {
      param: name,
    });
  }

  /** 400 — a param is present but malformed / out of range / not allowed. */
  static invalidParam(name: string, why: string): HttpError {
    return new HttpError(400, "INVALID_PARAM", `Invalid '${name}': ${why}`, {
      param: name,
    });
  }

  /** 404 — the addressed resource does not exist. */
  static notFound(message: string): HttpError {
    return new HttpError(404, "NOT_FOUND", message);
  }

  /** 409 — conflicts with existing state (duplicate name, stale write). */
  static conflict(message: string): HttpError {
    return new HttpError(409, "CONFLICT", message);
  }
}

// ---------------------------------------------------------------------------
// Async-handler safety
// ---------------------------------------------------------------------------

/**
 * Wrap an async route handler so its rejection reaches the error middleware
 * instead of hanging the response (Express 4 leaves async rejections
 * unhandled). Every async route handler uses this:
 *
 *   router.get("/x", wrapAsync(async (req, res) => { ... }));
 */
export function wrapAsync(
  handler: (req: Request, res: Response, next: NextFunction) => unknown,
): RequestHandler {
  return (req, res, next) => {
    void Promise.resolve(handler(req, res, next)).catch(next);
  };
}

// ---------------------------------------------------------------------------
// Error responses — the one non-2xx body (ApiErrorBody in shared/types.ts)
// ---------------------------------------------------------------------------

/** Default machine code per HTTP status when the throwable carries none. */
const STATUS_DEFAULT_CODE: Readonly<Record<number, ApiErrorCode>> = {
  400: "INVALID_PARAM",
  404: "NOT_FOUND",
  409: "CONFLICT",
  502: "UPSTREAM_FAILED",
};

function codeForStatus(status: number): ApiErrorCode {
  return (
    STATUS_DEFAULT_CODE[status] ?? (status >= 500 ? "INTERNAL" : "INVALID_PARAM")
  );
}

/** Send the standard error body for an explicit status (CONTRACTS.md §errors). */
export function sendError(
  res: Response,
  status: number,
  message: string,
  code?: ApiErrorCode,
  details?: Record<string, string>,
): void {
  res.status(status).json({
    success: false,
    error: message,
    code: code ?? codeForStatus(status),
    ...(details === undefined ? {} : { details }),
  });
}

/** Send the standard error body for a caught throwable — honors HttpError
 *  (status/code/details) and the service-layer convention
 *  `Object.assign(new Error(msg), { status })`; everything else is 500. */
export function sendThrowable(res: Response, err: unknown): void {
  const rawStatus =
    err instanceof HttpError
      ? err.status
      : (err as { status?: unknown } | null)?.status;
  const status =
    typeof rawStatus === "number" &&
    Number.isInteger(rawStatus) &&
    rawStatus >= 400
      ? rawStatus
      : 500;
  const code = err instanceof HttpError ? err.code : undefined;
  const details = err instanceof HttpError ? err.details : undefined;
  const message = err instanceof Error ? err.message : String(err);
  sendError(res, status, message, code, details);
}

// ---------------------------------------------------------------------------
// Query-param parsers
//
// Typed reads off req.query with each endpoint's historical semantics
// preserved (silent fallback / clamp / strict validate); `required` fires
// HttpError(400, MISSING_PARAM) on absent or blank. A query param arrives as
// string | string[] | ParsedQs — anything that is not a plain string counts
// as absent, matching the previous `typeof req.query.x === "string"` idiom.
// ---------------------------------------------------------------------------

/** Trimmed string param. Absent/blank → undefined (MISSING_PARAM when required). */
export function strParam(
  req: Request,
  name: string,
  opts: { required?: boolean } = {},
): string | undefined {
  const raw = req.query[name];
  const v = typeof raw === "string" ? raw.trim() : undefined;
  if (v === undefined || v === "") {
    if (opts.required) throw HttpError.missingParam(name);
    return undefined;
  }
  return v;
}

/** Integer param.
 *  - absent/blank → `default` (MISSING_PARAM when `required`)
 *  - malformed (not a finite number) → `default`, silent — matches the
 *    historical `Number(x) || fallback` endpoints; pass `strict: true` to
 *    make it INVALID_PARAM instead
 *  - `min`/`max` violated → INVALID_PARAM (only where the endpoint already
 *    400s out-of-range values)
 *  - `clamp` → silently bounded (the monitor endpoints' historical style). */
export function intParam(
  req: Request,
  name: string,
  opts: {
    required?: boolean;
    default?: number;
    strict?: boolean;
    min?: number;
    max?: number;
    clamp?: [number, number];
  } = {},
): number | undefined {
  const raw = req.query[name];
  const s = typeof raw === "string" ? raw.trim() : undefined;
  if (s === undefined || s === "") {
    if (opts.required) throw HttpError.missingParam(name);
    return opts.default;
  }
  const n = Number(s);
  if (!Number.isFinite(n)) {
    if (opts.strict) {
      throw HttpError.invalidParam(name, `expected an integer, got '${s}'`);
    }
    return opts.default;
  }
  if (opts.clamp !== undefined) {
    const [lo, hi] = opts.clamp;
    return Math.min(Math.max(n, lo), hi);
  }
  if (opts.min !== undefined && n < opts.min) {
    throw HttpError.invalidParam(name, `must be ≥ ${opts.min}`);
  }
  if (opts.max !== undefined && n > opts.max) {
    throw HttpError.invalidParam(name, `must be ≤ ${opts.max}`);
  }
  return n;
}

const DATE_RX = /^\d{4}-\d{2}-\d{2}$/;

/** `YYYY-MM-DD` date param. Absent → `default` (MISSING_PARAM when
 *  required); malformed → `default`, silent (the live-data regex-then-fallback
 *  style); a longer timestamp whose date prefix is well-formed is sliced
 *  (mirrors db.service's toDateParam). */
export function dateParam(
  req: Request,
  name: string,
  opts: { required?: boolean; default?: string } = {},
): string | undefined {
  const v = strParam(req, name);
  if (v === undefined) {
    if (opts.required) throw HttpError.missingParam(name);
    return opts.default;
  }
  if (DATE_RX.test(v)) return v;
  if (DATE_RX.test(v.slice(0, 10))) return v.slice(0, 10);
  return opts.default;
}

/** Enum param from `allowed`. Absent → `default` (MISSING_PARAM when
 *  required). Present-but-unlisted → `default` when given (the silent
 *  coercion style) or INVALID_PARAM when `strict` (the explicit-400 style). */
export function enumParam<T extends string>(
  req: Request,
  name: string,
  allowed: readonly T[],
  opts: { required?: boolean; default?: T; strict?: boolean } = {},
): T | undefined {
  const v = strParam(req, name);
  if (v === undefined) {
    if (opts.required) throw HttpError.missingParam(name);
    return opts.default;
  }
  if ((allowed as readonly string[]).includes(v)) return v as T;
  if (opts.strict) {
    throw HttpError.invalidParam(
      name,
      `expected one of ${allowed.join("/")}, got '${v}'`,
    );
  }
  return opts.default;
}

/** Boolean param — `"1"`/`"true"` → true, `"0"`/`"false"` → false,
 *  anything else/absent → undefined. */
export function boolParam(req: Request, name: string): boolean | undefined {
  const v = strParam(req, name);
  if (v === undefined) return undefined;
  if (v === "1" || v === "true") return true;
  if (v === "0" || v === "false") return false;
  return undefined;
}
