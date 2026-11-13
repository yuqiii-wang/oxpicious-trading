/** Shared formatters + truncation constants of the ThreadGraph rows and
 *  the Contexts panel. */
import type { AgentLlmUsage } from "@/lib/api-client/aiThreads";

/** Seconds → compact elapsed text ("42.3s" / "1m 12s" / "1h 03m"). */
export function formatElapsedS(s: number | null): string {
  if (s === null || !Number.isFinite(s)) return "—";
  if (s < 60) return `${s.toFixed(1)}s`;
  if (s < 3600) {
    return `${Math.floor(s / 60)}m ${String(Math.floor(s % 60)).padStart(2, "0")}s`;
  }
  return `${Math.floor(s / 3600)}h ${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}m`;
}

/** Token count → the k unit ("18.3k" — the Contexts panel's consumption
 *  scale; sub-k counts keep the one-decimal k form). */
export function formatTokensK(n: number | null): string {
  return n === null ? "—" : `${(n / 1000).toFixed(1)}k`;
}

/** The node/context consumption tail — "zhipu · 12.4k tok (8.1k in ·
 *  4.3k out · 3.1k cached)" off the context's llm_usage rollup; empty
 *  when nothing recorded. The provider type leads (the ext_llm_provider
 *  label the secrets registry loaded at agent start — which TYPE served
 *  the tokens; omitted on legacy null). cached = the history the
 *  chain's requests re-send on every tool turn (the overlapping prefix
 *  chained by prev_request_id) — the provider's context-cache lane,
 *  already inside `in`, so the truly new input = in − cached. Omitted
 *  for single-request contexts. */
export function usageTail(usage: AgentLlmUsage | null): string {
  if (usage === null) return "";
  return (usage.provider !== null ? ` · ${usage.provider}` : "")
    + ` · ${formatTokensK(usage.total_tokens)} tok`
    + ` (${formatTokensK(usage.input_tokens)} in`
    + ` · ${formatTokensK(usage.output_tokens)} out`
    + (usage.cache_tokens > 0
        ? ` · ${formatTokensK(usage.cache_tokens)} cached`
        : "")
    + ")";
}

/** A chat message's content beyond this many chars renders truncated
 *  with a show-full toggle (the markdown parse + mount of tens-of-KB
 *  bodies is the panel's freeze). */
export const LONG_CONTENT_CHARS = 8000;

/** A task's request list renders at most this many latest rows until
 *  "show all" (a spawn child's chain records 30+ calls). */
export const INITIAL_REQUEST_ROWS = 8;
