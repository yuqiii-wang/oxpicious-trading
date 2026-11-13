/** The label/tooltip fragment helpers of the segment build — the
 *  ONE-NODE-ONE-CONTEXT tail, the call-count fragments and the elapsed
 *  + token consumption lines. Pure over their inputs: the per-context
 *  usage rollup (and, for the delegated rollup, the spawn-children map)
 *  ride in as arguments from the segment build they serve. */
import type {
  AgentLlmUsage,
  AgentThreadState,
} from "@/lib/api-client/aiThreads";

/** Per-context token consumptions, keyed by context id — every node's
 *  tooltip carries its elapsed + tokens alongside the call counts.
 *  Built once per segment build off the merged llm_usage rollup. */
export function buildUsageByContext(
  llmUsage: AgentLlmUsage[],
): Map<string, AgentLlmUsage> {
  const usageByContext = new Map<string, AgentLlmUsage>();
  for (const u of llmUsage) usageByContext.set(u.context_id, u);
  return usageByContext;
}

/** ONE NODE ONE CONTEXT, said on the canvas: the node's context id
 *  (id tail only — the data itself is never persisted) + the calls
 *  that ran under it. */
export const ctxTail = (contextId: string | null): string =>
  contextId ? ` · ctx ${contextId.slice(0, 6)}` : "";

export const fmtK = (n: number | null): string =>
  n === null || !Number.isFinite(n) ? "—" : `${(n / 1000).toFixed(1)}k`;

export const fmtElapsed = (s: number): string =>
  s < 60 ? `${s.toFixed(1)}s`
  : s < 3600
    ? `${Math.floor(s / 60)}m ${String(Math.floor(s % 60)).padStart(2, "0")}s`
    : `${Math.floor(s / 3600)}h ${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}m`;

/** The "n llm · n tools" counts fragment off a node item. The item's
 *  call counters can read 0 even though the conversation ran — they
 *  land only at finish (the writer's begin stamps running with no
 *  counts) AND the failure path settles without them — while the
 *  token tail (read live off the llm_requests rollup) already shows
 *  the consumption. Whenever the item carries no count, the llm count
 *  falls back to the context's usage rollup (n_requests — one
 *  provider request per loop call, the same chain the tail rolls up):
 *  live while running, recovered for failed settles; the tools clause
 *  has no such source and is omitted (it lands only on a successful
 *  finish). A genuinely idle node has no usage row and keeps "0 · 0". */
export const countsFragment = (
  nLlm: number,
  nTools: number,
  contextId: string | null,
  usageByContext: Map<string, AgentLlmUsage>,
): string => {
  if (nLlm > 0) return `${nLlm} llm · ${nTools} tools`;
  const u = contextId !== null ? usageByContext.get(contextId) : undefined;
  return u !== undefined
    ? `${u.n_requests} llm`
    : `${nLlm} llm · ${nTools} tools`;
};

/** A state row's settled wall time (its begin/end stamps are Shanghai-
 *  local naive text — the DIFF is zone-independent). Running rows have
 *  no end stamp; the store item's server-computed elapsed_s covers
 *  those on the canvas lane. */
export const stateElapsedS = (st: AgentThreadState): number | null => {
  if (st.started_at === null || st.finished_at === null) return null;
  const ms = new Date(st.finished_at).getTime()
    - new Date(st.started_at).getTime();
  return Number.isFinite(ms) && ms >= 0 ? ms / 1000 : null;
};

/** The node's consumption line for the tooltip: elapsed (the store
 *  item's server-computed value first — it also covers running nodes —
 *  then the state stamps) + the usage rollup of the node's ONE
 *  context. Empty when nothing ran. */
export const metricsTail = (
  elapsedS: number | null,
  contextId: string | null,
  usageByContext: Map<string, AgentLlmUsage>,
): string => {
  const parts: string[] = [];
  if (elapsedS !== null && Number.isFinite(elapsedS)) {
    parts.push(`elapsed ${fmtElapsed(elapsedS)}`);
  }
  const u = contextId !== null ? usageByContext.get(contextId) : undefined;
  if (u !== undefined) {
    // The provider type leads (the ext_llm_provider label the secrets
    // registry loaded — which TYPE produced these tokens); omitted on
    // legacy null rows.
    parts.push((u.provider !== null ? `${u.provider} · ` : "")
      + `${fmtK(u.total_tokens)} tok`
      + ` (${fmtK(u.input_tokens)} in · ${fmtK(u.output_tokens)} out)`);
  } else if (contextId !== null) {
    parts.push("— tok");
  }
  return parts.length > 0 ? ` · ${parts.join(" · ")}` : "";
};

/** A delegated step's consumption read off its SPAWNS (the same
 *  aggregation rule the sublabel's llm/tools counts follow): tokens
 *  summed over the spawns' contexts, elapsed as the spawns' span
 *  (earliest begin → latest end across the child stamps). */
export const spawnMetrics = (
  parentId: number,
  childrenByParent: Map<number, AgentThreadState[]>,
  usageByContext: Map<string, AgentLlmUsage>,
): string => {
  const children = childrenByParent.get(parentId) ?? [];
  let total: number | null = null;
  let begin: number | null = null;
  let end: number | null = null;
  for (const c of children) {
    const u = c.llm_context_id !== null
      ? usageByContext.get(c.llm_context_id)
      : undefined;
    if (u !== undefined) {
      total = (total ?? 0) + (u.total_tokens ?? 0);
    }
    if (c.started_at !== null) {
      begin = Math.min(begin ?? Infinity, new Date(c.started_at).getTime());
    }
    if (c.finished_at !== null) {
      end = Math.max(end ?? -Infinity, new Date(c.finished_at).getTime());
    }
  }
  const parts: string[] = [];
  if (begin !== null && end !== null && end >= begin) {
    parts.push(`elapsed ${fmtElapsed((end - begin) / 1000)}`);
  }
  if (total !== null) {
    parts.push(`${fmtK(total)} tok (spawns aggregated)`);
  }
  return parts.length > 0 ? ` · ${parts.join(" · ")}` : "";
};
