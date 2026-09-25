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
  /** Stable product identity for the persisted ask history (keyword
   *  search / the AiPage "QA by Ask" feed), e.g. "forecast",
   *  "options-greeks". When omitted the ask falls back to the submitting
   *  page's route path (plotInfo.page, set by AiAskModal). */
  product?: string;
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
  /** Free-form caveats worth telling the LLM (e.g. "rebased to 100"). */
  notes?: string[];
  /**
   * Whether the modal gets the as-of date selector (pin the question to an
   * exact date "YYYY-MM-DD" or a year-month "YYYY-MM"). Omitted =
   * auto-detect: on when the plotted window (x-axis labels or this spec's
   * window) parses as dates, off otherwise. Set false to force it off for
   * a date-looking chart whose dates are meaningless (e.g. rebased
   * buckets), true to force it on when the derivation can't see dates.
   */
  dateSelectable?: boolean;
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
  /** Value unit. Auto-derived as "%" when the series' value axis is
   *  percent-encoded (its label formatter renders "%") — in that case the
   *  stats are rescaled into what the axis displays (percent points, e.g.
   *  0.61 for a plotted fraction of 0.0061). The spec's per-series unit
   *  overrides it. */
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
  notes: string[];
  /** Product identity for the persisted ask history (the spec's `product`;
   *  absent when the author gave none — the backend then uses `page`). */
  product?: string;
  /** Route path the ask was submitted from (set by AiAskModal at submit) —
   *  the product fallback for ask-history keyword search. */
  page?: string;
  /**
   * Pinned as-of date for the ask — "YYYY-MM-DD" or "YYYY-MM" (the
   * modal's date selector, seeded with the chart's latest plotted date
   * and editable per ask). The adviser treats it as "now" for
   * time-relative questions; absent when the user cleared the field or
   * the chart has no date selector.
   */
  date?: string;
  /**
   * Whether the modal shows the as-of date selector (computed by
   * derivePlotInfo: the spec's `dateSelectable` when set, else whether
   * the plotted window parses as dates).
   */
  dateSelectable?: boolean;
}
