/**
 * DateEventStrip — shared horizontal date-event marker strip (ECharts).
 *
 * Extracted from the Debt Baseline page's PBoC OMA panel so any page can show
 * "what happened on which date" as one dot per event on a time axis:
 *
 *   • one scatter series per event TYPE (legend toggle + per-type color via
 *     `typeMeta`; unlisted types fall back to the muted palette);
 *   • tooltip per marker: title line + date/type line + optional detail;
 *   • click → `onEventClick(event)` (debt expands the announcement content;
 *     news toggles that date as the list filter);
 *   • selected marker (matched against `selectedId` via String equality)
 *     renders larger — ids are caller-defined (row index for debt, the date
 *     string itself for news);
 *   • `minDate`/`maxDate` pin the x-axis range (debt aligns the strip with
 *     its sibling chart panels; omit to use the events' own range).
 *
 * Pure presentation: data fetching, loading/error state, and any detail panel
 * below the strip stay with the caller.
 */
import { useCallback, useMemo } from "react";
import { Box, Chip, Stack, Typography } from "@mui/material";
import React from "react";
import EChart from "@/components/EChart";
import { useStore } from "@/store/filters";
import {
  MUTED_PALETTE,
  axisColors,
  commonDataZoom,
  commonGrid,
  commonLegend,
} from "@/theme/chart-palette";
import { renderReactElement } from "@/lib/react-tooltip-renderer";

/** One marker on the strip. */
export interface DateEvent {
  /** Event date (YYYY-MM-DD) — the x position. */
  date: string;
  /** Stable identity for selection/clicks. Defaults to the array index. */
  id?: number | string;
  /** Type key — groups markers into one legend-toggling scatter series. */
  type?: string;
  /** Tooltip title (defaults to the type label). */
  title?: string;
  /** Extra tooltip line(s) (e.g. keywords, article count). */
  detail?: string;
  /** Base marker size override (px, default 10; selected → 16). */
  size?: number;
}

export interface DateEventTypeMeta {
  label: string;
  color: string;
}

interface Props {
  events: DateEvent[];
  /** Type key → legend label + marker color. */
  typeMeta?: Record<string, DateEventTypeMeta>;
  /** x-axis range pins (e.g. a sibling chart's min/max for alignment). */
  minDate?: string | null;
  maxDate?: string | null;
  /** id (DateEvent.id or array index) of the highlighted marker. */
  selectedId?: number | string | null;
  /** Marker click. Receives the resolved DateEvent (id defaults to index). */
  onEventClick?: (event: DateEvent) => void;
  /** Strip height in px (default 100). */
  height?: number;
  /** Show the ECharts legend + per-type chips + count caption (default true). */
  showLegend?: boolean;
  /** Empty hint when events is empty (rendered as muted caption). */
  emptyText?: string;
  /** Show the in-chart ECharts dataZoom slider (commonDataZoom — the same
   *  style as the code-trend charts) to window the strip. Default false. */
  enableZoom?: boolean;
  /** Initial window size in days when `enableZoom` (default 92 ≈ one season —
   *  the slider opens showing only the most recent season of events). */
  defaultWindowDays?: number;
  /** Shaded band marking a SIBLING chart's visible window (e.g. the code
   *  trend's dataZoom range on the AI page) — the strip stays full-range
   *  so every event dot stays visible while the band shows which slice
   *  the sibling is on. Null/omitted = no band. */
  highlightRange?: { start: string; end: string } | null;
}

const BASE_SIZE = 10;
const SELECTED_SIZE = 16;
const DEFAULT_WINDOW_DAYS = 92;

