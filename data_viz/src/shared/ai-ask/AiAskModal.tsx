/**
 * AI Ask modal — chart intro + question box for the per-chart "ask the AI"
 * feature.
 *
 * Opened by the AiAskButton beside a chart title. Shows what the chart is
 * (intro + series summary), takes the user's question, and on submit:
 *   1. captures the live canvas via the echarts instance's getDataURL
 *      (theme-aware background — the chart canvas itself is transparent),
 *      reflecting the user's CURRENT zoom viewport;
 *   2. POSTs { question, plotInfo, screenshots, onlineSearch, searchQuery }
 *      to /api/ai/ask, with the global filter context (date range / sector /
 *      industry) injected from the zustand store at submit time — the
 *      "online search" tick beside the Ask button routes the ask through
 *      the online-search agent (web search + cited [来源：ref_N] answer,
 *      text-only) instead of the plain chart-adviser completion, and
 *      reveals a search-query line pre-filled with the chart title and the
 *      chart's latest plotted date (the engine searches THAT text, not the
 *      question);
 *      When the plot is date-selectable the modal also shows the as-of
 *      date selector (an exact "YYYY-MM-DD" or a year-month "YYYY-MM"),
 *      pre-filled with the chart's latest plotted date and sent inside
 *      plotInfo (`date`) — the adviser's reference "now" for
 *      time-relative questions;
 *   3. renders the answer.
 *
 * Concise layout: for registered analysis pages (the spec's `insight`)
 * the control-state + series-tags strips are REPLACED by the page's
 * METRICS TABLE — the
 * metrics of particular interest translated from the insight registry
 * jsonb (analysis_insight_metrics.insight_templates), each value resolved
 * server-side from the analysis tables (Δ vs the previous date) or
 * computed on demand when missing, reacting to the ask's instrument
 * (the classification-nav chosen security) and the as-of date (seeded
 * with the code trend's chosen date). Otherwise the series-tags strip
 * shows, capped at two rows with a gradient fade over the third
 * (double-arrow toggle expands all — stacked charts can carry 100+
 * series chips); submitted screenshots shrink to thumbnails that open a
 * zoom lightbox on click; the answer box takes most of the remaining
 * dialog height.
 *
 * The clamped intro is the doorway to the full description view: clicking
 * it (or the "full description" hint under it) replaces the ask UI with a
 * documentation-style page — the intro as separated paragraphs, then the
 * per-series descriptions and the author's notes — with a back arrow in
 * the dialog's upper-left corner returning to the ask view (question /
 * answer state is kept). The page body is the shared DescriptionView
 * (shared/components/description), so it renders exactly what the adviser
 * knows — the same authored text feeds the LLM.
 */
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import type { ECharts } from "echarts";
import {
  Alert,
  Box,
  Button,
  Checkbox,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  IconButton,
  Skeleton,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Typography,
} from "@mui/material";
import ArrowBackIcon from "@mui/icons-material/ArrowBack";
import ChevronRightIcon from "@mui/icons-material/ChevronRight";
import KeyboardDoubleArrowDownIcon from "@mui/icons-material/KeyboardDoubleArrowDown";
import KeyboardDoubleArrowUpIcon from "@mui/icons-material/KeyboardDoubleArrowUp";
import { Close as CloseIcon } from "@mui/icons-material";
import { askChartAi } from "@/lib/api-client/aiAsk";
import { fetchInsightMetrics } from "@/lib/api-client/analysis-insights";
import type { InsightMetricsResponse } from "@/lib/api-client/analysis-insights";
import { DescriptionView } from "@/shared/components/description";
import {
  DateOrMonthField,
  normalizeDateOrMonth,
} from "@/shared/components/date-selector";
// Leaf-module import (not the base-chart barrel): BaseChart imports this
// package, so going through the barrel would close a module cycle.
import { useChartThemeMode } from "@/shared/charts/base-chart/useChartThemeMode";
import type { AiAskInsightMetric, AiAskPlotInfo, AiAskSeriesInfo } from "./types";
import { useStore } from "@/store/filters";

/** Paper colors the chart cards sit on (mui-theme.ts fallbacks) — the chart
 *  canvas itself is transparent, so the export needs an explicit background. */
