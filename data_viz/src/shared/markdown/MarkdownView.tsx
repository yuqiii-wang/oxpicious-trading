/**
 * MarkdownView — shared markdown renderer (react-markdown + remark-gfm)
 * mapped onto MUI theme tokens, so headings/tables/code render consistently
 * in both theme modes on any page that shows an AI-authored or generated
 * document (analysis AI summary, future report surfaces).
 *
 * Most styling is descendant CSS on the wrapper Box; only the block
 * elements whose scale should follow the theme typography (headings,
 * paragraphs) are mapped to Typography components.
 */
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Box, Typography } from "@mui/material";
import type { Components } from "react-markdown";

const components: Components = {
  h1: ({ children }) => (
    <Typography variant="h5" sx={{ fontWeight: 700, mt: 0.5, mb: 1.5 }}>
      {children}
    </Typography>
  ),
  h2: ({ children }) => (
    <Typography variant="h6" sx={{ fontWeight: 700, mt: 3, mb: 1 }}>
      {children}
    </Typography>
  ),
  h3: ({ children }) => (
    <Typography variant="subtitle1" sx={{ fontWeight: 600, mt: 2.5, mb: 0.75 }}>
      {children}
    </Typography>
  ),
  h4: ({ children }) => (
    <Typography variant="subtitle2" sx={{ fontWeight: 600, mt: 2, mb: 0.5 }}>
      {children}
    </Typography>
  ),
  p: ({ children }) => (
    <Typography variant="body1" sx={{ mb: 1.25, lineHeight: 1.7 }}>
      {children}
    </Typography>
  ),
  a: ({ children, href }) => (
    <Box component="a" href={href} target="_blank" rel="noreferrer" sx={{ color: "primary.main" }}>
      {children}
    </Box>
  ),
};

/** The dense variant's mapping — document-scale headings and body1
 *  paragraphs shrink to embedding scale (chat messages, side panels):
 *  subtitle headings, body2 paragraphs, tighter rhythm. */
const denseComponents: Components = {
  h1: ({ children }) => (
    <Typography variant="subtitle1" sx={{ fontWeight: 700, mt: 0.5, mb: 0.75 }}>
      {children}
    </Typography>
  ),
  h2: ({ children }) => (
    <Typography variant="subtitle2" sx={{ fontWeight: 700, mt: 1.5, mb: 0.5 }}>
      {children}
    </Typography>
  ),
  h3: ({ children }) => (
    <Typography variant="subtitle2" sx={{ fontWeight: 600, mt: 1.25, mb: 0.5 }}>
      {children}
    </Typography>
  ),
  h4: ({ children }) => (
    <Typography variant="subtitle2" sx={{ fontWeight: 600, mt: 1, mb: 0.5 }}>
      {children}
    </Typography>
  ),
  p: ({ children }) => (
    <Typography variant="body2" sx={{ mb: 0.75, lineHeight: 1.6 }}>
      {children}
    </Typography>
  ),
  a: components.a,
};

/** The dense variant's rhythm overrides on the wrapper's descendant CSS
 *  (later keys win over the base object's same selectors). */
const denseSx = {
  "& li": { mb: 0.25, lineHeight: 1.6 },
  "& pre": { p: 1.25, mb: 0.75, fontSize: "0.75rem" },
  "& th, & td": { px: 1, py: 0.4, fontSize: "0.75rem" },
  "& hr": { my: 1.5 },
} as const;

export interface MarkdownViewProps {
  /** Raw markdown source (GFM tables, strikethrough, task lists supported). */
  source: string;
  /** Compact embedding scale (chat messages, panels): subtitle-scale
   *  headings, body2 paragraphs, tighter rhythm. Default false (the
   *  document scale the summary page renders at). */
  dense?: boolean;
}

export default function MarkdownView({ source, dense = false }: MarkdownViewProps) {
  return (
    <Box
      sx={{
        minWidth: 0,
        color: "text.primary",
        // Paragraph/list rhythm beyond what the Typography mappings cover.
        // Tailwind preflight strips list markers globally — restore them.
        "& ul": { m: 0, pl: 3, mb: 1.25, listStyle: "disc" },
        "& ol": { m: 0, pl: 3.5, mb: 1.25, listStyle: "decimal" },
        "& ul ul": { listStyle: "circle", mb: 0 },
        "& li": { mb: 0.5, lineHeight: 1.7 },
        "& li > p": { mb: 0.5 },
        // Inline vs block code — the :not(pre) scope keeps fenced blocks clean.
        "& code": { fontFamily: "monospace", fontSize: "0.875em" },
        "& :not(pre) > code": {
          px: 0.6,
          py: 0.2,
          borderRadius: 1,
          border: "1px solid",
          borderColor: "divider",
          bgcolor: "action.hover",
        },
        "& pre": {
          p: 2,
          mb: 1.5,
          borderRadius: 1,
          overflowX: "auto",
          bgcolor: "action.hover",
          border: "1px solid",
          borderColor: "divider",
          "& code": { p: 0, border: "none", bgcolor: "transparent" },
        },
        // GFM tables — hairline grid, hover-tinted header row.
        "& table": { borderCollapse: "collapse", mb: 1.5, maxWidth: "100%", display: "block", overflowX: "auto" },
        "& th, & td": { border: "1px solid", borderColor: "divider", px: 1.25, py: 0.6, fontSize: "0.875rem" },
        "& th": { fontWeight: 600, bgcolor: "action.hover", textAlign: "left" },
        "& blockquote": {
          m: 0,
          mb: 1.5,
          pl: 2,
          borderLeft: 3,
          borderColor: "primary.main",
          color: "text.secondary",
        },
        "& hr": { border: "none", borderTop: "1px solid", borderColor: "divider", my: 2.5 },
        ...(dense ? denseSx : {}),
      }}
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={dense ? denseComponents : components}
      >
        {source}
      </ReactMarkdown>
    </Box>
  );
}
