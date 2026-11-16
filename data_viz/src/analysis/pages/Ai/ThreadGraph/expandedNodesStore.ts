/**
 * expandedNodesStore — the Thread Graph view's fold registry, persisted
 * per thread in localStorage (`ai-thread-graph:expanded`) so a page
 * refresh freezes the canvas: the expanded/folded nodes sit exactly as
 * before the reload (2026-11-11 — the in-memory registry used to reset
 * to all-folded on every load, and a live run watched across a refresh
 * reflowed under the user's feet).
 *
 * 2026-11-14 the DEFAULT-EXPAND round: a thread with NO stored entry
 * has never been touched — the view AUTO-EXPANDS its small spawn
 * subtrees (the forked/spawned nodes show without hunting for the
 * ▸N cue; big subtrees stay folded so the graph fits the window).
 * 2026-11-15: an EMPTY entry reads as never-touched again — the
 * first cut persisted the user's all-folded state as `[]`, but the
 * persist effect's stale-closure pass wrote `[]` for auto-armed
 * threads too (a StrictMode double-invoke slipped the first-run
 * skip), and one empty entry blocked the auto default forever. Only
 * a NON-empty entry is an explicit registry now; the all-folded
 * state does not survive a reload (the auto default re-arms).
 *
 * One JSON object entry keyed by thread id holds an array of canvas
 * node id sets (`step-<state_id>` / `spawn-<state_id>` — DB serial
 * ids, stable across sessions), insertion-ordered as an LRU: each
 * save re-dates the thread's entry and the oldest entries beyond
 * MAX_THREADS are dropped, so the store cannot grow without bound
 * across pruned runs. All reads are fail-soft (corrupted/unavailable
 * storage → null — the auto default); writes are fail-soft too (the
 * fold still works, it just does not survive the reload).
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
      // Empty arrays are KEPT: [] is the user's explicit all-folded.
      out[threadId] = ids.filter((id): id is string => typeof id === "string");
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

/** The persisted fold registry for one thread: null = never touched
 *  (or an empty entry — 2026-11-15: an all-folded registry no longer
 *  persists, the buggy empty writes taught that an empty entry and
 *  never-touched must read the same, or one StrictMode pass blocks the
 *  auto default forever) — the view's AUTO default applies (small
 *  spawn subtrees expand); a non-empty Set = the user's explicit
 *  registry, restored verbatim. A null threadId has nothing to
 *  restore. */
export function loadExpandedNodes(threadId: string | null): Set<string> | null {
  if (threadId === null) return null;
  const ids = readStore()[threadId];
  if (ids === undefined || ids.length === 0) return null;
  return new Set(ids);
}

/** Persist one thread's explicit fold registry (an empty set persists
 *  as the explicit all-folded marker) and re-date it in the LRU. */
export function saveExpandedNodes(
  threadId: string | null,
  nodes: ReadonlySet<string>,
): void {
  if (threadId === null) return;
  const store = readStore();
  delete store[threadId];
  store[threadId] = [...nodes];
  // Drop the oldest entries beyond the cap (insertion order = recency).
  const keys = Object.keys(store);
  for (const key of keys.slice(0, Math.max(0, keys.length - MAX_THREADS))) {
    delete store[key];
  }
  writeStore(store);
}
