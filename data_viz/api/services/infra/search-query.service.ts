/**
 * Search-query segmentation service — the query side of the text schema's
 * GIN full-text search for the Node API.
 *
 * The text/llm schemas' `search_tsv` columns are StructBERT-segmented weighted
 * tsvectors (written by _common.tokenization.SearchIndexer), so a Chinese
 * query matches nothing unless it is segmented the SAME way — a raw Chinese
 * sentence is one giant 'simple'-config lexeme. The segmenter is a torch
 * model the Node process cannot run, so this service calls the resident
 * local-model host's HTTP endpoint (server_cluster/llm,
 * POST http://127.0.0.1:8767/v1/tokenization/segment — {texts} in,
 * {words} out; the 2026-11 refactor of the former local WSL CLI spawn)
 * and caches the result.
 *
 * Contract — segmentSearchTerms(search):
 *   • whitespace-split into terms, capped at the 16-term budget;
 *   • each CJK-bearing term → its space-joined segmented token string (the
 *     websearch_to_tsquery input), or null when segmentation failed;
 *   • non-CJK terms → null (the 'simple' config already handles latin text;
 *     callers keep their ILIKE clause for those);
 *   • whole-call failure (HTTP error, unparseable body, timeout) → null
 *     for every term — callers degrade to the pre-GIN substring search.
 *
 * Results are cached (1 h TTL, 200 entries) so repeat searches — paging,
 * re-picking a date under an active search, the AI page's calendar+items
 * pair — cost no model call; the in-flight map dedupes the same query's
 * concurrent calls (first search ~seconds: model load if offloaded).
 */

/** CJK ideographs (extension A + unified + compatibility) — the same ranges
 *  the Python segmenter gates on (_common/tokenization/chinese_word_segmenter). */
const CJK_RE = /[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/;

/** Must match the CLI's MAX_TERMS (_common/tokenization/__main__.py). */
const MAX_TERMS = 16;

/** WSL spawn wall-clock budget (torch import + model load + inference). */
const SEGMENT_TIMEOUT_MS = 90_000;

const CACHE_TTL_MS = 60 * 60 * 1000;
const CACHE_MAX_ENTRIES = 200;

interface CacheEntry {
  terms: Array<string | null>;
  cachedAt: number;
}

const cache = new Map<string, CacheEntry>();

/** In-flight segmentation promises, keyed like the cache — the pages fire
 *  calendar/authors/items (or calendar+items) CONCURRENTLY with the same
 *  search string, and those callers must share one spawn (the py-runner's
 *  process-id-tag dedupe alone would resolve the siblings immediately with
 *  empty stdout, silently degrading them to the substring-only path). */
const inFlight = new Map<string, Promise<Array<string | null> | null>>();

function cacheGet(key: string): Array<string | null> | null {
  const hit = cache.get(key);
  if (!hit) return null;
  if (Date.now() - hit.cachedAt > CACHE_TTL_MS) {
    cache.delete(key);
    return null;
  }
  return hit.terms;
}

function cachePut(key: string, terms: Array<string | null>): void {
  if (cache.size >= CACHE_MAX_ENTRIES) {
    const oldest = cache.keys().next().value;
    if (oldest !== undefined) cache.delete(oldest);
  }
  cache.set(key, { terms, cachedAt: Date.now() });
}

/** The one term splitter for the text-schema search clauses — services use
 *  it for BOTH the ILIKE terms and the segmentation so the two stay
 *  position-aligned. */
export function splitSearchTerms(search: string | null | undefined): string[] {
  return (search ?? "").trim().split(/\s+/).filter(Boolean).slice(0, MAX_TERMS);
}

/**
 * Segment a raw search string per whitespace term for the text-schema
 * full-text clauses. Position-aligned with splitSearchTerms — services
 * split with it, then zip.
 */
export async function segmentSearchTerms(
  search: string,
): Promise<Array<string | null> | null> {
  const trimmed = search.trim();
  if (!trimmed) return [];
  const terms = splitSearchTerms(trimmed);

  if (!terms.some((t) => CJK_RE.test(t))) {
    // Pure-ASCII search: nothing to segment, no WSL spawn.
    return terms.map(() => null);
  }
  const cached = cacheGet(trimmed);
  if (cached) return cached;
  const pending = inFlight.get(trimmed);
  if (pending) return pending;

  const promise = segmentViaHttp(trimmed, terms);
  inFlight.set(trimmed, promise);
  void promise.finally(() => inFlight.delete(trimmed));
  return promise;
}

/** One HTTP call to the local-model host + per-term join. Never rejects —
 *  every failure mode resolves null (callers degrade to the
 *  substring-only path). */
async function segmentViaHttp(
  trimmed: string,
  terms: string[],
): Promise<Array<string | null> | null> {
  // Per-term texts (the whitespace split IS the segmentation unit — the
  // HTTP contract segments each text independently, order preserved).
  const cjkTerms = terms.filter((t) => CJK_RE.test(t));
  let wordLists: unknown;
  try {
    wordLists = await Promise.race([
      segmentRequest(cjkTerms),
      new Promise<"timeout">((resolve) =>
        setTimeout(() => resolve("timeout"), SEGMENT_TIMEOUT_MS),
      ),
    ]);
  } catch (err) {
    console.error(
      `[search-query] segmentation failed for "${trimmed.slice(0, 80)}":`,
      err instanceof Error ? err.message : err,
    );
    return null;
  }
  if (wordLists === "timeout") {
    console.error(
      `[search-query] segmentation timed out after ${SEGMENT_TIMEOUT_MS}ms `
        + `for "${trimmed.slice(0, 80)}"`,
    );
    return null;
  }
  if (
    !Array.isArray(wordLists) ||
    wordLists.length !== cjkTerms.length ||
    wordLists.some(
      (w) => !Array.isArray(w) || w.some((s) => typeof s !== "string"),
    )
  ) {
    console.error(
      `[search-query] segmentation failed for "${trimmed.slice(0, 80)}":`,
      "unparseable response body",
    );
    return null;
  }
  // Zip back onto the full term list: CJK terms → space-joined words,
  // non-CJK terms → null (the ILIKE clause's business).
  const byTerm = new Map<string, string>();
  cjkTerms.forEach((term, i) => {
    byTerm.set(term, (wordLists as string[][])[i].join(" "));
  });
  const out = terms.map((t) => byTerm.get(t) ?? null);
  cachePut(trimmed, out);
  return out;
}

const SEGMENTER_BASE_URL = "http://127.0.0.1:8767";

/** The known-dummy credential — the segment route verifies the Bearer
 *  like every local-model-host serving route (the host checks the
 *  header's presence, not the secret). */
const SEGMENTER_BEARER = "lmh-dummy";

/** One POST /v1/tokenization/segment — {texts} in, {words} out
 *  (server_cluster/llm, the word-segmenter-zh backend). */
async function segmentRequest(texts: string[]): Promise<unknown> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), SEGMENT_TIMEOUT_MS);
  try {
    const res = await fetch(
      `${SEGMENTER_BASE_URL}/v1/tokenization/segment`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${SEGMENTER_BEARER}`,
        },
        body: JSON.stringify({ texts }),
        signal: controller.signal,
      },
    );
    if (!res.ok) {
      throw new Error(`segmenter HTTP ${res.status}`);
    }
    const body = (await res.json()) as { words?: unknown };
    return body?.words ?? null;
  } finally {
    clearTimeout(timer);
  }
}
