/**
 * SharedSkewPanel — mode-parameterized skew-over-time panel used by ALL
 * data sources in analysis.options_skewness_stats (skew_type):
 *
 *   • mode='oi_moneyness' — OI-wtd mean moneyness positioning skew
 *     (skew CURVE from the materialized server series
 *     analysis.options_moneyness_series via /oi-moneyness-series — real
 *     expiry dates, full per-expiry shade bands on all dates; per-expiry
 *     OI level/changes read from the dedicated per-real-expiry DB table
 *     options_oi_stats via /oi-stats, arriving async after the chart).
 *   • mode='greek_<name>' — PAIR-level CALL-vs-PUT positioning balance
 *     (greek_delta: delta-wtd put/call ratio; greek_gamma: GEX-style
 *     gamma balance; greek_vega: OTM-wing vega balance), computed in the
 *     DB pipeline and fetched from /skewness-series (The Greeks context;
 *     the browser only joins stored values with spot and rebases them
 *     around the per-mode neutral: S × (1 + (skew − neutral) × 0.10)).
 *
 * The oi_moneyness skew curve is server-computed from the materialized
 * series table (real expiry dates → full per-expiry lines + shade bands
 * on all dates, incl. the latest); its absolute-OI tooltip stats (OI /
 * Δ5d / Δ20d / 20d max) and the per-expiry line-width encoding come
 * from the DB (options_oi_stats + options_bands load ASYNC after the
 * chart renders — markers/widths layer in when they land, never
 * blocking the initial paint). greek_* curves come fully from the DB.
 * ONE time slider (on the main skew chart) drives BOTH charts via shared
 * zoom state (useDataZoomSync); tooltip crosshair sync between the charts.
 * The convergence chart has no slider or wheel-zoom of its own. A slider
 * window pinned at the FULL range cannot pan (both ends clamped) and its
 * drag strip is dead, so on long axes (CFFEX index options, ~900 bars)
 * the initial window is capped to the most recent bars — the slider is
 * draggable from the first frame; the user can widen back to full with
 * the handles/wheel.
 *
 * Options-implied BANDS layer (analysis.options_bands v2 — OI-MASS
 * CORRIDOR), shown behind the top-right "Bands On/Off" toggle: the gray
 * shaded corridor = strikes enclosing the central 80% of pooled chain OI
 * (OI-CDF P10..P90), with corridor-cross markers on the spot curve
 * (EXIT_LOW green below the line — snap-back-up side; EXIT_HIGH red
 * above; REENTER gray diamond). Validated: close <= P10 = 90% up over
 * the next 5 sessions (mean +4.5%); close >= P90 = 89% down; the legend
 * toggles the corridor and each marker class individually. AMPLIFIED
 * exits render bigger with a glow when the exchange had just extended
 * the strike grid the same way (>= 2 new strikes in the trailing 40
 * sessions, edge_sig) — a breach into freshly-listed price territory.
 *
 * (The IV-smile pricing skew — 25Δ/10Δ risk reversal vs spot over
 * time — lives in spot-skew/SpotSkewTrendPanel; see
 * docs/options_vol_smile_study.md.)
 */
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import ChartCard from "@/shared/components/ChartCard";
import EChart from "@/shared/charts/EChart";
import { useChartThemeMode } from "@/shared/charts/base-chart";
import { useAiAskAddon } from "@/shared/ai-ask";
import type { AiAskSpec } from "@/shared/ai-ask";
import {
  fetchOptionsBands,
  fetchOptionsOiStats,
  fetchOptionsSkewnessSeries,
  fetchOptionsMoneynessSeries,
  fetchOptionsWalls,
  type OptionsTargetType,
  type OptionsVenue,
} from "@/lib/api-client/options";
import { oiStatsLookupFromRows } from "../vol-smile/skewSeries";
import { moneynessSpec, greekLabel } from "./skewSpec";
import { moneynessSpecFromSeries, spotByDateFromSeries } from "./moneynessSeriesSpec";
import { greekSpecFromSeries } from "./greekSpec";
import type { OptionsMoneynessSeriesResponse } from "@shared/types";
import { buildSharedSkewOption } from "./sharedSkewOption";
import { buildChainWallZones, NO_WALL_ZONES } from "./wallZonesSpec";
import {
  buildSkewConvergenceOption,
  convergenceTitle,
} from "./convergenceOption";
import { useDataZoomSync } from "../options-trend/useDataZoomSync";
import { sharedSkewAxisDates } from "./sharedSkewOption";
import { PRICE_SCALE } from "@/theme/chart-palette";
import type {
  OptionsBandsRow,
  OptionsOiStatsRow,
  OptionsWallRow,
  SkewnessSeriesRow,
} from "@shared/types";
import type {
  GreekSkewMode,
  SharedBandEvent,
  SharedCorridorPoint,
  SharedSkewHighlights,
  SharedSkewMode,
  SharedWallZones,
} from "./types";
import type { ECharts } from "echarts";
import {
  Box,
  CircularProgress,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";

interface Props {
  mode: SharedSkewMode;
  /** Fetch scope — the panel loads its own per-plot SQL (one plot, one
   *  query); pages no longer hand it the full combined row blob. */
  underlying: string;
  targetType?: OptionsTargetType;
  exchange?: OptionsVenue;
  selectedDate: string;
  onDateChange?: (date: string) => void;
  /** Refresh-button epoch — >0 refetches with caches bypassed. */
  reloadKey?: number;
  /** FORECAST TRIGGER-DAY OVERLAY (the Options Bands signal page's
   *  forecast row clicks — the same visual language the Recent
   *  Movements trend chart uses): a purple pinpoint on the spot curve
   *  at each trigger day + full-height bands (dark = each merged
   *  signal's qualifying streak period; light = each trigger day's
   *  forward forecast window, highlightHorizonDays axis rows). Keep
   *  the array identities STABLE across renders (state arrays or
   *  module consts) — the option memo keys on them. Empty/undefined =
   *  no overlay (the option stays bit-identical to the plain panel). */
  highlightDates?: readonly string[];
  highlightSpans?: ReadonlyArray<{
    start: string;
    end: string;
    days: number | null;
  }>;
  highlightHorizonDays?: number;
  /** Fired after the skew option (re)builds while a highlight overlay
   *  is active — the "overlay rendered" gate a page's forecast-table
   *  freeze waits on (zoom / theme / date rebuilds re-apply the
   *  overlay and re-fire this). */
  onHighlightSettled?: () => void;
  /** Optional header action node (rendered in the ChartCard header's
   *  action slot — e.g. the trigger-days chip of the forecast overlay). */
  headerAction?: ReactNode;
}

const GREEK_MODES: GreekSkewMode[] = [
  "greek_delta",
  "greek_gamma",
  "greek_vega",
];

/** Identity-stable empty defaults — keep the skewOption memo inputs
 * reference-clean when no band rows are loaded (ui-charts rule). */
const NO_BAND_EVENTS: SharedBandEvent[] = [];
const NO_CORRIDOR: SharedCorridorPoint[] = [];
const NO_HL_DATES: readonly string[] = [];
const NO_HL_SPANS: ReadonlyArray<{
  start: string;
  end: string;
  days: number | null;
}> = [];

/**
 * Initial slider window cap, in category-axis bars (data dates + future
 * expiry tail). Axes longer than this open on the most recent
 * INITIAL_WINDOW_MAX_BARS bars (see the cap effect in the component) so
 * the slider strip is pannable from the first frame; shorter axes keep
 * the full-range view. ~420 bars ≈ the last 17-18 months of sessions.
 */
const INITIAL_WINDOW_MAX_BARS = 420;

/** Per-greek metric semantics for the panel subtitle (industry anchors). */
const GREEK_METRIC_TEXT: Record<GreekSkewMode, string> = {
  greek_delta:
    "Delta-wtd Put/Call Ratio dpcr = Σ OI·|Δ| (puts) / Σ OI·|Δ| (all) per expiry — " +
    "where DIRECTIONAL exposure sits (delta-weighted refinement of the put/call ratio; " +
    "0.5 = balanced book, >0.5 put-tilted/bearish, <0.5 call-tilted/bullish; OI is two-sided " +
    "— exposure concentration, not signed bets)",
  greek_gamma:
    "Gamma Balance = (Σ OI·Γ calls − Σ OI·Γ puts)/(Σ OI·Γ all) per expiry — GEX-style " +
    "dealer-sign convention: >0 call OI dominates where gamma lives (long-gamma regime, vol " +
    "suppression/pin), <0 put OI dominates (short-gamma regime, moves amplify)",
  greek_vega:
    "Vega Wing Balance = (Σ OI·ν calls − Σ OI·ν puts)/(Σ OI·ν wings) on the 0<|Δ|<0.5 OTM " +
    "wings per expiry — the open-interest mirror of the 25Δ risk reversal: >0 upside vol demand, " +
    "<0 downside (crash-hedge) vol demand",
};

function greekPanelMeta(mode: GreekSkewMode): {
  title: string;
  /** Reading guide — lives in the AI Ask modal intro. */
  subtitle: string;
  /** Concise card subtitle — just the decoding essentials. */
  short: string;
} {
  const label = greekLabel(mode);
  return {
    title: `${label} Positioning · Underlying Price & CALL-vs-PUT Balance`,
    subtitle:
      `Spot vs Skew Price = S × (1 + (metric − neutral) × 10%). Metric: ${GREEK_METRIC_TEXT[mode]}. ` +
      "Contracts vote with raw OI (zero OI = no vote); greeks from Black-76 IV off settlements — " +
      `premium is the value, never the vote (skew_type=${mode}, neutral on the spot curve). ` +
      "Dashed blue = mean skew price, thin dashed = per-expiry; shades = spot↔skew gap; " +
      "dots = expiries; bottom = expiry convergence (contrarian). Indication: skew price " +
      "riding above spot = positioning tailwind; converging gaps into expiry = positioning " +
      "spent. Click to select date.",
    short:
      `Spot vs ${label} skew price · dashed: mean + per-expiry · shades: spot↔skew gap · ` +
      "convergence below · click to select date",
  };
}

const META: Partial<
  Record<SharedSkewMode, { title: string; subtitle: string; short: string }>
> = {
  oi_moneyness: {
    title: "OI-weighted Moneyness Skew · Underlying Price & Positioning",
    subtitle:
      "Spot vs Skew-Adjusted Price = S × E[M], E[M] = Σ(OI·K/S) / Σ(OI) per expiry — where " +
      "open interest sits vs spot. Contracts vote with raw OI lots (floored at 1) — never " +
      "premium/notional, no IV: a 500-lot deep-OTM strike outvotes a 100-lot ATM one " +
      "(skew_type=oi_moneyness). Dashed blue = OI-wtd skew price, per-expiry dashed (line " +
      "width ∝ expiry OI, plotted value stays the ratio); shades = spot↔skew gap; dots = " +
      "expiries; bottom = OI convergence (contrarian). Indication: E[M] > 1 = OI parked " +
      "overhead (supply), < 1 = support beneath. Click to select date.",
    short:
      "Spot vs OI-wtd skew price · dashed: mean + per-expiry (width ∝ OI) · shades: spot↔skew gap · " +
      "convergence below · click to select date",
  },
};
for (const m of GREEK_MODES) {
  META[m] = greekPanelMeta(m);
}

export default function SharedSkewPanel({
  mode,
  underlying,
  targetType,
  exchange,
  selectedDate,
  onDateChange,
  reloadKey = 0,
  highlightDates = NO_HL_DATES,
  highlightSpans = NO_HL_SPANS,
  highlightHorizonDays = 1,
  onHighlightSettled,
  headerAction,
}: Props) {
  const themeMode = useChartThemeMode();
  const underlyingCode = underlying;
  const [seriesRows, setSeriesRows] = useState<SkewnessSeriesRow[]>([]);
  const [oiStatsRows, setOiStatsRows] = useState<OptionsOiStatsRow[]>([]);
  const [moneynessResp, setMoneynessResp] = useState<OptionsMoneynessSeriesResponse | null>(null);
  const [seriesLoading, setSeriesLoading] = useState(true);
  const [seriesError, setSeriesError] = useState<string | null>(null);
  const [showConvergence, setShowConvergence] = useState<boolean>(true);
  const [bandsRows, setBandsRows] = useState<OptionsBandsRow[]>([]);
  const [wallRows, setWallRows] = useState<OptionsWallRow[]>([]);
  const [showBandEvents, setShowBandEvents] = useState<boolean>(true);

  // Shared time-slider state (the useDataZoomSync mechanism from the
  // Options Trend panel): the skew chart owns the visible slider and
  // inside wheel-zoom; BOTH options rebuild from the same {start,end}, so
  // the slider-less convergence chart follows and option regeneration
  // (date clicks, toggles, theme) keeps the current window.
  const { zoom, handleDataZoom, setDataZoom } = useDataZoomSync();

  // Venue quote scale: SSE/SZSE ETF closes are stored in 厘 (÷1000 →
  // yuan); CFFEX index closes are native index points (÷1) — same
  // convention as SpotSkewTrendPanel.
  const priceScale = exchange === "CFFEX" ? 1 : PRICE_SCALE;
  const priceUnit = priceScale === 1 ? "index points" : "yuan";

  // First user zoom gesture (slider drag / handle / wheel / brush) —
  // after it the initial-window cap below must never fire again.
  const userZoomedRef = useRef(false);
  const onZoomEvent = useCallback(
    (params: unknown) => {
      userZoomedRef.current = true;
      handleDataZoom(params);
    },
    [handleDataZoom],
  );

  const startDate = moneynessResp?.spot[0]?.date;
  const endDate = moneynessResp?.spot[moneynessResp.spot.length - 1]?.date;

  // Per-plot load #1 — the oi_moneyness series aggregate (ALSO supplies the
  // spot-per-date map the greek_* rebases need; one cached fetch serves
  // both). Reset state on scope change so the spec is NEVER computed with
  // a mismatched mode/data pair.
  useEffect(() => {
    if (!underlyingCode) return;
    let cancelled = false;
    setSeriesLoading(true);
    setSeriesError(null);
    setMoneynessResp(null);
    fetchOptionsMoneynessSeries(underlyingCode, targetType, exchange, {
      fresh: reloadKey > 0,
    })
      .then((resp) => {
        if (cancelled) return;
        setMoneynessResp(resp);
        setSeriesLoading(false);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setSeriesError(e.message);
        setSeriesLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [underlyingCode, targetType, exchange, reloadKey]);

  // Daily skewness series (greek modes only), read from
  // options_skewness_stats for the mode's skew_type.
  useEffect(() => {
    setSeriesRows([]);
    const greek = mode.startsWith("greek_");
    if (!underlyingCode || !greek || !startDate || !endDate) return;
    let cancelled = false;
    fetchOptionsSkewnessSeries(underlyingCode, startDate, endDate, mode)
      .then((seriesResp) => {
        if (cancelled) return;
        setSeriesRows(seriesResp.rows);
      })
      .catch(() => {
        if (!cancelled) setSeriesRows([]);
      });
    return () => {
      cancelled = true;
    };
  }, [underlyingCode, startDate, endDate, mode]);

  // Per-plot load #2 (oi_moneyness mode) — per-expiry OI stats from the
  // dedicated per-real-expiry table options_oi_stats (pipeline-computed OI
  // level / Δ5d/Δ20d / trailing-20d max). Reset on mode change so the
  // spec is NEVER computed with a mismatched mode/data pair.
  useEffect(() => {
    setOiStatsRows([]);
    const greek = mode.startsWith("greek_");
    if (!underlyingCode || greek) return;
    let cancelled = false;
    fetchOptionsOiStats(underlyingCode)
      .then((resp) => {
        if (cancelled) return;
        setOiStatsRows(resp.rows);
      })
      .catch(() => {
        if (!cancelled) setOiStatsRows([]);
      });
    return () => {
      cancelled = true;
    };
  }, [underlyingCode, mode, reloadKey]);

  // Per-plot load #3 — options-implied corridor bands (analysis.options_bands
  // v2): the OI-mass corridor (P10..P90 strikes) + fresh corridor crosses
  // vs the prior session's corridor. Rendered as the corridor shade +
  // markers on the spot curve; failure degrades to no layer (never blocks
  // the panel). The wall/zone rows (analysis.options_walls) ride the same
  // load — the /walls URL is LRU-cached, so the Options Trend card's
  // identical fetch is served from cache.
  useEffect(() => {
    setBandsRows([]);
    setWallRows([]);
    if (!underlyingCode) return;
    let cancelled = false;
    Promise.all([
      fetchOptionsBands(underlyingCode),
      fetchOptionsWalls(underlyingCode),
    ])
      .then(([bands, walls]) => {
        if (cancelled) return;
        setBandsRows(bands.rows);
        setWallRows(walls.rows);
      })
      .catch(() => {
        if (!cancelled) {
          setBandsRows([]);
          setWallRows([]);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [underlyingCode, targetType, exchange, reloadKey]);

  // Spot (yuan) per date — from the series response (was: derived from
  // raw quote rows; zero closes are already null server-side).
  const spotByDate = useMemo(
    () => (moneynessResp ? spotByDateFromSeries(moneynessResp, priceScale) : new Map<string, number>()),
    [moneynessResp, priceScale],
  );

  // DB OI stats keyed by (date, expiry month) for the spec adapters.
  const oiStats = useMemo(
    () => oiStatsLookupFromRows(oiStatsRows),
    [oiStatsRows],
  );

  // Skew-over-time spec — oi_moneyness comes from the server aggregate
  // (real expiry dates → full per-expiry lines + shade bands on all dates,
  // incl. the latest; the absolute-OI stats joined from the DB); greek_*
  // specs come fully from the DB series.
  const spec = useMemo(() => {
    const base = mode.startsWith("greek_")
      ? greekSpecFromSeries(
          mode as GreekSkewMode,
          seriesRows,
          spotByDate,
        )
      : moneynessResp
        ? moneynessSpecFromSeries(moneynessResp, oiStats, priceScale)
        : moneynessSpec([]);
    return { ...base, priceUnit };
  }, [mode, moneynessResp, seriesRows, spotByDate, oiStats, priceScale, priceUnit]);

  // Slider reactivity: a dataZoom window pinned at the FULL range cannot
  // pan (both ends clamped) — dragging the slider strip is then a silent
  // no-op, so neither the skew chart nor the convergence chart below
  // reacts. Fresh views of LONG histories (CFFEX index options: ~900
  // bars + expiry tail) always start full, which is exactly where the
  // slider felt dead. Cap the initial window to the most recent bars so
  // the slider is immediately pannable; short axes (ETF venues) keep the
  // full view. Never re-cap once the user has zoomed.
  useEffect(() => {
    if (!moneynessResp || userZoomedRef.current) return;
    const axisLen = sharedSkewAxisDates(spec, selectedDate).length;
    if (axisLen <= INITIAL_WINDOW_MAX_BARS) return;
    setDataZoom({
      start: Math.round((1 - INITIAL_WINDOW_MAX_BARS / axisLen) * 100),
      end: 100,
    });
  }, [moneynessResp, spec, selectedDate, setDataZoom]);

  // Corridor events: fresh crosses of the OI-mass corridor vs the prior
  // session's corridor (EXIT_LOW / EXIT_HIGH / REENTER); markers need no
  // price scaling — the builder joins them to the spec's spot values by
  // date. sig = the edge-significance amplifier: this side's cross
  // coincides with fresh same-side strike-grid expansion (>= 2 new
  // strikes in the trailing 40 sessions, analysis.options_bands.edge_sig).
  const bandEvents = useMemo<SharedBandEvent[]>(() => {
    const out: SharedBandEvent[] = [];
    for (const r of bandsRows) {
      if (r.event === "EXIT_LOW" || r.event === "EXIT_HIGH"
          || r.event === "REENTER") {
        const amplified = r.edge_sig === "BOTH"
          || (r.event === "EXIT_HIGH" && r.edge_sig === "UP")
          || (r.event === "EXIT_LOW" && r.edge_sig === "DOWN");
        out.push({
          date: r.date,
          side: r.event === "EXIT_LOW" ? "lower"
            : r.event === "EXIT_HIGH" ? "upper" : "none",
          kind: r.event,
          spotPct: r.spot_pct,
          lean: r.lean,
          sig: amplified,
          sigNewStrikes: r.event === "EXIT_HIGH" ? r.new_hi_40d
            : r.event === "EXIT_LOW" ? r.new_lo_40d : null,
        });
      }
    }
    return out.length > 0 ? out : NO_BAND_EVENTS;
  }, [bandsRows]);

  // OI-mass corridor bounds (OI-CDF P10/P90 strikes), price-scaled to
  // the chart unit (ETF venues store raw levels in 厘 — divide by
  // PRICE_SCALE exactly like the spot series).
  const corridor = useMemo<SharedCorridorPoint[]>(() => {
    const scale = (v: number | null): number | null =>
      v == null ? null : v / priceScale;
    const out: SharedCorridorPoint[] = [];
    for (const r of bandsRows) {
      if (r.corridor_low == null && r.corridor_high == null) continue;
      out.push({
        date: r.date,
        low: scale(r.corridor_low),
        high: scale(r.corridor_high),
      });
    }
    return out.length > 0 ? out : NO_CORRIDOR;
  }, [bandsRows, priceScale]);

  // Chain-wide dominant wall zones per (date, side) — the wall/zone light
  // shades of the bands layer (analysis.options_walls, same source the
  // Options Trend OI Bands chart overlays per cohort), price-scaled like
  // the corridor.
  const wallZones = useMemo<SharedWallZones>(
    () => (wallRows.length > 0 ? buildChainWallZones(wallRows, priceScale) : NO_WALL_ZONES),
    [wallRows, priceScale],
  );

  // The forecast trigger-day overlay payload — identity-stable per
  // inputs; undefined when nothing is highlighted so the plain-panel
  // option stays bit-identical.
  const hasHighlights =
    highlightDates.length > 0 || highlightSpans.length > 0;
  const highlights = useMemo<SharedSkewHighlights | undefined>(
    () =>
      hasHighlights
        ? {
            dates: highlightDates,
            spans: highlightSpans,
            horizonDays: highlightHorizonDays,
          }
        : undefined,
    [hasHighlights, highlightDates, highlightSpans, highlightHorizonDays],
  );

  const skewOption = useMemo(
    () =>
      buildSharedSkewOption(
        spec,
        themeMode,
        selectedDate,
        zoom.start,
        zoom.end,
        showBandEvents ? bandEvents : undefined,
        showBandEvents ? corridor : undefined,
        showBandEvents ? wallZones : undefined,
        highlights,
      ),
    [spec, selectedDate, themeMode, zoom.start, zoom.end, showBandEvents, bandEvents, corridor, wallZones, highlights],
  );

  // "Overlay rendered" gate — the option rebuild CARRIES the highlight
  // overlay (notMerge), so the option being built is the moment the
  // overlay lands. Fired only while a highlight is active; zoom / theme
  // / date rebuilds re-apply the overlay and re-fire.
  useEffect(() => {
    if (highlights) onHighlightSettled?.();
  }, [skewOption, highlights, onHighlightSettled]);

  // Expiry-convergence (contrarian) chart — per-expiry skew price vs spot
  // gap (%), collapsing into the zero (spot) line at expiry. Mode-specific:
  // OI-wtd moneyness gap on the Open Interests tab, IV smile skewness gap
  // on the Volatility Smile tab, greek balances on the Greeks tabs.
  const convOption = useMemo(
    () =>
      showConvergence
        ? buildSkewConvergenceOption(
            spec,
            themeMode,
            selectedDate,
            zoom.start,
            zoom.end,
          )
        : null,
    [spec, selectedDate, showConvergence, themeMode, zoom.start, zoom.end],
  );

  // Refs to chart instances for cross-chart dataZoom + tooltip sync
  const skewChartRef = useRef<ECharts | null>(null);
  const convChartRef = useRef<ECharts | null>(null);
  const [skewReady, setSkewReady] = useState(false);
  const [convReady, setConvReady] = useState(false);

  useEffect(() => {
    const skewChart = skewChartRef.current;
    if (!skewReady || !skewChart) return;
    const convChart =
      showConvergence && convReady ? convChartRef.current : null;

    // Tooltip crosshair sync via zrender mouse events: hovering EITHER
    // chart shows the axis tooltip on the OTHER at the same date, so both
    // plots read simultaneously. (dataZoom sync is NOT wired here — both
    // charts share the zoom state from useDataZoomSync, fed by the
    // onEvents dataZoom handlers below.)
    const bindTooltipSync = (source: ECharts, target: ECharts) => {
      const zr = source.getZr();
      const onMove = (ev: { offsetX?: number; offsetY?: number }) => {
        const x = ev.offsetX;
        const y = ev.offsetY;
        if (x == null || y == null) return;
        if (!source.containPixel("grid", [x, y])) return;
        // Map through the CATEGORY INDEX, not raw pixels: the two grids
        // have different margins, so the same x lands a few days apart.
        // Both charts share one category axis (sharedSkewAxisDates), so
        // the index identifies the same date on both.
        const idx = Math.round(source.convertFromPixel({ xAxisIndex: 0 }, x));
        if (idx < 0) return;
        // Pixel-position showTip on the target: a seriesIndex/dataIndex
        // tip silently no-ops when the target's series has a NULL at that
        // index (per-expiry lines only exist within their lifetime). A
        // pixel tip snaps the axis pointer regardless of any single
        // series' data.
        const tx = target.convertToPixel({ xAxisIndex: 0 }, idx);
        target.dispatchAction({
          type: "showTip",
          x: tx,
          y: target.getHeight() / 2,
        });
      };
      const onOut = () => {
        target.dispatchAction({ type: "hideTip" });
      };
      zr.on("mousemove", onMove);
      zr.on("mouseout", onOut);
      return () => {
        zr.off("mousemove", onMove);
        zr.off("mouseout", onOut);
      };
    };

    const unbinds = convChart
      ? [
          bindTooltipSync(skewChart, convChart),
          bindTooltipSync(convChart, skewChart),
        ]
      : [];

    return () => {
      unbinds.forEach((u) => u());
    };
  }, [skewReady, convReady, showConvergence]);

  const handleSkewChartReady = useCallback((chart: ECharts) => {
    skewChartRef.current = chart;
    setSkewReady(true);
  }, []);

  const handleConvChartReady = useCallback((chart: ECharts) => {
    convChartRef.current = chart;
    setConvReady(true);
  }, []);

  const handleCanvasClick = useCallback(
    (dataIndex: number) => {
      if (!onDateChange) return;
      const point = dataIndex < spec.points.length ? spec.points[dataIndex] : undefined;
      if (point) onDateChange(point.date);
    },
    [spec.points, onDateChange],
  );

  const meta = META[mode] ?? greekPanelMeta(mode as GreekSkewMode);

  // AI Ask — the reading guide (META subtitle) lives in the intro; the
  // card shows only the concise `short` decoding line.
  // Suggested questions carry the guide's actionable angle.
  const aiAskSpec = useMemo<AiAskSpec>(
    () => ({
      intro: meta.subtitle,
      instruments: underlyingCode ? [{ code: underlyingCode }] : [],
      series: [
        {
          name: "Underlying Spot",
          unit: priceUnit === "index points" ? "pts" : "元",
          description: "underlying daily closing price (the neutral anchor)",
        },
        {
          name: spec.meanSeriesName,
          unit: priceUnit === "index points" ? "pts" : "元",
          description: `mean skew-adjusted price — spot rebased by the ${mode} positioning metric`,
        },
        { name: "Skew price vs spot gap (%)", unit: "%", description: "per-expiry convergence chart: skew price minus spot, collapsing to 0 at expiry" },
      ],
      state: {
        mode,
        convergence: showConvergence ? "shown" : "hidden",
        bandEvents: showBandEvents ? "shown" : "hidden",
        selectedDate,
      },
      notes: [
        "Shade bands: spot↔skew gap per active expiry set; dots mark expiries; click the chart to select the date.",
        "Bands layer (toggle top-right, analysis.options_bands v2 — OI-mass corridor): the gray shade = strikes enclosing the central 80% of pooled chain OI (P10..P90) — the options-bounded region; markers = fresh corridor crosses vs the prior session (EXIT_LOW green below the line — validated snap-back-up: close <= P10 = 90% up over the next 5 sessions, mean +4.5%; EXIT_HIGH red above — 89% down; REENTER gray diamond). AMPLIFIED exits render bigger with a colored glow when the exchange had just extended the strike grid the same way (>= 2 new strikes listed in the trailing 40 sessions — edge_sig; e.g. 000300 2025-08-28 EXIT_HIGH with 6 fresh higher strikes): the breach is into freshly-listed price territory, not routine corridor noise. The light green/red WALL SHADES mark each side's chain-dominant OI wall zone (analysis.options_walls, wall_type='zone' — same source the Options Trend OI Bands chart overlays per cohort; ranked chain-wide here): band = the zone's strike range, center line = its OI-weighted strike; CALL walls act as resistance/cap, PUT walls as support/floor; brightness = lifecycle (ACTIVE > ERODED > BREACHED, breach drawn dashed). Spot's OI-mass position (<=25% of OI below spot) is the UP lean zone, strongest when every expiry's moneyness skew sits above spot (mean 10-session +1.57%). Legend items toggle the corridor, the wall zones and each marker class.",
        "Per-expiry line WIDTH encodes the expiry's absolute open interest (peak daily calls+puts contracts, sqrt-scaled); the plotted values remain ratio-based. Tooltips show each group's pipeline-computed OI stats (analysis.options_oi_stats): OI at the hovered date, its change vs 5/20 sessions (Δ5d/Δ20d, + = positions added / − = closed or rolled) and the trailing-20-session max (current OI at that max = a 20-session OI high). Hovering either chart shows both tooltips.",
      ],
    }),
    [meta.subtitle, underlyingCode, spec.meanSeriesName, mode, showConvergence, showBandEvents, selectedDate, priceUnit],
  );
  const extraOptions = useMemo(() => [convOption] as const, [convOption]);
  const aiAskAddon = useAiAskAddon({
    title: meta.title,
    subtitle: meta.short,
    option: skewOption,
    extraOptions,
    spec: aiAskSpec,
    getInstance: () => skewChartRef.current,
    getExtraInstances: () => [convChartRef.current],
  });

  const toggleSx = {
    bgcolor: "background.paper",
    "& .MuiToggleButton-root": {
      px: 1.5,
      py: 0.25,
      fontSize: "0.7rem",
      minWidth: 48,
    },
  } as const;

  return (
    <ChartCard
      title={meta.title}
      subtitle={meta.short}
      titleAddon={aiAskAddon}
      action={headerAction}
      height={540}
    >
      {seriesLoading ? (
        <Box sx={{ display: "flex", justifyContent: "center", alignItems: "center", height: 300 }}>
          <CircularProgress size={28} />
        </Box>
      ) : seriesError ? (
        <Box sx={{ display: "flex", justifyContent: "center", alignItems: "center", height: 300 }}>
          <Typography variant="body2" color="error">
            Failed to load skew series: {seriesError}
          </Typography>
        </Box>
      ) : (
      <>
      <div style={{ position: "relative" }}>
        <EChart
          option={skewOption}
          height={300}
          onCanvasClick={handleCanvasClick}
          onEvents={{ dataZoom: onZoomEvent }}
          onReady={handleSkewChartReady}
        />
        {/* Bands Show/Hide — absolute top-right (the corner the centered
            legend keeps free). Layer = the OI-mass corridor shade (central
            80% of chain OI, gray) + corridor-cross markers on the spot
            curve (EXIT_LOW green below / EXIT_HIGH red above / REENTER
            gray diamond) + the chain-dominant wall-zone light shades
            (CALL green / PUT red low→high bands with OI-weighted center
            lines, analysis.options_walls). */}
        <ToggleButtonGroup
          value={showBandEvents}
          exclusive
          onChange={(_, v: boolean | null) => {
            if (v != null) setShowBandEvents(v);
          }}
          size="small"
          sx={{ ...toggleSx, position: "absolute", top: 0, right: 8 }}
        >
          <ToggleButton value={true}>Bands On</ToggleButton>
          <ToggleButton value={false}>Off</ToggleButton>
        </ToggleButtonGroup>
      </div>
      {showConvergence && convOption ? (
        <Box sx={{ mt: 1 }}>
          <Box
            sx={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              mb: 0.5,
            }}
          >
            <Typography variant="body2" sx={{ fontWeight: 600 }}>
              {convergenceTitle(mode)}
              <Typography
                component="span"
                variant="body2"
                color="text.secondary"
                sx={{ ml: 1 }}
              >
                (zero = spot · dots mark current &amp; expiry gaps)
              </Typography>
            </Typography>
            <ToggleButtonGroup
              value={showConvergence}
              exclusive
              onChange={(_, v: boolean | null) => {
                if (v != null) setShowConvergence(v);
              }}
              size="small"
              sx={toggleSx}
            >
              <ToggleButton value={true}>Show</ToggleButton>
              <ToggleButton value={false}>Hide</ToggleButton>
            </ToggleButtonGroup>
          </Box>
          {/* Slider-less follower: zoom events still flow through the
              shared handler, but the builder's inside dataZoom is
              interaction-locked, so only the skew chart can move it. */}
          <EChart
            option={convOption}
            height={260}
            onEvents={{ dataZoom: onZoomEvent }}
            onReady={handleConvChartReady}
          />
        </Box>
      ) : null}
      </>
      )}
    </ChartCard>
  );
}
