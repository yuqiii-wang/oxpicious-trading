/**
 * SignalCodeTrendChart — THE shared signal-indicated per-security code
 * trend: the shared CodeTrendChart carrying the code's FULL signal
 * history (analysis_signals.history_signals ∪ live.live_signals, one
 * record per config-day) as buy (green ▲ below the low) / sell (red ▼
 * above the high) markers — every recorded event, the live Trading
 * Signals row-expansion treatment. Consumed by that expansion AND the
 * analysis Recent Movements page; the marker build lives here so the
 * two pages cannot drift.
 *
 * History source: GET /api/live-data/trading-signals/history — either
 * fetched here (omit `history`) or passed in by a parent that already
 * holds it for its own table (the live expansion). A failed internal
 * fetch degrades to the plain trend (no markers) — the chart itself
 * must never block or fail on the signal overlay.
 */
import { useEffect, useMemo, useState } from "react";
import { CodeTrendChart } from "@/ui/OhlcChart";
import type {
  CodeTrendChartOptions,
  CodeTrendSecType,
  OhlcTradeSignal,
} from "@/ui/OhlcChart";
import {
  fetchTradingSignalHistory,
  type TradingSignal,
  type TradingSignalHistoryResponse,
} from "@/lib/api-client";

/** Stable empty default for the marker memo (a fresh [] per render would
 *  dirty it every render — the notMerge rebuild tax). */
const NO_SIGNALS: TradingSignal[] = [];

export interface SignalCodeTrendChartProps {  /** Security type — selects the baseline endpoint (CodeTrendChart). */
  secType: CodeTrendSecType;
  /** Security code — suffixed ("000001.SZ") or bare ("000001"). */
  code: string;
  /** Display name; falls back to the trend endpoint's name. */
  name?: string;
  /** History anchor date (the live page's viewed day). Scopes only the
   *  response's recent-window boundary — the PARENT's table window; the
   *  chart markers plot every recorded event either way. Null/omitted
   *  anchors on the newest baseline close. */
  anchorDate?: string | null;
  /** Pre-fetched history response — skips the internal fetch (a parent
   *  that already fetched it for its history table). Null = parent-owned
   *  and still loading → renders without markers yet. Omit (undefined)
   *  to let the component fetch the history itself. */
  history?: TradingSignalHistoryResponse | null;
  /** The marker POPULATION — the parent's toggle-filtered subset of
   *  `history.signals` (the OHLCV variant + episode-pass picks). Omit to
   *  mark every row of `history` (the unfiltered record). */
  population?: TradingSignal[];
  /** The SELECTED signal date — its buy/sell marker renders enlarged
   *  with a glow (the expanded date's emphasis). Null = none. */
  selectedSignalDate?: string | null;
  /** Content height in px. Default 380 — the live expansion height. */
  height?: number;
  /** Extra header control rendered after the mode toggle. */
  headerAction?: React.ReactNode;
  /** Inner-chart option passthrough; `tradeSignals` is owned here. */
  chartOptions?: CodeTrendChartOptions;
}

export default function SignalCodeTrendChart({
  secType,
  code,
  name,
  anchorDate = null,
  history,
  population,
  selectedSignalDate = null,
  height = 380,
  headerAction,
  chartOptions,
}: SignalCodeTrendChartProps) {
  // The component-fetched history (unused while the parent owns the
  // fetch). A failure degrades to no markers — never blocks the trend.
  const [ownHistory, setOwnHistory] =
    useState<TradingSignalHistoryResponse | null>(null);

  useEffect(() => {
    if (history !== undefined) return;
    let cancelled = false;
    setOwnHistory(null);
    fetchTradingSignalHistory(secType, code, null, null, anchorDate)
      .then((resp) => {
        if (!cancelled) setOwnHistory(resp);
      })
      .catch(() => {
        if (!cancelled) setOwnHistory(null);
      });
    return () => {
      cancelled = true;
    };
  }, [history, secType, code, anchorDate]);

  const active = history !== undefined ? history : ownHistory;

  // Chart markers: the POPULATION rows (the parent's toggle-filtered
  // subset when given, else every history row — the full record). The
  // memo keeps the [] identity stable while the history is unsettled, so
  // the option memo does not dirty on unrelated parent re-renders.
  const markers: OhlcTradeSignal[] = useMemo(
    () =>
      (population ?? active?.signals ?? NO_SIGNALS).map((h) => ({
        date: h.date,
        action: h.action,
        signal_type: h.signal_type,
        signal_sub_type: h.signal_sub_type,
        confidence: h.confidence_pct,
      })),
    [population, active],
  );

  return (
    <CodeTrendChart
      secType={secType}
      code={code}
      name={name}
      height={height}
      headerAction={headerAction}
      chartOptions={{
        ...chartOptions,
        tradeSignals: markers,
        selectedSignalDate,
      }}
    />
  );
}
