/**
 * AiQaRefBox — renders an LLM Q&A text passage (the answer) with its inline
 * citation markers turned into expandable ref buttons.
 *
 * The stored text embeds ZhiPu-style citation brackets — [ref_2],
 * [来源：ref_1], date-suffixed [来源：ref_1, 2026-09-11], and multi-ref
 * brackets [来源：ref_1，2026-09；来源：ref_2，2026-09-10] (fullwidth
 * ，/；, per-model variance) — which otherwise surface as raw text. Parsing
 * mirrors llm_agents' extract_cited_refs two-stage scan: bracketed spans
 * first, then every ref_N tag inside (brackets without any ref_N tag are
 * plain text and pass through untouched). Each citation bracket is replaced
 * with one small toggle button per cited tag; clicking a button expands an
 * inline box right below the citation showing the resolved article
 * (text.llm_qa_refs joined back to text.news): the title link, the
 * exact/relevant typing with its persisted provenance (resolved_via —
 * summary echo / corpus match / ddgs title search) and source as tags,
 * and a scrollable content preview (text.news.content, server-capped at
 * 4000 chars). A cited tag with no resolution row expands to a
 * placeholder instead.
 */
import { useMemo, useState } from "react";
import {
  Box,
  Button,
  Chip,
  Stack,
  Typography,
  type SxProps,
} from "@mui/material";
import type { Theme } from "@mui/material/styles";
import { OpenInNew as OpenInNewIcon } from "@mui/icons-material";
import type { AiQaRefItem } from "@shared/types";

// Same shapes as llm_agents/online_search_summary/core/citations.py: bracketed
// spans, then ref_N tags inside. Nested brackets can't occur (the span
// charset excludes [ ]). Non-citation brackets (e.g. "[要点]") stay text.
const BRACKET_SPAN_RE = /\[([^\[\]]*)\]/g;
const REF_TAG_RE = /ref_(\d+)/g;

/** Split the passage into plain-text and citation-bracket segments (tags in
 *  citation order, deduplicated). */
function parseSegments(text: string): Array<
  | { kind: "text"; value: string }
  | { kind: "cites"; tags: string[] }
> {
  const segments: Array<
    | { kind: "text"; value: string }
    | { kind: "cites"; tags: string[] }
  > = [];
  let last = 0;
  for (const m of text.matchAll(BRACKET_SPAN_RE)) {
    const tags: string[] = [];
    for (const t of m[1].matchAll(REF_TAG_RE)) {
      const tag = `ref_${t[1]}`;
      if (!tags.includes(tag)) tags.push(tag);
    }
    if (tags.length === 0) continue; // no citation tags → literal text
    const start = m.index ?? 0;
    if (start > last) {
      segments.push({ kind: "text", value: text.slice(last, start) });
    }
    segments.push({ kind: "cites", tags });
    last = start + m[0].length;
  }
  if (last < text.length) {
    segments.push({ kind: "text", value: text.slice(last) });
  }
  return segments;
}

/** One toggle button per cited tag — filled while its box is expanded;
 *  primary-tinted for content-verified (exact) refs, neutral otherwise. */
function RefTagButton({
  tag,
  refItem,
  open,
  onToggle,
}: {
  tag: string;
  refItem?: AiQaRefItem;
  open: boolean;
  onToggle: () => void;
}) {
  return (
    <Button
      size="small"
      variant={open ? "contained" : "outlined"}
      color={refItem?.ref_type === "exact" ? "primary" : "inherit"}
      title={
        refItem
          ? `${tag} · ${refItem.ref_type} — ${refItem.title}`
          : `${tag}（未解析）`
      }
      onClick={(e) => {
        e.stopPropagation();
        onToggle();
      }}
      sx={{
        minWidth: 0,
        px: 0.5,
        py: 0,
        mx: 0.15,
        fontSize: "0.6rem",
        lineHeight: 1.7,
        whiteSpace: "nowrap",
        verticalAlign: "baseline",
      }}
    >
      {tag}
    </Button>
  );
}

/** The persisted resolution provenance (text.llm_qa_refs.resolved_via)
 *  behind the ref_type tag: 'summary' — the article came with the
 *  provider's summary response; 'corpus' / 'ddgs' — found after the
 *  summary by title search (stored corpus / ddgs candidate page). Legacy
 *  rows without the column fall back to the old ref_type semantics. */
function viaChipMeta(refItem: AiQaRefItem): { label: string; title: string } {
  if (refItem.resolved_via === "corpus") {
    return refItem.source === "zhihu"
      ? { label: "zhihu", title: "摘要后标题匹配 zhihu 语料（relevant）" }
      : { label: "corpus", title: "摘要后标题匹配本地语料（relevant）" };
  }
  if (refItem.resolved_via === "ddgs") {
    return { label: "ddgs", title: "摘要后 ddgs 标题检索 + 正文核验（relevant）" };
  }
  if (refItem.resolved_via === "summary") {
    return { label: "summary", title: "来自 ZhiPu summary 响应的引用原文（exact）" };
  }
  return refItem.ref_type === "exact"
    ? { label: "ddgs", title: "旧数据：ddgs 标题检索 + 正文核验" }
    : { label: "web", title: "旧数据：web 检索命中" };
}

/** The expanded inline box for one cited tag — the resolved article row
 *  (title link, ref_type + provenance + source tags, date) plus a
 *  scrollable content preview (text.news.content, 4000-char server cap),
 *  or a placeholder when the tag has no text.llm_qa_refs resolution. */
