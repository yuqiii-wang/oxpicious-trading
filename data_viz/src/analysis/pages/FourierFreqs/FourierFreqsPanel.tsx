/**
 * FourierFreqsPanel — one card per code: the data-viz index price plot on
 * top (imported verbatim from the index-baseline feature) + the per-date
 * full-FFT-spectrum bar charts below.
 *
 * Layout:
 *   • TOP — IndexPanel (the exact data-viz index plot: OHLC + MA5/20/60/120
 *     + trading-amt + PE, with intraday expansion + composition + linked-
 *     ETFs side panels). Clicking ANY date on its curve fires onDateClick,
 *     which sets `selectedDate` and reactively refreshes the bar charts
 *     below. The IndexPanel is imported as-is — no re-implementation.
 *   • BELOW — one day-frequency spectrum chart per range_days window
 *     (20/60/255/500/750/1275), laid out in a responsive grid. For the
 *     selected date, each chart shows per integer day freq: amp bars
 *     (left axis — energy-merged FFT amplitude, the Fourier REFERENCE
 *     for which day freqs carry energy) and pattern-score bars (right
 *     axis — the CONSOLIDATED periodic-pattern audit:
 *     (amp/σ_band) × extrema evidence × ACF coherence, see
 *     patternScore.ts). A day freq that actually recurs with
 *     noticeable highs and lows shows a score peak; noise does not.
 *     FFT bins whose period rounds to the same integer day are MERGED
 *     into one bar; the dominant day freq (max merged amp) is
 *     highlighted green.
 *     Day freqs < 5d are hidden by default; an expand/collapse toggle
 *     shows the full granular spectrum.
 *
 * The selected last_date defaults to the latest date in the index bundle,
 * so a spectrum is shown immediately on load (before the user clicks).
 *
 * Backed by analysis.fourier_freqs (sec_type='index' only for now) — the
 * spectrum comes from the amplitude_spectrum double-precision array column.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  CircularProgress,
  IconButton,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import ExpandLessIcon from "@mui/icons-material/ExpandLess";
import ChartCard from "@/components/ChartCard";
import EChart from "@/components/EChart";
import AnalysisRunButton from "@/components/AnalysisRunButton";
import IndexPanel from "@/dataviz/features/index-baseline/IndexPanel";
import {
  fetchIndicesCombined,
  fetchFourierFreqsSpectrum,
  invalidateCacheForUrl,
} from "@/lib/api-client";
import { buildSpectrumOption } from "./spectrumOption";
import type { PanelProps } from "./types";
import type {
  IndexBundle,
  IndexCombinedResponse,
  FourierFreqsSpectrumResponse,
} from "@shared/types";

export function FourierFreqsPanel({
  code,
  name,
  secType,
  themeMode,
}: PanelProps) {
  // ---- Top plot: the index bundle (drives IndexPanel) -------------------
  const [bundle, setBundle] = useState<IndexBundle | null>(null);
  const [bundleLoading, setBundleLoading] = useState(false);
  const [bundleError, setBundleError] = useState<string | null>(null);

  // ---- Reactive selected last_date (set by clicking the top plot) -------
  const [selectedDate, setSelectedDate] = useState<string | null>(null);

  // ---- Spectrum for the selected date (drives the bar charts) -----------
  const [spectrum, setSpectrum] = useState<FourierFreqsSpectrumResponse | null>(null);
  const [spectrumLoading, setSpectrumLoading] = useState(false);
  const [spectrumError, setSpectrumError] = useState<string | null>(null);

  // ---- Expand/collapse toggle for granular high-freq bins ---------------
  const [expanded, setExpanded] = useState(false);

  // Bumped by the per-security AnalysisRunButton after a rebuild run —
  // retriggers the spectrum fetch (the cache entry is invalidated first
  // in the completion handler).
  const [refreshKey, setRefreshKey] = useState(0);

  // Fetch the IndexBundle whenever code changes. fetchIndicesCombined with
  // a bare `code` returns a single-index page (page_size=1).
  useEffect(() => {
    let cancelled = false;
    setBundleLoading(true);
    setBundleError(null);
    setBundle(null);
    fetchIndicesCombined(undefined, undefined, undefined, undefined, undefined, undefined, code)
      .then((resp: IndexCombinedResponse) => {
        if (cancelled) return;
        const b = resp.indices.length > 0 ? resp.indices[0] : null;
        setBundle(b);
        // Default the selected date to the last date in the bundle so a
        // spectrum shows immediately (before the user clicks anything).
        if (b && b.rows.length > 0) {
          setSelectedDate(b.rows[b.rows.length - 1].date);
        } else {
          setSelectedDate(null);
        }
        setBundleLoading(false);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setBundleError(e.message);
        setBundleLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [code]);

  // Fetch the spectrum whenever the selected date changes.
  useEffect(() => {
    if (!selectedDate) {
      setSpectrum(null);
      return;
    }
    let cancelled = false;
    setSpectrumLoading(true);
    setSpectrumError(null);
    fetchFourierFreqsSpectrum(code, secType, selectedDate)
      .then((d) => {
        if (cancelled) return;
        setSpectrum(d);
        setSpectrumLoading(false);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setSpectrumError(e.message);
        setSpectrumLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [code, secType, selectedDate, refreshKey]);

  // The effective last_date shown in the spectrum header. Falls back to
  // the backend-resolved date in the spectrum response (when the selected
  // date had no spectrum rows, the backend may have resolved differently).
  const effectiveDate = spectrum?.last_date ?? selectedDate ?? "";

  // Whether this security has fourier-freqs analysis rows — drives the
  // bold highlight of the per-security build button (AnalysisRunButton).
  // Loading counts as "present" so the button doesn't bold-flicker.
  const hasAnalysisData =
    spectrumLoading || (spectrum != null && spectrum.spectrums.length > 0);

  // Refetch after a per-security analysis rebuild (AnalysisRunButton):
  // drop the cached spectrum response, then bump the refresh key.
  const handleAnalysisRunCompleted = useCallback(() => {
    const qs = new URLSearchParams({ code, sec_type: secType });
    if (selectedDate) qs.set("last_date", selectedDate);
    invalidateCacheForUrl(`/api/analysis/fourier-freqs/spectrum?${qs.toString()}`);
    setRefreshKey((k) => k + 1);
  }, [code, secType, selectedDate]);

  const spectrumOptions = useMemo(() => {
    if (!spectrum || spectrum.spectrums.length === 0) return [];
    // Window closes (chronological, ending at the effective date) for
    // the time-domain ACF repeat audit — sliced from the SAME
    // trading-day series the FFT windows were built on. Null closes
    // (no trading that day) are dropped so the series stays indexed by
    // actual trading days.
    const rows = bundle?.rows ?? [];
    let end = rows.length;
    if (effectiveDate) {
      const i = rows.findIndex((r) => r.date === effectiveDate);
      if (i >= 0) end = i + 1;
    }
    return spectrum.spectrums.map((row) => {
      const start = Math.max(0, end - row.range_days);
      const closes: number[] = [];
      for (let i = start; i < end; i++) {
        const v = rows[i].close;
        if (v != null) closes.push(v);
      }
      return {
        range_days: row.range_days,
        option: buildSpectrumOption({ row, themeMode, expanded, closes }),
      };
    });
  }, [spectrum, themeMode, expanded, bundle, effectiveDate]);

  return (
    <Stack spacing={1.5}>
      {/* ---- TOP: the exact data-viz index plot (imported) ------------ */}
      {bundleLoading && (
        <ChartCard title={`${code} · ${name}`} subtitle="Loading index plot…">
          <Box sx={{ display: "flex", justifyContent: "center", py: 6 }}>
            <CircularProgress size={28} />
          </Box>
        </ChartCard>
      )}
      {bundleError && (
        <ChartCard title={`${code} · ${name}`}>
          <Alert severity="error" variant="filled">
            Failed to load index plot for {code}: {bundleError}
          </Alert>
        </ChartCard>
      )}
      {!bundleLoading && !bundleError && bundle && (
        <IndexPanel
          index={bundle}
          themeMode={themeMode}
          onDateClick={(date: string) => setSelectedDate(date)}
        />
      )}
      {!bundleLoading && !bundleError && !bundle && (
        <ChartCard title={`${code} · ${name}`}>
          <Alert severity="warning">
            No index-baseline data for {code}. The Fourier Frequencies
            spectrum is available, but the top price plot needs an
            index_basic_stats row.
          </Alert>
        </ChartCard>
      )}

      {/* ---- BELOW: per-date full-FFT-spectrum bar charts ------------- */}
      <ChartCard
        title={`FFT Amplitude Spectrum${effectiveDate ? ` · ${effectiveDate}` : ""}`}
        subtitle={
          effectiveDate
            ? `Click any date on the plot above to refresh these spectra. ` +
              `X-axis: integer day freqs (FFT bins rounding to the same day are merged). ` +
              `Bars (left) = merged FFT amplitude — the Fourier reference. ` +
              `Bars (right) = pattern score — the consolidated periodic-pattern bar: ` +
              `(amp/σ_band) × extrema evidence × ACF coherence. A day freq that ` +
              `actually recurs with noticeable highs and lows peaks here.`
            : "Click any date on the plot above to show its spectrum."
        }
        action={
          <Stack direction="row" alignItems="center">
            <Tooltip title={expanded ? "Collapse high-freq bins (period < 5d)" : "Show granular high-freq bins"}>
              <IconButton
                size="small"
                onClick={() => setExpanded((v) => !v)}
                sx={{ ml: 1 }}
              >
                {expanded ? <ExpandLessIcon /> : <ExpandMoreIcon />}
              </IconButton>
            </Tooltip>
            <AnalysisRunButton
              module="fourier_freqs"
              secType={secType}
              code={code}
              hasData={hasAnalysisData}
              onCompleted={handleAnalysisRunCompleted}
            />
          </Stack>
        }
      >
        {spectrumLoading && (
          <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
            <CircularProgress size={24} />
          </Box>
        )}
        {spectrumError && (
          <Alert severity="error" sx={{ py: 0.5 }}>
            Failed to load spectrum for {effectiveDate}: {spectrumError}
          </Alert>
        )}
        {!spectrumLoading && !spectrumError && spectrumOptions.length === 0 && (
          <Alert severity="info" sx={{ py: 0.5 }}>
            {effectiveDate
              ? `No spectrum data for ${code} on ${effectiveDate}. ` +
                `Dates without ${1275}-day history have no long-window spectrum.`
              : "Select a date on the plot above."}
          </Alert>
        )}
        {!spectrumLoading && !spectrumError && spectrumOptions.length > 0 && (
          <Stack spacing={1}>
            {spectrumOptions.map(({ range_days, option }) => (
              <Box
                key={range_days}
                sx={{
                  border: (t) => `1px solid ${t.palette.divider}`,
                  borderRadius: 1,
                  p: 0.5,
                }}
              >
                <EChart option={option} height={220} />
              </Box>
            ))}
          </Stack>
        )}
        <Typography variant="caption" color="text.secondary" sx={{ mt: 0.5, display: "block" }}>
          amp bars (left): energy-merged FFT amplitude per day freq — the Fourier reference
          (dominant in green). pattern-score bars (right): consolidated periodic-pattern audit —
          (amp/σ_band) × extrema evidence (prominence-filtered swing extrema whose full-cycle
          spacing lands within ±15% of the day freq) × ACF coherence (multiples of the day freq
          with significant autocorrelation ≥ 1.96/√N after MA detrending). Noise scores ~0 at
          every day freq; a recurring noticeable-swing pattern peaks. Periods over ⅓ of the window
          are not auditable (under 3 cycles). Windows 20/60/255/500/750/1275d.
          {!expanded && " · Day freqs < 5d hidden by default — use the expand button to show all."}
        </Typography>
      </ChartCard>
    </Stack>
  );
}