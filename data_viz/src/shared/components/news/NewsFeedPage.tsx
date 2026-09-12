/**
 * NewsFeedPage — social-media-style news feed (shared component).
 *
 * A drop-in replacement for the old NewsList: same props contract (one page
 * of items + pagination + loading/error/scope label), but each item renders
 * as a post card (NewsPostCard) — click to expand the full article body and
 * open the threaded comments (CommentThread), which expand further per root.
 *
 * Lives in shared/components so any page can embed a news feed, not just the
 * dataviz News page.
 */
import { Alert, Box, CircularProgress, Pagination, Typography } from "@mui/material";
import type { NewsItem } from "@shared/types";
import NewsPostCard from "./NewsPostCard";

/** Server-side page size — also imported by NewsPage for the offset math. */
export const PAGE_SIZE = 50;

interface Props {
  items: NewsItem[];
  total: number;
  page: number; // 1-based
  onPageChange: (page: number) => void;
  loading: boolean;
  error: string | null;
  /** Reflects the active co-filters in the count line when set. */
  scopeLabel: string;
  /** Empty-state hint (defaults to the corpus-browsing text). */
  emptyText?: string;
}

export default function NewsFeedPage({
  items,
  total,
  page,
  onPageChange,
  loading,
  error,
  scopeLabel,
  emptyText = "没有匹配的新闻 — 调整行业 / 日期 / 关键词后再试。",
}: Props) {
  if (error) {
    return <Alert severity="error" variant="filled" sx={{ mb: 2 }}>Failed to load news: {error}</Alert>;
  }
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <Box>
      <Box sx={{ display: "flex", alignItems: "center", mb: 1 }}>
        <Typography variant="subtitle2" color="text.secondary">
          {scopeLabel} · {total.toLocaleString()} 篇
          {loading && (
            <CircularProgress size={12} sx={{ ml: 1, verticalAlign: "middle" }} />
          )}
        </Typography>
        {totalPages > 1 && (
          <Pagination
            count={totalPages}
            page={page}
            onChange={(_, v) => onPageChange(v)}
            size="small"
            siblingCount={1}
            boundaryCount={1}
            sx={{ ml: "auto" }}
          />
        )}
      </Box>

      <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
        {items.map((it) => (
          <NewsPostCard key={it.news_id} item={it} />
        ))}
        {!loading && items.length === 0 && (
          <Typography variant="body2" color="text.secondary" sx={{ py: 4, textAlign: "center" }}>
            {emptyText}
          </Typography>
        )}
      </Box>
    </Box>
  );
}
