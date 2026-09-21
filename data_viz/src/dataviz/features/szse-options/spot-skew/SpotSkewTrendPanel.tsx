/**
 * SpotSkewTrendPanel — "Smile Chronology · Spot vs Level vs Skew".
 *
 * The chronological companion to the Volatility Smile · Snapshot: instead
 * of overplotting full smiles over time (unreadable — strikes drift with
 * spot), it tracks the spot-centered skew SCALAR (risk reversal measured
 * in delta space around ATM) against spot and the smile level:
 *
 *   • Today (default) — the selected date's skew term structure: RR at
 *     the chosen wing per real expiry (green = call wing richer, red =
 *     put wing richer) + each expiry's ATM IV.
 *   • History — 3 stacked grids on a shared time axis: spot / ATM IV
 *     (smile LEVEL — the VIX analog) / RR skew (smile TILT — the SKEW
 *     index analog) with front expiry, all-expiry mean and a ±2σ(20d)
 *     extreme-skew envelope. Click any date to move the snapshot.
 *
 * Semantics + rationale (why 25Δ default, 10Δ optional; VIX vs SKEW
 * decomposition): docs/options_vol_smile_study.md.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Box,
  FormControl,
  InputLabel,
  MenuItem,
  Select,
  ToggleButton,
  ToggleButtonGroup,
  type SelectChangeEvent,
} from "@mui/material";
import { BaseChart, useChartThemeMode } from "@/shared/charts/base-chart";
import type { AiAskSpec } from "@/shared/ai-ask";
import { useStore } from "@/store/filters";
import { PRICE_SCALE } from "@/theme/chart-palette";
import { fetchOptionsVolIndex } from "@/lib/api-client/options";
import type { OptionsRow, VolIndexRow } from "@shared/types";
import {
  buildExpiryGroupAgg,
  buildSkewDayPoints,
  buildSkewTermStructure,
  skewAxisDates,
  type SkewDelta,
} from "./spotSkewData";
import { buildHistoryOption, buildTodayOption } from "./spotSkewOption";

type ViewMode = "today" | "history";

interface Props {
  rows: OptionsRow[];
  selectedDate: string;
  onDateChange?: (date: string) => void;
  /** When provided, overlays the underlying's 30d model-free vol index
   *  (VIX-style, analysis.options_vol_index) on the History LEVEL grid. */
  underlyingCode?: string;
}

