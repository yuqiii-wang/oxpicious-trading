/**
 * AI Ask client — submit a chart question (+ plot info + canvas screenshot)
 * to the llm_ask chart-adviser agent behind POST /api/ai/ask.
 *
 * Deliberately NOT routed through fetchJson/_retry: it is a POST (nothing to
 * cache) that synchronously spawns a WSL python process server-side, so it
 * gets one generous-timeout attempt and surfaces errors to the modal — a
 * blind retry could double-submit the question to the LLM.
 */
import type { AiAskPlotInfo } from "@/shared/ai-ask";
import type { ThemeMode } from "@/store/filters";

export interface AiAskRequest {
  question: string;
  plotInfo: AiAskPlotInfo;
  /** PNG data URLs of the chart canvas(es) ("data:image/png;base64,…"),
   *  captured at submit time so they reflect the user's current zoom
   *  viewport; one per stacked chart in the card. */
  screenshots: string[];
  themeMode: ThemeMode;
  /** The modal's "online search" tick — route the ask through the
   *  online-search agent (web search + cited summary) instead of the
   *  plain chart-adviser completion. */
  onlineSearch?: boolean;
  /** Web-search term for the online-search path (the modal's search line,
   *  seeded with chart title + date) — the engine searches this instead of
   *  the question. Ignored when onlineSearch is false; falls back to the
   *  question server-side when absent/blank. */
  searchQuery?: string;
}

export interface AiAskResponse {
  success: boolean;
  answer: string;
  /** Model that produced the answer (e.g. "glm-5.2"; a vision model such
   *  as "glm-4.5v" when screenshots were attached). */
  model?: string;
  error?: string;
}

/** One WSL python spawn + (later) one LLM call — see file header. */
const AI_ASK_TIMEOUT_MS = 120_000;

export async function askChartAi(req: AiAskRequest): Promise<AiAskResponse> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), AI_ASK_TIMEOUT_MS);
  try {
    const res = await fetch("/api/ai/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
      signal: controller.signal,
    });
    const text = await res.text();
    if (!res.ok) {
      throw new Error(
        `AI ask failed (HTTP ${res.status}): ${text.slice(0, 300)}`,
      );
    }
    return JSON.parse(text) as AiAskResponse;
  } finally {
    clearTimeout(timer);
  }
}
