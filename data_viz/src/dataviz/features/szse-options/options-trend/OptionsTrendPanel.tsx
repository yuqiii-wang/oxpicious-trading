/**
 * OptionsTrendPanel — merged parent card for:
 *   1. Expiry OI Bands (vs Spot)
 *   2. Put/Call OI Ratio (Sentiment)
 *   3. Total Open Interest Trend
 *
 * All three charts share:
 *   - One ChartCard container
 *   - One dataZoom slider (synchronized across all via useDataZoomSync)
 *   - One x-axis (full trading date range — aligned across all plots)
 *   - One group for axisPointer/tooltip sync (mouse hover shows
 *     simultaneous tooltips on all three charts)
 *   - One cohort selection (Active/History + month): charts 2 and 3
 *     aggregate only the selected expiry cohorts' rows, leaving line
 *     breaks on dates where the selection has no data, while the x-axis
 *     keeps the full trading range for zoom/tooltip alignment
 *
 * The ETF Price & Volume chart from AnnualSentimentPanel is intentionally
 * NOT included here — it remains a separate ChartCard below.
 */
import { useEffect, useMemo, useState } from "react";
import { Alert, Box, Stack } from "@mui/material";
import ChartCard from "@/components/ChartCard";
import EChart from "@/components/EChart";
import { useStore } from "@/store/filters";
import type { OptionsRow, OptionsWallRow } from "@shared/types";
import { buildCohorts, buildCells, buildZoneWalls, remapCells, remapZones } from "./bandData";
import { buildDailyOi, buildExpiryMarkers } from "./sharedData";
import { buildPcRatioOptionWithBroken } from "./pcRatioOption";
import { buildOiTrendOptionWithBroken } from "./oiTrendOption";
import { buildBandsOption } from "./bandsOption";
import { useDataZoomSync } from "./useDataZoomSync";
import CohortSelector from "./cohortSelector";
import { breakArraysAtGaps, safeMa } from "@/lib/series";

export const CHART_GROUP = "options-trend";

interface Props {
  rows: OptionsRow[];
  /** Zone walls (analysis.options_walls, wall_type='zone') for the underlying. */
  walls?: OptionsWallRow[];
}

