/**
 * codeTrendAiAsk — the AI Ask "?" button of CodeTrendChart's card header.
 * Builds the plot info (intro / instruments / window / state / search
 * keywords) from the chart's current state and renders the shared
 * AiAskButton. Split out of CodeTrendChart.tsx to keep the class shell
 * thin.
 */
import { AiAskButton, derivePlotInfo } from "@/shared/ai-ask";
import { useDocs } from "@/shared/docs";
import type { ECharts } from "echarts";
import type { OhlcMode } from "@/lib/ohlc";
import type { CodeTrendRow, CodeTrendSecType } from "./codeTrendTypes";

export function CodeTrendAiAsk({
  code,
  displayName,
  secType,
  allRows,
  windowedRows,
  ohlcMode,
  showRegimes,
  sliderOn,
  aiSearchKeywords,
  insightPage,
  getInstance,
}: {
  code: string;
  /** Resolved display name (prop name, else API name, else "—"). */
  displayName: string;
  secType: CodeTrendSecType;
  /** Full loaded history (bar count + date span in the subtitle). */
  allRows: CodeTrendRow[];
  /** Rows currently visible through the windowing (slider or full). */
  windowedRows: CodeTrendRow[];
  ohlcMode: OhlcMode;
  showRegimes: boolean;
  /** Whether the MUI date-range slider (vs the in-chart dataZoom) owns
   *  the window. */
  sliderOn: boolean;
  /** Extra page-level search seed keywords (the aiSearchKeywords prop),
   *  merged ahead of the auto-derived toggle keywords. */
  aiSearchKeywords?: string[];
  /** Insight-registry item for the AI-Ask metrics table — spread in only
   *  when set (undefined must not land as a spec key). */
  insightPage?: { tab: string; page: string };
  /** Live inner-chart instance getter for the screenshot. */
  getInstance: () => ECharts | null;
}) {
  // Plot info for the "?" beside the title. The inner OhlcChart
  // builds its option internally, so the intro + live screenshot carry
  // the value (derivePlotInfo tolerates the omitted option). A fresh
  // plotInfo per render is fine — the button keeps its own open state.
  // Authored text: static.docs `ui.code-trend.panel`.
  const docs = useDocs();
  const nBars = allRows.length;
  return (
    <AiAskButton
      plotInfo={derivePlotInfo({
        title: `${code} · ${displayName}`,
        subtitle: nBars > 0
          ? `${nBars} bars · ${allRows[0].date} → ${allRows[allRows.length - 1].date}`
          : undefined,
        spec: {
          ...(insightPage ? { insight: insightPage } : {}),
          intro: docs.text("ui.code-trend.panel", { code, secType }),
          instruments: [
            {
              code,
              ...(displayName !== "—" ? { name: displayName } : {}),
              assetClass: secType,
            },
          ],
          window: windowedRows.length > 0
            ? {
                start: windowedRows[0].date,
                end: windowedRows[windowedRows.length - 1].date,
                granularity: "daily",
              }
            : undefined,
          state: {
            mode: ohlcMode,
            regime_shading: showRegimes,
            window: sliderOn ? "date-range slider" : "in-chart dataZoom",
          },
          searchKeywords: [
            ...(aiSearchKeywords ?? []),
            // the header toggles' active states — items of interest a
            // web search can use (trend terms + the shaded episodes)
            ...(showRegimes ? ["market regimes"] : []),
          ],
          notes: docs.notes("ui.code-trend.panel"),
        },
      })}
      getInstance={getInstance}
    />
  );
}
