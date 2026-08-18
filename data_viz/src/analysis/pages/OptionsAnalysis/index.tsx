/**
 * Options Analysis page — options analytics: OI, Volatility Smile, Greeks.
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
import { DatePicker } from "@mui/x-date-pickers/DatePicker";
import dayjs, { type Dayjs } from "dayjs";
import RefreshButton from "@/components/RefreshButton";
import StatTable from "@/components/StatTable";
import { autoDeriveSnapshots } from "@/components/SnapshotControls";
import { computeSnapshotStats } from "@/lib/options-stats";
import {
  fetchUnderlyings,
  fetchOptionsCombined,
  invalidateCacheForPrefix,
} from "@/lib/api-client";
import type {
  OptionsCombinedResponse,
  OptionsUnderlying,
} from "@shared/types";
import { useStore } from "@/store/filters";
import { UNDERLYING_LABELS } from "@/theme/chart-palette";
import MarketInterestWallPanel from "@/dataviz/features/szse-options/MarketInterestWallPanel";
import AnalysisVolSmilePanel from "./VolSmilePanel";
import { OptionsTrendPanel } from "@/dataviz/features/szse-options/options-trend";
import GreeksPanel from "./GreeksPanel";

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
  const optionsTargetType = useStore((s) => s.optionsTargetType);
  const setOptionsTargetType = useStore((s) => s.setOptionsTargetType);

  const [underlyings, setUnderlyings] = useState<OptionsUnderlying[]>([]);
  const [data, setData] = useState<OptionsCombinedResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedDate, setSelectedDate] = useState<string>("");
  const [activeTab, setActiveTab] = useState<TabKey>("oi");
  const [activeGreek, setActiveGreek] = useState<GreekKey>("delta");
  const [refreshKey, setRefreshKey] = useState(0);

  // Load underlyings list
  useEffect(() => {
    fetchUnderlyings(optionsTargetType)
      .then((list) => {
        setUnderlyings(list);
        if (list.length > 0 && !list.some((u) => u.code === underlyingCode)) {
          setUnderlyingCode(list[0].code);
        }
      })
      .catch((e: Error) => setError(e.message));
  }, [optionsTargetType, refreshKey]); // eslint-disable-line react-hooks/exhaustive-deps

  // Load options data
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchOptionsCombined(underlyingCode, null, null, optionsTargetType)
      .then((d) => {
        if (cancelled) return;
        setData(d);
        setSelectedDate(d.dates.length > 0 ? d.dates[d.dates.length - 1] : "");
        setLoading(false);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setError(e.message);
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [underlyingCode, optionsTargetType, refreshKey]);

  const handleRefresh = () => {
    invalidateCacheForPrefix("/api/szse-options/");
    setRefreshKey((k) => k + 1);
  };

  const underlyingName =
    UNDERLYING_LABELS[underlyingCode] ??
    underlyings.find((u) => u.code === underlyingCode)?.name ??
    underlyingCode;

  const minDate = data?.dates.length ? dayjs(data.dates[0]) : undefined;
  const maxDate = data?.dates.length ? dayjs(data.dates[data.dates.length - 1]) : undefined;

  const snapshotStats = useMemo(() => {
    if (!data || data.dates.length === 0) return [];
    const snaps = autoDeriveSnapshots(data.dates);
    return snaps.map((sd) => {
      const snap = data.rows.filter((r) => r.date === sd.date);
      return {
        label: sd.label,
        date: sd.date,
        stats: computeSnapshotStats(snap),
      };
    });
  }, [data]);

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
            {optionsTargetType === "INDEX" ? "CFFEX index options" : "SZSE ETF options"} analytics
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
          value={optionsTargetType}
          onChange={(_, v) => {
            if (v) setOptionsTargetType(v as "ETF" | "INDEX");
          }}
        >
          <ToggleButton value="ETF" sx={{ px: 1.5, py: 0.25, fontSize: "0.7rem" }}>
            ETF · SZSE
          </ToggleButton>
          <ToggleButton value="INDEX" sx={{ px: 1.5, py: 0.25, fontSize: "0.7rem" }}>
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

        <DatePicker
          label="Select date"
          value={selectedDate ? dayjs(selectedDate) : null}
          format="YYYY-MM-DD"
          minDate={minDate}
          maxDate={maxDate}
          slotProps={{
            textField: { size: "small", sx: { width: 180 } },
          }}
          onChange={(v: Dayjs | null) => setSelectedDate(v ? v.format("YYYY-MM-DD") : "")}
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

      {/* Panels */}
      {!loading && !error && data && (
        <>
          {data.rows.length === 0 ? (
            <Alert severity="warning">No options data available.</Alert>
          ) : (
            <>
              <Typography variant="caption" color="text.secondary">
                {data.rows.length} option contracts · {data.dates.length} trading days ·{" "}
                {data.dates[0] ?? "—"} → {data.dates[data.dates.length - 1] ?? "—"}
              </Typography>

              {activeTab === "oi" && selectedDate && (
                <>
                  <MarketInterestWallPanel rows={data.rows} selectedDate={selectedDate} />
                  <OptionsTrendPanel rows={data.rows} />
                </>
              )}
              {activeTab === "smile" && selectedDate && (
                <AnalysisVolSmilePanel rows={data.rows} selectedDate={selectedDate} onDateChange={setSelectedDate} />
              )}
              {activeTab === "greeks" && selectedDate && (
                <>
                  <StatTable statsList={snapshotStats} />
                  <GreeksPanel rows={data.rows} selectedDate={selectedDate} greekKey={activeGreek} />
                </>
              )}
            </>
          )}
        </>
      )}
    </Stack>
  );
}