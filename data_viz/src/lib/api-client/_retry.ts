/**
 * Shared retry + timeout layer for UI API fetches.
 *
 * Why: the dev API server briefly disappears during its restart cycles
 * (nodemon file-watch restarts, port rebinds) and the Vite proxy answers
 * that window with a bare 5xx (empty body). Blind one-shot fetches surface
 * that as a page-level error even though a retry a second later succeeds.
 *
 * Retry policy — only TRANSIENT failures are retried:
 *   • network errors (fetch rejects: connection reset / refused)   → retry
 *   • per-attempt timeout (AbortController)                        → retry
 *   • HTTP 502 / 503 / 504 (gateway-level)                         → retry
 *   • HTTP 5xx with an EMPTY body (the Vite-proxy-down signature)  → retry
 *   • HTTP 5xx WITH a body (the server handled the request and
 *     reported a real error — deterministic, e.g. bad params)      → no retry
 *   • HTTP 4xx                                                     → never
 *
 * Timeouts are PER-ENDPOINT, not global: measured latencies differ by
 * ~3 orders of magnitude across the API surface — light lookups
 * (themes / lists / latest-dates) return in <1s, the big combined payloads
 * (5–10 MB JSON) take 0.5–2s, and a few analysis GETs synchronously run a
 * python module server-side and can take tens of seconds. One global
 * timeout either cuts the slow endpoints off mid-computation or leaves the
 * fast ones hanging on a dead connection — hence resolveTimeoutMs().
 * Endpoints not in the table use the (generous) default; callers can also
 * pass { timeoutMs } / { attempts } explicitly.
 */
export interface FetchRetryOptions {
  /** Per-attempt timeout in ms (AbortController). Default: resolved from
   *  the per-endpoint table below, falling back to DEFAULT_TIMEOUT_MS. */
  timeoutMs?: number;
  /** Total attempts (first call + retries). Default 3. */
  attempts?: number;
  /** Backoff base: delay before retry i is baseDelayMs * i. Default 1.5s. */
  baseDelayMs?: number;
}

// ---------------------------------------------------------------------------
//  Per-endpoint timeout table.
//  Order matters for overlapping prefixes — entries are matched LONGEST
//  prefix first. Add a row when an endpoint's latency profile differs from
//  the default; keep the comment grounded in a measured latency.
// ---------------------------------------------------------------------------
export const DEFAULT_TIMEOUT_MS = 30_000;

interface TimeoutOverride {
  prefix: string;
  timeoutMs: number;
  reason: string;
}

const TIMEOUT_OVERRIDES: TimeoutOverride[] = [
  // Synchronous python analysis runs server-side (measured: seconds when
  // warm, tens of seconds on a cold first computation) — far beyond the
  // DB-only endpoints.
  {
    prefix: "/api/analysis/industry-correlations",
    timeoutMs: 120_000,
    reason: "GET runs python analysis synchronously",
  },
  {
    prefix: "/api/analysis/industry-corr-offsets/industries",
    timeoutMs: 120_000,
    reason: "GET runs python analysis synchronously",
  },
];

/** Resolve the per-attempt timeout for a URL (longest matching prefix wins). */
export function resolveTimeoutMs(url: string): number {
  let best: TimeoutOverride | undefined;
  for (const o of TIMEOUT_OVERRIDES) {
    if (url.startsWith(o.prefix) && (!best || o.prefix.length > best.prefix.length)) {
      best = o;
    }
  }
  return best?.timeoutMs ?? DEFAULT_TIMEOUT_MS;
}

// ---------------------------------------------------------------------------
//  Error type + retry classification
// ---------------------------------------------------------------------------
export class HttpError extends Error {
  readonly status: number;
  readonly body: string;
  constructor(status: number, body: string) {
    super(`HTTP ${status}: ${body}`);
    this.name = "HttpError";
    this.status = status;
    this.body = body;
  }
  /** True when the failure is worth retrying (see policy in header). */
  get transient(): boolean {
    if (this.status === 502 || this.status === 503 || this.status === 504) return true;
    // Vite-proxy-down signature: 5xx with an empty body.
    return this.status >= 500 && this.body.trim() === "";
  }
}

function isAbortError(err: unknown): boolean {
  return err instanceof DOMException && err.name === "AbortError";
}

/** Human-readable message for a failed fetch — surface this in UI alerts. */
export function formatFetchError(e: unknown): string {
  if (e instanceof HttpError) {
    if (e.status >= 500 && e.body.trim() === "") {
      return "API server unreachable (it may be restarting) — try Refresh in a moment.";
    }
    return e.message;
  }
  if (isAbortError(e)) {
    return "Request timed out — the API may be busy; try Refresh in a moment.";
  }
  return e instanceof Error ? e.message : String(e);
}

// ---------------------------------------------------------------------------
//  One attempt: GET with per-attempt timeout. Throws HttpError on non-2xx,
//  DOMException("AbortError") on timeout, TypeError on network failure.
// ---------------------------------------------------------------------------
async function attemptOnce(url: string, timeoutMs: number): Promise<unknown> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(url, { signal: controller.signal });
    const text = await res.text();
    if (!res.ok) throw new HttpError(res.status, text);
    return text ? JSON.parse(text) : null;
  } finally {
    clearTimeout(timer);
  }
}

/**
 * GET with retry + per-endpoint timeout — the shared transport behind
 * fetchJson. Retries only transient failures (see policy in the file
 * header); anything else fails fast with the original error.
 */
export async function fetchJsonWithRetry<T>(
  url: string,
  opts: FetchRetryOptions = {},
): Promise<T> {
  const attempts = opts.attempts ?? 3;
  const baseDelayMs = opts.baseDelayMs ?? 1_500;
  const timeoutMs = opts.timeoutMs ?? resolveTimeoutMs(url);
  let lastErr: unknown;
  for (let i = 0; i < attempts; i++) {
    if (i > 0) await new Promise((r) => setTimeout(r, baseDelayMs * i));
    try {
      return (await attemptOnce(url, timeoutMs)) as T;
    } catch (err) {
      lastErr = err;
      const transient =
        err instanceof HttpError ? err.transient : true; // network error / timeout
      if (!transient) break;
    }
  }
  throw lastErr;
}
