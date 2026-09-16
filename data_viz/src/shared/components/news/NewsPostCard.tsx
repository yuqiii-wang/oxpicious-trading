/**
 * NewsPostCard — one social-media-style post in the shared NewsFeedPage.
 *
 * Collapsed it shows the header (author avatar + date + votes), chips and
 * the 2-line snippet. Clicking the card expands it: the full article body
 * is lazily fetched (GET /api/news/item) and rendered; a comments toggle
 * lazily fetches the threaded comments (GET /api/news/comments) which
 * themselves expand further per root comment (see CommentThread).
 */
import { useState } from "react";
import {
  Avatar,
  Box,
  Button,
  Chip,
  CircularProgress,
  Stack,
  Typography,
} from "@mui/material";
import {
  ChatBubbleOutline as ChatIcon,
  ExpandLess as ExpandLessIcon,
  ExpandMore as ExpandMoreIcon,
  OpenInNew as OpenInNewIcon,
} from "@mui/icons-material";
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
  const [expanded, setExpanded] = useState(false);
  const [detail, setDetail] = useState<NewsItemDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [showComments, setShowComments] = useState(false);
  const [comments, setComments] = useState<NewsComment[] | null>(null);
  const [commentsTotal, setCommentsTotal] = useState<number | null>(null);
  const [commentsLoading, setCommentsLoading] = useState(false);

  const name = item.author ?? item.source ?? "新闻";
  const commentCount = detail?.comment_count ?? item.comment_count ?? 0;

  const toggleExpand = async () => {
    const next = !expanded;
    setExpanded(next);
    if (next && detail === null && !detailLoading) {
      setDetailLoading(true);
      try {
        setDetail(await fetchNewsItem(item.news_id));
      } catch {
        setDetail(null);
      } finally {
        setDetailLoading(false);
      }
    }
  };

  const toggleComments = async () => {
    const next = !showComments;
    setShowComments(next);
    if (next && comments === null && !commentsLoading) {
      setCommentsLoading(true);
      try {
        const resp = await fetchNewsComments(item.news_id);
        setComments(resp.comments);
        setCommentsTotal(resp.total);
      } catch {
        setComments([]);
      } finally {
        setCommentsLoading(false);
      }
    }
  };

  return (
    <Box
      onClick={() => { if (!expanded) void toggleExpand(); }}
      sx={{
        p: 1.25,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1.5,
        bgcolor: "background.paper",
        cursor: expanded ? "default" : "pointer",
        "&:hover": { bgcolor: "action.hover" },
      }}
    >
      {/* ---- header: avatar · author · date ..... ▲ votes ---- */}
      <Stack direction="row" spacing={1} sx={{ alignItems: "center", mb: 0.75 }}>
        <Avatar
          sx={{
            width: 28,
            height: 28,
            fontSize: "0.8rem",
            bgcolor: avatarColor(name),
          }}
        >
          {name.slice(0, 1).toUpperCase()}
        </Avatar>
        <Typography variant="subtitle2" sx={{ fontWeight: 700, fontSize: "0.8rem" }}>
          {name}
        </Typography>
        <Typography variant="subtitle2" color="text.secondary" sx={{ fontSize: "0.7rem" }}>
          {item.date}
        </Typography>
        <Box sx={{ ml: "auto", display: "flex", alignItems: "center", gap: 0.5 }}>
          {item.votes != null && item.votes > 0 && (
            <Chip label={`▲ ${item.votes.toLocaleString()}`} size="small" variant="outlined"
              sx={{ fontSize: "0.7rem" }} />
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
        </Box>
      </Stack>

      {/* ---- chips: source · sector (sector-only items) · industry ---- */}
      <Stack direction="row" spacing={0.5} sx={{ flexWrap: "wrap", gap: 0.5, mb: 0.5 }}>
        {item.source && (
          <Chip label={item.source} size="small" color="primary" variant="outlined"
            sx={{ fontSize: "0.65rem", height: 18 }} />
        )}
        {!item.industry_id && item.sector_id && (
          <Chip label={item.sector_id} size="small" color="secondary" variant="outlined"
            sx={{ fontSize: "0.65rem", height: 18 }} />
        )}
        {item.industry_id && (
          <Chip label={item.industry_id} size="small" color="secondary" variant="outlined"
            sx={{ fontSize: "0.65rem", height: 18 }} />
        )}
      </Stack>

      {/* ---- title (post headline) ---- */}
      <Typography
        component={item.url ? "a" : "div"}
        href={item.url ?? undefined}
        target={item.url ? "_blank" : undefined}
        rel="noopener noreferrer"
        onClick={(e) => { if (item.url) e.stopPropagation(); }}
        sx={{
          display: "block",
          fontWeight: 700,
          fontSize: "0.9rem",
          lineHeight: 1.35,
          color: "text.primary",
          textDecoration: "none",
          mb: item.snippet || expanded ? 0.5 : 0,
          "&:hover": item.url ? { textDecoration: "underline" } : {},
        }}
      >
        {item.title}
      </Typography>

      {/* ---- body: snippet when collapsed, full content when expanded ---- */}
      {!expanded && item.snippet && (
        <Typography variant="body2" color="text.secondary"
          sx={{
            display: "-webkit-box",
            WebkitLineClamp: 2,
            WebkitBoxOrient: "vertical",
            overflow: "hidden",
          }}>
          {item.snippet}
        </Typography>
      )}
      {expanded && (
        <Box sx={{ my: 0.75 }}>
          {detailLoading ? (
            <Stack direction="row" spacing={1} sx={{ alignItems: "center", py: 1 }}>
              <CircularProgress size={14} thickness={5} />
              <Typography variant="body2" color="text.secondary">加载中…</Typography>
            </Stack>
          ) : detail?.content ? (
            <Typography
              variant="body2"
              sx={{
                whiteSpace: "pre-wrap",
                lineHeight: 1.65,
                maxHeight: 520,
                overflowY: "auto",
                pr: 1,
              }}>
              {detail.content}
            </Typography>
          ) : (
            <Typography variant="body2" color="text.secondary" sx={{ py: 0.5 }}>
              （无正文内容）
            </Typography>
          )}
        </Box>
      )}

      {/* ---- actions: expand toggle · comments toggle ---- */}
      <Stack direction="row" spacing={0.5} sx={{ alignItems: "center", mt: 0.75 }}>
        <Button
          size="small"
          color="inherit"
          startIcon={expanded ? <ExpandLessIcon /> : <ExpandMoreIcon />}
          onClick={(e) => { e.stopPropagation(); void toggleExpand(); }}
          sx={{ fontSize: "0.7rem", minWidth: 0, px: 0.75, color: "text.secondary" }}
        >
          {expanded ? "收起" : "展开全文"}
        </Button>
        <Button
          size="small"
          color="inherit"
          startIcon={<ChatIcon sx={{ fontSize: 14 }} />}
          onClick={(e) => { e.stopPropagation(); void toggleComments(); }}
          sx={{ fontSize: "0.7rem", minWidth: 0, px: 0.75, color: "text.secondary" }}
        >
          {commentsTotal != null ? commentsTotal : commentCount} 条评论
        </Button>
      </Stack>

      {/* ---- comments section (lazily loaded, threads expand further) ---- */}
      {showComments && (
        <Box
          onClick={(e) => e.stopPropagation()}
          sx={{
            mt: 1,
            pt: 1,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          {commentsLoading ? (
            <Stack direction="row" spacing={1} sx={{ alignItems: "center", py: 1 }}>
              <CircularProgress size={14} thickness={5} />
              <Typography variant="body2" color="text.secondary">评论加载中…</Typography>
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
    </Box>
  );
}
