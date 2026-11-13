/**
 * expandedNodesStore — the Thread Graph view's fold registry, persisted
 * per thread in localStorage (`ai-thread-graph:expanded`) so a page
 * refresh freezes the canvas: the expanded/folded nodes sit exactly as
 * before the reload (2026-11-11 — the in-memory registry used to reset
 * to all-folded on every load, and a live run watched across a refresh
 * reflowed under the user's feet).
 *
 * One JSON object entry keyed by canvas node id sets
 * (`step-<state_id>` / `spawn-<state_id>` — DB serial ids, stable across
 * sessions), insertion-ordered as an LRU: each save re-dates the
 * thread's entry and the oldest entries beyond MAX_THREADS are dropped,
 * so the store cannot grow without bound across pruned runs. All reads
 * are fail-soft (corrupted/unavailable storage → an empty set — the
 * default folded canvas); writes are fail-soft too (the fold still
 * works, it just does not survive the reload).
 */

const STORE_KEY = "ai-thread-graph:expanded";

/** Kept threads before the oldest entry is dropped (runs are pruned;
 *  40 comfortably covers a page's working set). */
const MAX_THREADS = 40;

type StoredShape = Record<string, string[]>;

function readStore(): StoredShape {
  try {
    const raw = localStorage.getItem(STORE_KEY);
    if (raw === null) return {};
    const parsed: unknown = JSON.parse(raw);
    if (parsed === null || typeof parsed !== "object") return {};
    const out: StoredShape = {};
    for (const [threadId, ids] of Object.entries(
      parsed as Record<string, unknown>,
    )) {
      if (!Array.isArray(ids)) continue;
      const names = ids.filter((id): id is string => typeof id === "string");
      if (names.length > 0) out[threadId] = names;
    }
    return out;
  } catch {
    return {};
  }
}

function writeStore(store: StoredShape): void {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify(store));
  } catch {
    // storage unavailable/corrupted — the fold still works this session
  }
}

/** The persisted fold registry for one thread (empty = all folded, the
 *  canvas default; a null threadId has nothing to restore). */
export function loadExpandedNodes(threadId: string | null): Set<string> {
  if (threadId === null) return new Set();
  const ids = readStore()[threadId];
  return new Set(ids ?? []);
}

/** Persist one thread's fold registry (and re-date it in the LRU). */
export function saveExpandedNodes(
  threadId: string | null,
  nodes: ReadonlySet<string>,
): void {
  if (threadId === null) return;
  const store = readStore();
  delete store[threadId];
  if (nodes.size > 0) store[threadId] = [...nodes];
  // Drop the oldest entries beyond the cap (insertion order = recency).
  const keys = Object.keys(store);
  for (const key of keys.slice(0, Math.max(0, keys.length - MAX_THREADS))) {
    delete store[key];
  }
  writeStore(store);
}
