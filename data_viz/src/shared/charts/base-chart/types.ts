/**
 * Common props contract for every chart component in data_viz.
 *
 * Chart components declare `interface Props extends BaseChartProps { ... }`
 * and render `<BaseChart>` — the base owns the card chrome, the async-state
 * placeholders, and the EChart passthroughs, so a chart component only
 * declares its data-specific props.
 *
 * Theme is intentionally NOT a prop: charts resolve it reactively from the
 * global store via `useChartThemeMode()` (single source of truth — no prop
 * drilling, no stale non-reactive reads).
 */
import type { ReactNode } from "react";
import type { EChartProps } from "@/components/EChart";
import type { AiAskSpec } from "@/shared/ai-ask";

export interface BaseChartProps
  extends Pick<
    EChartProps,
    "height" | "minHeight" | "group" | "onReady" | "onEvents" | "onCanvasClick"
  > {
  /**
   * Fully-built ECharts option. When null/undefined the base renders the
   * shared loading / error / empty placeholder instead of the chart.
   * Components with their own empty-state option (e.g. a centered
   * "[No data]" title via `emptyChartOption`) always pass an option.
   */
  option?: EChartProps["option"] | null;

  /**
   * Card chrome. "card" (default) wraps the chart in the shared ChartCard
   * (title + subtitle + header action); "bare" renders just the chart body —
   * for charts embedded in an outer card or custom layout.
   */
  variant?: "card" | "bare";
  /** Card header title (card variant only). */
  title?: string;
  /** Card header subtitle (card variant only). */
  subtitle?: string;
  /**
   * AI Ask — the "?" beside the title (card variant only). `undefined`
   * (default) shows it with auto-derived plot info from the built option;
   * a spec adds the chart's semantics (intro, instruments, series units);
   * `null` hides it. See shared/ai-ask.
   */
  aiAsk?: AiAskSpec | null;
  /** Right-aligned action in the card header — toggle groups, menus. */
  headerAction?: ReactNode;
  /**
   * Content rendered between the card header and the chart body — filters,
   * toggles, sub-view switches. Rendered in EVERY state (also while
   * loading / error), matching the controls-always-visible convention of
   * the existing panels.
   */
  children?: ReactNode;

  // --- async lifecycle states, rendered by the shared placeholders ---

  /**
   * True while fetching. Without an option yet, a centered spinner replaces
   * the chart; with an option already rendered (background refresh) the
   * chart stays mounted — keeping zoom / tooltip state — with a small
   * non-blocking spinner on top (or the `freezeOnLoading` backdrop).
   */
  loading?: boolean;
  /**
   * Freeze mode for refreshes while an option is mounted: instead of the
   * small corner spinner, a translucent full-cover backdrop with a centered
   * spinner overlays the plot and swallows pointer events — the plot can't
   * be zoomed / clicked until the fetch resolves. No effect before the
   * first option exists (the placeholder spinner already replaces the
   * chart).
   */
  freezeOnLoading?: boolean;
  /** Error message; replaces the chart body with an error Alert. */
  error?: string | null;
  /** Placeholder text when there is no option to render. Default "No data". */
  emptyText?: string;
}
