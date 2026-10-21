/**
 * SignalRulesAskButton — the RULES panel's Ask AI "?". The rules surface
 * is a TABLE (no echarts instance), so the modal runs without a
 * screenshot and — the panel being the registered `signals/signal-rules`
 * analysis item — its metrics table REPLACES the series-tags strip: the
 * rule-cell inputs (trend state, the RSI 14/10/6 grid, the momentum
 * axes) resolved for the host's security, from
 * analysis_insights.insight_templates.
 *
 * Rendered by the hosts that own the ask context (the Recent Movements
 * panel's picked security; the Options page's option underlying) beside
 * the shared "Signal rules" title — one spec here so the two pages
 * cannot drift.
 */
import { useMemo } from "react";
import { AiAskButton, INSIGHT_PAGES } from "@/shared/ai-ask";
import type { AiAskPlotInfo } from "@/shared/ai-ask";

export interface SignalRulesAskButtonProps {
  /** The ask's security type (label vocabulary: etf / index / stock). */
  secType: "etf" | "index" | "stock";
  /** The ask's security code — the cells' own code (or the option
   *  underlying on the Options page). */
  code: string;
  /** Display name, e.g. "上证指数". */
  name?: string;
  /** The host's SIGNAL family pick — rides the ask state so the adviser
   *  knows which family's cells the user reads. */
  signalType?: string | null;
}

export default function SignalRulesAskButton({
  secType,
  code,
  name,
  signalType,
}: SignalRulesAskButtonProps) {
  const plotInfo = useMemo<AiAskPlotInfo>(() => {
    const state: Record<string, string> = {};
    if (signalType) state.signal = signalType;
    return {
      chart: {
        kind: "table",
        title: "Signal rules — self-adaptive cells",
        subtitle: name,
        intro:
          `Rule cells of ${name ?? code} (${secType}) — one row per ` +
          "(sub signal · trend · regime · action) key of the picked " +
          "family: the cell's CONSTANT breach bar (self-adaptively picked " +
          "per key on the code's own history), its gate verdict and its " +
          "measured evidence (episodes · blended forward return · hit). " +
          "A cell fires when the live value pierces its bar from the " +
          "trigger side under a matching day context; buys and sells are " +
          "NOT mirror images — sharp falls revert (buy the capitulation), " +
          "sharp rises continue (sells are exit timing). The metrics table " +
          "below resolves the cells' INPUT values (trend state, the RSI " +
          "14/10/6 entry grid, the momentum axes) for this security at " +
          "the latest close.",
      },
      scope: { instruments: [{ code, name, assetClass: secType }] },
      window: {},
      series: [],
      state: Object.keys(state).length > 0 ? state : undefined,
      searchKeywords: ["signal rules", name ?? code, signalType ?? ""]
        .filter(Boolean),
      notes: [
        "Rule cells are the CURRENT measured state (signal_rules is wiped " +
          "and re-inserted per pipeline run) — there is no as-of replay; " +
          "the metrics table resolves at the latest available close.",
        "Confidence is the cell's measured dir_ave (the fixed " +
          "0/3/5/10/20d blend), depth- and sample-scaled — never a fitted " +
          "curve; a 'Valid ✓' cell cleared its side's measured bar.",
      ],
      insight: INSIGHT_PAGES.signalRules,
      product: "signal-rules",
    };
  }, [secType, code, name, signalType]);
  // No echarts instance — the modal skips the screenshot capture.
  return <AiAskButton plotInfo={plotInfo} getInstance={() => null} />;
}
