/**
 * VolIndexPanel — "Vol Index · 30d Model-Free (VIX-style)".
 *
 * A SHAREABLE panel with two modes (the SharedSkewPanel convention):
 *
 *  • mode="in-analysis" (default) — the Options Analysis / Options
 *    page's own chart: the daily 30-day constant-maturity model-free
 *    implied-volatility index (100·√variance of the OTM-strip
 *    replication off settlement prices — the smile LEVEL, per
 *    docs/options_vol_smile_study.md §9) for the SELECTED underlying,
 *    with the spot close overlaid on a second right-hand axis, driven
 *    by the store's Underlying selector. Rendered exactly as before.
 *
 *  • mode="in-signal" — the Options Signals page's OPTIONS VIX view:
 *    the same chart + the signal-history MARKER layer (the
 *    options_vix family's events — buy ▲ / sell ▼ via THE shared
 *    triangles, the grey ◆ diamond on gate-rejected events), a
 *    selected-day pin + click-to-select (the page's history-row
 *    clicks move it; clicking the chart moves the page), and the
 *    family's corr BARS (-0.8 fear / -0.5 unwind) as guide lines on
 *    the corr companion — the emphasized bar (the picked trigger-bar
 *    chip's config, the wall-panel chip→bold-wall analog) thick.
 *
 * Inside the SAME card (BaseChart `footer` slot), a hover-synced chart
 * plots the rolling correlation of the vol index against that spot
 * (analysis.options_vol_index_vs_spot_price_corr — Pearson ρ of daily
 * log-changes over 5/20/60/255-session windows; the stored table
 * samples a 5-session stride grid + the daily tail, and the API
 * LINEARLY INTERPOLATES it onto every vol-index session — the same
 * convention the options_vix signal engine reads; nothing is written
 * back to the DB): the CBOE VIX-vs-spot "fear gauge" relation,
 * conventionally measured on RETURNS vs CHANGES (never the trending
 * levels). Both charts share the "vol-index" echarts.connect group —
 * identical date axes, so hover tooltips and dataZoom stay
 * index-aligned between them.
 */
import { useEffect, useMemo, useState } from "react";
import { Box, Typography } from "@mui/material";
import { BaseChart, useChartThemeMode } from "@/shared/charts/base-chart";
import type { AiAskSpec } from "@/shared/ai-ask";
import { useStore } from "@/store/filters";
import {
  fetchEtfOhlcv,
  fetchOptionsVolIndex,
  fetchOptionsVolIndexCorr,
  venueToTargetType,
} from "@/lib/api-client/options";
import type {
  EtfOhlcvResponse,
  VolIndexCorrRow,
  VolIndexRow,
} from "@shared/types";
import {
  buildVolIndexCorrOption,
  buildVolIndexOption,
  volIndexUnderlyingLabel,
  type VolIndexSignalEvent,
} from "./volIndexOption";

/** echarts.connect group shared by the two charts (registered once in
 *  main.tsx) — hover tooltips + dataZoom sync by index. */
const CONNECT_GROUP = "vol-index";

/** The panel's rendering mode (the SharedSkewPanel convention):
 * in-analysis = the analysis pages' plain chart; in-signal = the
 * options_vix signals view (markers + selected-day + corr bars). */
export type VolIndexPanelMode = "in-analysis" | "in-signal";

export interface VolIndexPanelProps {
  /** The rendering mode — see the module doc. Default in-analysis. */
  mode?: VolIndexPanelMode;
  /** in-signal: the underlying's options_vix signal history narrowed
   *  by the page's sub-type pick (markers on the vol curve). */
  signalEvents?: readonly VolIndexSignalEvent[];
  /** in-signal: the chart's selected day ('YYYY-MM-DD'; the pin). */
  selectedDate?: string | null;
  /** in-signal: chart canvas click → select that trading day. */
  onDateSelect?: (date: string) => void;
  /** in-signal: the emphasized corr bar (the picked trigger-bar
   *  chip's config bar, -0.8 / -0.5); null = none emphasized. */
  emphasizedCorrBar?: number | null;
}

