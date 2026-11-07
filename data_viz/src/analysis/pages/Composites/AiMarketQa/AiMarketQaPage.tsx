/**
 * AI Market Sentiments QA (AI 市场情绪问答) — Composites detail page
 * (/analysis/composites/ai-market-qa): the LLM Q&A knowledge base feed
 * (llm.llm_qa — AiQaCard posts; question, answer, per-ref citations).
 * The page is the SHARED sentiments composition (../MarketSentiments/
 * SentimentsPage — nav, benchmark/market-trend plot, date strip, feed)
 * FIXED to the AI source; its asks (charts + the feed heading's "?")
 * resolve the marketQa keyword-audit registry page (the Q&A corpus's
 * keyword occurrences — read against the sibling News page's audit for
 * AI-vs-news tone divergence). The legacy combined slug
 * /ai-market-sentiments redirects here.
 */
import { INSIGHT_PAGES } from "@/shared/ai-ask";
import SentimentsPage from "../MarketSentiments/SentimentsPage";

export default function AiMarketQaPage() {
  return (
    <SentimentsPage
      source="ai"
      title="AI Market Sentiments QA (AI 市场情绪问答)"
      dataLabel="ai-market-qa"
      chartInsight={INSIGHT_PAGES.marketQa}
    />
  );
}