export default function DateEventStrip({
  events,
  typeMeta = {},
  minDate = null,
  maxDate = null,
  selectedId = null,
  onEventClick,
  height = 100,
  showLegend = true,
  emptyText = "No events.",
  enableZoom = false,
  defaultWindowDays = DEFAULT_WINDOW_DAYS,
  highlightRange = null,
}: Props) {
  const themeMode = useStore((s) => s.themeMode);

  // ---- Zoom window (enableZoom) — in-chart ECharts dataZoom slider via the
  // shared commonDataZoom (identical style to the code-trend charts). The
  // initial window opens on the most recent `defaultWindowDays`, expressed
  // as a start % of the full axis range; the EChart wrapper keeps the user's
  // live viewport across option rebuilds and applies a new start % whenever
  // the events change (a filter change re-opens the recent window).
  const fullMin = events.length ? events[0].date : null;
  const fullMax = events.length ? events[events.length - 1].date : null;

  // Slider-mode axis: a too-short events range (even a SINGLE day — e.g. a
  // fresh Q&A corpus) would give the time axis min==max and the dataZoom
  // slider a degenerate, unusable track. Pad the domain by
  // `defaultWindowDays` on each side, so the axis is at least twice the
  // window and the initial (right-anchored) window is a proper draggable
  // half instead of the full track.
  const zoomAxis = useMemo(() => {
    if (!enableZoom) return null;
    const lo = minDate ?? fullMin;
    const hi = maxDate ?? fullMax;
    if (!lo || !hi) return null;
    const loMs = Date.parse(`${lo}T00:00:00`);
    const hiMs = Date.parse(`${hi}T00:00:00`);
    if (Number.isNaN(loMs) || Number.isNaN(hiMs)) return null;
    const padMs = defaultWindowDays * 86_400_000;
    if (hiMs - loMs >= padMs) return { min: lo, max: hi };
    const mid = (loMs + hiMs) / 2;
    const fmt = (ms: number) =>
      new Date(ms).toISOString().slice(0, 10);
    return { min: fmt(mid - padMs), max: fmt(mid + padMs) };
  }, [enableZoom, minDate, maxDate, fullMin, fullMax, defaultWindowDays]);

  const zoomStartPct = useMemo(() => {
    if (!enableZoom || !zoomAxis) return 0;
    const minMs = Date.parse(`${zoomAxis.min}T00:00:00`);
    const maxMs = Date.parse(`${zoomAxis.max}T00:00:00`);
    if (maxMs <= minMs) return 0;
    const cutoffMs = maxMs - defaultWindowDays * 86_400_000;
    return Math.min(100, Math.max(0, ((cutoffMs - minMs) / (maxMs - minMs)) * 100));
  }, [enableZoom, zoomAxis, defaultWindowDays]);
  // With the dataZoom owning windowing, the axis stays pinned to the FULL
  // event range (so the start % math and the slider backdrop cover everything).
  const axisMin = enableZoom ? zoomAxis?.min ?? null : minDate;
  const axisMax = enableZoom ? zoomAxis?.max ?? null : maxDate;

  // Normalize: resolve ids (default index) + effective type label/color.
  const normalized = useMemo(
    () =>
      events.map((e, idx) => {
        const type = e.type ?? "default";
        const meta = typeMeta[type];
        // Unlisted types get a stable palette color (string hash → slot).
        let hash = 0;
        for (let i = 0; i < type.length; i++) hash = (hash * 31 + type.charCodeAt(i)) | 0;
        const color = meta?.color
          ?? MUTED_PALETTE[Math.abs(hash) % MUTED_PALETTE.length];
        return {
          ...e,
          _id: e.id ?? idx,
          _idx: idx,
          _type: type,
          _label: meta?.label ?? type,
          _color: color,
        };
      }),
    [events, typeMeta],
  );

  const seriesByType = useMemo(() => {
    const map = new Map<string, typeof normalized>();
    for (const e of normalized) {
      const arr = map.get(e._type) ?? [];
      arr.push(e);
      map.set(e._type, arr);
    }
    return map;
  }, [normalized]);

  const isSelected = useCallback(
    (id: number | string) =>
      selectedId != null && String(id) === String(selectedId),
    [selectedId],
  );

  const option = useMemo(() => {
    const c = axisColors(themeMode);
    const xMin = axisMin || events[0]?.date || "";
    const xMax = axisMax || events[events.length - 1]?.date || "";
    return {
      backgroundColor: "transparent",
      animation: false,
      // Roomier bottom margin when the dataZoom slider renders inside the
      // canvas (slider 18px + axis labels), mirroring the code-trend charts.
      grid: commonGrid({ left: 16, right: 16, top: 16, bottom: enableZoom ? 52 : 28 }),
      tooltip: {
        trigger: "item" as const,
        backgroundColor: c.tooltipBg,
        borderColor: c.splitLineColor,
        textStyle: { color: c.textColor, fontSize: 11 },
        formatter: (params: unknown) => {
          const p = params as { data?: { event?: DateEvent & { _id: number | string; _label: string } } };
          const e = p.data?.event;
          if (!e) return "";
          const children: React.ReactNode[] = [];
          children.push(React.createElement("div", {
            style: { fontWeight: 600, maxWidth: 380 as number | string },
          }, e.title ?? e._label));
          children.push(React.createElement("div", {
            style: { fontSize: 10, opacity: 0.7, marginTop: 2 },
          }, `${e.date} · ${e._label}`));
          if (e.detail) {
            children.push(React.createElement("div", {
              style: { fontSize: 10, opacity: 0.8, marginTop: 2 },
            }, e.detail));
          }
          return renderReactElement(React.createElement(React.Fragment, null, children));
        },
      },
      xAxis: {
        type: "time" as const,
        min: xMin || undefined,
        max: xMax || undefined,
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: {
          color: c.textColor,
          fontSize: 10,
          formatter: (v: number) => {
            const d = new Date(v);
            const yyyy = d.getFullYear();
            const mm = String(d.getMonth() + 1).padStart(2, "0");
            return `${yyyy}-${mm}`;
          },
        },
        axisTick: { show: false },
        splitLine: { show: false },
      },
      yAxis: { type: "value" as const, min: -1, max: 1, show: false },
      // In-chart dataZoom slider — commonDataZoom's default labelFormatter
      // assumes category date strings, but this strip's x-axis is a TIME
      // axis whose slider labels arrive as ms timestamps; format those.
      dataZoom: enableZoom
        ? commonDataZoom(
            {
              labelFormatter: (val: string | number) => {
                const d = new Date(Number(val));
                return Number.isNaN(d.getTime())
                  ? String(val)
                  : `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
              },
            },
            zoomStartPct,
            100,
          )
        : undefined,
      legend: showLegend
        ? commonLegend(themeMode, {
            left: "right",
            data: Array.from(seriesByType.keys()).map((t) => typeMeta[t]?.label ?? t),
          })
        : undefined,
      series: Array.from(seriesByType.entries()).map(([type, points], seriesIdx) => ({
        name: typeMeta[type]?.label ?? type,
        type: "scatter" as const,
        data: points.map((e) => ({
          value: [e.date, 0],
          event: e,
        })),
        symbolSize: (val: unknown, params: unknown) => {
          const p = params as { data?: { event?: { _id: number | string; size?: number } } };
          const ev = p.data?.event;
          if (ev && isSelected(ev._id)) return SELECTED_SIZE;
          return ev?.size ?? BASE_SIZE;
        },
        itemStyle: {
          color: typeMeta[type]?.color ?? points[0]?._color,
          opacity: 0.85,
          borderColor: "#fff",
          borderWidth: 1,
          shadowBlur: 2,
          shadowColor: "rgba(0,0,0,0.25)",
        },
        emphasis: {
          itemStyle: { borderColor: "#fff", borderWidth: 2, shadowBlur: 6 },
          scale: 1.3,
        },
        // highlightRange band — attached to the FIRST series only (one band
        // for the whole strip), silent so dot clicks/hovers pass through.
        markArea: seriesIdx === 0 && highlightRange
          ? {
              silent: true,
              itemStyle: {
                color: themeMode === "dark"
                  ? "rgba(148, 163, 184, 0.22)"
                  : "rgba(100, 116, 139, 0.16)",
              },
              data: [
                [
                  { xAxis: highlightRange.start },
                  { xAxis: highlightRange.end },
                ] as [{ xAxis: string }, { xAxis: string }],
              ],
            }
          : undefined,
        z: 3,
      })),
    };
  }, [events, themeMode, axisMin, axisMax, seriesByType, typeMeta, showLegend, isSelected, enableZoom, zoomStartPct, highlightRange]);

  // Click — resolve the event back for the caller.
  const handleClick = useCallback((params: unknown) => {
    const p = params as { data?: { event?: DateEvent & { _id: number | string; _idx: number } } };
    const e = p.data?.event;
    if (e && onEventClick) {
      onEventClick({ ...e, id: e.id ?? e._idx });
    }
  }, [onEventClick]);

  if (events.length === 0) {
    return (
      <Typography variant="caption" color="text.secondary">
        {emptyText}
      </Typography>
    );
  }

  // The dataZoom slider renders INSIDE the canvas — reserve extra height for
  // it so the marker band keeps its full `height` (news passes a tight 72px).
  const effHeight = enableZoom ? height + 24 : height;

  return (
    <Box>
      <Box sx={{ height: effHeight, position: "relative" }}>
        <EChart option={option} height={effHeight} minHeight={80} onEvents={{ click: handleClick }} />
      </Box>
      {showLegend && (
        <Stack direction="row" spacing={1} sx={{ flexWrap: "wrap", gap: 0.5, mt: 0.5, mb: 1 }}>
          {Array.from(seriesByType.keys()).map((type) => (
            <Chip
              key={type}
              size="small"
              label={typeMeta[type]?.label ?? type}
              sx={{
                fontSize: "0.65rem",
                height: 18,
                bgcolor: typeMeta[type]?.color ?? normalized.find((e) => e._type === type)?._color,
                color: "#fff",
                opacity: 0.9,
              }}
            />
          ))}
          <Typography variant="caption" color="text.secondary" sx={{ fontSize: "0.65rem", alignSelf: "center" }}>
            {events.length} events · {events[0].date} → {events[events.length - 1].date}
          </Typography>
        </Stack>
      )}
    </Box>
  );
}
