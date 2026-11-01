/**
 * AiResearchQaCard — one research-logic Q&A post in the DataViz > AI feed's
 * "Research" source (text.llm_qa_by_research, written by
 * llm_agents.llm_qa_by_research's five-request generation flow).
 *
 * Same PostCard chrome as AiQaCard (expand-on-click, lazy detail fetch),
 * different content contract: the card body is the row's SUMMARY (the 5th
 * request's answer); the five evaluation dimensions — 投资逻辑总览 (intro) /
 * 乐观前景预测 (optimistic_forecast) / 悲观前景预测 (pessimistic_forecast) /
 * 风险因素 (risks) / 市值与市场关注 (capitalization) —
 * are collapsed sections inside the expanded detail (a failed dimension
 * renders as a disabled 未生成 note). Each dimension's inline
 * [来源：ref_N] citations render through AiQaRefBox — the same expandable
 * per-tag buttons as AiQaCard — resolved from the row's dim_refs column
 * joined to text.news (served as detail.dimension_refs; rows stored
 * before dim_refs render plain). The merged news group remains as the
 * 全部来源 list (title link + meta + content preview per source).
 */
import { useState } from "react";
import { Box, Button, Link, Typography } from "@mui/material";
import {
  ExpandLess as ExpandLessIcon,
  ExpandMore as ExpandMoreIcon,
  Insights as InsightsIcon,
} from "@mui/icons-material";
import PostCard from "@/shared/components/post-feed/PostCard";
import PostChip from "@/shared/components/post-feed/PostChip";
import AiQaRefBox from "@/dataviz/features/ai/AiQaRefBox";
import { fetchAiResearchDetail } from "@/lib/api-client";
import type {
  AiQaRefItem,
  AiQaResearchDetail,
  AiQaResearchItem,
  AiQaResearchSource,
} from "@shared/types";

/** Chinese gloss of the research_logic label (decoded English name). */
const LOGIC_LABEL_ZH: Record<string, string> = {
  growth: "成长",
  value: "价值",
  dividend: "红利",
  commodity: "商品",
  cyclical: "周期",
  defensive: "防御",
  quality: "质量",
  theme: "主题",
  other: "其他",
};

/** Sentiment chip color by score ([-5, 5] — red bearish, green bullish). */
function sentimentColor(level: number): "error" | "success" | "default" {
  return level < -0.5 ? "error" : level > 0.5 ? "success" : "default";
}

/** One collapsible evaluation-dimension section (the AllSourcesSection
 *  collapse idiom): a leading toggle button + the dimension text. The
 *  text renders through AiQaRefBox — its inline [来源：ref_N] brackets
 *  become the same expandable per-tag buttons as the llm_qa card,
 *  expanding to the dimension's resolved articles — falling back to the
 *  plain render on rows stored before dim_refs existed. */
function DimensionSection({ title, text, refs }: {
  title: string;
  text: string | null;
  refs?: AiQaRefItem[] | null;
}) {
  const [open, setOpen] = useState(false);
  return (
    <Box sx={{ mt: 0.5 }}>
      <Button
        size="small"
        color="inherit"
        disabled={!text}
        startIcon={open ? <ExpandLessIcon /> : <ExpandMoreIcon />}
        onClick={(e) => { e.stopPropagation(); setOpen((v) => !v); }}
        sx={{ fontSize: "0.7rem", minWidth: 0, px: 0.75, color: "text.secondary" }}
      >
        {title}
        {text == null && "（未生成）"}
      </Button>
      {open && text && (refs && refs.length > 0 ? (
        <AiQaRefBox
          text={text}
          refs={refs}
          sx={{ pl: 1, borderLeft: 1, borderColor: "divider", fontSize: "0.85rem" }}
        />
      ) : (
        <Typography
          variant="body2"
          sx={{ whiteSpace: "pre-wrap", lineHeight: 1.65, pl: 1, borderLeft: 1, borderColor: "divider" }}
        >
          {text}
        </Typography>
      ))}
    </Box>
  );
}

/** One source row of the merged news group: title (external link when the
 * article has a URL) + meta line; the content preview expands inline. */
