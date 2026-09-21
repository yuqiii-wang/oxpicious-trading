/**
 * AI Ask — the per-chart "ask the AI" feature.
 *
 * Public surface:
 *   - `<AiAskButton>`  — the "?" beside a chart title; owns the modal.
 *   - `<AiAskModal>`   — intro + question box + screenshot capture + answer;
 *     clicking the intro turns to the full description view (back arrow in
 *     the dialog's upper-left corner returns).
 *   - `derivePlotInfo` — auto plot-info from a built ECharts option, merged
 *     with the author's `AiAskSpec`.
 *
 * BaseChart renders the button automatically for every card-variant chart
 * (`aiAsk` prop: undefined = auto-derived info, a spec = rich info,
 * null = hidden). Charts outside BaseChart adopt it by rendering
 * `<AiAskButton>` next to their own title.
 */
export { default as AiAskButton } from "./AiAskButton";
export { default as AiAskModal } from "./AiAskModal";
export { derivePlotInfo } from "./derivePlotInfo";
export { useAiAskAddon } from "./useAiAskAddon";
export type { UseAiAskAddonArgs } from "./useAiAskAddon";
export type {
  AiAskSpec,
  AiAskPlotInfo,
  AiAskInstrument,
  AiAskGranularity,
  AiAskSeriesInfo,
  AiAskSeriesStat,
} from "./types";
