/**
 * SignalTrendPanel — the Recent Movements content section for one picked
 * security, top to bottom: the shared population controls strip (the
 * SIGNAL pick + the OHLCV variant + episode pass), the CURVE overlay
 * panel (the continuous depth-response curves mu(s) per regime), the
 * shared SignalCodeTrendChart (daily trend + the FULL signal history as
 * buy/sell markers, PE overlay suppressed — the signal overlay above
 * replaces the valuation context), then the signal-history table.
 * (The retired THRESHOLD bars overlay was removed 2026-10-05; the
 * curves subsume it.)
 *
 * TWO panel-level picks drive the WHOLE panel: the SIGNAL pick (the
 * indicator family — mov_rsi / bollinger; lifted OUT of the curve
 * overlay panel 2026-10-11) and the population pick (the OHLCV variant
 * + episode pass). Both select the history events (markers + table)
 * AND the curve overlay's fit above — one pick each for the whole
 * panel ("pass: all" is a history-only pick and keeps the curve's last
 * pass). The Signal options come from the curves response (fetched
 * here — the overlay draws the same curves) unioned with the history's
 * families, so a picker option exists wherever either surface has the
 * family; mov_rsi leads (the flagship family), the rest alphabetical.
 *
 * The history response feeds BOTH halves: the trend chart's markers and
 * the table's per-date groups — through the same signal + population
 * filter, so the picks move markers and table together. Clicking a
 * buy/sell marker (or any point of a signal date) on the trend chart
 * expands that date's signals in the table — the chart's onDateClick
 * resolves any in-grid click to its date, and the handler keeps only
 * dates that actually carry signal records.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  CircularProgress,
  Paper,
  Stack,
  Typography,
} from "@mui/material";
import {
  SignalCodeTrendChart,
  SignalOverlayPanel,
  SignalPopulationToggles,
  useSignalPopulation,
} from "@/shared/components/signal-code-trend";
import type { CodeTrendChartOptions, CodeTrendSecType } from "@/ui/OhlcChart";
import { stripCodeSuffix } from "@/ui/OhlcChart/codeTrendSources";
import {
  fetchSignalCurves,
  fetchTradingSignalHistory,
  type SignalCurve,
  type TradingSignalHistoryResponse,
} from "@/lib/api-client";
import SignalHistoryTable from "./SignalHistoryTable";

export interface SignalTrendPanelProps {
  secType: CodeTrendSecType;
  code: string;
  /** Display name (e.g. from the nav tree); falls back to the API's. */
  name?: string;
}

/** mov_rsi leads the Signal picker (the flagship family), the rest
 *  alphabetical — the picker's stable option order. */
const signalTypeOrder = (types: string[]): string[] =>
  [...types].sort((a, b) =>
    (a === "mov_rsi" ? 0 : 1) - (b === "mov_rsi" ? 0 : 1) ||
    a.localeCompare(b),
  );