const SCREENSHOT_BG: Record<"light" | "dark", string> = {
  light: "#FFFFFF",
  dark: "#1A2238",
};

/** Collapsed series-tags strip: two full rows, the third fading out. Chip
 *  rows are ~24px + 4px gap, so 64px shows row 3's top half for the mask. */
const TAGS_COLLAPSED_PX = 64;
const TAGS_MASK = "linear-gradient(180deg, #000 46px, transparent 64px)";

/** Two-line clamp for the chart intro — long intros must not push the
 *  question box below the fold (clicking it opens the description view). */
const CLAMP_2 = {
  display: "-webkit-box",
  WebkitBoxOrient: "vertical",
  WebkitLineClamp: 2,
  overflow: "hidden",
} as const;

function fmtNum(v: number | undefined): string {
  if (v === undefined) return "–";
  return Math.abs(v) >= 1000
    ? v.toLocaleString("en-US", { maximumFractionDigits: 1 })
    : String(Math.round(v * 100) / 100);
}

/** One insight-metric cell — concise: number + unit, "–" when unresolved
 *  (blanket reason goes to the row tooltip). */
function fmtMetricValue(m: AiAskInsightMetric): string {
  if (m.value === null || m.value === undefined) return "–";
  if (typeof m.value === "boolean") return m.value ? "1" : "0";
  if (typeof m.value === "string") return m.value;
  return fmtNum(m.value) + (m.unit && m.unit !== "%" ? ` ${m.unit}` : m.unit === "%" ? "%" : "");
}

/** Δ cell — signed 2dp (pp for %-unit metrics, else raw). */
function fmtMetricDelta(m: AiAskInsightMetric): string {
  if (m.delta === null || m.value === null) return "–";
  const sign = m.delta >= 0 ? "+" : "−";
  return `${sign}${fmtNum(Math.abs(m.delta))}${m.unit === "%" ? "pp" : ""}`;
}

/** Rarity cell — the value's prior-history percentile as pNN (p95 = only
 *  5% of the trailing history sat higher). Tails (≤p5 / ≥p95) are the
 *  rare readings and render highlighted. */
function metricRankCell(m: AiAskInsightMetric): {
  text: string;
  rare: "high" | "low" | null;
} {
  if (m.pct_rank === null) return { text: "–", rare: null };
  const p = Math.round(m.pct_rank * 100);
  const rare = p >= 95 ? "high" : p <= 5 ? "low" : null;
  return { text: `p${p}`, rare };
}

/** Row tooltip suffix for a rarity cell — the cell's one-line contract. */
function metricRankTitle(m: AiAskInsightMetric): string {
  if (m.pct_rank === null) return "";
  return (
    ` — rarity p${Math.round(m.pct_rank * 100)}: ` +
    `${(m.pct_rank * 100).toFixed(0)}% of its prior 10y history sat at or ` +
    `below this value (n=${m.pct_rank_n ?? "–"}); tails p≤5 / p≥95 are rare`
  );
}

/** "12.3 → 15.6 (+26.8%)" — the one number a trader reads first. Percent
 *  series (unit "%", stats already in percent points) instead print the
 *  move as a Δ in percentage points: a ratio between two near-zero
 *  percent changes is meaningless ("−0.12% → −0.07%" is a flat day, not
 *  "−44.5%"). */
function seriesLine(s: AiAskSeriesInfo): string {
  const { name, stats, unit } = s;
  if (!stats || stats.first === undefined || stats.last === undefined) {
    return name;
  }
  if (unit === "%") {
    const move = stats.last - stats.first;
    const sign = move >= 0 ? "+" : "";
    return (
      `${name}: ${stats.first.toFixed(2)}% → ${stats.last.toFixed(2)}% ` +
      `(Δ${sign}${move.toFixed(2)}pp)`
    );
  }
  const pct =
    stats.first !== 0
      ? ` (${((stats.last - stats.first) / stats.first) * 100 >= 0 ? "+" : ""}` +
        `${(((stats.last - stats.first) / stats.first) * 100).toFixed(1)}%)`
      : "";
  return `${name}: ${fmtNum(stats.first)} → ${fmtNum(stats.last)}${pct}`;
}