function RefExpandBox({ tag, refItem }: { tag: string; refItem?: AiQaRefItem }) {
  if (!refItem) {
    return (
      <Box
        sx={{
          my: 0.5,
          p: 0.75,
          borderRadius: 1,
          bgcolor: "action.hover",
          border: "1px dashed",
          borderColor: "divider",
        }}
      >
        <Typography variant="caption" color="text.secondary">
          {tag} — 未找到引用解析记录
        </Typography>
      </Box>
    );
  }
  const link = refItem.url ?? refItem.resolved_url ?? null;
  const via = viaChipMeta(refItem);
  const exact = refItem.ref_type === "exact";
  const tagSx = { fontSize: "0.6rem", height: 16, flexShrink: 0, alignSelf: "center" } as const;
  return (
    <Box
      sx={{
        my: 0.5,
        p: 0.75,
        borderRadius: 1,
        bgcolor: "action.hover",
        border: "1px solid",
        borderColor: "divider",
      }}
    >
      <Stack direction="row" spacing={0.75} sx={{ alignItems: "baseline" }}>
        <Chip
          label={refItem.ref_type}
          size="small"
          variant="outlined"
          color={exact ? "primary" : "default"}
          title={exact ? "引用原文来自 summary 响应" : "摘要后按标题检索匹配的相关文章"}
          sx={tagSx}
        />
        <Chip
          label={via.label}
          size="small"
          variant="outlined"
          title={via.title}
          sx={{ ...tagSx, color: "text.secondary" }}
        />
        {refItem.source && !["web", "zhihu"].includes(refItem.source) && refItem.source !== via.label && (
          <Chip
            label={refItem.source}
            size="small"
            variant="outlined"
            title={`来源 · ${refItem.source}`}
            sx={{ ...tagSx, color: "text.secondary" }}
          />
        )}
        <Box sx={{ minWidth: 0, overflow: "hidden" }}>
          <Typography
            component={link ? "a" : "span"}
            href={link ?? undefined}
            target={link ? "_blank" : undefined}
            rel="noopener noreferrer"
            onClick={(e) => e.stopPropagation()}
            sx={{
              fontSize: "0.75rem",
              fontWeight: 600,
              color: "text.primary",
              textDecoration: "none",
              wordBreak: "break-all",
              "&:hover": link ? { textDecoration: "underline" } : {},
            }}
          >
            {refItem.title}
          </Typography>{" "}
          <Typography component="span" color="text.secondary" sx={{ fontSize: "0.65rem" }}>
            {refItem.date}
          </Typography>
        </Box>
        {link && (
          <OpenInNewIcon
            sx={{ fontSize: 12, color: "text.secondary", flexShrink: 0, alignSelf: "center" }}
          />
        )}
      </Stack>
      {refItem.content && (
        <Typography
          variant="caption"
          sx={{
            display: "block",
            mt: 0.5,
            pt: 0.5,
            borderTop: "1px dashed",
            borderColor: "divider",
            maxHeight: 180,
            overflowY: "auto",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
            lineHeight: 1.6,
            color: "text.secondary",
          }}
        >
          {refItem.content}
        </Typography>
      )}
    </Box>
  );
}

/** One citation bracket: the tag buttons inline, then the expanded boxes of
 *  every opened tag right below them (block-level, breaking the passage
 *  flow at the citation site). Expansion is per bracket group. */
function CitationGroup({
  tags,
  byRef,
}: {
  tags: string[];
  byRef: Map<string, AiQaRefItem>;
}) {
  const [open, setOpen] = useState<ReadonlySet<string>>(new Set());
  const toggle = (tag: string) =>
    setOpen((cur) => {
      const next = new Set(cur);
      if (next.has(tag)) next.delete(tag);
      else next.add(tag);
      return next;
    });
  return (
    <>
      {tags.map((tag) => (
        <RefTagButton
          key={tag}
          tag={tag}
          refItem={byRef.get(tag)}
          open={open.has(tag)}
          onToggle={() => toggle(tag)}
        />
      ))}
      {tags
        .filter((tag) => open.has(tag))
        .map((tag) => (
          <RefExpandBox key={`box-${tag}`} tag={tag} refItem={byRef.get(tag)} />
        ))}
    </>
  );
}

export default function AiQaRefBox({
  text,
  refs,
  sx,
}: {
  /** The passage with inline citation brackets. */
  text: string;
  /** The Q&A's resolved refs (text.llm_qa_refs) keyed by their ref tag. */
  refs: AiQaRefItem[];
  /** Caller styling (font size / color — the box only fixes pre-wrap). */
  sx?: SxProps<Theme>;
}) {
  const byRef = useMemo(() => new Map(refs.map((r) => [r.ref, r])), [refs]);
  const segments = useMemo(() => parseSegments(text), [text]);
  return (
    <Box
      sx={[
        { whiteSpace: "pre-wrap", lineHeight: 1.65 },
        ...(Array.isArray(sx) ? sx : sx ? [sx] : []),
      ]}
    >
      {segments.map((seg, i) =>
        seg.kind === "text" ? (
          <span key={i}>{seg.value}</span>
        ) : (
          <CitationGroup key={i} tags={seg.tags} byRef={byRef} />
        ),
      )}
    </Box>
  );
}
