/**
 * SentimentFeedAskButton — the sentiment feed heading's Ask AI "?".
 *
 * The feed is NOT a chart (no echarts instance — the modal skips the
 * screenshot capture), so this builds a no-chart plotInfo the same way
 * AnalysisItemAskButton / SignalRulesAskButton do. The ask follows the
 * PAGE's source-split analysis-item identity (the two composites items
 * ai-market-qa / market-sentiment-news — separate pages, each with its
 * own registry page; descriptions and metrics separate):
 *   • AI  → INSIGHT_PAGES.marketQa + the authored QA feed doc
 *     (ui.composites.ai-market-qa.feed): the keyword audit over the Q&A
 *     corpus (下跌/上涨, 放量/缩量, 利空/利好 occurrence counts with 10y
 *     rarity — text.llm_qa_keyword_audit), read against the news audit
 *     for tone divergence.
 *   • News → INSIGHT_PAGES.marketNews + the news feed doc
 *     (ui.composites.market-sentiment-news.feed): the keyword audit over
 *     the news corpus (the same families' occurrence counts —
 *     text.news_keyword_audit mentions).
 * The ask is LINKED TO THE CURRENT FEED SCOPE — the active source, the
 * mode's scope label (the classification pick / the top-ranked
 * HYPE+DRAIN industries), the live keyword search, and the picked day
 * (riding `state`, `window` as the ±DATE_WINDOW_DAYS pick window, and
 * `asOfDate` seeding the modal's as-of selector so the metrics table
 * re-resolves at that day). Rebuilt (useMemo) whenever any of those
 * move, so the modal and the LLM always see the CURRENT view.
 */
import { useMemo } from "react";
import { AiAskButton, INSIGHT_PAGES } from "@/shared/ai-ask";
import type { AiAskPlotInfo, InsightPageIdentity } from "@/shared/ai-ask";
import { useDocs } from "@/shared/docs";
import type { FeedSource } from "./useSentimentFeed";

/** The per-source split identity: doc id + insight page + product. */
const SOURCE_ASK: Record<
  FeedSource,
  { docId: string; insight: InsightPageIdentity; product: string }
> = {
  ai: {
    docId: "ui.composites.ai-market-qa.feed",
    insight: INSIGHT_PAGES.marketQa,
    product: "ai-market-qa",
  },
  news: {
    docId: "ui.composites.market-sentiment-news.feed",
    insight: INSIGHT_PAGES.marketNews,
    product: "market-sentiment-news",
  },
};

export default function SentimentFeedAskButton({
  feed,
  mode,
  scopeLabel,
  search,
  selectedDate,
  dateWindow,
}: {
  /** Active feed source — the heading the button sits beside. */
  feed: FeedSource;
  mode: "market" | "industry";
  /** Resolved feed scope label (the classification pick / top-ranked). */
  scopeLabel: string;
  /** Live keyword search (null = none). */
  search: string | null;
  /** Picked day (null = all days) — seeds the ask's as-of date. */
  selectedDate: string | null;
  /** The ±DATE_WINDOW_DAYS window around the pick (null = no pick). */
  dateWindow: { from: string; to: string } | null;
}) {
  const docs = useDocs();
  const title = feed === "ai" ? "AI 市场情绪问答" : "市场情绪新闻";
  const plotInfo = useMemo<AiAskPlotInfo>(() => {
    const source = SOURCE_ASK[feed];
    const state: Record<string, string> = {
      source: feed === "ai" ? "AI Q&A (text.llm_qa)" : "News (text.news)",
      mode: mode === "market" ? "Market" : "Industry",
      scope: scopeLabel,
    };
    if (search) state.keyword = search;
    if (selectedDate) state.day = selectedDate;
    return {
      chart: {
        kind: "feed",
        title,
        subtitle: feed === "ai" ? "AI Market Sentiments QA" : "Market Sentiments News",
        intro: docs.text(source.docId, {
          mode: mode === "market" ? "Market" : "Industry",
          scope: scopeLabel,
          keyword: search ?? undefined,
          date: selectedDate ?? undefined,
        }),
      },
      scope: { instruments: [] },
      window: dateWindow
        ? { start: dateWindow.from, end: dateWindow.to, granularity: "daily" }
        : {},
      series: [],
      state,
      searchKeywords: [title, scopeLabel, search ?? "", selectedDate ?? ""]
        .filter(Boolean),
      notes: docs.notes(source.docId),
      insight: source.insight,
      asOfDate: selectedDate ?? undefined,
      dateSelectable: true,
      product: source.product,
    };
  }, [docs, feed, mode, scopeLabel, search, selectedDate, dateWindow, title]);
  // No echarts instance — the modal skips the screenshot capture.
  return (
    <AiAskButton
      plotInfo={plotInfo}
      getInstance={() => null}
      label={`Ask AI about the ${feed === "ai" ? "AI Q&A" : "news"} feed`}
    />
  );
}