interface Props {
  open: boolean;
  onClose: () => void;
  plotInfo: AiAskPlotInfo;
  /** Live echarts instance accessor — null/disposed after unmount (the ask
   *  then proceeds without a screenshot). */
  getInstance: () => ECharts | null;
  /** Additional stacked charts in the same card (multi-chart cards) — each
   *  live instance contributes one screenshot. */
  getExtraInstances?: () => (ECharts | null)[];
}

/** Screenshots are for the LLM's visual reasoning — enough to cover a
 *  multi-chart card without bloating the payload. */
const MAX_SCREENSHOTS = 4;

export default function AiAskModal({
  open,
  onClose,
  plotInfo,
  getInstance,
  getExtraInstances,
}: Props) {
  const themeMode = useChartThemeMode();
  const pathname = useLocation().pathname;
  const [question, setQuestion] = useState("");
  const [onlineSearch, setOnlineSearch] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [answer, setAnswer] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [screenshots, setScreenshots] = useState<string[]>([]);
  const [zoomed, setZoomed] = useState<string | null>(null);
  const [tagsExpanded, setTagsExpanded] = useState(false);
  const [tagsOverflow, setTagsOverflow] = useState(false);
  // Description view (clicked intro) vs ask view — ask-side state below is
  // kept alive across the round trip so going back resumes where it left.
  const [showDescription, setShowDescription] = useState(false);
  // Callback-ref + state (not a plain ref): the strip mounts with the open
  // dialog, possibly AFTER the open-flip effect ran, so the element itself
  // must re-trigger the measurement when it appears.
  const [tagsEl, setTagsEl] = useState<HTMLDivElement | null>(null);
  const answerRef = useRef<HTMLDivElement | null>(null);

  // As-of date (the modal's date selector): pins time-relative questions
  // ("now", "latest", …) to a point in time the adviser treats as now —
  // an exact "YYYY-MM-DD" or a year-month "YYYY-MM". Shown only when the
  // plot is date-selectable; seeded from the chart window on open (an
  // empty field means no date pinned), cleared on close so every fresh
  // open re-seeds from the CURRENT plotted window.
  const [asOfDate, setAsOfDate] = useState("");
  const [asOfTouched, setAsOfTouched] = useState(false);
  const dateSelectable = plotInfo.dateSelectable === true;
  const asOfTrim = asOfDate.trim();
  const asOfNorm = asOfTrim ? normalizeDateOrMonth(asOfTrim) : undefined;
  const asOfValid = asOfTrim === "" || asOfNorm !== undefined;

  // Overflow check + reset whenever the plot info changes or the dialog
  // opens (MUI mounts the dialog content only when open). A one-shot
  // layout-effect read races the dialog's portal mount/transition and
  // measured ~0, leaving the expand toggle hidden even when the tags
  // clearly overflow — so re-measure after layout settles (rAF) and again
  // whenever the strip's box changes (fonts, transition, data refresh).
  useLayoutEffect(() => {
    if (!open || !tagsEl) return;
    setTagsExpanded(false);
    const measure = () => setTagsOverflow(tagsEl.scrollHeight > TAGS_COLLAPSED_PX + 2);
    measure();
    const raf = requestAnimationFrame(measure);
    const ro = new ResizeObserver(measure);
    ro.observe(tagsEl);
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
    };
  }, [plotInfo, open, tagsEl]);

  // Seed the as-of date whenever the dialog opens: the chart's own
  // chosen/anchor date (the code trend click, roof/floor anchor) leads,
  // then the chart's latest plotted date, then the global filter range.
  // Only fills an empty field — user edits are never clobbered (close()
  // clears so each open re-seeds).
  useEffect(() => {
    if (!open || !dateSelectable) return;
    setAsOfDate((prev) => {
      if (prev.trim()) return prev;
      const s = useStore.getState();
      return (
        normalizeDateOrMonth(plotInfo.asOfDate) ??
        normalizeDateOrMonth(plotInfo.window.end) ??
        normalizeDateOrMonth(plotInfo.window.start) ??
        normalizeDateOrMonth(s.endDate) ??
        normalizeDateOrMonth(s.startDate) ??
        ""
      );
    });
  }, [open, dateSelectable, plotInfo]);

  // ---- insight metrics table (registered analysis pages) ----
  // The page's metrics of particular interest (analysis_insight_metrics.
  // insight_templates, translated from the registry jsonb) with values
  // resolved server-side — from the analysis tables, or on-demand
  // computed when missing — for the CURRENT instrument (the
  // classification-nav chosen security) and the as-of date chosen above
  // (the code trend's chosen date seeds it). Refetches whenever any of
  // those move; a failed fetch falls back to the series-tags strip.
  const insightTab = plotInfo.insight?.tab;
  const insightPage = plotInfo.insight?.page;
  const instrument = plotInfo.scope.instruments[0];
  const insightCode = instrument?.code ?? null;
  const insightSecType = instrument?.assetClass ?? null;
  const industrySlug = useStore((s) => s.industrySlug);
  const [insightMetrics, setInsightMetrics] =
    useState<InsightMetricsResponse | null>(null);
  const [insightFailed, setInsightFailed] = useState(false);
  useEffect(() => {
    if (!open || !insightTab || !insightPage) {
      setInsightMetrics(null);
      setInsightFailed(false);
      return;
    }
    let cancelled = false;
    setInsightMetrics(null);
    setInsightFailed(false);
    fetchInsightMetrics({
      tab: insightTab,
      page: insightPage,
      code: insightCode ?? undefined,
      secType: insightSecType ?? undefined,
      industry: industrySlug ?? undefined,
      date: asOfNorm ?? undefined,
    })
      .then((r) => {
        if (!cancelled) setInsightMetrics(r);
      })
      .catch(() => {
        if (!cancelled) setInsightFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [open, insightTab, insightPage, insightCode, insightSecType, industrySlug, asOfNorm]);

  const hasInsight = Boolean(insightTab && insightPage);
  // loading while fetching (skeleton table); loaded-but-empty (unknown
  // page) or failed fetch → fall back to the series-tags strip
  const insightLoading = hasInsight && !insightFailed && insightMetrics === null;
  const insightKnown =
    hasInsight && !insightFailed && (insightMetrics?.metrics.length ?? 0) > 0;
  const showInsightTable = insightLoading || insightKnown;
  const anyComputed =
    insightKnown && insightMetrics.metrics.some((m) => m.computed);

  const close = () => {
    if (submitting) return; // one ask at a time — don't orphan an in-flight POST
    setAnswer(null);
    setError(null);
    setScreenshots([]);
    setZoomed(null);
    setShowDescription(false);
    setAsOfDate("");
    setAsOfTouched(false);
    // the modal component outlives its Dialog — reset the tick so every
    // fresh open starts with online search OFF (the default), and the
    // search line so it re-seeds from the current chart on the next tick.
    setOnlineSearch(false);
    setSearchQuery("");
    onClose();
  };

  /** Seed the web-search line the first time the tick turns on: chart
   *  title + items-of-interest keywords (index/instrument names and the
   *  active indicator tags — RSI, Bollinger, …, auto-derived or chart-
   *  author-supplied) + the latest plotted date. Search engines want the
   *  topic + recency, not the full question. The user's chosen as-of date
   *  (when pinned and valid) leads; the chart window and the store's date
   *  range follow; user edits are never clobbered. */
  const seedSearchQuery = () => {
    setSearchQuery((prev) => {
      if (prev.trim()) return prev;
      const s = useStore.getState();
      const date =
        (asOfTrim && asOfNorm ? asOfNorm : undefined) ??
        plotInfo.window.end ??
        s.endDate ??
        undefined;
      return (
        [plotInfo.chart.title, ...(plotInfo.searchKeywords ?? []), date]
          .filter(Boolean)
          .join(" ")
          .trim() || prev
      );
    });
  };

  const captureScreenshots = (): string[] => {
    const instances = [getInstance(), ...(getExtraInstances?.() ?? [])];
    const out: string[] = [];
    for (const inst of instances) {
      if (out.length >= MAX_SCREENSHOTS) break;
      try {
        if (!inst || inst.isDisposed()) continue;
        out.push(
          inst.getDataURL({
            type: "png",
            pixelRatio: 2,
            backgroundColor: SCREENSHOT_BG[themeMode],
          }),
        );
      } catch {
        // disposed mid-capture — skip this chart, keep the rest
      }
    }
    return out;
  };

  const submit = async () => {
    if (!question.trim() || submitting) return;
    if (!asOfValid) {
      setAsOfTouched(true);
      setError("As-of date must be YYYY-MM-DD or YYYY-MM.");
      return;
    }
    setSubmitting(true);
    setError(null);
    setAnswer(null);
    try {
      const shots = captureScreenshots();
      setScreenshots(shots);

      // Global filter context — read at submit time (handler-scope getState
      // is the reactive-correct pattern here, matching the theme rule). The
      // page path rides along as the ask-history product fallback (persisted
      // with the ask — see llm_agents/llm_ask/storage.py).
      const s = useStore.getState();
      const payloadPlotInfo: AiAskPlotInfo = {
        ...plotInfo,
        scope: {
          ...plotInfo.scope,
          industry: plotInfo.scope.industry ?? s.industrySlug,
          sector: plotInfo.scope.sector ?? s.sectorId,
        },
        window: {
          ...plotInfo.window,
          start: plotInfo.window.start ?? s.startDate ?? undefined,
          end: plotInfo.window.end ?? s.endDate ?? undefined,
        },
        // Pinned as-of date (the selector above) — the adviser treats it
        // as "now" for time-relative questions; undefined drops the key.
        date: asOfTrim && asOfNorm ? asOfNorm : undefined,
        page: plotInfo.page ?? pathname,
        // The insight metrics table's resolved values (the page's metrics
        // of particular interest for the chosen security/date) — rides
        // along so the adviser answers with the actual curve values.
        ...(plotInfo.insight && insightKnown
          ? {
              insight: {
                ...plotInfo.insight,
                metrics: insightMetrics.metrics,
              },
            }
          : {}),
      };

      const res = await askChartAi({
        question: question.trim(),
        plotInfo: payloadPlotInfo,
        screenshots: shots,
        themeMode,
        onlineSearch,
        // only meaningful on the online-search path — the plain adviser
        // never sees it; an empty line falls back to the question server-side.
        searchQuery: onlineSearch ? searchQuery.trim() || undefined : undefined,
      });
      if (!res.success) {
        setError(res.error || "The adviser returned no answer.");
      } else {
        setAnswer(res.answer);
        requestAnimationFrame(() =>
          answerRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" }),
        );
      }
    } catch (e) {
      setError(
        e instanceof DOMException && e.name === "AbortError"
          ? "AI ask timed out — the adviser may be busy; try again."
          : e instanceof Error
            ? e.message
            : String(e),
      );
    } finally {
      setSubmitting(false);
    }
  };

  const seriesCount = plotInfo.series.length;

  return (
    <>
      <Dialog open={open} onClose={close} maxWidth="lg" fullWidth>
        <DialogTitle
          sx={{
            position: "relative",
            fontSize: "1rem",
            fontWeight: 600,
            pr: 6, // clear of the close X (and back arrow sits at the left)
          }}
        >
          {showDescription && (
            <IconButton
              aria-label="back to ask"
              onClick={() => setShowDescription(false)}
              disabled={submitting}
              title="Back to Ask AI"
              sx={{ position: "absolute", left: 8, top: 8, color: "var(--chart-subtitle)" }}
            >
              <ArrowBackIcon />
            </IconButton>
          )}
          <Box component="span" sx={showDescription ? { pl: 5 } : undefined}>
            {showDescription ? "Description" : "Ask AI"} · {plotInfo.chart.title || "Chart"}
          </Box>
          <IconButton
            aria-label="close"
            onClick={close}
            disabled={submitting}
            title="Close"
            sx={{ position: "absolute", right: 8, top: 8, color: "var(--chart-subtitle)" }}
          >
            <CloseIcon />
          </IconButton>
        </DialogTitle>
        <DialogContent dividers sx={{ display: "flex", flexDirection: "column", gap: 1, pb: 1.5 }}>
          {showDescription ? (
            /* ---- description view — the chart's full authored description,
               replaces the ask UI until the upper-left back arrow ---- */
            <DescriptionView
              intro={plotInfo.chart.intro}
              series={plotInfo.series}
              notes={plotInfo.notes}
            />
          ) : (
            /* ---- ask view ---- */
            <>
              {/* chart context — intro + current view, compact; the intro
                  itself is the doorway to the full description view */}
              {plotInfo.chart.intro && (
                <Box>
                  <Typography
                    variant="body2"
                    onClick={() => setShowDescription(true)}
                    title="Click to read the full description"
                    sx={{
                      color: "var(--chart-subtitle)",
                      cursor: "pointer",
                      ...CLAMP_2,
                      "&:hover": { color: "primary.main" },
                    }}
                  >
                    {plotInfo.chart.intro}
                  </Typography>
                  <Typography
                    variant="caption"
                    onClick={() => setShowDescription(true)}
                    sx={{
                      color: "var(--chart-subtitle)",
                      cursor: "pointer",
                      display: "inline-flex",
                      alignItems: "center",
                      userSelect: "none",
                      "&:hover": { color: "primary.main", textDecoration: "underline" },
                    }}
                  >
                    Full description
                    <ChevronRightIcon sx={{ fontSize: "0.9rem" }} />
                  </Typography>
                </Box>
              )}

          {/* control-state tags — non-registry charts only: on a registered
              analysis page the insight metrics table below IS the current
              view's readout, so these k:v chips would duplicate it */}
          {!showInsightTable &&
            plotInfo.state && Object.keys(plotInfo.state).length > 0 && (
            <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.5, alignItems: "center" }}>
              {Object.entries(plotInfo.state).map(([k, v]) => (
                <Typography
                  key={k}
                  variant="caption"
                  sx={{
                    color: "var(--chart-subtitle)",
                    border: "1px solid var(--chart-subtitle)",
                    borderRadius: 1,
                    px: 0.75,
                    py: 0.25,
                  }}
                >
                  {k}: {String(v)}
                </Typography>
              ))}
            </Box>
          )}

          {/* insight metrics table — for registered analysis pages this
              REPLACES the tags strips: the page's metrics of
              particular interest (translated from the registry jsonb),
              values from the analysis tables (Δ vs the previous date) or
              computed on demand ("*"). Non-registry charts (and a failed
              fetch) keep the state + series tags strips instead. */}
          {showInsightTable && (
            <TableContainer
              sx={{
                maxHeight: 220,
                borderRadius: 1,
                border: "1px solid",
                borderColor: "divider",
              }}
            >
              <Table size="small" stickyHeader>
                <TableHead>
                  <TableRow>
                    {["Metric", "Value", "Δ", "Rarity", "As of"].map((h, i) => (
                      <TableCell
                        key={h}
                        align={i === 0 ? "left" : "right"}
                        sx={{
                          color: "var(--chart-subtitle)",
                          fontWeight: 600,
                          fontSize: "0.7rem",
                          py: 0.5,
                          whiteSpace: "nowrap",
                        }}
                      >
                        {h}
                      </TableCell>
                    ))}
                  </TableRow>
                </TableHead>
                <TableBody>
                  {insightLoading ? (
                    [0, 1, 2, 3, 4].map((i) => (
                      <TableRow key={i}>
                        {[0, 1, 2, 3, 4].map((c) => (
                          <TableCell key={c} sx={{ py: 0.5 }}>
                            <Skeleton height={14} />
                          </TableCell>
                        ))}
                      </TableRow>
                    ))
                  ) : (
                    insightMetrics.metrics.map((m) => {
                      const rank = metricRankCell(m);
                      return (
                      <TableRow
                        key={m.template_id}
                        title={
                          m.description +
                          metricRankTitle(m) +
                          (m.unresolved ? ` — ${m.unresolved}` : "")
                        }
                      >
                        <TableCell
                          sx={{
                            py: 0.25,
                            fontSize: "0.75rem",
                            whiteSpace: "nowrap",
                          }}
                        >
                          {m.label}
                          {m.computed && (
                            <Typography
                              component="sup"
                              sx={{ fontSize: "0.6rem", color: "var(--chart-subtitle)" }}
                            >
                              *
                            </Typography>
                          )}
                        </TableCell>
                        <TableCell
                          align="right"
                          sx={{
                            py: 0.25,
                            fontSize: "0.75rem",
                            fontWeight: 600,
                            whiteSpace: "nowrap",
                          }}
                        >
                          {fmtMetricValue(m)}
                        </TableCell>
                        <TableCell
                          align="right"
                          sx={{
                            py: 0.25,
                            fontSize: "0.75rem",
                            whiteSpace: "nowrap",
                            color:
                              m.delta === null || m.delta === 0
                                ? "var(--chart-subtitle)"
                                : m.delta > 0
                                  ? "success.main"
                                  : "error.main",
                          }}
                        >
                          {fmtMetricDelta(m)}
                        </TableCell>
                        <TableCell
                          align="right"
                          sx={{
                            py: 0.25,
                            fontSize: "0.7rem",
                            whiteSpace: "nowrap",
                            color:
                              rank.rare === "high"
                                ? "error.main"
                                : rank.rare === "low"
                                  ? "info.main"
                                  : "var(--chart-subtitle)",
                            fontWeight: rank.rare ? 700 : 400,
                          }}
                        >
                          {rank.text}
                        </TableCell>
                        <TableCell
                          align="right"
                          sx={{
                            py: 0.25,
                            fontSize: "0.7rem",
                            color: "var(--chart-subtitle)",
                            whiteSpace: "nowrap",
                          }}
                        >
                          {m.value_date ?? "–"}
                        </TableCell>
                      </TableRow>
                      );
                    })
                  )}
                </TableBody>
              </Table>
            </TableContainer>
          )}
          {showInsightTable && anyComputed && (
            <Typography
              variant="caption"
              sx={{ color: "var(--chart-subtitle)", mt: -0.5 }}
            >
              * computed on demand for the chosen security/date
            </Typography>
          )}

          {/* series tags — two rows + gradient fade, double-arrow expands
              (the fallback / non-registry charts' key-metrics strip) */}
          {!showInsightTable && seriesCount > 0 && (
            <Box>
              <Box
                ref={setTagsEl}
                sx={{
                  display: "flex",
                  flexWrap: "wrap",
                  gap: 0.5,
                  maxHeight: tagsExpanded ? "none" : `${TAGS_COLLAPSED_PX}px`,
                  overflow: "hidden",
                  maskImage: tagsExpanded || !tagsOverflow ? "none" : TAGS_MASK,
                  WebkitMaskImage: tagsExpanded || !tagsOverflow ? "none" : TAGS_MASK,
                }}
              >
                {plotInfo.series.map((s, i) => (
                  <Typography
                    key={`${s.name}-${i}`}
                    variant="caption"
                    sx={{
                      color: "var(--chart-subtitle)",
                      border: "1px solid var(--chart-subtitle)",
                      borderRadius: 1,
                      px: 0.75,
                      py: 0.25,
                    }}
                  >
                    {seriesLine(s)}
                    {s.unit && s.unit !== "%" ? ` ${s.unit}` : ""}
                  </Typography>
                ))}
              </Box>
              {tagsOverflow && (
                <IconButton
                  size="small"
                  onClick={() => setTagsExpanded((v) => !v)}
                  title={tagsExpanded ? "Collapse series list" : `Show all ${seriesCount} series`}
                  sx={{ mt: -0.5, mx: "auto", display: "flex", color: "var(--chart-subtitle)" }}
                >
                  {tagsExpanded ? (
                    <KeyboardDoubleArrowUpIcon sx={{ fontSize: "1.1rem" }} />
                  ) : (
                    <KeyboardDoubleArrowDownIcon sx={{ fontSize: "1.1rem" }} />
                  )}
                </IconButton>
              )}
            </Box>
          )}

          {/* as-of date — shown for date-selectable plots and pre-filled
              with the chart's latest plotted date; the adviser treats it
              as the reference "now" for time-relative questions. Accepts
              an exact date or a year-month; clear to ask unpinned. */}
          {dateSelectable && (
            <DateOrMonthField
              label="As-of date"
              value={asOfDate}
              onChange={(v) => {
                setAsOfDate(v);
                setAsOfTouched(false);
              }}
              error={asOfTouched && !asOfValid}
              helperText={
                asOfTouched && !asOfValid ? "Use YYYY-MM-DD or YYYY-MM" : undefined
              }
              disabled={submitting}
              tooltip="Reference date for the answer — exact (YYYY-MM-DD) or year-month (YYYY-MM); pre-filled with the chart's latest plotted date."
            />
          )}

          <TextField
            label="Ask a question about this chart"
            placeholder="Type your question…"
            multiline
            minRows={2}
            maxRows={4}
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) void submit();
            }}
            fullWidth
            size="small"
          />

          {/* web-search line — revealed by the online-search tick; the
              engine searches this text (seeded title + date), the LLM still
              answers the question above */}
          {onlineSearch && (
            <TextField
              label="Web search query"
              placeholder="Chart title + indicator tags + date"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              disabled={submitting}
              fullWidth
              size="small"
              helperText="What the web search looks up — your question stays the question."
            />
          )}

          {screenshots.length > 0 && (
            <Box sx={{ display: "flex", flexWrap: "wrap", gap: 1 }}>
              {screenshots.map((src, i) => (
                <Box
                  key={i}
                  component="img"
                  src={src}
                  alt={`Chart snapshot ${i + 1} — click to zoom`}
                  title="Click to zoom"
                  onClick={() => setZoomed(src)}
                  sx={{
                    height: 72,
                    width: "auto",
                    borderRadius: 1,
                    border: "1px solid",
                    borderColor: "divider",
                    cursor: "zoom-in",
                    display: "block",
                    "&:hover": { borderColor: "primary.main" },
                  }}
                />
              ))}
            </Box>
          )}

          {error && <Alert severity="error" sx={{ py: 0.5 }}>{error}</Alert>}

          {answer && (
            <Box
              ref={answerRef}
              sx={{
                whiteSpace: "pre-wrap",
                bgcolor: "action.hover",
                borderRadius: 1,
                p: 1.5,
                maxHeight: "60vh",
                overflowY: "auto",
              }}
            >
              <Typography variant="body2">{answer}</Typography>
            </Box>
          )}
            </>
          )}
        </DialogContent>
        {!showDescription && (
          <DialogActions>
          <Button onClick={close} disabled={submitting}>
            Close
          </Button>
          {/* the online-search tick — routes the ask through the
              online-search agent (web search + cited answer, text-only)
              and reveals the pre-filled web-search line above */}
          <FormControlLabel
            disabled={submitting}
            checked={onlineSearch}
            onChange={(_, v) => {
              setOnlineSearch(v);
              if (v) seedSearchQuery();
            }}
            control={<Checkbox size="small" sx={{ p: 0.5 }} />}
            label="online search"
            slotProps={{
              typography: { variant: "caption", sx: { color: "var(--chart-subtitle)" } },
            }}
            sx={{ userSelect: "none" }}
          />
          <Button
            onClick={() => void submit()}
            variant="contained"
            size="small"
            disabled={!question.trim() || submitting}
            endIcon={submitting ? <CircularProgress size={14} color="inherit" /> : null}
          >
            {submitting ? "Asking…" : "Ask"}
          </Button>
          </DialogActions>
        )}
      </Dialog>

      {/* screenshot zoom lightbox — click the image or backdrop to close */}
      <Dialog
        open={zoomed !== null}
        onClose={() => setZoomed(null)}
        maxWidth={false}
        PaperProps={{ sx: { bgcolor: "transparent", boxShadow: "none" } }}
      >
        {zoomed !== null && (
          <Box
            component="img"
            src={zoomed}
            alt="Chart snapshot (zoomed)"
            onClick={() => setZoomed(null)}
            sx={{
              display: "block",
              maxWidth: "94vw",
              maxHeight: "88vh",
              borderRadius: 1,
              cursor: "zoom-out",
            }}
          />
        )}
      </Dialog>
    </>
  );
}
