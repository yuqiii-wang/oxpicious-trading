/**
 * AiQaCard — one LLM Q&A knowledge-base post in the DataViz > AI feed.
 *
 * Built on the shared PostCard base (expand-on-click, lazy detail fetch).
 * Collapsed it shows the question (post headline), chips (industry · model
 * · language · n_sources) and the 2-line answer snippet. Clicking the
 * card expands it: the full row is lazily fetched (GET /api/ai/qa) — the
 * untruncated answer and the per-ref citation list (text.llm_qa_refs
 * joined back to text.news, each typed exact/relevant and linked to its
 * source). The answer renders through AiQaRefBox: its inline
 * [来源：ref_N …] citation brackets become toggle buttons that expand the
 * resolved article inline at the citation site — tags (typing +
 * provenance, source) plus a scrollable content preview. The stored
 * context passage (a format_references text dump) is NOT rendered.
 */
import { useState } from "react";
import { Box, Button } from "@mui/material";
import {
  ExpandLess as ExpandLessIcon,
  ExpandMore as ExpandMoreIcon,
  Psychology as PsychologyIcon,
} from "@mui/icons-material";
import PostCard from "@/shared/components/post-feed/PostCard";
import PostChip from "@/shared/components/post-feed/PostChip";
import { fetchAiQaDetail } from "@/lib/api-client";
import type { AiQaDetail, AiQaItem, AiQaRefItem } from "@shared/types";
import AiQaRefBox, { RefExpandBox } from "./AiQaRefBox";

/** Sentiment chip color by score ([-5, 5] — red bearish, green bullish). */
function sentimentColor(level: number): "error" | "success" | "default" {
  return level < -0.5 ? "error" : level > 0.5 ? "success" : "default";
}

function refNum(ref: string): number {
  const m = /ref_(\d+)/.exec(ref);
  return m ? Number(m[1]) : Number.MAX_SAFE_INTEGER;
}

/** Every stored ref of the Q&A in ref-number order — not just the ones the
 *  answer text cites. Null-content (title-only) news rows render as
 *  RefExpandBox's no-content shape, so every ref the response carried is
 *  always inspectable. */
function AllRefsList({ refs }: { refs: AiQaRefItem[] }) {
  const sorted = [...refs].sort((a, b) => refNum(a.ref) - refNum(b.ref));
  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 0.75, mt: 0.5 }}>
      {sorted.map((r) => (
        <RefExpandBox key={r.ref} tag={r.ref} refItem={r} />
      ))}
    </Box>
  );
}

/** Collapsible full source list under the expanded answer — one ref row per
 *  resolved article (PK qa_id + news_id), so the label shows the article
 *  count and how many of them the answer actually cites (is_used). */
function AllSourcesSection({ refs }: { refs: AiQaRefItem[] }) {
  const [open, setOpen] = useState(false);
  const nUsed = refs.filter((r) => r.is_used).length;
  return (
    <Box sx={{ mt: 0.75 }}>
      <Button
        size="small"
        color="inherit"
        startIcon={open ? <ExpandLessIcon /> : <ExpandMoreIcon />}
        onClick={(e) => { e.stopPropagation(); setOpen((v) => !v); }}
        sx={{ fontSize: "0.7rem", minWidth: 0, px: 0.75, color: "text.secondary" }}
      >
        {open
          ? "收起全部来源"
          : `全部来源（${refs.length} 篇文章 · 回答引用 ${nUsed}）`}
      </Button>
      {open && <AllRefsList refs={refs} />}
    </Box>
  );
}

export default function AiQaCard({ item }: { item: AiQaItem }) {
  return (
    <PostCard<AiQaDetail>
      header={{
        icon: <PsychologyIcon sx={{ fontSize: 20, color: "primary.main" }} />,
        title: `AI Q&A #${item.qa_id}`,
        date: item.date,
      }}
      chips={
        <>
          {!item.industry_id && item.sector_id && (
            <PostChip label={item.sector_id} color="secondary" />
          )}
          {item.industry_id && <PostChip label={item.industry_id} color="secondary" />}
          {item.llm_model && <PostChip label={item.llm_model} color="primary" />}
          {item.language && <PostChip label={item.language} />}
          <PostChip label={`${item.n_sources} 篇来源`} />
        </>
      }
      headline={item.question}
      snippet={item.answer_snippet}
      fetchDetail={() => fetchAiQaDetail(item.qa_id)}
      renderHeaderTrailing={(detail) =>
        detail?.avg_ref_sentiment != null && (
          <PostChip
            label={`情绪 ${detail.avg_ref_sentiment > 0 ? "+" : ""}${detail.avg_ref_sentiment}`}
            color={sentimentColor(detail.avg_ref_sentiment)}
          />
        )
      }
      renderDetail={(detail) => (
        <>
          {/* Answer — inline [来源：ref_N] citation brackets become
              expandable ref buttons (AiQaRefBox); each expansion carries
              the resolved article's tags + content preview. */}
          <AiQaRefBox
            text={detail.answer}
            refs={detail.refs}
            sx={{ typography: "body2", lineHeight: 1.65 }}
          />
          {/* The response's full reference list — cited refs only cover
              a handful of the stored rows; the rest surface here. */}
          <AllSourcesSection refs={detail.refs} />
        </>
      )}
      detailEmptyText="（详情加载失败）"
      openLabel="展开回答"
    />
  );
}
