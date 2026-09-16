/**
 * SentimentFeedSection — the feed half of the Market Sentiments by AI and
 * News page: heading + AI/News source toggle, the date-event strip, the
 * keyword search row, and the paginated feed of AiQaCard (AI) / NewsPostCard
 * (News) posts. All state lives in useSentimentFeed (passed in whole); the
 * page only contributes the scope label, the market rankings' loading flag,
 * and the industry-mode benchmark-chart focus jump on strip picks.
 */
import {
  Box,
  CircularProgress,
  IconButton,
  InputAdornment,
  Pagination,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import {
  Close as CloseIcon,
  Search as SearchIcon,
} from "@mui/icons-material";
import DateEventStrip from "@/shared/components/date-events/DateEventStrip";
import AiQaCard from "@/dataviz/features/ai/AiQaCard";
import NewsPostCard from "@/shared/components/news/NewsPostCard";
import type { useSentimentFeed } from "./useSentimentFeed";

export default function SentimentFeedSection({
  mode,
  hdLoading,
  scopeLabel,
  feedState,
  onStripFocusJump,
}: {
  mode: "market" | "industry";
  /** Market-mode rankings loading — shows in the strip's empty text. */
  hdLoading: boolean;
  /** Resolved feed scope label (mode-dependent — built in the page). */
  scopeLabel: string;
  feedState: ReturnType<typeof useSentimentFeed>;
  /** A strip-dot pick became ACTIVE — industry mode jumps the benchmark
   *  chart's slider to the picked date (the chart↔strip sync). */
  onStripFocusJump: (date: string) => void;
}) {
  const {
    feed,
    setFeed,
    searchInput,
    setSearchInput,
    search,
    submitSearch,
    clearSearch,
    selectedDate,
    setSelectedDate,
    dateWindow,
    page,
    setPage,
    activeCalendarLoading,
    activeCalendarError,
    stripEvents,
    stripMin,
    stripMax,
    activeItems,
    activeItemsLoading,
    activeItemsError,
    aiItems,
    newsItems,
    totalPages,
  } = feedState;

  return (
    <>
      {/* ---- Feed heading + the AI / News source toggle. The toggle
              switches the source — LLM Q&A vs raw news — under the SAME
              scope, day pick, and keyword search. ---- */}
      <Box sx={{ display: "flex", alignItems: "center", gap: 1.5, mt: 2.5, mb: 1 }}>
        <Typography variant="h6" sx={{ fontWeight: 700 }}>
          {feed === "ai" ? "AI 市场情绪问答" : "市场情绪新闻"}
        </Typography>
        <ToggleButtonGroup
          value={feed}
          exclusive
          size="small"
          onChange={(_, v: "ai" | "news" | null) => {
            if (v) setFeed(v);
          }}
          sx={{ ml: "auto" }}
        >
          <ToggleButton value="ai">AI</ToggleButton>
          <ToggleButton value="news">News</ToggleButton>
        </ToggleButtonGroup>
      </Box>

      {/* Date event bar — one dot per day with rows for the ACTIVE source
          (AI Q&A or news), scoped by the mode's feed scope + keyword; click
          windows the feed below to ±120 days around that day AND (industry
          mode) jumps the benchmark chart's slider to that date. Dates are
          TRADING days (holidays/weekends rolled back by the backend). In
          industry mode the strip has NO slider of its own — its VISIBLE
          window is the benchmark chart's visible window (same
          start/end/length; drag the chart's slider and the dots re-window
          in lockstep), the same trend-driven strip as the DataViz AI page.
          In market mode there is no benchmark chart, so the strip falls
          back to its own calendar range + time slider. ---- */}
      <DateEventStrip
        events={stripEvents}
        typeMeta={{
          qa: { label: "AI 问答", color: "#9a60b4" },
          news: { label: "新闻", color: "#5470c6" },
        }}
        minDate={stripMin}
        maxDate={stripMax}
        selectedId={selectedDate}
        onEventClick={(e) => {
          const d = String(e.id);
          setSelectedDate((cur) => (d === cur ? null : d));
          if (mode === "industry") onStripFocusJump(d);
        }}
        height={72}
        showLegend={false}
        emptyText={
          hdLoading || activeCalendarLoading
            ? "加载中…"
            : feed === "ai"
              ? "无 AI 问答记录"
              : "无新闻记录"
        }
        enableZoom={mode === "market"}
      />
      {activeCalendarError && (
        <Box component="pre" sx={{ color: "error.main", fontSize: "0.75rem", whiteSpace: "pre-wrap" }}>
          Failed to load calendar: {activeCalendarError}
        </Box>
      )}

      {/* ---- Keyword row — narrows the date strip and the active feed. ---- */}
      <Stack direction="row" spacing={0.5} sx={{ flexWrap: "wrap", gap: 0.5, my: 1, alignItems: "center" }}>
        <TextField
          size="small"
          placeholder={feed === "ai" ? "搜索问答（Enter）" : "搜索新闻（Enter）"}
          value={searchInput}
          onChange={(e) => setSearchInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") submitSearch();
          }}
          InputProps={{
            sx: { fontSize: "0.75rem", height: 26 },
            startAdornment: (
              <InputAdornment position="start">
                <SearchIcon sx={{ fontSize: 15, color: "text.secondary" }} />
              </InputAdornment>
            ),
            endAdornment: search ? (
              <InputAdornment position="end">
                <IconButton
                  size="small"
                  aria-label="清除搜索"
                  onClick={clearSearch}
                  sx={{ p: 0.25 }}
                >
                  <CloseIcon sx={{ fontSize: 13 }} />
                </IconButton>
              </InputAdornment>
            ) : undefined,
          }}
          sx={{ ml: "auto", width: 220, "& .MuiOutlinedInput-root": { borderRadius: 5 } }}
        />
      </Stack>

      {/* ---- Feed header: scope label · day window · keyword · total + pager ---- */}
      <Box sx={{ display: "flex", alignItems: "center", mb: 1 }}>
        <Typography variant="subtitle2" color="text.secondary">
          {scopeLabel}
          {dateWindow ? ` · ${dateWindow.from} ~ ${dateWindow.to}` : ""}
          {search ? ` · “${search}”` : ""}
          {" · "}
          {(activeItems?.total ?? 0).toLocaleString()} {feed === "ai" ? "条问答" : "条新闻"}
          {(activeItemsLoading || hdLoading) && (
            <CircularProgress size={12} sx={{ ml: 1, verticalAlign: "middle" }} />
          )}
        </Typography>
        {totalPages > 1 && (
          <Pagination
            count={totalPages}
            page={page}
            onChange={(_, v) => setPage(v)}
            size="small"
            siblingCount={1}
            boundaryCount={1}
            sx={{ ml: "auto" }}
          />
        )}
      </Box>
      {activeItemsError && (
        <Box component="pre" sx={{ color: "error.main", fontSize: "0.75rem", whiteSpace: "pre-wrap", mb: 1 }}>
          {feed === "ai" ? "Failed to load Q&A: " : "Failed to load news: "}
          {activeItemsError}
        </Box>
      )}
      <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
        {feed === "ai"
          ? (aiItems ?? []).map((it) => <AiQaCard key={it.qa_id} item={it} />)
          : (newsItems ?? []).map((it) => <NewsPostCard key={it.news_id} item={it} />)}
        {!activeItemsLoading && (activeItems?.items ?? []).length === 0 && (
          <Typography variant="body2" color="text.secondary" sx={{ py: 4, textAlign: "center" }}>
            {feed === "ai" ? "没有匹配的 AI 问答 — " : "没有匹配的新闻 — "}
            {mode === "market"
              ? "调整 Top N / 日期 / 关键词后再试。"
              : "调整行业 / 日期 / 关键词后再试。"}
          </Typography>
        )}
      </Box>
    </>
  );
}
