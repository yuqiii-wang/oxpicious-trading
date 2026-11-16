/**
 * SignalCrowdednessChart — the crowdedness FAMILY's chart on the
 * Short-Term Tech signals surfaces: the crowdedness CURVE chart (close
 * + trading amount + the crowdedness_score / crowdedness_score_ma5
 * curves from analysis.market_crowdedness via
 * /api/analysis/market-crowdedness/chart) with the family's signal
 * events as the shared buy ▲ / sell ▼ / no-action-diamond markers
 * anchored ON THE UNDERLYING INDEX CLOSE CURVE (user-directed — the
 * actions read on the traded curve; the crowdedness curves stay the
 * trigger evidence beside them) — the family plots its crowdedness
 * source, NOT the plain OHLC code trend (SignalCodeTrendChart, the
 * other families' chart). A click anywhere in the grid resolves to
 * its date through the same onDateClick contract the OHLC chart uses.
 *
 * Index-only by construction (the crowdedness pipeline covers index +
 * industry pools; the signals tier runs the family at index scope —
 * the family's picker option only exists where the code has
 * crowdedness rules/history rows).
 */
import { useMemo } from "react";
import {
  BaseChart,
  useChartThemeMode,
  useChartData,
} from "@/shared/charts/base-chart";
import { buildCrowdednessCurveOption } from "@/shared/charts/crowdednessCurveOption";
import type { SignalEventPoint } from "@/shared/charts/signalMarker";
import { stripCodeSuffix } from "@/ui/OhlcChart/codeTrendSources";
import {
  fetchMarketCrowdednessChart,
  type TradingSignal,
} from "@/lib/api-client";

/** Stable empty default for the events memo (a fresh [] per render
 * would dirty it every render — the notMerge rebuild tax). */
const NO_SIGNALS: TradingSignal[] = [];

export interface SignalCrowdednessChartProps {
  /** Security code — suffixed ("000001.SS") or bare ("000001"); the
   * crowdedness chart API takes the bare code. */
  code: string;
  /** Display name; falls back to the chart response's. */
  name?: string;
  /** The marker POPULATION — the parent's family-filtered subset of
   * the trading-signal history (the crowdedness pick's events). */
  population?: TradingSignal[];
  /** The SELECTED signal date — its marker(s) render enlarged with a
   * glow (the expanded date's emphasis). Null = none. */
  selectedSignalDate?: string | null;
  /** Grid-click → that date (the shared date-click contract — the
   * parent keeps only dates that carry signal records). */
  onDateClick?: (date: string) => void;
  /** Content height in px. Default 380 — the signals chart height. */
  height?: number;
}

export default function SignalCrowdednessChart({
  code,
  name,
  population,
  selectedSignalDate = null,
  onDateClick,
  height = 380,
}: SignalCrowdednessChartProps) {
  const themeMode = useChartThemeMode();
  const bare = stripCodeSuffix(code);
  // the crowdedness chart source itself (the rolling variant — the
  // signals' bars are the rolling score's own trailing quantiles)
  const { data, loading, error } = useChartData(
    () => fetchMarketCrowdednessChart(bare, "index"),
    [bare],
  );

  // the family's events as the shared marker layer's points
  const events = useMemo<SignalEventPoint[]>(
    () =>
      (population ?? NO_SIGNALS).map((h) => ({
        date: h.date,
        action: h.action,
        is_rejection: h.is_rejection ?? false,
      })),
    [population],
  );

  const option = useMemo(
    () =>
      data && data.rows.length > 0
        ? buildCrowdednessCurveOption(data, themeMode, {
            signalEvents: events,
            selectedDate: selectedSignalDate,
          })
        : null,
    [data, themeMode, events, selectedSignalDate],
  );

  const label = name || data?.name || code;

  return (
    <BaseChart
      title={`${label} — Crowdedness`}
      subtitle={
        data && data.rows.length > 0
          ? `${data.rows.length} daily bars · close + trading amount + crowdedness · the family's trigger source`
          : undefined
      }
      loading={loading}
      error={error ? `Failed to load crowdedness chart: ${error}` : null}
      option={option}
      emptyText={`No close history for ${label} — nothing to plot.`}
      height={height}
      onCanvasClick={
        onDateClick && data
          ? (idx) => {
              const date = data.rows[idx]?.date;
              if (date) onDateClick(date);
            }
          : undefined
      }
    />
  );
}
