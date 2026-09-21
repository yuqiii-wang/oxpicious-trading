/**
 * NewsPostCard — one social-media-style post in the shared NewsFeedPage.
 *
 * Built on the shared PostCard base (expand-on-click, lazy detail fetch):
 * the header carries the author avatar + date + votes chip + 原文 link,
 * the chips row the source/sector/industry tags, and the expanded body
 * the full article text. The comments toggle lives in the action bar and
 * lazily fetches the threaded comments (GET /api/news/comments) into the
 * section below it — see CommentThread for the per-root expansion.
 */
import { useState } from "react";
import { Avatar, Box, Button, CircularProgress, Stack, Typography } from "@mui/material";
import { ChatBubbleOutline as ChatIcon, OpenInNew as OpenInNewIcon } from "@mui/icons-material";
import PostCard from "@/shared/components/post-feed/PostCard";
import PostChip from "@/shared/components/post-feed/PostChip";
import { fetchNewsComments, fetchNewsItem } from "@/lib/api-client";
import type { NewsComment, NewsItem, NewsItemDetail } from "@shared/types";
import CommentThread from "./CommentThread";

/** Deterministic avatar hue per author so the feed reads like a social app. */
const AVATAR_COLORS = ["#5470c6", "#91cc75", "#fac858", "#ee6666", "#73c0de", "#3ba272", "#fc8452", "#9a60b4"];
function avatarColor(seed: string): string {
  let h = 0;
  for (let i = 0; i < seed.length; i++) h = (h * 31 + seed.charCodeAt(i)) >>> 0;
  return AVATAR_COLORS[h % AVATAR_COLORS.length];
}

export default function NewsPostCard({ item }: { item: NewsItem }) {
  // Comments state — a separate lazy fetch (below the expand toggle).
  const [showComments, setShowComments] = useState(false);
  const [comments, setComments] = useState<NewsComment[] | null>(null);
  const [commentsTotal, setCommentsTotal] = useState<number | null>(null);
  const [commentsLoading, setCommentsLoading] = useState(false);
  const [commentsError, setCommentsError] = useState<string | null>(null);

  const name = item.author ?? item.source ?? "新闻";

  const loadComments = async () => {
    if (commentsLoading) return;
    setCommentsLoading(true);
    setCommentsError(null);
    try {
      const resp = await fetchNewsComments(item.news_id);
      setComments(resp.comments);
      setCommentsTotal(resp.total);
    } catch (e) {
      // Stay in the error state (comments stays null) so the retry button —
      // or collapsing and re-expanding — refetches instead of caching a
      // failure as "no comments".
      setCommentsError(e instanceof Error ? e.message : "网络错误");
    } finally {
      setCommentsLoading(false);
    }
  };

  const toggleComments = () => {
    const next = !showComments;
    setShowComments(next);
    if (next && comments === null) void loadComments();
  };

  return (
    <PostCard<NewsItemDetail>
      header={{
        icon: (
          <Avatar sx={{ width: 28, height: 28, fontSize: "0.8rem", bgcolor: avatarColor(name) }}>
            {name.slice(0, 1).toUpperCase()}
          </Avatar>
        ),
        title: name,
        date: item.date,
        trailing: (
          <>
            {item.votes != null && item.votes > 0 && (
              <PostChip label={`▲ ${item.votes.toLocaleString()}`} sx={{ fontSize: "0.7rem" }} />
            )}
            {item.url && (
              <Typography
                component="a"
                href={item.url}
                target="_blank"
                rel="noopener noreferrer"
                onClick={(e) => e.stopPropagation()}
                aria-label="原文"
                sx={{ color: "text.secondary", display: "flex", "&:hover": { color: "primary.main" } }}
              >
                <OpenInNewIcon sx={{ fontSize: 15 }} />
              </Typography>
            )}
          </>
        ),
      }}
      chips={
        <>
          {item.source && <PostChip label={item.source} color="primary" />}
          {!item.industry_id && item.sector_id && <PostChip label={item.sector_id} color="secondary" />}
          {item.industry_id && <PostChip label={item.industry_id} color="secondary" />}
        </>
      }
      headline={item.title}
      headlineHref={item.url}
      snippet={item.snippet}
      fetchDetail={() => fetchNewsItem(item.news_id)}
      renderDetail={(detail) => (
        <Typography
          variant="body2"
          sx={{
            whiteSpace: "pre-wrap",
            lineHeight: 1.65,
            maxHeight: 520,
            overflowY: "auto",
            pr: 1,
          }}
        >
          {detail.content}
        </Typography>
      )}
      detailEmptyText="（无正文内容）"
      renderActions={(detail) => {
        const commentCount = detail?.comment_count ?? item.comment_count ?? 0;
        return (
          <Button
            size="small"
            color="inherit"
            startIcon={<ChatIcon sx={{ fontSize: 14 }} />}
            onClick={(e) => { e.stopPropagation(); toggleComments(); }}
            sx={{ fontSize: "0.7rem", minWidth: 0, px: 0.75, color: "text.secondary" }}
          >
            {commentsTotal != null ? commentsTotal : commentCount} 条评论
          </Button>
        );
      }}
    >
      {/* ---- comments section (lazily loaded, threads expand further) ---- */}
      {showComments && (
        <Box
          onClick={(e) => e.stopPropagation()}
          sx={{ mt: 1, pt: 1, borderTop: "1px solid", borderColor: "divider" }}
        >
          {commentsLoading ? (
            <Stack direction="row" spacing={1} sx={{ alignItems: "center", py: 1 }}>
              <CircularProgress size={14} thickness={5} />
              <Typography variant="body2" color="text.secondary">评论加载中…</Typography>
            </Stack>
          ) : commentsError ? (
            <Stack direction="row" spacing={1} sx={{ alignItems: "center", py: 1 }}>
              <Typography variant="body2" color="error.main">
                评论加载失败：{commentsError}
              </Typography>
              <Button
                size="small"
                color="inherit"
                variant="outlined"
                onClick={() => void loadComments()}
                sx={{ fontSize: "0.7rem", minWidth: 0, px: 0.75, color: "text.secondary" }}
              >
                重试
              </Button>
            </Stack>
          ) : (comments?.length ?? 0) === 0 ? (
            <Typography variant="body2" color="text.secondary" sx={{ py: 1 }}>
              暂无评论（该来源评论尚未抓取）
            </Typography>
          ) : (
            <CommentThread comments={comments!} />
          )}
        </Box>
      )}
    </PostCard>
  );
}
