/**
 * Market Sentiments News (市场情绪新闻) — Composites detail page
 * (/analysis/composites/market-sentiment-news): the raw news corpus feed
 * (text.news — NewsPostCard posts; zhihu market recaps, gov.cn policy,
 * PBoC operations, with comments). The page is the SHARED sentiments
 * composition (../MarketSentiments/SentimentsPage — nav, benchmark/
 * market-trend plot, date strip, feed) FIXED to the news source; its
 * asks (charts + the feed heading's "?") resolve the marketNews
 * keyword-audit registry page (the news corpus's keyword occurrences —
 * read against the sibling AI QA page's audit for AI-vs-news tone
 * divergence).
 */
import { INSIGHT_PAGES } from "@/shared/ai-ask";
import SentimentsPage from "../MarketSentiments/SentimentsPage";

export default function MarketSentimentNewsPage() {
  return (
    <SentimentsPage
      source="news"
      title="Market Sentiments News (市场情绪新闻)"
      dataLabel="market-sentiment-news"
      chartInsight={INSIGHT_PAGES.marketNews}
    />
  );
}
