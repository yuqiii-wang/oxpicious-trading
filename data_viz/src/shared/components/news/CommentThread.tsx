/**
 * CommentThread — threaded comments for one news post.
 *
 * Roots render by votes (as served by GET /api/news/comments); each root
 * that carries replies gets a "N 条回复" toggle which expands the nested
 * replies inline — the social-media "comments expand further" pattern.
 */
import { useState } from "react";
import { Avatar, Box, Button, Stack, Typography } from "@mui/material";
import { ExpandLess, ExpandMore } from "@mui/icons-material";
import type { NewsComment } from "@shared/types";

function CommentAvatar({ name }: { name: string }) {
  return (
    <Avatar
      sx={{
        width: 20,
        height: 20,
        fontSize: "0.6rem",
        bgcolor: "grey.500",
        flexShrink: 0,
      }}
    >
      {name.slice(0, 1).toUpperCase()}
    </Avatar>
  );
}

/** Zhihu comment content is light HTML (<p>, <br>, entities) — flatten it. */
function htmlToText(html: string): string {
  return html
    .replace(/<br\s*\/?>/gi, "\n")
    .replace(/<\/p>\s*/gi, "\n")
    .replace(/<[^>]+>/g, "")
    .replace(/&nbsp;/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .trim();
}

function CommentBody({ comment, isReply }: { comment: NewsComment; isReply: boolean }) {
  const name = comment.author ?? "匿名";
  return (
    <Box sx={{ minWidth: 0 }}>
      <Stack direction="row" spacing={0.75} sx={{ alignItems: "baseline", flexWrap: "wrap" }}>
        <Typography variant="subtitle2" sx={{ fontWeight: 700, fontSize: isReply ? "0.7rem" : "0.75rem" }}>
          {name}
        </Typography>
        {comment.date && (
          <Typography variant="subtitle2" color="text.secondary" sx={{ fontSize: "0.65rem" }}>
            {comment.date}
          </Typography>
        )}
        {comment.votes != null && comment.votes > 0 && (
          <Typography variant="subtitle2" sx={{ fontSize: "0.65rem", color: "primary.main" }}>
            ▲ {comment.votes.toLocaleString()}
          </Typography>
        )}
      </Stack>
      {comment.content && (
        <Typography
          variant="body2"
          color="text.primary"
          sx={{ fontSize: isReply ? "0.72rem" : "0.76rem", whiteSpace: "pre-wrap", mt: 0.25 }}
        >
          {htmlToText(comment.content)}
        </Typography>
      )}
    </Box>
  );
}

function CommentRow({ comment, isReply }: { comment: NewsComment; isReply: boolean }) {
  const [repliesOpen, setRepliesOpen] = useState(false);
  const hasReplies = comment.replies.length > 0;

  return (
    <Box sx={{ mb: isReply ? 0.75 : 1 }}>
      <Stack direction="row" spacing={0.75} sx={{ alignItems: "flex-start" }}>
        <Box sx={{ pt: 0.25 }}>
          <CommentAvatar name={comment.author ?? "匿名"} />
        </Box>
        <CommentBody comment={comment} isReply={isReply} />
      </Stack>
      {hasReplies && (
        <Box sx={{ ml: 3.5, mt: 0.25 }}>
          <Button
            size="small"
            color="inherit"
            startIcon={repliesOpen ? <ExpandLess /> : <ExpandMore />}
            onClick={() => setRepliesOpen((v) => !v)}
            sx={{ fontSize: "0.68rem", minWidth: 0, px: 0.5, color: "text.secondary" }}
          >
            {comment.replies.length} 条回复
          </Button>
          {repliesOpen && (
            <Box sx={{ mt: 0.5, pl: 1, borderLeft: "2px solid", borderColor: "divider" }}>
              {comment.replies.map((r) => (
                <CommentRow key={r.comment_id} comment={r} isReply />
              ))}
            </Box>
          )}
        </Box>
      )}
    </Box>
  );
}

export default function CommentThread({ comments }: { comments: NewsComment[] }) {
  return (
    <Box sx={{ py: 0.5 }}>
      {comments.map((c) => (
        <CommentRow key={c.comment_id} comment={c} isReply={false} />
      ))}
    </Box>
  );
}
