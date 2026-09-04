/**
 * RecurringCyclesPanel — one card per code: the data-viz index price plot on
 * top (imported verbatim from the index-baseline feature) + the per-date
 * recurring-cycle bar charts below.
 *
 * Layout:
 *   • TOP — IndexPanel (the exact data-viz index plot: OHLC + MA5/20/60/120
 *     + trading-amt + PE, with intraday expansion + composition + linked-
 *     ETFs side panels). Clicking ANY date on its curve fires onDateClick,
 *     which sets `selectedDate` and reactively refreshes the bar charts
 *     below. The IndexPanel is imported as-is — no re-implementation.
 *   • BELOW — one recurring-cycle bar chart per range_days window
 *     (20/60/255/500/750/1275), laid out in a vertical stack. For the
 *     selected date, each chart shows per integer day period THREE bars
 *     (day-aligned spectra precomputed in Python by
 *     analyze.recurring_cycles.pattern_score and stored in
 *     analysis.recurring_cycles.amplitude_spectrum / count_spectrum /
 *     strength_spectrum, element j = day j+2):
 *       amp (left axis — energy-merged FFT amplitude, the Fourier
 *            REFERENCE for which day periods carry swing energy),
 *       count (right axis — the recurrence COUNT factor: extrema
 *            evidence × ACF coherence — says whether price actually
 *            REPEATED that rise/drop spacing),
 *       strength (right axis — the summarized recurring strength:
 *            (amp/σ_band) × count). The RECURRING period (argmax of
 *            strength, row.period_days) is highlighted green.
 *     Day periods < 5d are hidden by default; an expand/collapse toggle
 *     shows the full granular spectrum.
 *
 * The selected last_date defaults to the latest date in the index bundle,
 * so a spectrum is shown immediately on load (before the user clicks).
 *
 * Backed by analysis.recurring_cycles (sec_type='index' only for now) —
 * the spectra come from the day-aligned double-precision array columns.
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
  fetchRecurringCyclesSpectrum,
  invalidateCacheForUrl,
} from "@/lib/api-client";
import { buildSpectrumOption } from "./spectrumOption";
import { PoissonAuditTable } from "./PoissonAuditTable";
import type { PanelProps } from "./types";
import type {
  IndexBundle,
  IndexCombinedResponse,
  RecurringCyclesSpectrumResponse,
} from "@shared/types";

export function RecurringCyclesPanel({
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
  const [spectrum, setSpectrum] = useState<RecurringCyclesSpectrumResponse | null>(null);
  const [spectrumLoading, setSpectrumLoading] = useState(false);
  const [spectrumError, setSpectrumError] = useState<string | null>(null);

  // ---- Expand/collapse toggle for granular high-freq day periods --------
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
    fetchRecurringCyclesSpectrum(code, secType, selectedDate)
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

  // Whether this security has recurring-cycles analysis rows — drives the
  // bold highlight of the per-security build button (AnalysisRunButton).
  // Loading counts as "present" so the button doesn't bold-flicker.
  const hasAnalysisData =
    spectrumLoading || (spectrum != null && spectrum.spectrums.length > 0);

  // Refetch after a per-security analysis rebuild (AnalysisRunButton):
  // drop the cached spectrum response, then bump the refresh key.
  const handleAnalysisRunCompleted = useCallback(() => {
    const qs = new URLSearchParams({ code, sec_type: secType });
    if (selectedDate) qs.set("last_date", selectedDate);
    invalidateCacheForUrl(`/api/analysis/recurring-cycles/spectrum?${qs.toString()}`);
    setRefreshKey((k) => k + 1);
  }, [code, secType, selectedDate]);

  const spectrumOptions = useMemo(() => {
    if (!spectrum || spectrum.spectrums.length === 0) return [];
    return spectrum.spectrums.map((row) => ({
      range_days: row.range_days,
      option: buildSpectrumOption({ row, themeMode, expanded }),
    }));
  }, [spectrum, themeMode, expanded]);

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
            No index-baseline data for {code}. The Recurring Cycles
            spectrum is available, but the top price plot needs an
            index_basic_stats row.
          </Alert>
        </ChartCard>
      )}

      {/* ---- BELOW: per-date recurring-cycle bar charts --------------- */}
      <ChartCard
        title={`Recurring Cycle Spectrum${effectiveDate ? ` · ${effectiveDate}` : ""}`}
        subtitle={
          effectiveDate
            ? `Click any date on the plot above to refresh these charts. ` +
              `X-axis: integer day periods. Bars (left) = amp — merged FFT ` +
              `amplitude, the Fourier reference. Bars (right) = count — ` +
              `recurrence evidence (extrema hits × ACF multiples), ` +
              `strength — the summarized bar: (amp/σ_band) × count, and ` +
              `significance — the Poisson audit (−log10 p; dashed line = ` +
              `p<0.05). A day period scores only when the price REPEATED ` +
              `its rise/drop spacing AND the repetition beats the chance ` +
              `rate; the recurring period peaks in strength (green).`
            : "Click any date on the plot above to show its spectrum."
        }
        action={
          <Stack direction="row" alignItems="center">
            <Tooltip title={expanded ? "Collapse high-freq day periods (< 5d)" : "Show granular high-freq day periods"}>
              <IconButton
                size="small"
                onClick={() => setExpanded((v) => !v)}
                sx={{ ml: 1 }}
              >
                {expanded ? <ExpandLessIcon /> : <ExpandMoreIcon />}
              </IconButton>
            </Tooltip>
            <AnalysisRunButton
              module="recurring_cycles"
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
          amp bars (left): energy-merged FFT amplitude per day period — the Fourier reference.
          count bars (right): recurrence evidence — prominence-filtered swing-extrema hits
          (full-cycle spacing within ±15% of the day period) × ACF coherence (multiples with
          significant autocorrelation ≥ 1.96/√N after MA detrending). strength bars (right): the
          summarized bar — (amp/σ_band) × count. significance bars (right): the Poisson audit —
          −log10 of the Bonferroni-adjusted p-value of the swing-hit count vs its empirically
          calibrated chance expectation λ̂₀ (point-process null on random-walk + stochastic-vol
          price nulls); bars above the dashed p&lt;0.05 line mark day periods whose repeated
          rise/drop pattern is statistically real rather than chance. One-off swings, trends, and
          noise score ~0 at every day period; a recurring noticeable-swing pattern peaks. The
          green bar marks the RECURRING period (argmax of strength); its audit verdict
          (significance tier · evidence × null) is in each chart title. Periods over ⅓ of the
          window are not auditable (under 3 cycles). Windows 20/60/255/500/750/1275d. Spectra are
          precomputed in Python (analyze.recurring_cycles.pattern_score) and stored in
          analysis.recurring_cycles.
          {!expanded && " · Day periods < 5d hidden by default — use the expand button to show all."}
        </Typography>
      </ChartCard>

      {/* ---- Poisson audit table: observed vs expected vs p ---------- */}
      {!spectrumLoading &&
        !spectrumError &&
        spectrum != null &&
        spectrum.spectrums.length > 0 && (
          <ChartCard
            title={`Poisson Audit${effectiveDate ? ` · ${effectiveDate}` : ""}`}
            subtitle={
              `Per auditable day period (d ≤ window/3): hits — observed ` +
              `swing-hit count; λ̂₀ — the calibrated chance expectation ` +
              `(point-process null); ×null — evidence hits/λ̂₀; −log10 p / ` +
              `p — the Bonferroni-adjusted Poisson tail (≥ 1.30 ⇔ p<0.05, ` +
              `≥ 2.0 ⇔ p<0.01). Sorted by significance — the day periods ` +
              `whose repeated rise/drop spacing beats chance surface ` +
              `first. ◆ green = the recurring period (strength argmax). ` +
              `Column headers carry filters (ticks / numeric ranges).`
            }
          >
            <PoissonAuditTable
              spectrums={spectrum.spectrums}
              scopeKey={`${code}-${effectiveDate}`}
            />
          </ChartCard>
        )}
    </Stack>
  );
}
