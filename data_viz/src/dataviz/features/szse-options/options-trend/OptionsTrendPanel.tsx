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
 *
 * The ETF Price & Volume chart from AnnualSentimentPanel is intentionally
 * NOT included here — it remains a separate ChartCard below.
 */
import { useEffect, useMemo, useState } from "react";
import { Alert, Stack } from "@mui/material";
import ChartCard from "@/components/ChartCard";
import EChart from "@/components/EChart";
import { useStore } from "@/store/filters";
import type { OptionsRow } from "@shared/types";
import { buildCohorts, buildCells, remapCells } from "./bandData";
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
}

export default function OptionsTrendPanel({ rows }: Props) {
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

  // Compute broken data once — used by all three charts for x-axis alignment
  const pcRatio = useMemo(() => daily.map((d) => d.pcRatio), [daily]);
  const callOi = useMemo(() => daily.map((d) => d.callOi), [daily]);
  const putOi = useMemo(() => daily.map((d) => d.putOi), [daily]);

  const brokenData = useMemo(() => {
    const ma5 = safeMa(pcRatio, 5);
    const ma20 = safeMa(pcRatio, 20);
    const callMil = callOi.map((v) => v / 1e6);
    const putMil = putOi.map((v) => v / 1e6);
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

  // Build bands with cells remapped to broken date indices
  const built = useMemo(() => {
    if (selectedExpiryKeys.length === 0) return null;
    const raw = buildCells(rows, selectedExpiryKeys, allDates);
    if (brokenData.dates.length !== allDates.length) {
      const { cells: remappedCells, spot: remappedSpot } = remapCells(
        raw.cells,
        raw.spot,
        allDates,
        brokenData.dates,
      );
      return { dates: brokenData.dates, cells: remappedCells, spot: remappedSpot, oiMax: raw.oiMax };
    }
    return raw;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, selectedExpiryKeysKey, allDates, brokenData.dates]);

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
      subtitle="Expiry OI evolution (selected months) vs spot · Put/Call sentiment · Total OI · time slider + tooltip synchronized across all three"
      height={960}
    >
      <CohortSelector
        cohortMode={cohortMode}
        onCohortModeChange={setCohortMode}
        monthFilter={monthFilter}
        onMonthFilterChange={setMonthFilter}
        availableMonths={availableMonths}
      />

      <Stack spacing={1}>
        {/* Chart 1: Expiry OI Bands (top) */}
        {built && built.cells.length > 0 ? (
          <EChart
            option={buildBandsOption(built.dates, built.cells, built.spot, themeMode, dataZoomOpt, expiryMarkers)}
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

        {/* Chart 2: Put/Call OI Ratio (middle) */}
        <EChart
          option={buildPcRatioOptionWithBroken(
            brokenData.dates,
            brokenData.pcRatio,
            brokenData.ma5,
            brokenData.ma20,
            themeMode,
            expiryMarkers,
            insideDataZoomOpt,
          )}
          height={300}
          group={CHART_GROUP}
          onEvents={{ dataZoom: handleDataZoom }}
        />

        {/* Chart 3: Total Open Interest Trend (bottom) */}
        <EChart
          option={buildOiTrendOptionWithBroken(
            brokenData.dates,
            brokenData.callMil,
            brokenData.putMil,
            themeMode,
            expiryMarkers,
            insideDataZoomOpt,
          )}
          height={300}
          group={CHART_GROUP}
          onEvents={{ dataZoom: handleDataZoom }}
        />
      </Stack>
    </ChartCard>
  );
}