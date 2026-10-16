/**
 * SignalTrendPanel — the Recent Movements content section for one picked
 * security, top to bottom: the shared population controls strip (the
 * SIGNAL pick + the OHLCV variant), the RULES panel (the
 * code's signal_rules cells — the self-adaptive prepared bars + measured
 * evidence per (sub · trend · regime · action); replaced the retired
 * depth-response CURVE overlay 2026-10-15), the shared
 * SignalCodeTrendChart (daily trend + the FULL signal history as
 * buy/sell markers, PE overlay suppressed), then the HISTORY panel (the
 * events replayed from those rules).
 *
 * TWO panel-level picks drive the WHOLE panel: the SIGNAL pick (the
 * indicator family — mov_rsi / bollinger / ma_cross) and the population
 * pick (the OHLCV variant). Both select the history
 * events (markers + table) AND the rules rows above — one pick each for
 * the whole panel. The Signal options come from the rules response
 * unioned with the history's families, so a picker option exists
 * wherever either surface has the family; mov_rsi leads (the flagship
 * family), the rest alphabetical.
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
  SignalHistoryPanel,
  SignalPopulationToggles,
  SignalRulesPanel,
} from "@/shared/components/signal-code-trend";
import type { CodeTrendChartOptions, CodeTrendSecType } from "@/ui/OhlcChart";
import { stripCodeSuffix } from "@/ui/OhlcChart/codeTrendSources";
import {
  fetchSignalRules,
  fetchTradingSignalHistory,
  type SignalRule,
  type TradingSignalHistoryResponse,
} from "@/lib/api-client";

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

/** The ma_cross EMA sub types (ma{W}_es{S} / ma{W}_eretest / ma{W}_ed{Q}pct)
 *  are hidden from this page: threshold/history-only twins of the SMA
 *  kinds sharing (window, side) pairs (no curve tier), removed from the
 *  Recent Movements surfaces 2026-10-11 — the SMA kinds keep them. */
const isHiddenEmaSubType = (subType: string): boolean =>
  /^ma\d+_e/.test(subType);

export default function SignalTrendPanel({
  secType,
  code,
  name,
}: SignalTrendPanelProps) {
  const [history, setHistory] = useState<TradingSignalHistoryResponse | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);
  // The expanded signal date in the history table (chart marker clicks
  // drive it) + the expanded rule cell in the rules panel.
  const [expandedDate, setExpandedDate] = useState<string | null>(null);
  const [expandedRule, setExpandedRule] = useState<string | null>(null);

  // The SIGNAL pick (the indicator family) — the panel's other pick:
  // it drives the rules rows AND the history events below. Null =
  // nothing chosen yet.
  const [signalType, setSignalType] = useState<string | null>(null);

  // The code's RULE CELLS (signal_rules — the self-adaptive prepared
  // params per (sub · trend · regime · action); fetched HERE, the
  // rules panel draws them and the Signal picker's options union with
  // them). A failed fetch degrades to the history-only surfaces.
  const [rules, setRules] = useState<SignalRule[]>([]);
  const [rulesError, setRulesError] = useState<string | null>(null);
  const bare = stripCodeSuffix(code);
  useEffect(() => {
    let cancelled = false;
    setRules([]);
    setRulesError(null);
    setExpandedRule(null);
    fetchSignalRules(secType, bare)
      .then((resp) => {
        if (!cancelled) setRules(resp.rules);
      })
      .catch((e: unknown) => {
        if (!cancelled) setRulesError(String(e));
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

  // The Signal picker's options: the rules' families unioned with the
  // history's (a family is pickable wherever either surface has it).
  const signalTypes = useMemo(() => {
    const set = new Set(rules.map((r) => r.signal_type));
    for (const s of history?.signals ?? []) set.add(s.signal_type);
    return signalTypeOrder([...set]);
  }, [rules, history]);
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

  // A SIGNAL pick switch re-scopes the rule rows — the expanded cell of
  // the old family is stale (the Options page resets the expansion with
  // its family pick the same way). The history date orphans clear with
  // the signals effect below.
  useEffect(() => {
    setExpandedRule(null);
  }, [signalType]);

  // The signal-filtered RULE CELLS (the panel's family pick) with the
  // hidden EMA sub types dropped, and the signal-filtered events (chart
  // markers + table rows together; one population since the boost
  // twins' retirement).
  const ruleRows = useMemo(
    () =>
      rules
        .filter((r) => signalType == null || r.signal_type === signalType)
        .filter((r) => !isHiddenEmaSubType(r.signal_sub_type)),
    [rules, signalType],
  );
  const signals = useMemo(
    () =>
      (history?.signals ?? [])
        .filter((s) => signalType == null || s.signal_type === signalType)
        .filter((s) => !isHiddenEmaSubType(s.signal_sub_type)),
    [history, signalType],
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
  // false: the rules/history panels above replace the PE overlay.
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
      {/* the panel's pick — the SIGNAL pick drives the rules panel, the
          trend chart's markers and the history table below */}
      {history !== null && (
        <SignalPopulationToggles
          count={signals.length}
          signalType={signalType}
          signalTypeOptions={signalTypes}
          onSignalTypeChange={setSignalType}
          advice={{ secType, code, signalType }}
        />
      )}
      <Paper variant="outlined" sx={{ px: 1.5, py: 1 }}>
        <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
          Signal rules — self-adaptive cells
        </Typography>
        {rulesError ? (
          <Alert severity="warning" sx={{ mb: 1 }}>
            Failed to load {code} signal rules: {rulesError}
          </Alert>
        ) : (
          <SignalRulesPanel
            rules={ruleRows}
            expandedKey={expandedRule}
            onExpandedKeyChange={setExpandedRule}
          />
        )}
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
        <SignalHistoryPanel
          signals={signals}
          expandedDate={expandedDate}
          onExpandedDateChange={setExpandedDate}
        />
      )}
    </Stack>
  );
}
