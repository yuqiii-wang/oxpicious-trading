/**
 * Options signal page (default export) — /analysis/signals/options.
 *
 * Copied from the Options Analysis page (/analysis/derivatives/options):
 *   • the underlying chosen component — the venue toggle (ETF · SZSE /
 *     ETF · SSE / Index · CFFEX) + the Underlying dropdown, wired to the
 *     SAME useStore fields (underlyingCode / optionsVenue) so the choice
 *     carries over between the two pages
 *
 * TWO option signal families live here, picked by the SIGNAL TYPE
 * dropdown (2026-10-13 — the Options VIX addition):
 *   • options_wall — the OI-weighted Moneyness Skew panel (SharedSkewPanel
 *     mode="oi_moneyness") carries the wall events as in-signal markers;
 *   • options_vix — the SHAREABLE VolIndexPanel in its IN-SIGNAL mode
 *     (the 30d model-free vol-index curve carries the fear-gauge events
 *     as buy/sell markers + a selected-day pin + the corr bars -0.8 /
 *     -0.5 on the corr companion).
 *
 * The RULES-ERA surfaces ride the Short-Term Tech composition
 * (2026-10-15 — the same SignalRulesPanel / SignalHistoryPanel pair
 * /analysis/signals/recent-movements renders; they replaced the retired
 * trigger-bar chip strip that rode inside the history panel):
 *   • the RULES panel (shared SignalRulesPanel) ABOVE the chart — ONE
 *     row per RULE CELL (sub signal · trend · regime · action): the
 *     cell's self-adaptive bar, measured evidence and confidence base;
 *     EXPANDING a row surfaces the cell's knowledge AND bolds the
 *     family's chart emphasis (options_wall: the cell's wall side on
 *     the skew chart; options_vix: its corr bar on the corr companion)
 *     — collapsing the row clears it;
 *   • the family chart with the events as in-signal markers;
 *   • the HISTORY panel (shared SignalHistoryPanel) BELOW the chart —
 *     ONE row per signal date, expandable to the day's every record;
 *     a history-date expansion also moves the chart's selected day.
 *
 * The page owns the ONE signal-history + rule-cells fetch pair for the
 * PICKED family (the sub-type pick narrows the rules rows, the chart's
 * markers and the history rows together — the one-pick-per-panel design
 * of the other signals pages; the pick row's sub-type vocabulary = the
 * rule cells ∪ the history's literals). The chart's selected-date
 * highlight defaults to the latest session (useOptionsDates index);
 * there is no date picker here by design.
 */
import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  CircularProgress,
  FormControl,
  IconButton,
  InputLabel,
  MenuItem,
  Paper,
  Select,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
  type SelectChangeEvent,
} from "@mui/material";
import { ArrowBack } from "@mui/icons-material";
import { useNavigate } from "react-router-dom";
import RefreshButton from "@/components/RefreshButton";
import {
  fetchSignalRules,
  fetchTradingSignalHistory,
  fetchUnderlyings,
  invalidateCacheForPrefix,
  venueToTargetType,
  type SignalRule,
  type TradingSignal,
} from "@/lib/api-client";
import type { OptionsUnderlying } from "@shared/types";
import { useStore } from "@/store/filters";
import { UNDERLYING_LABELS } from "@/theme/chart-palette";
import SharedSkewPanel from "@/dataviz/features/options/skew-shared/SharedSkewPanel";
import VolIndexPanel from "@/dataviz/features/options/vol-index/VolIndexPanel";
import { useOptionsDates } from "@/dataviz/features/options/data-hooks";
import {
  SignalHistoryPanel,
  SignalRulesPanel,
} from "@/shared/components/signal-code-trend";
import { ruleCellKey } from "@/shared/components/signal-code-trend/ruleParams";
import {
  owSubTypeLabel,
  owSubTypeWallSide,
} from "@/shared/components/signal-code-trend/families/optionsWall";
import {
  ovixSubTypeCorrBar,
  ovixSubTypeLabel,
} from "@/shared/components/signal-code-trend/families/optionsVix";
import type { SharedSkewSignalEvent } from "@/dataviz/features/options/skew-shared/types";
import type { VolIndexSignalEvent } from "@/dataviz/features/options/vol-index/volIndexOption";

