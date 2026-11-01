/**
 * AnalysisItemAskButton — one analysis-item tree row's "Ask AI" "?".
 *
 * Wraps the SHARED AiAskButton with a no-chart plotInfo (`getInstance`
 * returning null — the modal skips the screenshot capture), so the ask
 * references the analysis item itself: the card description as the intro,
 * the item's registered insight page (ASK_INSIGHT_BY_ITEM) driving the
 * metrics table, the item slug as the persisted-ask product identity.
 * No instruments — the metrics resolve at the page scope (market-level
 * rows) and the classification-nav pick still narrows them via the
 * modal's industry context.
 *
 * Lives in the Ai scope (single consumer, like SignalRulesAskButton is
 * for the rules panel); the shared kit stays the only duplicated-nothing
 * surface — every host builds its own plotInfo.
 */
import { useMemo } from "react";
import { AiAskButton } from "@/shared/ai-ask";
import type { AiAskPlotInfo } from "@/shared/ai-ask";
import { ASK_INSIGHT_BY_ITEM, analysisItemKey } from "./constants";
import type { AnalysisCategoryEntry, AnalysisItemEntry } from "./constants";

export interface AnalysisItemAskButtonProps {
  category: AnalysisCategoryEntry;
  item: AnalysisItemEntry;
}

export default function AnalysisItemAskButton({
  category,
  item,
}: AnalysisItemAskButtonProps) {
  const plotInfo = useMemo<AiAskPlotInfo>(
    () => ({
      chart: {
        kind: "analysis",
        title: item.title,
        subtitle: category.title,
        intro: item.description,
      },
      scope: { instruments: [] },
      window: {},
      series: [],
      searchKeywords: [category.title, item.title],
      notes: [],
      insight: ASK_INSIGHT_BY_ITEM[analysisItemKey(category, item)],
      product: item.slug,
    }),
    [category, item],
  );
  return <AiAskButton plotInfo={plotInfo} getInstance={() => null} label={`Ask AI about ${item.title}`} />;
}