export default function VolIndexPanel({
  mode = "in-analysis",
  signalEvents,
  selectedDate = null,
  onDateSelect,
  emphasizedCorrBar = null,
}: VolIndexPanelProps) {
  const themeMode = useChartThemeMode();
  const underlyingCode = useStore((s) => s.underlyingCode);
  const optionsVenue = useStore((s) => s.optionsVenue);
  const [rows, setRows] = useState<VolIndexRow[] | null>(null);
  const [corrRows, setCorrRows] = useState<VolIndexCorrRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [corrError, setCorrError] = useState<string | null>(null);
  const [ohlcv, setOhlcv] = useState<EtfOhlcvResponse | null>(null);
  const inSignal = mode === "in-signal";

  useEffect(() => {
    if (!underlyingCode) return;
    let cancelled = false;
    setRows(null);
    setCorrRows(null);
    setError(null);
    setCorrError(null);
    fetchOptionsVolIndex(underlyingCode)
      .then((resp) => {
        if (!cancelled) setRows(resp.rows);
      })
      .catch((e: Error) => {
        if (!cancelled) setError(e.message);
      });
    fetchOptionsVolIndexCorr(underlyingCode)
      .then((resp) => {
        if (!cancelled) setCorrRows(resp.rows);
      })
      .catch((e: Error) => {
        if (!cancelled) setCorrError(e.message);
      });
    return () => {
      cancelled = true;
    };
  }, [underlyingCode]);

  // Spot-close overlay source (v_etf_margin / v_index_daily — closes in
  // display units). Optional context: a failure just drops the overlay.
  useEffect(() => {
    if (!underlyingCode) {
      setOhlcv(null);
      return;
    }
    let cancelled = false;
    setOhlcv(null);
    fetchEtfOhlcv(underlyingCode, null, null, venueToTargetType(optionsVenue))
      .then((resp) => {
        if (!cancelled) setOhlcv(resp);
      })
      .catch(() => {
        if (!cancelled) setOhlcv(null);
      });
    return () => {
      cancelled = true;
    };
  }, [underlyingCode, optionsVenue]);

  const hasSpot = ohlcv != null && ohlcv.rows.length > 0;
  const spotByDate = useMemo(() => {
    if (!hasSpot) return undefined;
    // close <= 0 is the loaders' "no spot" convention (unclosed session,
    // v_etf_margin fallback 0) — same treatment as bandData/skewSeries.
    return new Map<string, number | null>(
      ohlcv!.rows.map((r) => [r.date, r.close != null && r.close > 0 ? r.close : null]),
    );
  }, [hasSpot, ohlcv]);

  // The chart's category axis (sorted union of the vol rows' dates) —
  // the click-to-select dataIndex maps through it.
  const axisDates = useMemo(
    () =>
      rows
        ? Array.from(new Set(rows.map((r) => r.date))).sort()
        : [],
    [rows],
  );

  const option = useMemo(
    () =>
      rows && rows.length > 0
        ? buildVolIndexOption(
            rows,
            themeMode,
            spotByDate,
            inSignal
              ? {
                  signalEvents,
                  selectedDate,
                }
              : undefined,
          )
        : null,
    [rows, themeMode, spotByDate, inSignal, signalEvents, selectedDate],
  );

  // Corr chart — only meaningful once the vol rows give the shared date
  // axis; absent corr rows just leave the three curves unsampled (null).
  const corrOption = useMemo(
    () =>
      rows && rows.length > 0 && corrRows && corrRows.length > 0
        ? buildVolIndexCorrOption(
            corrRows,
            rows,
            themeMode,
            inSignal
              ? {
                  // the options_vix family's corr bars: the -0.8 fear
                  // bar (the three BUY configs) and the -0.5 unwind
                  // bar — the picked chip's bar thick + labeled
                  corrBars: [
                    { value: -0.8, emphasized: emphasizedCorrBar === -0.8 },
                    { value: -0.5, emphasized: emphasizedCorrBar === -0.5 },
                  ],
                  selectedDate,
                }
              : undefined,
          )
        : null,
    [rows, corrRows, themeMode, inSignal, emphasizedCorrBar, selectedDate],
  );

  // AI Ask — concise formula + pointer; the card shows only the identity.
  const aiAskSpec = useMemo<AiAskSpec>(
    () => ({
      intro:
        "CBOE-VIX analog for the selected underlying: 100·√(30d constant-maturity model-free " +
        "variance) from the OTM-strip replication on settlement prices (CBOE method, r = 2%) " +
        "— the smile LEVEL. Indication: swell = fear bid, hedges chased; trough = " +
        "complacency — both extremes revert. docs/options_vol_smile_study.md §9. " +
        "The chart below correlates it with spot: rolling Pearson ρ of the daily spot " +
        "log-return vs the daily vol-index log-change over 5/20/60/255 sessions (the CBOE " +
        "VIX-vs-spot 'fear gauge' convention — returns vs changes, never the trending " +
        "levels; the 5-session stride samples are linearly interpolated to daily for " +
        "display and signal reads). ρ toward -1 = spot down days bid vol (classic " +
        "leverage effect); ρ rising toward 0 = vol decoupling from direction." +
        (inSignal
          ? " IN-SIGNAL mode: the options_vix family's events mark the vol curve " +
            "(buy ▲ / sell ▼ / the grey ◆ no-action diamond); the corr bars -0.8 " +
            "(deep fear) and -0.5 (unwind) ride the corr chart — click the chart " +
            "to move the selected day."
          : ""),
      instruments: underlyingCode ? [{ code: underlyingCode }] : [],
      series: [
        {
          name: `${volIndexUnderlyingLabel(underlyingCode)} VIX`,
          unit: "vol pts",
          description: "daily 30-day model-free implied-volatility index (the smile LEVEL)",
        },
        ...(hasSpot
          ? [
              {
                name: underlyingCode
                  ? volIndexUnderlyingLabel(underlyingCode)
                  : "Spot",
                unit: "price",
                description:
                  "underlying close on the right-hand axis (context overlay, v_etf_margin / v_index_daily)",
              },
            ]
          : []),
        ...(corrRows != null && corrRows.length > 0
          ? [
              {
                name: "Corr 5d/20d/60d/255d",
                unit: "ρ",
                description:
                  "rolling Pearson corr of daily spot log-return vs vol-index log-change (options_vol_index_vs_spot_price_corr)",
              },
            ]
          : []),
      ],
      notes: [
        "SZSE ETF legs inherit the known ETF settle-data condition; CFFEX index legs carry the deep history.",
      ],
    }),
    [underlyingCode, hasSpot, corrRows, inSignal],
  );

  return (
    <BaseChart
      title="Vol Index · 30d Model-Free (VIX-style)"
      subtitle={
        inSignal
          ? hasSpot
            ? "options_vix signals · the fear-gauge events mark the vol curve · spot close on the right axis"
            : "options_vix signals · the fear-gauge events mark the vol curve"
          : hasSpot
            ? "30-day model-free vol index (VIX-style) · the smile LEVEL · vol pts · spot close on the right axis"
            : "30-day model-free vol index (VIX-style) · the smile LEVEL · vol pts"
      }
      aiAsk={aiAskSpec}
      height={340}
      group={CONNECT_GROUP}
      option={option}
      onCanvasClick={
        inSignal && onDateSelect
          ? (dataIndex) => {
              const d = axisDates[dataIndex];
              if (d) onDateSelect(d);
            }
          : undefined
      }
      // rows still null and no error → the fetch is in flight (or no
      // underlying selected) — keep the prior centered-spinner behavior.
      loading={rows == null && error == null}
      error={error ? `Failed to load vol index: ${error}` : null}
      emptyText="No vol-index rows for this underlying — the 30-day model-free replication needs settle-priced OTM strips on expiries bracketing 30 days."
      footer={
        // The hover-synced corr companion — bare body inside the same
        // card, below the vol chart (its own loading/error/empty state);
        // the caption carries the identity (bare renders no chrome).
        <Box sx={{ borderTop: 1, borderColor: "divider", mt: 1.5, pt: 1 }}>
          <Typography
            variant="caption"
            sx={{ color: "var(--chart-subtitle)", fontSize: "0.75rem" }}
          >
            Vol Index vs Spot · Rolling Correlation — Pearson ρ of daily
            spot log-return vs vol-index log-change · 5d / 20d / 60d /
            255d windows · stride samples interpolated to daily ·
            hover-synced with the chart above
            {inSignal
              ? " · the options_vix bars: -0.8 deep fear (BUY configs) / -0.5 unwind (exit)"
              : ""}
          </Typography>
          <BaseChart
            variant="bare"
            height={280}
            group={CONNECT_GROUP}
            option={corrOption}
            loading={
              corrRows == null &&
              corrError == null &&
              rows != null &&
              rows.length > 0
            }
            error={corrError ? `Failed to load vol-vs-spot corr: ${corrError}` : null}
            emptyText="No vol-vs-spot correlation rows for this underlying yet — the table fills once the vol index has joint-valid sessions with the spot close (corr_5d first, corr_255d last)."
          />
        </Box>
      }
    />
  );
}
