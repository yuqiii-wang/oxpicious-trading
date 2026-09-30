/**
 * Options Analysis page — options analytics: OI, Volatility Smile, Greeks.
 *
 * Per-plot loading (one plot, one SQL — see api/services/options.service.ts
 * "Per-plot queries"): the page itself only loads the light /dates index
 * (date picker + caption) and the selected date's /snapshot (wall / smile /
 * greeks / StatTable share it via the LRU cache); the trend and skew
 * panels fetch their own aggregates. No full-history /combined blob.
 *
 * Layout:
 *   • Header — title + subtitle + back button + Refresh
 *   • Underlying selector (dropdown)
 *   • Target type toggle (ETF / INDEX)
 *   • Snapshot date picker
 *   • Tab buttons: Open Interests | Volatility Smile | The Greeks
 *   • Dynamic panels based on tab selection
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
  Select,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  type SelectChangeEvent,
  Typography,
} from "@mui/material";
import { ArrowBack } from "@mui/icons-material";
import { useNavigate } from "react-router-dom";
import RefreshButton from "@/components/RefreshButton";
import StatTable from "@/components/StatTable";
import { autoDeriveSnapshots } from "@/components/SnapshotControls";
import { DateSelector } from "@/shared/components/date-selector";
import { computeSnapshotStats } from "@/lib/options-stats";
import {
  fetchUnderlyings,
  fetchOptionsSnapshot,
  invalidateCacheForPrefix,
  venueToTargetType,
} from "@/lib/api-client";
import type { OptionsUnderlying } from "@shared/types";
import { useStore } from "@/store/filters";
import { UNDERLYING_LABELS } from "@/theme/chart-palette";
import MarketInterestWallPanel from "@/dataviz/features/options/MarketInterestWallPanel";
import SharedSkewPanel from "@/dataviz/features/options/skew-shared/SharedSkewPanel";
import AnalysisVolSmilePanel from "./VolSmilePanel";
import { OptionsTrendPanel } from "@/dataviz/features/options/options-trend";
import GreeksPanel from "./GreeksPanel";
import {
  useOptionsDates,
  useOptionsSnapshot,
} from "@/dataviz/features/options/data-hooks";

type TabKey = "oi" | "smile" | "greeks";
type GreekKey = "delta" | "theta" | "gamma" | "vega" | "rho";

const GREEK_TABS: { key: GreekKey; label: string }[] = [
  { key: "delta", label: "Delta" },
  { key: "theta", label: "Theta" },
  { key: "gamma", label: "Gamma" },
  { key: "vega", label: "Vega" },
  { key: "rho", label: "Rho" },
];

export default function OptionsAnalysisPage() {
  const navigate = useNavigate();

  const underlyingCode = useStore((s) => s.underlyingCode);
  const setUnderlyingCode = useStore((s) => s.setUnderlyingCode);
  const optionsVenue = useStore((s) => s.optionsVenue);
  const setOptionsVenue = useStore((s) => s.setOptionsVenue);
  const optionsTargetType = venueToTargetType(optionsVenue);

  const [underlyings, setUnderlyings] = useState<OptionsUnderlying[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [selectedDate, setSelectedDate] = useState<string>("");
  const [activeTab, setActiveTab] = useState<TabKey>("oi");
  const [activeGreek, setActiveGreek] = useState<GreekKey>("delta");
  const [refreshKey, setRefreshKey] = useState(0);

  // Load underlyings list
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

  // Per-plot #1: the trading-date index (date picker + caption). Default
  // the selected date to the latest session once it arrives.
  const datesInfo = useOptionsDates(underlyingCode, optionsTargetType, optionsVenue);
  useEffect(() => {
    setSelectedDate((cur) =>
      cur && datesInfo.dates.includes(cur)
        ? cur
        : datesInfo.dates.length > 0
          ? datesInfo.dates[datesInfo.dates.length - 1]
          : "",
    );
  }, [datesInfo.dates]);

  // Per-plot #2: the selected date's snapshot rows (wall / smile / greeks).
  const { snapshot, loading: snapshotLoading } = useOptionsSnapshot(
    underlyingCode,
    selectedDate,
    optionsTargetType,
    optionsVenue,
  );

  // Per-plot #3: StatTable's auto-derived snapshot columns — one small
  // /snapshot fetch per derived date (LRU-cached, shared with the picker).
  const [snapshotStats, setSnapshotStats] = useState<
    { label: string; date: string; stats: ReturnType<typeof computeSnapshotStats> }[]
  >([]);
  useEffect(() => {
    if (datesInfo.dates.length === 0 || !underlyingCode) {
      setSnapshotStats([]);
      return;
    }
    let cancelled = false;
    const snaps = autoDeriveSnapshots(datesInfo.dates);
    Promise.all(
      snaps.map((sd) =>
        sd.date
          ? fetchOptionsSnapshot(underlyingCode, sd.date, optionsTargetType, optionsVenue)
              .then((r) => computeSnapshotStats(r.rows))
              .catch(() => null)
          : Promise.resolve(null),
      ),
    ).then((stats) => {
      if (cancelled) return;
      setSnapshotStats(snaps.map((sd, i) => ({ label: sd.label, date: sd.date, stats: stats[i] })));
    });
    return () => {
      cancelled = true;
    };
  }, [underlyingCode, datesInfo.dates, optionsTargetType, optionsVenue, refreshKey]);

  const handleRefresh = () => {
    invalidateCacheForPrefix("/api/szse-options/");
    setRefreshKey((k) => k + 1);
  };

  const underlyingName =
    UNDERLYING_LABELS[underlyingCode] ??
    underlyings.find((u) => u.code === underlyingCode)?.name ??
    underlyingCode;

  const loading = datesInfo.loading;
  const snapshotRows = useMemo(() => snapshot?.rows ?? [], [snapshot]);

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
              onClick={() => navigate("/analysis/derivatives")}
              size="small"
              aria-label="back to derivatives"
            >
              <ArrowBack />
            </IconButton>
            <Typography variant="h5" sx={{ fontWeight: 700 }}>
              Options Analysis
            </Typography>
          </Box>
          <Typography variant="body2" color="text.secondary">
            {underlyingName} ({underlyingCode}) —{" "}
            {optionsVenue === "CFFEX"
              ? "CFFEX index options"
              : optionsVenue === "SSE"
                ? "SSE ETF options"
                : "SZSE ETF options"}{" "}
            analytics
          </Typography>
        </Box>
        <RefreshButton
          onClick={handleRefresh}
          loading={loading}
          label="Refresh"
          tooltip="Refresh options data (bypass cache)"
        />
      </Box>

      {/* Controls */}
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

        <Typography variant="subtitle2" sx={{ fontWeight: 600, minWidth: 110 }}>
          Snapshot Date
        </Typography>

        <DateSelector
          dates={datesInfo.dates}
          value={selectedDate || null}
          label="Select date"
          minWidth={180}
          onChange={(v) => setSelectedDate(v ?? "")}
        />
      </Box>

      {/* Tab buttons */}
      <Box
        sx={{
          display: "flex",
          gap: 1,
          flexWrap: "wrap",
          alignItems: "center",
        }}
      >
        <ToggleButtonGroup
          size="small"
          exclusive
          value={activeTab}
          onChange={(_, v) => {
            if (v) setActiveTab(v as TabKey);
          }}
        >
          <ToggleButton value="oi" sx={{ px: 2, py: 0.5 }}>
            Open Interests
          </ToggleButton>
          <ToggleButton value="smile" sx={{ px: 2, py: 0.5 }}>
            Volatility Smile
          </ToggleButton>
          <ToggleButton value="greeks" sx={{ px: 2, py: 0.5 }}>
            The Greeks
          </ToggleButton>
        </ToggleButtonGroup>

        {activeTab === "greeks" && (
          <ToggleButtonGroup
            size="small"
            exclusive
            value={activeGreek}
            onChange={(_, v) => {
              if (v) setActiveGreek(v as GreekKey);
            }}
            sx={{ ml: 1 }}
          >
            {GREEK_TABS.map((g) => (
              <ToggleButton key={g.key} value={g.key} sx={{ px: 1.5, py: 0.25 }}>
                {g.label}
              </ToggleButton>
            ))}
          </ToggleButtonGroup>
        )}
      </Box>

      {/* Loading / Error states */}
      {loading && (
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
      {!loading && !error && (
        <>
          {datesInfo.dates.length === 0 ? (
            <Alert severity="warning">No options data available.</Alert>
          ) : (
            <>
              <Typography variant="caption" color="text.secondary">
                {datesInfo.rowCount} option contracts · {datesInfo.dates.length} trading days ·{" "}
                {datesInfo.dates[0] ?? "—"} → {datesInfo.dates[datesInfo.dates.length - 1] ?? "—"}
              </Typography>

              {snapshotLoading && (
                <Box sx={{ display: "flex", justifyContent: "center", py: 2 }}>
                  <CircularProgress size={24} />
                </Box>
              )}

              {activeTab === "oi" && selectedDate && (
                <>
                  <MarketInterestWallPanel
                    rows={snapshotRows}
                    selectedDate={selectedDate}
                    unifiedStrikes={snapshot?.strikes ?? []}
                    expiryMonths={snapshot?.expiry_months ?? []}
                    underlyingCode={underlyingCode}
                  />
                  <OptionsTrendPanel
                    underlying={underlyingCode}
                    targetType={optionsTargetType}
                    exchange={optionsVenue}
                  />
                  <SharedSkewPanel
                    mode="oi_moneyness"
                    underlying={underlyingCode}
                    targetType={optionsTargetType}
                    exchange={optionsVenue}
                    selectedDate={selectedDate}
                    onDateChange={setSelectedDate}
                  />
                </>
              )}
              {activeTab === "smile" && selectedDate && (
                <AnalysisVolSmilePanel rows={snapshotRows} selectedDate={selectedDate} />
              )}
              {activeTab === "greeks" && selectedDate && (
                <>
                  <StatTable statsList={snapshotStats} />
                  <GreeksPanel rows={snapshotRows} selectedDate={selectedDate} greekKey={activeGreek} />
                  {activeGreek !== "theta" && activeGreek !== "rho" && (
                    <SharedSkewPanel
                      key={`greek_${activeGreek}`}
                      mode={`greek_${activeGreek}`}
                      underlying={underlyingCode}
                      targetType={optionsTargetType}
                      exchange={optionsVenue}
                      selectedDate={selectedDate}
                      onDateChange={setSelectedDate}
                    />
                  )}
                </>
              )}
            </>
          )}
        </>
      )}
    </Stack>
  );
}
