/**
 * SignalRulesAskButton — the RULES panel's Ask AI "?". The rules surface
 * is a TABLE (no echarts instance), so the modal runs without a
 * screenshot and — the panel being the registered `signals/signal-rules`
 * analysis item — its metrics table REPLACES the series-tags strip: the
 * rule-cell inputs (trend state, the RSI 14/10/6 grid, the momentum
 * axes) resolved for the host's security, from
 * analysis_insight_metrics.insight_templates.
 *
 * Rendered by the hosts that own the ask context (the Recent Movements
 * panel's picked security; the Options page's option underlying) beside
 * the shared "Signal rules" title — one spec here so the two pages
 * cannot drift.
 */
import { useMemo } from "react";
import { AiAskButton, INSIGHT_PAGES } from "@/shared/ai-ask";
import type { AiAskPlotInfo } from "@/shared/ai-ask";
import { useDocs } from "@/shared/docs";

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
  const docs = useDocs();
  const plotInfo = useMemo<AiAskPlotInfo>(() => {
    const state: Record<string, string> = {};
    if (signalType) state.signal = signalType;
    return {
      chart: {
        kind: "table",
        title: "Signal rules — self-adaptive cells",
        subtitle: name,
        intro: docs.text("ui.signal-rules.ask", {
          name: name ?? code,
          code,
          secType,
        }),
      },
      scope: { instruments: [{ code, name, assetClass: secType }] },
      window: {},
      series: [],
      state: Object.keys(state).length > 0 ? state : undefined,
      searchKeywords: ["signal rules", name ?? code, signalType ?? ""]
        .filter(Boolean),
      notes: docs.notes("ui.signal-rules.ask"),
      insight: INSIGHT_PAGES.signalRules,
      product: "signal-rules",
    };
  }, [docs, secType, code, name, signalType]);
  // No echarts instance — the modal skips the screenshot capture.
  return <AiAskButton plotInfo={plotInfo} getInstance={() => null} />;
}
