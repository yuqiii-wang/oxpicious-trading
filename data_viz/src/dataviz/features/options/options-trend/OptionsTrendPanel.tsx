/**
 * OptionsTrendPanel — merged parent card for:
 *   1. Expiry OI Bands (vs Spot)
 *   2. Put/Call OI Ratio (Sentiment)
 *   3. Total Open Interest Trend
 *
 * All three charts share:
 *  - One ChartCard container
 *  - One dataZoom slider (synchronized across all via useDataZoomSync)
 *  - One x-axis (full trading date range — aligned across all plots)
 *  - One group for axisPointer/tooltip sync (mouse hover shows
 *    simultaneous tooltips on all three charts)
 *  - One cohort selection (Active/History + month): charts 2 and 3
 *    aggregate only the selected expiry cohorts' rows, leaving line
 *    breaks on dates where the selection has no data, while the x-axis
 *    keeps the full trading range for zoom/tooltip alignment
 *
 * Per-plot loading (one plot, one SQL — see api/services/options.service.ts
 * "Per-plot queries"): the panel fetches its own aggregates instead of
 * consuming the full-history /combined row blob:
 *   • /oi-daily          — per (date × expiry) call/put OI + spot (charts'
 *                          shared axis, daily aggregates, frozen y-ranges)
 *   • /expiry-meta       — per-contract final OI (expiry markers) + cohort
 *                          lifetime totals (the cohort selector's options)
 *   • /oi-cells?expiries — per (date × expiry × strike) OI cells for the
 *                          SELECTED cohorts only (refetched on selection)
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { Alert, Box, CircularProgress, Stack } from "@mui/material";
import ChartCard from "@/shared/components/ChartCard";
import EChart from "@/shared/charts/EChart";
import { useChartThemeMode } from "@/shared/charts/base-chart";
import { useAiAskAddon } from "@/shared/ai-ask";
import type { AiAskSpec } from "@/shared/ai-ask";
import type { ECharts, EChartsOption } from "echarts";
import type { OptionsOiCellsResponse, OptionsOiDailyResponse, OptionsExpiryMetaResponse, OptionsWallRow } from "@shared/types";
import {
  fetchOptionsOiCells,
  fetchOptionsOiDaily,
  fetchOptionsExpiryMeta,
  type OptionsTargetType,
  type OptionsVenue,
} from "@/lib/api-client/options";
import {
  BAND_H_MAX,
  BAND_H_MIN,
  COLOR_STRENGTH_MIN,
  COLOR_STRENGTH_POWER,
  buildZoneWalls,
  remapCells,
  remapZones,
  type BandCell,
  type ExpiryCohort,
} from "./bandData";
import { buildExpiryMarkersFromMeta, buildDailyFromOiDaily, type DailyOi, type ExpiryMarker } from "./sharedData";
import { buildPcRatioOptionWithBroken } from "./pcRatioOption";
import { buildOiTrendOptionWithBroken } from "./oiTrendOption";
import { buildBandsOption } from "./bandsOption";
import { useDataZoomSync } from "./useDataZoomSync";
import CohortSelector from "./cohortSelector";
import { breakArraysAtGaps, safeMa } from "@/lib/series";

export const CHART_GROUP = "options-trend";

interface Props {
  /** Fetch scope — the panel loads its own per-plot SQL aggregates. */
  underlying: string;
  targetType?: OptionsTargetType;
  exchange?: OptionsVenue;
  /** Zone walls (analysis.options_walls, wall_type='zone') for the underlying. */
  walls?: OptionsWallRow[];
}