/** The page's own option signal families (the SIGNAL TYPE menu). */
type OptionsSignalFamily = "options_wall" | "options_vix";

const SIGNAL_TYPE_OPTIONS: ReadonlyArray<{
  value: OptionsSignalFamily;
  label: string;
}> = [
  { value: "options_wall", label: "Options Wall" },
  { value: "options_vix", label: "Options VIX" },
];

const SECTION_TEXT: Record<OptionsSignalFamily, string> = {
  options_wall:
    "Dominant OI zone-wall signals (options_wall — the wall-edge BREACHES " +
    "plus the hit-and-bounce APPROACHES: the put zone-center hit and the " +
    "call-cap rally approach; this family's own page; the shared signals " +
    "pages stay RSI / Bollinger / MA Cross). The RULES panel above states " +
    "each cell's self-adaptive bar and measured evidence — click a cell " +
    "row for its knowledge and to BOLD its wall side on the skew chart " +
    "below (click again to clear). The events themselves mark the skew " +
    "chart (▲▼ = actionable buy/sell triggers, the grey ◆ diamond = an " +
    "event the strategy's VRP gate DECLINED — NO ACTION). Pick the sub " +
    "signal type to narrow the rule rows, the history rows and the " +
    "chart's markers together; click a history date to expand the day's " +
    "signals and move the chart's selected day there.",
  options_vix:
    "Vol-index fear-gauge signals (options_vix — the 30d model-free " +
    "VIX-style index vs its rolling correlation to spot: ovix_spike the " +
    "corr < -0.8 + vol-spike panic BUY, ovix_spike_hivol its high-vol " +
    "regime refinement, ovix_rollover the peak-confirmed turn, " +
    "ovix_fear_unwind the corr ≥ -0.5 exit companion SELL, plus the " +
    "populate additions ovix_deep_hivol the no-spike " +
    "established-fear-regime BUY and ovix_spike_wide the widened-bar " +
    "panic BUY; this family's own page; the shared signals pages stay " +
    "RSI / Bollinger / MA Cross). The RULES panel above states each " +
    "cell's self-adaptive bar and measured evidence — click a cell row " +
    "for its knowledge and to BOLD its corr bar on the corr companion " +
    "below (click again to clear). The events themselves mark the " +
    "vol-index curve (▲ = the fear-trade BUY entries, ▼ = the " +
    "fear-unwind exit); pick the sub signal type to narrow the rule " +
    "rows, the history rows and the chart's markers together; click a " +
    "history date to expand the day's signals and move the chart's " +
    "selected day there. Full rule documents (the multi-axis conditions " +
    "+ the exit advice) ride every strategy row — the family knowledge " +
    "lives one row per family in signal_family_docs.",
};

