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
 *   3. renders the answer.
 *
 * Concise layout: the series-tags strip is capped at two rows with a
 * gradient fade over the third (double-arrow toggle expands all — stacked
 * charts can carry 100+ series chips); submitted screenshots shrink to
 * thumbnails that open a zoom lightbox on click; the answer box takes most
 * of the remaining dialog height.
 */
import { useLayoutEffect, useRef, useState } from "react";
import type { ECharts } from "echarts";
import {
  Alert,
  Box,
  Button,
  Checkbox,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  IconButton,
  TextField,
  Typography,
} from "@mui/material";
import KeyboardDoubleArrowDownIcon from "@mui/icons-material/KeyboardDoubleArrowDown";
import KeyboardDoubleArrowUpIcon from "@mui/icons-material/KeyboardDoubleArrowUp";
import { askChartAi } from "@/lib/api-client/aiAsk";
// Leaf-module import (not the base-chart barrel): BaseChart imports this
// package, so going through the barrel would close a module cycle.
import { useChartThemeMode } from "@/shared/charts/base-chart/useChartThemeMode";
import type { AiAskPlotInfo } from "./types";
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
 *  question box below the fold (full text stays in the title tooltip). */
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

/** "12.3 → 15.6 (+26.8%)" — the one number a trader reads first. */
function seriesLine(
  name: string,
  stats: { first?: number; last?: number; min?: number; max?: number } | undefined,
): string {
  if (!stats || stats.first === undefined || stats.last === undefined) {
    return name;
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
  const tagsRef = useRef<HTMLDivElement | null>(null);
  const answerRef = useRef<HTMLDivElement | null>(null);

  // Overflow check + reset whenever the plot info changes or the dialog
  // opens (MUI mounts the dialog content only when open — the ref is null
  // while closed, so the open flip must re-trigger the measurement).
  useLayoutEffect(() => {
    setTagsExpanded(false);
    const el = tagsRef.current;
    setTagsOverflow(!!el && el.scrollHeight > TAGS_COLLAPSED_PX + 2);
  }, [plotInfo, open]);

  const close = () => {
    if (submitting) return; // one ask at a time — don't orphan an in-flight POST
    setAnswer(null);
    setError(null);
    setScreenshots([]);
    setZoomed(null);
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
   *  topic + recency, not the full question. Store endDate covers charts
   *  whose x-axis window wasn't derived; user edits are never clobbered. */
  const seedSearchQuery = () => {
    setSearchQuery((prev) => {
      if (prev.trim()) return prev;
      const s = useStore.getState();
      const date = plotInfo.window.end ?? s.endDate ?? undefined;
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
    setSubmitting(true);
    setError(null);
    setAnswer(null);
    try {
      const shots = captureScreenshots();
      setScreenshots(shots);

      // Global filter context — read at submit time (handler-scope getState
      // is the reactive-correct pattern here, matching the theme rule).
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
      <Dialog open={open} onClose={close} maxWidth="md" fullWidth>
        <DialogTitle sx={{ fontSize: "1rem", fontWeight: 600 }}>
          Ask AI · {plotInfo.chart.title || "Chart"}
        </DialogTitle>
        <DialogContent dividers sx={{ display: "flex", flexDirection: "column", gap: 1, pb: 1.5 }}>
          {/* chart context — intro + current view, compact */}
          <Typography
            variant="body2"
            title={plotInfo.chart.intro}
            sx={{ color: "var(--chart-subtitle)", ...CLAMP_2 }}
          >
            {plotInfo.chart.intro}
          </Typography>

          {plotInfo.state && Object.keys(plotInfo.state).length > 0 && (
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

          {/* series tags — two rows + gradient fade, double-arrow expands */}
          {seriesCount > 0 && (
            <Box>
              <Box
                ref={tagsRef}
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
                    {seriesLine(s.name, s.stats)}
                    {s.unit ? ` ${s.unit}` : ""}
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

          {(plotInfo.suggestedQuestions ?? []).length > 0 && (
            <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.5, alignItems: "center" }}>
              {(plotInfo.suggestedQuestions ?? []).slice(0, 3).map((q) => (
                <Chip
                  key={q}
                  label={q}
                  size="small"
                  variant="outlined"
                  onClick={() => setQuestion(q)}
                  sx={{
                    maxWidth: "100%",
                    "& .MuiChip-label": {
                      whiteSpace: "normal",
                      fontSize: "0.72rem",
                    },
                  }}
                />
              ))}
            </Box>
          )}

          <TextField
            label="Ask a question about this chart"
            placeholder={plotInfo.suggestedQuestions?.[0] ?? "e.g. What does the current level imply for entry?"}
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
        </DialogContent>
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