export default function SignalTrendPanel({
  secType,
  code,
  name,
}: SignalTrendPanelProps) {
  const [history, setHistory] = useState<TradingSignalHistoryResponse | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);
  // The expanded signal date in the table (chart marker clicks drive it).
  const [expandedDate, setExpandedDate] = useState<string | null>(null);
  // The history population controls (OHLCV variant + episode pass) —
  // one of the panel's TWO picks: it drives the curve overlay above
  // (the fit's variant + pass) as well as the history events (markers
  // + table).
  const population = useSignalPopulation();
  // The episode pass the CURVE draws: typical / re-entry (a curve fit
  // exists per pass — there is no all-pass fit). "pass: all" is a
  // history-only pick; the curve keeps its last pass then.
  const [curvePass, setCurvePass] = useState<"typical" | "re-entry">(
    "typical",
  );
  useEffect(() => {
    if (population.epPass !== "all") setCurvePass(population.epPass);
  }, [population.epPass]);

  // The SIGNAL pick (the indicator family) — the panel's other pick,
  // lifted out of the curve overlay panel: it drives the curve's cell
  // AND the history events below. Null = nothing chosen yet.
  const [signalType, setSignalType] = useState<string | null>(null);

  // The code's curves (fetched HERE — the overlay draws them and the
  // Signal picker's options come from them; cancelled-guard effect).
  const [curves, setCurves] = useState<SignalCurve[]>([]);
  const [curvesLoading, setCurvesLoading] = useState(false);
  const [curvesError, setCurvesError] = useState<string | null>(null);
  const bare = stripCodeSuffix(code);
  useEffect(() => {
    let cancelled = false;
    setCurvesLoading(true);
    setCurvesError(null);
    fetchSignalCurves(secType, bare)
      .then((r) => {
        if (!cancelled) {
          setCurves(r.curves);
          setCurvesLoading(false);
        }
      })
      .catch((e: unknown) => {
        if (!cancelled) {
          setCurvesError(String(e));
          setCurvesLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [secType, bare]);

  // The code's FULL signal history (all dates). Kept here — not inside
  // the chart — so the same response feeds the table; fetchJson's cache
  // keeps chart-side and table-side on one request.
  useEffect(() => {
    let cancelled = false;
    setHistory(null);
    setError(null);
    setExpandedDate(null);
    fetchTradingSignalHistory(secType, code, null, null, null)
      .then((resp) => {
        if (!cancelled) setHistory(resp);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [secType, code]);

  // The Signal picker's options: the curves' families unioned with the
  // history's (a family is pickable wherever either surface has it).
  const signalTypes = useMemo(() => {
    const set = new Set(curves.map((cv) => cv.signal_type));
    for (const s of history?.signals ?? []) set.add(s.signal_type);
    return signalTypeOrder([...set]);
  }, [curves, history]);
  // Keep the pick valid: default to the first option (mov_rsi when
  // present) on first load, a code switch, or a pick the new code
  // lacks; null only while neither surface has any family.
  useEffect(() => {
    if (signalTypes.length === 0) {
      setSignalType(null);
      return;
    }
    if (signalType == null || !signalTypes.includes(signalType)) {
      setSignalType(signalTypes[0]!);
    }
  }, [signalTypes, signalType]);

  // The signal + population-filtered events (chart markers + table rows
  // together).
  const signals = useMemo(
    () => population.apply(history?.signals ?? [])
      .filter((s) => signalType == null || s.signal_type === signalType),
    [population, history, signalType],
  );

  // Chart marker/date click → expand that date's signals. Dates without
  // records (plain chart clicks) are ignored.
  const signalDates = useMemo(
    () => new Set(signals.map((h) => h.date)),
    [signals],
  );
  const handleDateClick = useCallback(
    (date: string) => {
      if (!signalDates.has(date)) return;
      setExpandedDate((cur) => (cur === date ? null : date));
    },
    [signalDates],
  );
  // Identity-stable chart options (the skill's memo rule) — showPe
  // false: the threshold/curve panel above replaces the PE overlay.
  const chartOptions = useMemo<CodeTrendChartOptions>(
    () => ({ onDateClick: handleDateClick, showPe: false }),
    [handleDateClick],
  );
  // A population/date change can orphan the expanded date — clear it.
  useEffect(() => {
    setExpandedDate(null);
  }, [signals]);

  return (
    <Stack spacing={2}>
      {/* the panel's picks — the SIGNAL pick (curve cell + history
          events) and the population pick (fit variant + pass + history
          events) drive the curve overlay AND the trend chart's markers
          and the history table below */}
      {history !== null && (
        <SignalPopulationToggles
          population={population}
          count={signals.length}
          signalType={signalType}
          signalTypeOptions={signalTypes}
          onSignalTypeChange={setSignalType}
        />
      )}
      <Paper variant="outlined" sx={{ px: 1.5, py: 1 }}>
        <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
          Signal overlay — depth-response curves μ(s)
        </Typography>
        <SignalOverlayPanel
          boost={population.boost}
          epPass={curvePass}
          signalType={signalType}
          curves={curves}
          curvesLoading={curvesLoading}
          curvesError={curvesError}
        />
      </Paper>
      <SignalCodeTrendChart
        secType={secType}
        code={code}
        name={name}
        // undefined→internal fetch would duplicate this panel's request;
        // null = parent-owned, still loading (markers arrive with it).
        history={history}
        population={signals}
        selectedSignalDate={expandedDate}
        chartOptions={chartOptions}
      />
      {error ? (
        <Alert severity="error">Failed to load {code} signal history: {error}</Alert>
      ) : history === null ? (
        <Box sx={{ display: "flex", justifyContent: "center", py: 2 }}>
          <CircularProgress size={24} />
        </Box>
      ) : (
        <SignalHistoryTable
          signals={signals}
          expandedDate={expandedDate}
          onExpandedDateChange={setExpandedDate}
        />
      )}
    </Stack>
  );
}
