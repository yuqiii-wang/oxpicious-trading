/**
 * AI Ask — shared types for the chart-level "ask the AI" feature.
 *
 * Two layers:
 *   - `AiAskSpec` — what a chart AUTHOR passes via BaseChart's `aiAsk` prop:
 *     the human-written semantics (intro, instruments, series meaning).
 *     Optional per chart; when absent the feature falls back to auto-derived
 *     plot info (see derivePlotInfo.ts).
 *   - `AiAskPlotInfo` — the complete payload sent to the backend: the spec
 *     merged with what can be derived mechanically from the built ECharts
 *     option (series types, per-series stats, x-axis window), plus the global
 *     filter context injected at submit time.
 *
 * The contract is deliberately minimal-but-rich for financial advice: chart
 * identity + how to read it, instrument scope, time window/granularity,
 * per-series semantics + summary stats. The visual structure travels as the
 * canvas screenshot, not as data.
 */

/** An instrument (or instrument-like scope) a chart is about. */
export interface AiAskInstrument {
  /** Ticker / code, e.g. "000300", "159919", "BANKS". */
  code: string;
  /** Human name, e.g. "沪深300". */
  name?: string;
  assetClass?: "stock" | "etf" | "index" | "futures" | "option" | "industry";
}

export type AiAskGranularity = "daily" | "weekly" | "monthly" | "intraday";

/** Author-facing spec — BaseChart's `aiAsk` prop. */
export interface AiAskSpec {
  /** What this chart shows and how to read it — shown in the modal and
   *  sent to the LLM as the chart explanation. */
  intro: string;
  /** The instruments / scopes plotted (any dimension the LLM should treat
   *  as the tradeable subject). */
  instruments?: AiAskInstrument[];
  /** Plotted time window. Auto-derived from the x-axis when omitted. */
  window?: { start?: string; end?: string; granularity?: AiAskGranularity };
  /** Semantic layer merged over the auto-derived series stats, matched by
   *  series name — units and what each series means. */
  series?: { name: string; unit?: string; description?: string }[];
  /**
   * Current in-plot control state (toggle / dropdown selections), e.g.
   * `{ mode: "Buy", window: "20d" }`. Rebuild the spec (useMemo deps on
   * the control variables) whenever a control changes so the modal and
   * the LLM always see the CURRENT view, not the default one.
   */
  state?: Record<string, string | number | boolean>;
  /**
   * Search-engine keywords for the modal's online-search seed line — the
   * chart's items of interest (instrument/index names, the active
   * indicator toggles/buttons: RSI, Bollinger, MA cross, …). The seed is
   * `<title> <keywords…> <last plotted date>`; auto-derived from
   * instruments + series names when omitted. Same rebuild rule as
   * `state`: the keywords must name what the user is looking at NOW, so
   * put the ACTIVE toggle labels here and memo on the control variables.
   */
  searchKeywords?: string[];
  /**
   * Chart-specific question seeds, phrased as the user would ask them
   * (max ~3). Shown as click-to-fill chips above the question box; the
   * first one becomes the box's placeholder. This is where a chart's
   * former long reading-guide subtitle gets repurposed — the guide moves
   * to `intro`, its actionable angle becomes these questions.
   */
  suggestedQuestions?: string[];
  /** Free-form caveats worth telling the LLM (e.g. "rebased to 100"). */
  notes?: string[];
}

/** Best-effort numeric summary of one series' plotted values. */
export interface AiAskSeriesStat {
  /** Number of plotted data points. */
  count: number;
  first?: number;
  last?: number;
  min?: number;
  max?: number;
}

/** One series as sent to the backend. */
export interface AiAskSeriesInfo {
  name: string;
  /** ECharts series type (line, bar, candlestick, pie, …). */
  kind: string;
  unit?: string;
  description?: string;
  stats?: AiAskSeriesStat;
}

/** The full plot-info payload POSTed to /api/ai/ask. */
export interface AiAskPlotInfo {
  chart: {
    /** Composite of the plotted series types, e.g. "candlestick + line". */
    kind: string;
    title: string;
    subtitle?: string;
    intro: string;
  };
  scope: {
    instruments: AiAskInstrument[];
    industry?: string | null;
    sector?: string | null;
  };
  window: { start?: string; end?: string; granularity?: AiAskGranularity };
  series: AiAskSeriesInfo[];
  /** Current in-plot control state (from the spec) — what the user is
   *  looking at RIGHT NOW. */
  state?: Record<string, string | number | boolean>;
  /** Search-engine keywords for the modal's online-search seed (from the
   *  spec, auto-derived from instruments + series names when the author
   *  provides none) — instrument names + active indicator tags. */
  searchKeywords?: string[];
  /** Chart-specific question seeds (from the spec) — click-to-fill chips in
   *  the modal; UI affordance, the backend ignores them. */
  suggestedQuestions?: string[];
  notes: string[];
}