export default function SpotSkewTrendPanel({
  rows,
  selectedDate,
  onDateChange,
  underlyingCode,
}: Props) {
  const themeMode = useChartThemeMode();
  const optionsVenue = useStore((s) => s.optionsVenue);
  const [delta, setDelta] = useState<SkewDelta>(25);
  const [view, setView] = useState<ViewMode>("today");
  const [volIndexRows, setVolIndexRows] = useState<VolIndexRow[] | null>(null);
  // History legend selection — the unified tooltip filters hidden series
  // out and the option re-applies it across notMerge rebuilds (EChart.tsx).
  const [legendSel, setLegendSel] = useState<Record<string, boolean>>({});

  // A delta toggle renames the RR series (RR25↔RR10) — stale keys would
  // pin hidden state onto names that no longer exist.
  useEffect(() => setLegendSel({}), [delta]);

  const handleLegendChanged = useCallback((raw: unknown) => {
    const e = raw as { selected?: Record<string, boolean> };
    if (e?.selected) setLegendSel(e.selected);
  }, []);
  const onEvents = useMemo(
    () =>
      view === "history"
        ? { legendselectchanged: handleLegendChanged }
        : undefined,
    [view, handleLegendChanged],
  );

  // 30d model-free vol index for the LEVEL grid overlay (silent when the
  // table/underlying has no data — the overlay is optional context).
  useEffect(() => {
    if (!underlyingCode) {
      setVolIndexRows(null);
      return;
    }
    let cancelled = false;
    setVolIndexRows(null);
    fetchOptionsVolIndex(underlyingCode)
      .then((resp) => {
        if (!cancelled) setVolIndexRows(resp.rows);
      })
      .catch(() => {
        if (!cancelled) setVolIndexRows(null);
      });
    return () => {
      cancelled = true;
    };
  }, [underlyingCode]);

  // ETF closes (SZSE/SSE) are stored in 厘 (÷1000 → yuan); CFFEX index
  // closes are native index points already.
  const priceScale = optionsVenue === "CFFEX" ? 1 : PRICE_SCALE;

  // One pass over the quote rows → per (date × real expiry) anchors for
  // BOTH wings; the delta dropdown then only re-derives the series.
  const groups = useMemo(
    () => buildExpiryGroupAgg(rows, priceScale),
    [rows, priceScale],
  );
  const points = useMemo(
    () => buildSkewDayPoints(groups, delta),
    [groups, delta],
  );
  const slices = useMemo(
    () => buildSkewTermStructure(groups, selectedDate, delta),
    [groups, selectedDate, delta],
  );
  const dates = useMemo(() => skewAxisDates(points), [points]);

  // Vol-index values aligned to the chronology dates (parallel array; the
  // option builder overlays it on the LEVEL grid).
  const volIndexAligned = useMemo(() => {
    if (volIndexRows == null || volIndexRows.length === 0) return undefined;
    const byDate = new Map<string, number | null>(
      volIndexRows.map((r) => [r.date, r.vol_index_30d]),
    );
    return points.map((p) => byDate.get(p.date) ?? null);
  }, [volIndexRows, points]);

  const handleCanvasClick = useCallback(
    (dataIndex: number) => {
      if (!onDateChange) return;
      const date = dataIndex < dates.length ? dates[dataIndex] : undefined;
      if (date) onDateChange(date);
    },
    [dates, onDateChange],
  );

  const toggleSx = {
    bgcolor: "background.paper",
    "& .MuiToggleButton-root": {
      px: 1.5,
      py: 0.25,
      fontSize: "0.7rem",
      minWidth: 56,
    },
  } as const;

  // Card subtitle — concise identity + interaction hint per view; the
  // concise reading guide lives in the AI Ask intro below.
  const shortSubtitle =
    view === "today"
      ? `RR${delta} per real expiry, vol pts · green = call wing richer, red = put wing richer · line: ATM IV`
      : `RR${delta} front (solid) vs all-expiry mean (dashed), vol pts · ±2σ(20d) extreme band · hover lists all 3 grids · click to select date`;

  // The panel renders exactly one chart body (Today or History); the empty
  // branches keep the prior hand-rolled messages as the shared placeholder.
  const option =
    points.length === 0
      ? null
      : view === "today"
        ? slices.length > 0
          ? buildTodayOption(slices, selectedDate, delta, themeMode)
          : null
        : buildHistoryOption(
            points,
            selectedDate,
            delta,
            themeMode,
            volIndexAligned,
            legendSel,
          );
  const emptyText =
    points.length === 0
      ? "No IV/delta-calibrated option rows for this underlying — the smile chronology needs premium-calibrated quotes (stats.options_greeks)."
      : view === "today"
        ? `No expiry groups on ${selectedDate || "the selected date"}.`
        : "No data";

  // AI Ask — concise per-view guide (the long-form rationale lives in
  // docs/options_vol_smile_study.md); state carries the CURRENT view /
  // delta / date.
  const aiAskSpec = useMemo<AiAskSpec>(
    () => ({
      intro:
        "Smile skew vs time in delta space — strikes drift with spot, so full smiles overplot; " +
        "we track the spot-centered scalar instead. Today: " +
        `RR${delta} = IV(${delta}Δ call) − IV(${delta}Δ put) per real expiry, vol pts — ` +
        "green = call wing rich, red = put wing rich; line = ATM IV. Nearest-|δ| contract per " +
        "expiry, no OI weighting; IVs Black-76 off settlements (OI-wtd positioning skews: " +
        "shared skew panel). History: 3 grids, shared time axis — spot / ATM IV (level, VIX " +
        "analog) / RR (tilt, SKEW analog); hover any grid for one tooltip listing all three; " +
        "solid = front expiry (≥7 DTE), dashed = all-expiry " +
        "mean, ±2σ(20d) = extreme band. Indication: fade ±2σ skew extremes — they historically " +
        "mean-revert; RR rising on a falling level = fear premium, quick to bleed. Click a " +
        "History date to move the snapshot. Rationale: docs/options_vol_smile_study.md.",
      instruments: underlyingCode ? [{ code: underlyingCode }] : [],
      series:
        view === "today"
          ? [
              {
                name: `RR${delta} (C−P)`,
                unit: "vol pts",
                description: `risk reversal per real expiry: IV(${delta}Δ OTM call) − IV(${delta}Δ OTM put)`,
              },
              { name: "ATM IV", unit: "%", description: "at-the-money IV per expiry (line)" },
            ]
          : [
              { name: "Spot", description: "underlying price (top grid)" },
              {
                name: "ATM IV",
                unit: "%",
                description: "smile LEVEL per date (middle grid — the VIX analog)",
              },
              {
                name: `RR${delta} front`,
                unit: "vol pts",
                description: "front-expiry risk reversal — the smile TILT, SKEW analog (solid)",
              },
              {
                name: `RR${delta} mean`,
                unit: "vol pts",
                description: "all-expiry mean risk reversal (dashed)",
              },
              ...(volIndexAligned != null
                ? [
                    {
                      name: "MF Vol 30d",
                      unit: "vol pts",
                      description:
                        "30-day model-free VIX-style index on the LEVEL grid (analysis.options_vol_index)",
                    },
                  ]
                : []),
            ],
      state: { view, delta, selectedDate },
      notes: [
        `Gaps in the RR series = no contract with |δ| near ${delta / 100} that day` +
          (delta === 10 ? " (the deep wing is sparse)" : "") +
          ".",
      ],
    }),
    [view, delta, selectedDate, underlyingCode, volIndexAligned],
  );

  return (
    <BaseChart
      title="Smile Chronology · Spot vs Level vs Skew"
      subtitle={shortSubtitle}
      aiAsk={aiAskSpec}
      height={view === "today" ? 320 : 560}
      headerAction={
        <Box sx={{ display: "flex", gap: 1, alignItems: "center" }}>
          <FormControl size="small" sx={{ minWidth: 84 }}>
            <InputLabel sx={{ fontSize: "0.7rem" }}>Delta</InputLabel>
            <Select
              value={delta}
              label="Delta"
              onChange={(e: SelectChangeEvent<unknown>) =>
                setDelta(Number(e.target.value) as SkewDelta)
              }
              sx={{ fontSize: "0.75rem", "& .MuiSelect-select": { py: 0.25 } }}
            >
              <MenuItem value={25} sx={{ fontSize: "0.75rem" }}>
                25Δ wing
              </MenuItem>
              <MenuItem value={10} sx={{ fontSize: "0.75rem" }}>
                10Δ wing
              </MenuItem>
            </Select>
          </FormControl>
          <ToggleButtonGroup
            value={view}
            exclusive
            onChange={(_, v: ViewMode | null) => {
              if (v) setView(v);
            }}
            size="small"
            sx={toggleSx}
          >
            <ToggleButton value="today">Skew · Today</ToggleButton>
            <ToggleButton value="history">Skew · History</ToggleButton>
          </ToggleButtonGroup>
        </Box>
      }
      option={option}
      emptyText={emptyText}
      onEvents={onEvents}
      onCanvasClick={view === "history" ? handleCanvasClick : undefined}
    />
  );
}
