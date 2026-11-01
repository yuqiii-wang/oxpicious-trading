/**
 * Search-query segmentation service — the query side of the text schema's
 * GIN full-text search for the Node API.
 *
 * The text.* tables' `search_tsv` columns are StructBERT-segmented weighted
 * tsvectors (written by _common.tokenization.SearchIndexer), so a Chinese
 * query matches nothing unless it is segmented the SAME way — a raw Chinese
 * sentence is one giant 'simple'-config lexeme. The segmenter is a torch
 * model the Node process cannot run, so this service spawns the repo CLI
 * `python -m _common.tokenization --text-b64 <q>` via the shared WSL
 * py-runner (same pattern as the news tokenize route) and caches the result.
 *
 * Contract — segmentSearchTerms(search):
 *   • whitespace-split into terms (exactly the CLI's split), capped at the
 *     CLI's 16-term budget;
 *   • each CJK-bearing term → its space-joined segmented token string (the
 *     websearch_to_tsquery input), or null when segmentation failed;
 *   • non-CJK terms → null (the 'simple' config already handles latin text;
 *     callers keep their ILIKE clause for those);
 *   • whole-call failure (spawn error, unparseable output, timeout) → null
 *     for every term — callers degrade to the pre-GIN substring search.
 *
 * Results are cached (1 h TTL, 200 entries) so repeat searches — paging,
 * re-picking a date under an active search, the AI page's calendar+items
 * pair — cost no WSL spawn; the py-runner's process-id-tag dedupes the same
 * query's concurrent spawns (first search ~seconds: torch + model load).
 */
import { createHash } from "crypto";
import { runPythonModule } from "./py-runner.service.js";

/** CJK ideographs (extension A + unified + compatibility) — the same ranges
 *  the Python segmenter gates on (_common/tokenization/chinese_word_segmenter). */
const CJK_RE = /[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/;

/** Must match the CLI's MAX_TERMS (_common/tokenization/__main__.py). */
const MAX_TERMS = 16;

/** Marker prefixing the JSON result on the CLI's stdout. */
const SEGMENT_RESULT_MARKER = "@@SEARCH_TOKENS_JSON@@";

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

  const promise = segmentViaCli(trimmed, terms);
  inFlight.set(trimmed, promise);
  void promise.finally(() => inFlight.delete(trimmed));
  return promise;
}

/** One WSL spawn + marker parse. Never rejects — every failure mode
 *  resolves null (callers degrade to the substring-only path). */
async function segmentViaCli(
  trimmed: string,
  terms: string[],
): Promise<Array<string | null> | null> {
  const textB64 = Buffer.from(trimmed, "utf8").toString("base64");
  const result = await Promise.race([
    runPythonModule("_common.tokenization", ["--text-b64", textB64], {
      // Per-query tag: dedupes the SAME query's spawns across in-flight
      // map misses (only one caller reaches here per query at a time).
      processIdTag: `text-seg:${createHash("sha1").update(trimmed).digest("hex")}`,
    }),
    new Promise<{ success: false; stdout: string; stderr: string }>(
      (resolve) =>
        setTimeout(
          () => resolve({ success: false, stdout: "", stderr: "timeout" }),
          SEGMENT_TIMEOUT_MS,
        ),
    ),
  ]);

  let segmented: unknown = null;
  const idx = result.stdout.lastIndexOf(SEGMENT_RESULT_MARKER);
  if (result.success && idx >= 0) {
    try {
      const payload = JSON.parse(
        result.stdout.slice(idx + SEGMENT_RESULT_MARKER.length),
      ) as { terms?: unknown };
      if (Array.isArray(payload?.terms)) segmented = payload.terms;
    } catch {
      segmented = null;
    }
  }
  if (
    !Array.isArray(segmented) ||
    segmented.length !== terms.length ||
    segmented.some((s) => s !== null && typeof s !== "string")
  ) {
    console.error(
      `[search-query] segmentation failed for "${trimmed.slice(0, 80)}":`,
      result.stderr.slice(-300) || result.stdout.slice(-300) || "no output",
    );
    return null;
  }
  const out = segmented as Array<string | null>;
  cachePut(trimmed, out);
  return out;
}
