/**
 * NewsFeedPage — social-media-style news feed (shared component).
 *
 * A drop-in replacement for the old NewsList: same props contract (one page
 * of items + pagination + loading/error/scope label), but each item renders
 * as a post card (NewsPostCard) — click to expand the full article body and
 * open the threaded comments (CommentThread), which expand further per root.
 *
 * The feed chrome (count line + pager + error + empty state) is the shared
 * PostFeed; this wrapper only maps NewsItems to NewsPostCards. Lives in
 * shared/components so any page can embed a news feed, not just the dataviz
 * News page.
 */
import type { NewsItem } from "@shared/types";
import PostFeed from "@/shared/components/post-feed/PostFeed";
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
  return (
    <PostFeed
      total={total}
      page={page}
      pageSize={PAGE_SIZE}
      onPageChange={onPageChange}
      loading={loading}
      error={error}
      errorLabel="Failed to load news"
      scopeLabel={scopeLabel}
      countNoun="篇"
      empty={items.length === 0}
      emptyText={emptyText}
    >
      {items.map((it) => (
        <NewsPostCard key={it.news_id} item={it} />
      ))}
    </PostFeed>
  );
}
