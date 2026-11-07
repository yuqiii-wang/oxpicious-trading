/**
 * Static data for the Analysis AI section — the analysis-item tree (the
 * four analysis categories with their card items, reusing the exact
 * slug/title entries of the category index pages so new analyses added
 * there flow through) and the placeholder markdown shown in the summary
 * panel before the first generation. The Skills catalog is NOT static —
 * it is the skills-type rows of the llm.ai_prompts registry
 * (GET /api/ai/prompts).
 */
import { COMMONS_ANALYSES } from "@/analysis/pages/CommonsPage";
import { DERIVATIVES_ANALYSES } from "@/analysis/pages/DerivativesPage";
import { COMPOSITE_ANALYSES } from "@/analysis/pages/CompositesPage";
import { SIGNALS_ANALYSES } from "@/analysis/pages/SignalsPage";
import {
  fetchEtfStrategyThemes,
  fetchIndexStrategyThemes,
  fetchIndexThemes,
  fetchMovAveSpreadStrategyThemes,
  fetchMovAveSpreadThemes,
  fetchStockStrategyThemes,
  fetchStockThemes,
  fetchThemes,
} from "@/lib/api-client";
import type { SecNavSecType, SecNavThemesSource } from "@/shared/components/sec-nav";
import { INSIGHT_PAGES, type InsightPageIdentity } from "@/shared/ai-ask";
import type { AiPrompt } from "@/lib/api-client/aiSummary";

/** One selectable analysis item — the card projection of an index page's
 *  entry: the tree shows the title, the Ask AI button uses the description
 *  as its ask intro. */
export interface AnalysisItemEntry {
  /** Slug used in the item's category page URL. */
  slug: string;
  title: string;
  /** The card's short description of what the analysis computes. */
  description: string;
}

/** One category group of the analysis-item tree. */
export interface AnalysisCategoryEntry {
  /** Category slug — the top-nav path segment (/analysis/<slug>). */
  slug: string;
  title: string;
  items: AnalysisItemEntry[];
}

/** Stable `categorySlug/itemSlug` key for a tree leaf. */
export function analysisItemKey(category: AnalysisCategoryEntry, item: AnalysisItemEntry): string {
  return `${category.slug}/${item.slug}`;
}

/** Category sources in top-nav order — the card arrays of the index pages. */
const CATEGORY_SOURCES: Array<{
  slug: string;
  title: string;
  analyses: AnalysisItemEntry[];
}> = [
  { slug: "commons", title: "Commons", analyses: COMMONS_ANALYSES },
  { slug: "derivatives", title: "Derivatives", analyses: DERIVATIVES_ANALYSES },
  { slug: "composites", title: "Composites", analyses: COMPOSITE_ANALYSES },
  { slug: "signals", title: "Signals", analyses: SIGNALS_ANALYSES },
];

/** The nested analysis-item tree, in top-nav category order. */
export const ANALYSIS_CATEGORIES: AnalysisCategoryEntry[] = CATEGORY_SOURCES.map(
  (category) => ({
    slug: category.slug,
    title: category.title,
    items: category.analyses.map(({ slug, title, description }) => ({
      slug,
      title,
      description,
    })),
  }),
);

/**
 * Per-item Ask AI identity — the analysis item's PRIMARY registered
 * insight page (the lead chart's registry entry, via the INSIGHT_PAGES
 * mirror). Lets the item's ask modal show that page's metrics table; an
 * item missing from the map (future analyses) degrades to the plain ask
 * (the modal falls back to the series-tags strip). Card slugs differ from
 * registry page slugs where a page owns several charts — the lead entry
 * is chosen per the card's description.
 */
export const ASK_INSIGHT_BY_ITEM: Record<string, InsightPageIdentity> = {
  "commons/ma-spread": INSIGHT_PAGES.maSpread,
  "commons/market-beta": INSIGHT_PAGES.marketBeta,
  "commons/pe": INSIGHT_PAGES.peBaseline,
  "commons/dividends": INSIGHT_PAGES.dividendsBaseline,
  "commons/etf-holdings": INSIGHT_PAGES.etfHoldings,
  "derivatives/margin-trends": INSIGHT_PAGES.marginComposite,
  "derivatives/futures": INSIGHT_PAGES.futuresPriceCurves,
  "derivatives/options": INSIGHT_PAGES.interestWall,
  "composites/opposite-industry-correlations": INSIGHT_PAGES.offsetCorrelations,
  "composites/ai-market-qa": INSIGHT_PAGES.marketQa,
  "composites/market-sentiment-news": INSIGHT_PAGES.marketNews,
  "signals/recent-movements": INSIGHT_PAGES.signalCodeTrend,
  "signals/hedge-pairs": INSIGHT_PAGES.hedgePairCurves,
  "signals/options": INSIGHT_PAGES.optionsSignals,
};

