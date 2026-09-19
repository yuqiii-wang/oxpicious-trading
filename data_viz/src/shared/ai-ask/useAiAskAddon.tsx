/**
 * AI Ask — one-line integration hook for chart surfaces that do NOT render
 * through BaseChart (ChartCard-direct panels, raw-EChart pages, class
 * charts). BaseChart owns this wiring internally; everyone else calls:
 *
 *   const aiAskRef = useRef<ECharts | null>(null);           // onReady={...}
 *   const option = useMemo(() => buildX(...), [...]);        // hoisted option
 *   const aiAskSpec = useMemo<AiAskSpec>(() => ({...}), [controlState]);
 *   titleAddon={useAiAskAddon({
 *     title, subtitle, option, spec: aiAskSpec,
 *     getInstance: () => aiAskRef.current,
 *   })}
 *
 * For multi-chart cards pass extraOptions / getExtraInstances for the
 * stacked charts — series merge into one plot info and each live instance
 * contributes a screenshot. Returns undefined when `spec` is null (hidden).
 *
 * Keep `option` / `extraOptions` / `spec` identity-stable (useMemo) — the
 * addon re-derives plot info whenever they change, matching the
 * option-memo rule in the ui-charts conventions.
 */
import { useMemo, useRef, type ReactNode } from "react";
import type { ECharts, EChartsOption } from "echarts";
import AiAskButton from "./AiAskButton";
import { derivePlotInfo } from "./derivePlotInfo";
import type { AiAskSpec } from "./types";

export interface UseAiAskAddonArgs {
  title?: string;
  subtitle?: string;
  /** Built option of the primary chart (series stats + window derive from it). */
  option?: EChartsOption | null;
  /** Built options of additional stacked charts in the same card. */
  extraOptions?: ReadonlyArray<EChartsOption | null | undefined>;
  /** null hides the "?" — same contract as BaseChart's aiAsk prop. */
  spec?: AiAskSpec | null;
  getInstance: () => ECharts | null;
  getExtraInstances?: () => (ECharts | null)[];
}

export function useAiAskAddon({
  title,
  subtitle,
  option,
  extraOptions,
  spec,
  getInstance,
  getExtraInstances,
}: UseAiAskAddonArgs): ReactNode {
  const getInstanceRef = useRef(getInstance);
  getInstanceRef.current = getInstance;
  const getExtraRef = useRef(getExtraInstances);
  getExtraRef.current = getExtraInstances;

  const addon = useMemo(() => {
    if (spec === null) return undefined;
    const plotInfo = derivePlotInfo({ title, subtitle, option, extraOptions, spec });
    return (
      <AiAskButton
        plotInfo={plotInfo}
        getInstance={() => getInstanceRef.current()}
        getExtraInstances={getExtraRef.current ? () => getExtraRef.current?.() ?? [] : undefined}
      />
    );
    // derivePlotInfo output depends on option/spec identities; refs keep the
    // instance accessors fresh without re-rendering the button.
  }, [title, subtitle, option, extraOptions, spec]);

  return addon;
}
