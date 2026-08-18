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
 *   • BELOW — one amplitude-spectrum bar chart per range_days window
 *     (20/60/255/500/750), laid out in a responsive grid. Each chart shows
 *     the FULL one-sided FFT amplitude spectrum for the selected date +
 *     window; the dominant bin (→ dominant cycle period) is highlighted.
 *
 * The selected last_date defaults to the latest date in the index bundle,
 * so a spectrum is shown immediately on load (before the user clicks).
 *
 * Backed by analysis.fourier_freqs (sec_type='index' only for now) — the
 * spectrum comes from the amplitude_spectrum double-precision array column.
 */
import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  CircularProgress,
  Stack,
  Typography,
} from "@mui/material";
import ChartCard from "@/components/ChartCard";
import EChart from "@/components/EChart";
import IndexPanel from "@/dataviz/features/index-baseline/IndexPanel";
import {
  fetchIndicesCombined,
  fetchFourierFreqsSpectrum,
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
  }, [code, secType, selectedDate]);

  // The effective last_date shown in the spectrum header. Falls back to
  // the backend-resolved date in the spectrum response (when the selected
  // date had no spectrum rows, the backend may have resolved differently).
  const effectiveDate = spectrum?.last_date ?? selectedDate ?? "";

  const spectrumOptions = useMemo(() => {
    if (!spectrum || spectrum.spectrums.length === 0) return [];
    return spectrum.spectrums.map((row) => ({
      range_days: row.range_days,
      option: buildSpectrumOption(row, themeMode),
    }));
  }, [spectrum, themeMode]);

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
              `Each bar = one FFT bin (cycle period label on x-axis); the ` +
              `dominant bin is highlighted.`
            : "Click any date on the plot above to show its spectrum."
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
                `Dates without ${750}-day history have no long-window spectrum.`
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
          Y-axis: amplitude in yuan (½ peak-to-peak swing of each sinusoidal
          component). X-axis: cycle period in trading days. Green bar = the
          dominant cycle (highest amplitude). Windows 20/60/255/500/750d.
        </Typography>
      </ChartCard>
    </Stack>
  );
}