/** All leaf keys, flattened — the "select all" universe. */
export const ALL_ANALYSIS_ITEM_KEYS: string[] = ANALYSIS_CATEGORIES.flatMap((category) =>
  category.items.map((item) => analysisItemKey(category, item)),
);

/**
 * The summary scope nav's classification-tree endpoints per sec_type —
 * the GENERIC per-sec_type trees (index / etf / stock baselines), and
 * for "industry" the industry-ENTITY trees (code = industry_id) under
 * their stats.sec_classification classification — the same two-column
 * shape the MA-Spread page's industry mode uses. The picked scope
 * (sec_type + code / industry id) rides in the generation request and
 * focuses the deep agent's metric queries. "all" has no tree — the nav
 * renders the locked exchange-only row and the run goes out unscoped
 * (market-wide).
 */
export const SUMMARY_THEMES_SOURCES: Record<Exclude<SecNavSecType, "all">, SecNavThemesSource> = {
  etf: {
    themes: (exchange) => fetchThemes(exchange),
    strategyThemes: (exchange) => fetchEtfStrategyThemes(exchange),
  },
  index: {
    themes: (exchange) => fetchIndexThemes(exchange),
    strategyThemes: (exchange) => fetchIndexStrategyThemes(exchange),
  },
  stock: {
    themes: (exchange) => fetchStockThemes(exchange),
    strategyThemes: (exchange) => fetchStockStrategyThemes(exchange),
  },
  industry: {
    themes: (exchange) => fetchMovAveSpreadThemes("industry", exchange),
    strategyThemes: (exchange) => fetchMovAveSpreadStrategyThemes("industry", exchange),
  },
};

/** Sec-type toggle order for the summary scope picker — "all" first: the
 *  locked exchange-only nav whose run scope stays ABSENT (market-wide). */
export const SUMMARY_SEC_TYPES: SecNavSecType[] = [
  "all",
  "industry",
  "index",
  "etf",
  "stock",
];

/** The study-scope plan-goal keys that apply only to a run NAMING codes
 *  ({{code}} carries the pick) — the complement family (study-market-*)
 *  applies only to an EMPTY pick (= all codes → the whole market).
 *  Mirrors the runner's mutually-exclusive family contract
 *  (llm_agents/deep_agents/planning/planner.py STUDY_SCOPE_PLAN_PROMPTS
 *  + default_scope_plan_key). */
const GOAL_CODES_ONLY_KEYS = new Set(["study-codes", "study-codes-related"]);
const GOAL_MARKET_ONLY_KEYS = new Set(["study-market-codes", "study-market-whole"]);

/** Whether a prompt row is selectable under the current scope pick:
 *  the codes-family goal rows need named codes, the market-family rows
 *  need the empty pick (all codes → whole market), and every non-goal
 *  row (and any future goal key outside the two families) applies
 *  regardless. Drives the Plan-goal radios' reactive enable/disable and
 *  the page's deselect-on-scope-change fallback to Auto. */
export function goalAppliesToScope(prompt: AiPrompt, scopeHasCodes: boolean): boolean {
  if (prompt.prompt_type_label !== "plan" || prompt.prompt_sub_type !== "goal") {
    return true;
  }
  if (GOAL_CODES_ONLY_KEYS.has(prompt.prompt_key)) return scopeHasCodes;
  if (GOAL_MARKET_ONLY_KEYS.has(prompt.prompt_key)) return !scopeHasCodes;
  return true;
}

/** Placeholder document shown in the summary markdown panel (before the
 *  first generation). */
export const SUMMARY_PLACEHOLDER_MD = `# Analysis Summary

The AI-generated summary for the **selected scopes** will be
rendered here as markdown.

## Workflow

1. Pick the scopes on the left (all selected by default).
2. Tick the prompts to apply, then hit **Generate**.
3. The deep agent grounds the scopes in the DB and writes the document
   into this panel (typically a couple of minutes).
`;
