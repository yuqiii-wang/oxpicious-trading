/**
 * INSIGHT_PAGES — the UI mirror of the analysis insight registry's page
 * identities (tab/page slugs of the insight registry —
 * analysis_insight_metrics.insight_templates, INSERT-seeded by
 * database/sql/analysis/static/analysis_insight_metrics/02..05, the per-tab
 * data files — and served by GET /api/analysis-insights/*).
 *
 * Chart authors attach `insight: INSIGHT_PAGES.x` to their AiAskSpec so
 * the Ask AI modal shows the page's metrics table instead of the
 * series-tags strip. Keep the slugs in sync with the SQL files (a typo
 * means no metrics — the modal then falls back to the tags strip).
 */
export const INSIGHT_PAGES = {
  // commons
  maSpread: { tab: "commons", page: "ma-spread" },
  peBaseline: { tab: "commons", page: "pe-baseline" },
  peStreaks: { tab: "commons", page: "pe-streaks" },
  dividendsBaseline: { tab: "commons", page: "dividends-baseline" },
  etfHoldings: { tab: "commons", page: "etf-holdings" },
  marketBeta: { tab: "commons", page: "market-beta" },
  marketTrend: { tab: "commons", page: "market-trend" },
  // derivatives
  marginComposite: { tab: "derivatives", page: "margin-composite" },
  hypedVsDrained: { tab: "derivatives", page: "hyped-vs-drained" },
  futuresPriceCurves: { tab: "derivatives", page: "futures-price-curves" },
  futuresCorr: { tab: "derivatives", page: "futures-corr" },
  futuresBasis: { tab: "derivatives", page: "futures-basis-convergence" },
  optionsTrend: { tab: "derivatives", page: "options-trend" },
  oiMoneynessSkew: { tab: "derivatives", page: "oi-moneyness-skew" },
  volSmile: { tab: "derivatives", page: "vol-smile" },
  volIndex: { tab: "derivatives", page: "vol-index" },
  interestWall: { tab: "derivatives", page: "interest-wall" },
  greeks: { tab: "derivatives", page: "greeks" },
  // composites
  offsetCorrelations: { tab: "composites", page: "offset-correlations" },
  pairHedgeRatio: { tab: "composites", page: "pair-hedge-ratio" },
  benchmarkPrice: { tab: "composites", page: "benchmark-price" },
  marketQa: { tab: "composites", page: "ai-market-qa" },
  marketNews: { tab: "composites", page: "market-sentiment-news" },
  // signals
  signalCodeTrend: { tab: "signals", page: "code-trend" },
  signalRules: { tab: "signals", page: "signal-rules" },
  hedgePairCurves: { tab: "signals", page: "hedge-pair-curves" },
  hedgeRatio: { tab: "signals", page: "hedge-ratio" },
  optionsSignals: { tab: "signals", page: "options-signals" },
} as const;

export type InsightPageIdentity = { tab: string; page: string };