export default function OptionsTrendPanel({
  underlying,
  targetType,
  exchange,
  walls = [],
}: Props) {
  const themeMode = useChartThemeMode();
  const { buildDataZoom, buildInsideDataZoom, handleDataZoom } = useDataZoomSync();

  // ---- per-plot loads ------------------------------------------------------
  const [oiDaily, setOiDaily] = useState<OptionsOiDailyResponse | null>(null);
  const [expiryMeta, setExpiryMeta] = useState<OptionsExpiryMetaResponse | null>(null);
  const [cellsResp, setCellsResp] = useState<OptionsOiCellsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!underlying) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setOiDaily(null);
    setExpiryMeta(null);
    setCellsResp(null);
    Promise.all([
      fetchOptionsOiDaily(underlying, targetType, exchange),
      fetchOptionsExpiryMeta(underlying, targetType, exchange),
    ])
      .then(([daily, meta]) => {
        if (cancelled) return;
        setOiDaily(daily);
        setExpiryMeta(meta);
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
  }, [underlying, targetType, exchange]);

  const daily = useMemo<DailyOi[]>(() => (oiDaily ? buildDailyFromOiDaily(oiDaily) : []), [oiDaily]);
  const allDates = useMemo(() => daily.map((d) => d.date), [daily]);
  const expiryMarkers = useMemo<ExpiryMarker[]>(
    () => (expiryMeta ? buildExpiryMarkersFromMeta(expiryMeta) : []),
    [expiryMeta],
  );

  const cohorts = useMemo<ExpiryCohort[]>(() => {
    if (!expiryMeta) return [];
    return expiryMeta.cohorts
      .map((c) => ({
        key: c.expiry_date,
        label: c.expiry_date.length >= 7 ? c.expiry_date.slice(0, 7) : c.expiry_date,
        totalOi: c.total_oi,
      }))
      .sort((a, b) => a.key.localeCompare(b.key));
  }, [expiryMeta]);

  const [cohortMode, setCohortMode] = useState<"active" | "history">("active");
  const [monthFilter, setMonthFilter] = useState<"all" | string>("all");

  const lastDate = allDates.length > 0 ? allDates[allDates.length - 1] : "";

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

  // Per-plot cells for the SELECTED cohorts — refetched on selection change
  // (the URL embeds the expiry list, so the LRU cache reuses each pick).
  useEffect(() => {
    if (!underlying || selectedExpiryKeys.length === 0) {
      setCellsResp(null);
      return;
    }
    let cancelled = false;
    fetchOptionsOiCells(underlying, selectedExpiryKeys, targetType, exchange)
      .then((resp) => {
        if (!cancelled) setCellsResp(resp);
      })
      .catch(() => {
        if (!cancelled) setCellsResp(null);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [underlying, targetType, exchange, selectedExpiryKeysKey]);

  // Daily aggregates of the selected cohorts, indexed by date
  const selectedDailyByDate = useMemo(() => {
    const m = new Map<string, DailyOi>();
    if (!oiDaily) return m;
    for (const r of oiDaily.daily) {
      if (!selectedExpirySet.has(r.expiry_date)) continue;
      let d = m.get(r.date);
      if (!d) {
        d = { date: r.date, callOi: 0, putOi: 0, pcRatio: NaN };
        m.set(r.date, d);
      }
      d.callOi += r.call_oi;
      d.putOi += r.put_oi;
    }
    for (const d of m.values()) {
      d.pcRatio = d.callOi > 0 ? d.putOi / d.callOi : NaN;
    }
    return m;
  }, [oiDaily, selectedExpirySet]);

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
  const realExpiries = useMemo(
    () => new Set((expiryMeta?.cohorts ?? []).map((c) => c.expiry_date)),
    [expiryMeta],
  );
  const built = useMemo(() => {
    if (selectedExpiryKeys.length === 0 || !cellsResp || !oiDaily) return null;
    const raw = buildCellsFromResponse(cellsResp, oiDaily, allDates);
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
  }, [cellsResp, oiDaily, walls, realExpiries, selectedExpiryKeysKey, lastDate, allDates, brokenData.dates]);

  // Chart instances for the AI Ask screenshots (one per stacked chart).
  const bandsRef = useRef<ECharts | null>(null);
  const pcRef = useRef<ECharts | null>(null);
  const oiRef = useRef<ECharts | null>(null);

  // Options hoisted into memos (was inline per render) so the AI Ask plot
  // info derives from the same objects the charts render.
  const bandsOption = useMemo<EChartsOption | null>(
    () =>
      built && built.cells.length > 0
        ? buildBandsOption(
            built.dates,
            built.cells,
            built.spot,
            themeMode,
            buildDataZoom(),
            expiryMarkers,
            built.zones,
          )
        : null,
    [built, themeMode, buildDataZoom, expiryMarkers],
  );
  const pcRatioOption = useMemo<EChartsOption | null>(
    () =>
      selectedExpiryKeys.length > 0
        ? buildPcRatioOptionWithBroken(
            brokenData.dates,
            brokenData.pcRatio,
            brokenData.ma5,
            brokenData.ma20,
            themeMode,
            selectedExpiryMarkers,
            buildInsideDataZoom(),
            yAxisRanges.pcRange,
          )
        : null,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [brokenData, themeMode, selectedExpiryMarkers, buildInsideDataZoom, yAxisRanges.pcRange, selectedExpiryKeysKey],
  );
  const oiTrendOption = useMemo<EChartsOption | null>(
    () =>
      selectedExpiryKeys.length > 0
        ? buildOiTrendOptionWithBroken(
            brokenData.dates,
            brokenData.callMil,
            brokenData.putMil,
            themeMode,
            selectedExpiryMarkers,
            buildInsideDataZoom(),
            yAxisRanges.oiRange,
          )
        : null,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [brokenData, themeMode, selectedExpiryMarkers, buildInsideDataZoom, yAxisRanges.oiRange, selectedExpiryKeysKey],
  );
  const extraOptions = useMemo(
    () => [pcRatioOption, oiTrendOption] as const,
    [pcRatioOption, oiTrendOption],
  );

  const underlyingCode = underlying;
  // AI Ask — intro + series semantics; state carries the CURRENT cohort
  // selection so the modal / LLM always describe the view on screen.
  const aiAskSpec = useMemo<AiAskSpec>(
    () => ({
      intro:
        `Three synchronized option-flow charts for ${underlyingCode || "(n/a)"}: ` +
        "(1) Expiry OI Bands — per-expiry OI vs the spot line (where exposure concentrates " +
        "by expiry); (2) P/C OI ratio = put OI / call OI of the selected cohorts, with " +
        "MA5/MA20, dashed neutral at 1; (3) call vs put total OI (mil contracts). " +
        "Indication: ratio >1 = hedging tilt (bear lean), <1 = chase; band walls mark the " +
        "strike zones spot grinds against. All follow the cohort selection, one slider + " +
        "crosshair; dots = expiries.",
      instruments: underlyingCode ? [{ code: underlyingCode }] : [],
      series: [
        { name: "Spot", unit: "元", description: "underlying daily closing price" },
        { name: "OI Bands", description: "per-expiry open-interest bands by strike over time" },
        { name: "P/C OI Ratio", unit: "ratio", description: "put OI / call OI of the selected cohorts" },
        { name: "MA5", unit: "ratio", description: "5-day mean of the P/C ratio" },
        { name: "MA20", unit: "ratio", description: "20-day mean of the P/C ratio" },
        { name: "Call OI", unit: "mil contracts", description: "daily total call open interest" },
        { name: "Put OI", unit: "mil contracts", description: "daily total put open interest" },
      ],
      state: {
        "expiry cohorts": cohortMode === "active" ? "Active (unexpired)" : "History (expired)",
        month: monthFilter,
        expiries_selected: selectedExpiryKeys.length,
      },
      notes: [
        "Charts 2 and 3 aggregate ONLY the selected expiry cohorts; lines break on dates where the selection has no data.",
        "All three charts share one time slider and a synchronized crosshair/tooltip.",
        `Latest data date: ${lastDate || "—"}.`,
      ],
    }),
    [underlyingCode, cohortMode, monthFilter, selectedExpiryKeys.length, lastDate],
  );
  const aiAskAddon = useAiAskAddon({
    title: "Options Trend — OI Bands · P/C Ratio · Total OI",
    subtitle: "Expiry OI evolution vs spot, put/call sentiment, and total OI trend",
    option: bandsOption,
    extraOptions,
    spec: aiAskSpec,
    getInstance: () => bandsRef.current,
    getExtraInstances: () => [pcRef.current, oiRef.current],
  });

  if (loading) {
    return (
      <ChartCard
        title="Options Trend — OI Bands · P/C Ratio · Total OI"
        subtitle="Expiry OI evolution vs spot, put/call sentiment, and total OI trend"
        height={900}
      >
        <Box sx={{ display: "flex", justifyContent: "center", py: 10 }}>
          <CircularProgress size={28} />
        </Box>
      </ChartCard>
    );
  }

  if (error) {
    return (
      <ChartCard
        title="Options Trend — OI Bands · P/C Ratio · Total OI"
        subtitle="Expiry OI evolution vs spot, put/call sentiment, and total OI trend"
        height={900}
      >
        <Alert severity="error">Failed to load options trend data: {error}</Alert>
      </ChartCard>
    );
  }

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

  return (
    <ChartCard
      title="Options Trend — OI Bands · P/C Ratio · Total OI"
      subtitle="Expiry OI bands vs spot · P/C sentiment · total OI — all follow the cohort selection"
      titleAddon={aiAskAddon}
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
        {bandsOption ? (
          <EChart
            option={bandsOption}
            height={340}
            group={CHART_GROUP}
            onEvents={{ dataZoom: handleDataZoom }}
            onReady={(c) => {
              bandsRef.current = c;
            }}
          />
        ) : (
          <Alert severity="info" sx={{ height: 340 }}>
            {selectedExpiryKeys.length > 0
              ? `No OI data for ${selectionLabel}.`
              : `No ${cohortMode} expiry cohorts (latest data date ${lastDate || "—"}).`}
          </Alert>
        )}

        {/* Chart 2: Put/Call OI Ratio (middle) — same cohort selection as chart 1 */}
        {pcRatioOption ? (
          <EChart
            option={pcRatioOption}
            height={300}
            group={CHART_GROUP}
            onEvents={{ dataZoom: handleDataZoom }}
            onReady={(c) => {
              pcRef.current = c;
            }}
          />
        ) : (
          <Alert severity="info" sx={{ height: 300 }}>
            No {cohortMode} expiry cohorts — P/C Ratio follows the selection above.
          </Alert>
        )}

        {/* Chart 3: Total Open Interest Trend (bottom) — same cohort selection as chart 1 */}
        {oiTrendOption ? (
          <EChart
            option={oiTrendOption}
            height={300}
            group={CHART_GROUP}
            onEvents={{ dataZoom: handleDataZoom }}
            onReady={(c) => {
              oiRef.current = c;
            }}
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

/**
 * Consume the /oi-cells payload into the bands chart's BandCell[] shape —
 * the per-(date, strike) aggregation over the SELECTED expiries the former
 * buildCells() computed from raw rows. Spot aligns to allDates from the
 * /oi-daily response (null on loader "no spot" dates).
 */
function buildCellsFromResponse(
  cellsResp: OptionsOiCellsResponse,
  oiDaily: OptionsOiDailyResponse,
  allDates: string[],
): { cells: BandCell[]; spot: (number | null)[]; oiMax: number } {
  const dateIdx = new Map<string, number>();
  allDates.forEach((d, i) => {
    if (!dateIdx.has(d)) dateIdx.set(d, i);
  });

  // Σ OI per (date, strike) across the fetched expiries
  const byDateStrike = new Map<string, Map<number, { c: number; p: number }>>();
  for (const cell of cellsResp.cells) {
    const xi = dateIdx.get(cell.date);
    if (xi == null) continue;
    const key = cell.date;
    let strikes = byDateStrike.get(key);
    if (!strikes) {
      strikes = new Map();
      byDateStrike.set(key, strikes);
    }
    const acc = strikes.get(cell.strike_price) ?? { c: 0, p: 0 };
    acc.c += cell.call_oi;
    acc.p += cell.put_oi;
    strikes.set(cell.strike_price, acc);
  }

  const cells: BandCell[] = [];
  let oiMax = 1;
  for (const [date, strikes] of byDateStrike) {
    const xi = dateIdx.get(date)!;
    for (const [k, acc] of strikes) {
      const totalOi = acc.c + acc.p;
      if (totalOi <= 0) continue;
      if (totalOi > oiMax) oiMax = totalOi;
      cells.push({
        value: [xi, k / 10000],
        date,
        strikeY: k / 10000,
        callOi: acc.c,
        putOi: acc.p,
        totalOi,
        putPct: (acc.p / totalOi) * 100,
        h: 0,
        strength: 0,
      });
    }
  }

  // Second pass — thickness + darkness (same curve as the former builder)
  for (const cell of cells) {
    const frac = cell.totalOi / oiMax;
    cell.h = BAND_H_MIN + (BAND_H_MAX - BAND_H_MIN) * Math.sqrt(frac);
    cell.strength =
      COLOR_STRENGTH_MIN + (1 - COLOR_STRENGTH_MIN) * Math.pow(frac, COLOR_STRENGTH_POWER);
  }

  const spotByDate = new Map(oiDaily.spot.map((s) => [s.date, s.close]));
  const spot: (number | null)[] = allDates.map(
    (dt) => {
      const close = spotByDate.get(dt);
      return close != null ? close / 10000 : null;
    },
  );

  return { cells, spot, oiMax };
}
