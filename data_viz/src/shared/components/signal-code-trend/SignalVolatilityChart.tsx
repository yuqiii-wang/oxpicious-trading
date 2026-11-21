/**
 * SignalVolatilityChart — the volatility FAMILY's chart on the
 * Short-Term Tech signals surfaces: the Commons Volatility page's TOP
 * chart (close + HV 20d + EWMA λ=0.94, percent annualized — the
 * realized-vol panel via /api/analysis/volatility/series) with the
 * family's signal events as the shared buy ▲ / sell ▼ / no-action /
 * reject markers anchored ON THE CLOSE CURVE (the crowdedness chart's
 * treatment — the actions read on the traded curve; the vol curves
 * stay the trigger evidence beside them) — the family plots its vol
 * source, NOT the plain OHLC code trend (SignalCodeTrendChart, the
 * other families' chart). A click anywhere in the grid resolves to
 * its date through the same onDateClick contract the OHLC chart uses.
 *
 * Index-only by construction (the vol panel runs index only — the
 * family's picker option only exists where the code has volatility
 * rules/history rows).
 */
import { useMemo } from "react";
import {
  BaseChart,
  useChartThemeMode,
  useChartData,
} from "@/shared/charts/base-chart";
import { buildVolSignalCurveOption } from "@/shared/charts/volSignalCurveOption";
import type { SignalEventPoint } from "@/shared/charts/signalMarker";
import { stripCodeSuffix } from "@/ui/OhlcChart/codeTrendSources";
import {
  fetchVolatilitySeries,
  type TradingSignal,
  type VolatilitySecType,
} from "@/lib/api-client";

/** Stable empty default for the events memo (a fresh [] per render
 * would dirty it every render — the notMerge rebuild tax). */
const NO_SIGNALS: TradingSignal[] = [];

export interface SignalVolatilityChartProps {
  /** Security code — suffixed ("000001.SS") or bare ("000001"); the
   * vol series API takes the bare code. */
  code: string;
  /** Display name; falls back to the API's. */
  name?: string;
  /** The security type — index while the vol panel runs index only. */
  secType: VolatilitySecType;
  /** The marker POPULATION — the parent's family-filtered subset of
   * the trading-signal history (the volatility pick's events). */
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

export default function SignalVolatilityChart({
  code,
  name,
  secType,
  population,
  selectedSignalDate = null,
  onDateClick,
  height = 380,
}: SignalVolatilityChartProps) {
  const themeMode = useChartThemeMode();
  const bare = stripCodeSuffix(code);
  // the vol panel source itself (the family's trigger axes hv_20 /
  // ewma_vol ride these rows; the bars are the panel's trailing-year
  // quantiles)
  const { data, loading, error } = useChartData(
    () => fetchVolatilitySeries(bare, secType),
    [bare, secType],
  );

  // the family's events as the shared marker layer's points
  const events = useMemo<SignalEventPoint[]>(
    () =>
      (population ?? NO_SIGNALS).map((h) => ({
        date: h.date,
        action: h.action,
        is_rejection: h.is_rejection ?? false,
        // NEGATIVE = the REJECT tier (the cross-triangle marker)
        confidence: h.confidence_pct,
      })),
    [population],
  );

  const option = useMemo(
    () =>
      data && data.rows.length > 0
        ? buildVolSignalCurveOption(data.rows, themeMode, {
            signalEvents: events,
            selectedDate: selectedSignalDate,
          })
        : null,
    [data, themeMode, events, selectedSignalDate],
  );

  const label = name || code;

  return (
    <BaseChart
      title={`${label} — Volatility`}
      subtitle={
        data && data.rows.length > 0
          ? `${data.rows.length} daily bars · close + HV 20d + EWMA (λ=0.94), % annualized · the family's trigger source`
          : undefined
      }
      loading={loading}
      error={error ? `Failed to load volatility chart: ${error}` : null}
      option={option}
      emptyText={`No volatility rows for ${label} — run analysis.volatility first.`}
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