export default function OptionsSignalsPage() {
  const navigate = useNavigate();

  const underlyingCode = useStore((s) => s.underlyingCode);
  const setUnderlyingCode = useStore((s) => s.setUnderlyingCode);
  const optionsVenue = useStore((s) => s.optionsVenue);
  const setOptionsVenue = useStore((s) => s.setOptionsVenue);
  const optionsTargetType = venueToTargetType(optionsVenue);

  const [underlyings, setUnderlyings] = useState<OptionsUnderlying[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [selectedDate, setSelectedDate] = useState<string>("");
  const [refreshKey, setRefreshKey] = useState(0);
  // The picked option signal family — drives the history/rules
  // fetches, the chart area and the panels below.
  const [signalFamily, setSignalFamily] =
    useState<OptionsSignalFamily>("options_wall");
  // The picked family's signal history of the selected underlying — the
  // ONE fetch feeding the chart's in-signal markers and the history
  // panel's table (the family's own page; the shared signals pages
  // never see it). The sub-type pick narrows both views client-side so
  // markers and rows move together.
  const signalsSecType = optionsVenue === "CFFEX" ? "index" : "etf";
  const [signalHistory, setSignalHistory] = useState<TradingSignal[]>([]);
  const [signalHistoryLoading, setSignalHistoryLoading] = useState(true);
  // The picked family's RULE CELLS (signal_rules — the 2026-10-15
  // rules-era replacement of the retired thresholds fetch; family-
  // filtered): the rules panel renders them, the sub-type pick's menu
  // lists them.
  const [rules, setRules] = useState<SignalRule[]>([]);
  const [rulesLoading, setRulesLoading] = useState(true);
  const [rulesError, setRulesError] = useState<string | null>(null);
  // The SUB SIGNAL TYPE pick (the family's sub_type literals, "all" =
  // no filter) — owned HERE so the chart's markers filter through it
  // too; the pick row renders the dropdown.
  const [signalSubType, setSignalSubType] = useState("all");
  // The expanded rule cell of the rules panel — ALSO the chart
  // emphasis pick (the retired chip strip's successor): the expanded
  // cell's wall side (options_wall) / corr bar (options_vix) bolds on
  // the chart until the row collapses.
  const [expandedRule, setExpandedRule] = useState<string | null>(null);
  // The expanded signal date in the history panel — also moves the
  // chart's selected day there.
  const [expandedDate, setExpandedDate] = useState<string | null>(null);

  // A family switch changes everything downstream — reset the picks and
  // the expansions with it.
  useEffect(() => {
    setSignalSubType("all");
    setExpandedRule(null);
    setExpandedDate(null);
  }, [signalFamily]);

  useEffect(() => {
    let cancelled = false;
    setSignalHistoryLoading(true);
    fetchTradingSignalHistory(signalsSecType, underlyingCode, signalFamily)
      .then((resp) => {
        if (!cancelled) setSignalHistory(resp.signals ?? []);
      })
      .catch(() => {
        if (!cancelled) setSignalHistory([]);
      })
      .finally(() => {
        if (!cancelled) setSignalHistoryLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [signalsSecType, underlyingCode, signalFamily, refreshKey]);

  useEffect(() => {
    let cancelled = false;
    setRulesLoading(true);
    setRulesError(null);
    // A fetch re-scopes the cell vocabulary — the expanded cell key of
    // the old scope is stale.
    setExpandedRule(null);
    fetchSignalRules(signalsSecType, underlyingCode)
      .then((resp) => {
        if (!cancelled) {
          setRules(
            (resp.rules ?? []).filter((r) => r.signal_type === signalFamily),
          );
        }
      })
      .catch((e: Error) => {
        if (!cancelled) setRulesError(e.message);
      })
      .finally(() => {
        if (!cancelled) setRulesLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [signalsSecType, underlyingCode, signalFamily, refreshKey]);

  // A venue/underlying switch changes the family's sub-type vocabulary
  // — reset the pick (a shrunk vocabulary also drops orphaned picks).
  useEffect(() => {
    setSignalSubType("all");
  }, [signalsSecType, underlyingCode]);

  // The pickable sub types — the family's rule cells unioned with the
  // history's literals (a sub type is pickable wherever either surface
  // has it), alphabetical after "all" (no filter).
  const subTypeOptions = useMemo(() => {
    const set = new Set(rules.map((r) => r.signal_sub_type));
    for (const s of signalHistory) set.add(s.signal_sub_type);
    return ["all", ...[...set].sort((a, b) => a.localeCompare(b))];
  }, [rules, signalHistory]);

  // Keep the pick valid: a fetch can shrink the vocabulary under it.
  useEffect(() => {
    if (!subTypeOptions.includes(signalSubType)) setSignalSubType("all");
  }, [subTypeOptions, signalSubType]);

  const subTypeLabel = (st: string): string =>
    st === "all"
      ? "All sub types"
      : signalFamily === "options_vix"
        ? (ovixSubTypeLabel(st) ?? st)
        : (owSubTypeLabel(st) ?? st);

  // The pick-filtered view — the rules rows, the history rows and the
  // chart's markers all narrow through the same pick.
  const pickedRules = useMemo(
    () =>
      signalSubType === "all"
        ? rules
        : rules.filter((r) => r.signal_sub_type === signalSubType),
    [rules, signalSubType],
  );
  const pickedSignals = useMemo(
    () =>
      signalSubType === "all"
        ? signalHistory
        : signalHistory.filter((s) => s.signal_sub_type === signalSubType),
    [signalHistory, signalSubType],
  );

  // Keep the expanded rule cell valid: the sub-type pick (or a fetch)
  // can drop the row under it.
  const pickedRuleKeys = useMemo(
    () => new Set(pickedRules.map(ruleCellKey)),
    [pickedRules],
  );
  useEffect(() => {
    if (expandedRule != null && !pickedRuleKeys.has(expandedRule)) {
      setExpandedRule(null);
    }
  }, [pickedRuleKeys, expandedRule]);

  // The expanded cell's chart emphasis (DERIVED — collapsing the row or
  // dropping it clears it): a wall cell bolds its wall side on the skew
  // chart, a vix cell its corr bar on the corr companion.
  const ruleByKey = useMemo(
    () => new Map(rules.map((r) => [ruleCellKey(r), r])),
    [rules],
  );
  const wallSide = useMemo<"call" | "put" | null>(() => {
    if (signalFamily !== "options_wall" || expandedRule == null) return null;
    return owSubTypeWallSide(
      ruleByKey.get(expandedRule)?.signal_sub_type ?? "",
    );
  }, [signalFamily, expandedRule, ruleByKey]);
  const corrBar = useMemo<number | null>(() => {
    if (signalFamily !== "options_vix" || expandedRule == null) return null;
    return ovixSubTypeCorrBar(
      ruleByKey.get(expandedRule)?.signal_sub_type ?? "",
    );
  }, [signalFamily, expandedRule, ruleByKey]);

  // the chart's in-signal marker payload (identity-stable — the option
  // memo keys on it), narrowed by the sub-type pick — one shape per
  // family chart (the skew spot markers / the vol-curve markers)
  const skewSignalEvents = useMemo<SharedSkewSignalEvent[]>(
    () =>
      signalFamily === "options_wall"
        ? pickedSignals.map((s) => ({
            date: s.date,
            action: s.action,
            is_rejection: s.is_rejection === true,
          }))
        : [],
    [pickedSignals, signalFamily],
  );
  const volSignalEvents = useMemo<VolIndexSignalEvent[]>(
    () =>
      signalFamily === "options_vix"
        ? pickedSignals.map((s) => ({
            date: s.date,
            action: s.action,
            is_rejection: s.is_rejection === true,
          }))
        : [],
    [pickedSignals, signalFamily],
  );

  // Load underlyings list (same as Options Analysis — venue switch
  // re-scopes the list and falls back to its first underlying).
  useEffect(() => {
    fetchUnderlyings(optionsTargetType, optionsVenue)
      .then((list) => {
        setUnderlyings(list);
        if (list.length > 0 && !list.some((u) => u.code === underlyingCode)) {
          setUnderlyingCode(list[0].code);
        }
      })
      .catch((e: Error) => setError(e.message));
  }, [optionsVenue, optionsTargetType, refreshKey]); // eslint-disable-line react-hooks/exhaustive-deps

  // Trading-date index — defaults the chart's highlighted date to
  // the latest session; the history panel's date expansion updates it.
  const datesInfo = useOptionsDates(underlyingCode, optionsTargetType, optionsVenue, refreshKey);
  useEffect(() => {
    setSelectedDate((cur) =>
      cur && datesInfo.dates.includes(cur)
        ? cur
        : datesInfo.dates.length > 0
          ? datesInfo.dates[datesInfo.dates.length - 1]
          : "",
    );
  }, [datesInfo.dates]);

  const handleRefresh = () => {
    invalidateCacheForPrefix("/api/szse-options/");
    setRefreshKey((k) => k + 1);
  };

  // History-row expansion — also moves the chart's selected day onto
  // the event (collapsing keeps the chart where it was moved).
  const handleExpandedDateChange = (d: string | null) => {
    setExpandedDate(d);
    if (d) setSelectedDate(d);
  };

  const underlyingName =
    UNDERLYING_LABELS[underlyingCode] ??
    underlyings.find((u) => u.code === underlyingCode)?.name ??
    underlyingCode;
  const familyLabel =
    SIGNAL_TYPE_OPTIONS.find((o) => o.value === signalFamily)?.label ??
    signalFamily;

  return (
    <Stack spacing={2}>
      {/* Header */}
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          gap: 2,
          flexWrap: "wrap",
        }}
      >
        <Box>
          <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
            <IconButton
              onClick={() => navigate("/analysis/signals")}
              size="small"
              aria-label="back to signals"
            >
              <ArrowBack />
            </IconButton>
            <Typography variant="h5" sx={{ fontWeight: 700 }}>
              Options
            </Typography>
          </Box>
          <Typography variant="body2" color="text.secondary">
            {underlyingName} ({underlyingCode}) — the options signal families'
            own page: options_wall (OI zone-wall breaches + the skew chart)
            and options_vix (the 30d model-free VIX-style fear gauge + its
            corr companion)
          </Typography>
        </Box>
        <RefreshButton
          onClick={handleRefresh}
          loading={datesInfo.loading}
          label="Refresh"
          tooltip="Refresh options data (bypass cache)"
        />
      </Box>

      {/* Underlying chosen component — copied from Options Analysis */}
      <Box
        sx={{
          display: "flex",
          gap: 2,
          flexWrap: "wrap",
          alignItems: "center",
          p: 1.5,
          bgcolor: "background.paper",
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1.5,
        }}
      >
        <ToggleButtonGroup
          size="small"
          exclusive
          value={optionsVenue}
          onChange={(_, v) => {
            if (v) setOptionsVenue(v as "SZSE" | "SSE" | "CFFEX");
          }}
        >
          <ToggleButton value="SZSE" sx={{ px: 1.5, py: 0.25, fontSize: "0.7rem" }}>
            ETF · SZSE
          </ToggleButton>
          <ToggleButton value="SSE" sx={{ px: 1.5, py: 0.25, fontSize: "0.7rem" }}>
            ETF · SSE
          </ToggleButton>
          <ToggleButton value="CFFEX" sx={{ px: 1.5, py: 0.25, fontSize: "0.7rem" }}>
            Index · CFFEX
          </ToggleButton>
        </ToggleButtonGroup>

        <FormControl size="small" sx={{ minWidth: 220 }}>
          <InputLabel>Underlying</InputLabel>
          <Select
            value={underlyingCode}
            label="Underlying"
            onChange={(e: SelectChangeEvent<string>) => setUnderlyingCode(e.target.value)}
          >
            {underlyings.map((u) => (
              <MenuItem key={u.code} value={u.code}>
                {UNDERLYING_LABELS[u.code] ?? u.name} ({u.code})
              </MenuItem>
            ))}
          </Select>
        </FormControl>
      </Box>

      {/* Loading / Error states */}
      {datesInfo.loading && (
        <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
          <CircularProgress size={32} />
        </Box>
      )}
      {error && (
        <Alert severity="error" variant="filled">
          Failed to load options data: {error}
        </Alert>
      )}
      {datesInfo.error && (
        <Alert severity="error" variant="filled">
          Failed to load options dates: {datesInfo.error}
        </Alert>
      )}

      {/* Panels */}
      {!datesInfo.loading && !error && (
        <>
          {datesInfo.dates.length === 0 ? (
            <Alert severity="warning">No options data available.</Alert>
          ) : (
            <>
              <Typography variant="caption" color="text.secondary">
                Highlighted date {selectedDate || "—"} · {datesInfo.rowCount} option contracts ·{" "}
                {datesInfo.dates.length} trading days · {datesInfo.dates[0] ?? "—"} →{" "}
                {datesInfo.dates[datesInfo.dates.length - 1] ?? "—"}
              </Typography>

              {/* The Options Signals section header + pick row — ABOVE the
                  rules panel and the chart (the sub-type pick drives the
                  rules rows here AND the chart's in-signal markers AND the
                  history rows below); the SIGNAL TYPE pick switches the
                  family (and with it the chart area + both panels). */}
              <Box>
                <Typography variant="h6" sx={{ fontWeight: 700 }}>
                  Options Signals
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  {SECTION_TEXT[signalFamily]}
                </Typography>
                <Box
                  sx={{
                    display: "flex",
                    gap: 1.5,
                    flexWrap: "wrap",
                    alignItems: "center",
                    mt: 1,
                  }}
                >
                  <FormControl size="small" sx={{ minWidth: 170 }}>
                    <InputLabel>Signal type</InputLabel>
                    <Select
                      value={signalFamily}
                      label="Signal type"
                      onChange={(e: SelectChangeEvent<string>) =>
                        setSignalFamily(e.target.value as OptionsSignalFamily)
                      }
                    >
                      {SIGNAL_TYPE_OPTIONS.map((o) => (
                        <MenuItem key={o.value} value={o.value}>
                          {o.label}
                        </MenuItem>
                      ))}
                    </Select>
                  </FormControl>
                  <FormControl size="small" sx={{ minWidth: 220 }}>
                    <InputLabel>Sub signal type</InputLabel>
                    <Select
                      value={signalSubType}
                      label="Sub signal type"
                      onChange={(e: SelectChangeEvent<string>) =>
                        setSignalSubType(e.target.value)
                      }
                    >
                      {subTypeOptions.map((st) => (
                        <MenuItem key={st} value={st}>
                          {subTypeLabel(st)}
                        </MenuItem>
                      ))}
                    </Select>
                  </FormControl>
                </Box>
              </Box>

              {/* The RULES panel — the Short-Term Tech composition's first
                  surface: the family's rule cells (sub signal · trend ·
                  regime · action), narrowed by the sub-type pick. A row
                  click expands the cell's knowledge AND bolds its wall
                  side / corr bar on the chart below (click again to
                  clear). */}
              {rulesError && (
                <Alert severity="warning">
                  Failed to load {familyLabel} rule cells: {rulesError}
                </Alert>
              )}
              {!rulesLoading && !rulesError &&
                rules.length === 0 && signalHistory.length === 0 && (
                <Alert severity="info">
                  No {familyLabel} rule cells or events recorded for{" "}
                  {underlyingCode} yet.
                </Alert>
              )}
              {!rulesError && (
                <Paper variant="outlined" sx={{ px: 1.5, py: 1 }}>
                  <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
                    Signal rules — self-adaptive cells
                  </Typography>
                  {rulesLoading ? (
                    <Box sx={{ display: "flex", justifyContent: "center", py: 2 }}>
                      <CircularProgress size={24} />
                    </Box>
                  ) : (
                    <SignalRulesPanel
                      rules={pickedRules}
                      expandedKey={expandedRule}
                      onExpandedKeyChange={setExpandedRule}
                    />
                  )}
                </Paper>
              )}

              {/* The family chart — options_wall: the moneyness-SKEW panel
                  with the wall events as in-signal markers + the expanded
                  rule cell's wall side bolded; options_vix: the vol-index
                  curve + corr companion with the events as markers + the
                  expanded rule cell's corr bar bolded (the in-analysis
                  modes stay on the Options Analysis page). */}
              {signalFamily === "options_wall" ? (
                <SharedSkewPanel
                  mode="oi_moneyness"
                  underlying={underlyingCode}
                  targetType={optionsTargetType}
                  exchange={optionsVenue}
                  selectedDate={selectedDate}
                  onDateChange={setSelectedDate}
                  reloadKey={refreshKey}
                  signalEvents={skewSignalEvents}
                  emphasizedWallSide={wallSide}
                />
              ) : (
                <VolIndexPanel
                  mode="in-signal"
                  signalEvents={volSignalEvents}
                  selectedDate={selectedDate || null}
                  onDateSelect={setSelectedDate}
                  emphasizedCorrBar={corrBar}
                />
              )}

              {/* The HISTORY panel — the events replayed from the rules
                  above: one row per signal date (the day's
                  highest-confidence record the representative, "+N" chip
                  for siblings); expanding a date also moves the chart's
                  selected day there. */}
              {signalHistoryLoading ? (
                <Box sx={{ display: "flex", justifyContent: "center", py: 2 }}>
                  <CircularProgress size={24} />
                </Box>
              ) : (
                <SignalHistoryPanel
                  signals={pickedSignals}
                  expandedDate={expandedDate}
                  onExpandedDateChange={handleExpandedDateChange}
                />
              )}
            </>
          )}
        </>
      )}
    </Stack>
  );
}