function SourceRow({ src, index }: { src: AiQaResearchSource; index: number }) {
  const [open, setOpen] = useState(false);
  return (
    <Box sx={{ pl: 1, borderLeft: 1, borderColor: "divider" }}>
      <Box sx={{ display: "flex", alignItems: "baseline", gap: 0.75, flexWrap: "wrap" }}>
        <Typography component="span" sx={{ fontSize: "0.75rem", fontWeight: 600 }}>
          [{index + 1}]
        </Typography>
        {src.url ? (
          <Link
            href={src.url}
            target="_blank"
            rel="noreferrer"
            onClick={(e) => e.stopPropagation()}
            sx={{ fontSize: "0.75rem" }}
          >
            {src.title}
          </Link>
        ) : (
          <Typography component="span" sx={{ fontSize: "0.75rem" }}>
            {src.title}
          </Typography>
        )}
        <Typography component="span" sx={{ fontSize: "0.7rem", color: "text.secondary" }}>
          {src.source ?? "-"}{src.date ? ` · ${src.date}` : ""}
        </Typography>
        {src.content && (
          <Button
            size="small"
            color="inherit"
            onClick={(e) => { e.stopPropagation(); setOpen((v) => !v); }}
            sx={{ fontSize: "0.65rem", minWidth: 0, px: 0.5, py: 0, color: "text.secondary" }}
          >
            {open ? "收起" : "预览"}
          </Button>
        )}
      </Box>
      {open && src.content && (
        <Box
          component="pre"
          sx={{
            mt: 0.5, mb: 0.5, maxHeight: 200, overflow: "auto",
            fontSize: "0.7rem", whiteSpace: "pre-wrap",
            color: "text.secondary", bgcolor: "action.hover", p: 0.75, borderRadius: 1,
          }}
        >
          {src.content}
        </Box>
      )}
    </Box>
  );
}

/** Collapsible full source list — the row's merged news group (the
 * by-research refs mapping), one row per article. */
function AllSourcesSection({ sources }: { sources: AiQaResearchSource[] }) {
  const [open, setOpen] = useState(false);
  if (sources.length === 0) return null;
  return (
    <Box sx={{ mt: 0.75 }}>
      <Button
        size="small"
        color="inherit"
        startIcon={open ? <ExpandLessIcon /> : <ExpandMoreIcon />}
        onClick={(e) => { e.stopPropagation(); setOpen((v) => !v); }}
        sx={{ fontSize: "0.7rem", minWidth: 0, px: 0.75, color: "text.secondary" }}
      >
        {open ? "收起全部来源" : `全部来源（${sources.length} 篇文章）`}
      </Button>
      {open && (
        <Box sx={{ display: "flex", flexDirection: "column", gap: 0.75, mt: 0.5 }}>
          {sources.map((s, i) => (
            <SourceRow key={s.news_id} src={s} index={i} />
          ))}
        </Box>
      )}
    </Box>
  );
}

export default function AiResearchQaCard({ item }: { item: AiQaResearchItem }) {
  return (
    <PostCard<AiQaResearchDetail>
      header={{
        icon: <InsightsIcon sx={{ fontSize: 20, color: "primary.main" }} />,
        title: `AI Research #${item.qa_id}`,
        date: item.date,
      }}
      chips={
        <>
          <PostChip label={LOGIC_LABEL_ZH[item.logic_label] ?? item.logic_label} color="success" />
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
      fetchDetail={() => fetchAiResearchDetail(item.qa_id)}
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
          {/* The 5th request's SUMMARY — the row's display content. */}
          <Typography variant="body2" sx={{ whiteSpace: "pre-wrap", lineHeight: 1.65 }}>
            {detail.answer}
          </Typography>
          {/* The five evaluation dimensions — collapsed by default; each
              renders its [来源：ref_N] citations as expandable ref tags
              (the AiQaCard pattern) when dim_refs resolution exists. */}
          <DimensionSection title="投资逻辑总览" text={detail.intro} refs={detail.dimension_refs?.intro} />
          <DimensionSection title="乐观前景预测" text={detail.optimistic_forecast} refs={detail.dimension_refs?.optimistic_forecast} />
          <DimensionSection title="悲观前景预测" text={detail.pessimistic_forecast} refs={detail.dimension_refs?.pessimistic_forecast} />
          <DimensionSection title="风险因素" text={detail.risks} refs={detail.dimension_refs?.risks} />
          <DimensionSection title="市值与市场关注" text={detail.capitalization} refs={detail.dimension_refs?.capitalization} />
          {/* The merged news group (the refs mapping) as the source list. */}
          <AllSourcesSection sources={detail.sources} />
        </>
      )}
      detailEmptyText="（详情加载失败）"
      openLabel="展开研究"
    />
  );
}