export default function OptionsTrendPanel({ rows, walls = [] }: Props) {
  const themeMode = useStore((s) => s.themeMode);
  const { buildDataZoom, buildInsideDataZoom, handleDataZoom } = useDataZoomSync();

  const daily = useMemo(() => buildDailyOi(rows), [rows]);
  const allDates = useMemo(() => daily.map((d) => d.date), [daily]);
  const expiryMarkers = useMemo(() => buildExpiryMarkers(rows, allDates), [rows, allDates]);

  const cohorts = useMemo(() => buildCohorts(rows), [rows]);
  const [cohortMode, setCohortMode] = useState<"active" | "history">("active");
  const [monthFilter, setMonthFilter] = useState<"all" | string>("all");

  const lastDate = useMemo(() => {
    let mx = "";
    for (const r of rows) if (r.date > mx) mx = r.date;
    return mx;
  }, [rows]);

  // Cohorts within the current active/history mode
  const modeCohorts = useMemo(
    () =>
      cohorts.filter((co) =>
        cohortMode === "active" ? co.key >= lastDate : co.key < lastDate,
      ),
    [cohorts, cohortMode, lastDate],
  );

  // Months available in the current mode — the single unified selector shows
  // "All" + these months (no separate expiry cohort buttons).
  const availableMonths = useMemo(() => {
    const months = new Set<string>();
    for (const co of modeCohorts) {
      const m = co.key.length >= 7 ? co.key.slice(0, 7) : co.key;
      months.add(m);
    }
    return Array.from(months).sort();
  }, [modeCohorts]);

  // Reset to "all" when the selected month has no cohorts in the current mode
  useEffect(() => {
    if (monthFilter !== "all" && !availableMonths.includes(monthFilter)) {
      setMonthFilter("all");
    }
  }, [availableMonths, monthFilter]);

  // "all" → every expiry cohort in the mode; a month → that month's cohorts
  const selectedCohorts = useMemo(
    () =>
      monthFilter === "all"
        ? modeCohorts
        : modeCohorts.filter(
            (co) => (co.key.length >= 7 ? co.key.slice(0, 7) : co.key) === monthFilter,
          ),
    [modeCohorts, monthFilter],
  );
  const selectedExpiryKeys = useMemo(
    () => selectedCohorts.map((co) => co.key),
    [selectedCohorts],
  );
  const selectedExpiryKeysKey = selectedExpiryKeys.join(",");

  // Expiry-key set of the current selection — drives filtering below
  const selectedExpirySet = useMemo(
    () => new Set(selectedExpiryKeys),
    [selectedExpiryKeys],
  );

  // Rows restricted to the selected expiry cohorts — charts 2 (P/C Ratio)
  // and 3 (Total OI) aggregate only these rows, so all three plots follow
  // the Active/History + month selection of the OI Bands chart above.
  const selectedRows = useMemo(
    () =>
      selectedExpirySet.size === 0
        ? []
        : rows.filter((r) => selectedExpirySet.has(r.expiry_date)),
    [rows, selectedExpirySet],
  );

  // Daily aggregates of the selected cohorts, indexed by date
  const selectedDailyByDate = useMemo(
    () => new Map(buildDailyOi(selectedRows).map((d) => [d.date, d])),
    [selectedRows],
  );

  // Expiry markers restricted to the selected cohorts (charts 2 & 3)
  const selectedExpiryMarkers = useMemo(
    () => expiryMarkers.filter((m) => selectedExpirySet.has(m.expiryDate)),
    [expiryMarkers, selectedExpirySet],
  );

  // Per-date series for the SELECTED cohorts, aligned to the full allDates
  // axis — null on dates where the selection has no data (lines break there)
  const pcRatio = useMemo(
    () =>
      allDates.map((dt) => {
        const d = selectedDailyByDate.get(dt);
        return d && Number.isFinite(d.pcRatio) ? d.pcRatio : null;
      }),
    [allDates, selectedDailyByDate],
  );
  const callOi = useMemo(
    () => allDates.map((dt) => selectedDailyByDate.get(dt)?.callOi ?? null),
    [allDates, selectedDailyByDate],
  );
  const putOi = useMemo(
    () => allDates.map((dt) => selectedDailyByDate.get(dt)?.putOi ?? null),
    [allDates, selectedDailyByDate],
  );

  // Broken data for charts 2 & 3 — computed from the selected cohorts with
  // lines broken at date gaps. brokenData.dates is also used to remap chart
  // 1's cells for x-axis alignment.
  const brokenData = useMemo(() => {
    const ma5 = safeMa(pcRatio, 5);
    const ma20 = safeMa(pcRatio, 20);
    const callMil = callOi.map((v) => (v == null ? null : v / 1e6));
    const putMil = putOi.map((v) => (v == null ? null : v / 1e6));
    const pcBroken = breakArraysAtGaps(allDates, [pcRatio, ma5, ma20]);
    const oiBroken = breakArraysAtGaps(allDates, [callMil, putMil]);
    return {
      dates: pcBroken.dates,
      pcRatio: pcBroken.arrays[0],
      ma5: pcBroken.arrays[1],
      ma20: pcBroken.arrays[2],
      callMil: oiBroken.arrays[0],
      putMil: oiBroken.arrays[1],
    };
  }, [allDates, pcRatio, callOi, putOi]);

  // Frozen y-axis ranges from the FULL dataset (all cohorts, all dates) —
  // charts 2 & 3 keep a constant vertical scale when the Active/History or
  // month toggle changes the plotted selection.
  const yAxisRanges = useMemo(() => {
    let pcMin = Infinity;
    let pcMax = -Infinity;
    let oiMin = Infinity;
    let oiMax = -Infinity;
    for (const d of daily) {
      if (Number.isFinite(d.pcRatio)) {
        if (d.pcRatio < pcMin) pcMin = d.pcRatio;
        if (d.pcRatio > pcMax) pcMax = d.pcRatio;
      }
      for (const v of [d.callOi / 1e6, d.putOi / 1e6]) {
        if (v < oiMin) oiMin = v;
        if (v > oiMax) oiMax = v;
      }
    }
    // Keep the P/C neutral markLine (y = 1) inside the frozen range
    if (Number.isFinite(pcMin)) pcMin = Math.min(pcMin, 1);
    if (Number.isFinite(pcMax)) pcMax = Math.max(pcMax, 1);

    const padRange = (min: number, max: number): [number, number] => {
      const pad = (max - min) * 0.05 || Math.max(Math.abs(max) * 0.05, 0.5);
      return [min - pad, max + pad];
    };

    return {
      pcRange:
        Number.isFinite(pcMin) && Number.isFinite(pcMax) ? padRange(pcMin, pcMax) : undefined,
      oiRange:
        Number.isFinite(oiMin) && Number.isFinite(oiMax) ? padRange(oiMin, oiMax) : undefined,
    };
  }, [daily]);

  // Build bands with cells remapped to broken date indices
  const realExpiries = useMemo(() => new Set(rows.map((r) => r.expiry_date)), [rows]);
  const built = useMemo(() => {
    if (selectedExpiryKeys.length === 0) return null;
    const raw = buildCells(rows, selectedExpiryKeys, allDates);
    // Zone walls follow the same expiry selection — the collapsed open
    // chain group (pseudo expiry not in the real expiry set) covers the
    // open cohorts.
    const rawZones = buildZoneWalls(walls, selectedExpiryKeys, allDates, {
      realExpiries,
      includeOpenChain: selectedExpiryKeys.some((k) => k >= lastDate),
    });
    if (brokenData.dates.length !== allDates.length) {
      const { cells: remappedCells, spot: remappedSpot } = remapCells(
        raw.cells,
        raw.spot,
        allDates,
        brokenData.dates,
      );
      const remappedZones = remapZones(rawZones, allDates, brokenData.dates);
      return {
        dates: brokenData.dates,
        cells: remappedCells,
        spot: remappedSpot,
        oiMax: raw.oiMax,
        zones: remappedZones,
      };
    }
    return { ...raw, zones: rawZones };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, walls, realExpiries, selectedExpiryKeysKey, lastDate, allDates, brokenData.dates]);

  if (cohorts.length === 0 || daily.length === 0) {
    return (
      <ChartCard
        title="Options Trend — OI Bands · P/C Ratio · Total OI"
        subtitle="Expiry OI evolution vs spot, put/call sentiment, and total OI trend"
        height={900}
      >
        <Alert severity="warning">No options data available for the selected underlying.</Alert>
      </ChartCard>
    );
  }

  const selectionLabel =
    monthFilter === "all"
      ? "All months"
      : `${monthFilter} (${selectedExpiryKeys.length} ${
          selectedExpiryKeys.length === 1 ? "expiry" : "expiries"
        })`;

  const dataZoomOpt = buildDataZoom();
  const insideDataZoomOpt = buildInsideDataZoom();

  return (
    <ChartCard
      title="Options Trend — OI Bands · P/C Ratio · Total OI"
      subtitle="Expiry OI evolution vs spot · Put/Call sentiment · Total OI — all three follow the Active/History + month selection · slider + tooltip synchronized"
      height={960}
    >
      <Stack spacing={1}>
        <Box sx={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 1, mb: 0.5 }}>
          <CohortSelector
            cohortMode={cohortMode}
            onCohortModeChange={setCohortMode}
            monthFilter={monthFilter}
            onMonthFilterChange={setMonthFilter}
            availableMonths={availableMonths}
          />
        </Box>

        {/* Chart 1: Expiry OI Bands (top) */}
        {built && built.cells.length > 0 ? (
          <EChart
            option={buildBandsOption(
              built.dates,
              built.cells,
              built.spot,
              themeMode,
              dataZoomOpt,
              expiryMarkers,
              built.zones,
            )}
            height={340}
            group={CHART_GROUP}
            onEvents={{ dataZoom: handleDataZoom }}
          />
        ) : (
          <Alert severity="info" sx={{ height: 340 }}>
            {selectedExpiryKeys.length > 0
              ? `No OI data for ${selectionLabel}.`
              : `No ${cohortMode} expiry cohorts (latest data date ${lastDate || "—"}).`}
          </Alert>
        )}

        {/* Chart 2: Put/Call OI Ratio (middle) — same cohort selection as chart 1 */}
        {selectedExpiryKeys.length > 0 ? (
          <EChart
            option={buildPcRatioOptionWithBroken(
              brokenData.dates,
              brokenData.pcRatio,
              brokenData.ma5,
              brokenData.ma20,
              themeMode,
              selectedExpiryMarkers,
              insideDataZoomOpt,
              yAxisRanges.pcRange,
            )}
            height={300}
            group={CHART_GROUP}
            onEvents={{ dataZoom: handleDataZoom }}
          />
        ) : (
          <Alert severity="info" sx={{ height: 300 }}>
            No {cohortMode} expiry cohorts — P/C Ratio follows the selection above.
          </Alert>
        )}

        {/* Chart 3: Total Open Interest Trend (bottom) — same cohort selection as chart 1 */}
        {selectedExpiryKeys.length > 0 ? (
          <EChart
            option={buildOiTrendOptionWithBroken(
              brokenData.dates,
              brokenData.callMil,
              brokenData.putMil,
              themeMode,
              selectedExpiryMarkers,
              insideDataZoomOpt,
              yAxisRanges.oiRange,
            )}
            height={300}
            group={CHART_GROUP}
            onEvents={{ dataZoom: handleDataZoom }}
          />
        ) : (
          <Alert severity="info" sx={{ height: 300 }}>
            No {cohortMode} expiry cohorts — Total OI follows the selection above.
          </Alert>
        )}
      </Stack>
    </ChartCard>
  );
}