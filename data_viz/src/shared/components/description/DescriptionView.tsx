/**
 * DescriptionView — documentation-style rendering of an authored chart
 * description: the intro split into paragraphs, then the per-series
 * descriptions ("Series") and the author's notes ("Notes").
 *
 * Extracted from AiAskModal's description page so every surface that shows
 * the same authored text (the AI Ask modal, help popovers, …) renders it
 * identically. Series entries without a description are skipped; the intro
 * and descriptions split on newlines into paragraphs, matching how authors
 * write them in the AiAskSpec.
 */
import { Box, Typography } from "@mui/material";
import { splitParagraphs } from "./splitParagraphs";

/** One described series — the display fields of the author spec's series
 *  entry / AiAskSeriesInfo. */
export interface DescriptionSeriesEntry {
  name: string;
  unit?: string;
  description?: string;
}

export interface DescriptionViewProps {
  /** Authored intro — newlines split into separate paragraphs. */
  intro: string;
  /** Per-series descriptions; entries without one are skipped. */
  series?: DescriptionSeriesEntry[];
  /** Free-form caveats — rendered as a bullet list under "Notes". */
  notes?: string[];
}

/** Small caps section label with a hairline top rule — separates the
 *  description view's intro paragraphs from the series / notes blocks. */
function SectionRule({ children }: { children: string }) {
  return (
    <Typography
      variant="caption"
      sx={{
        color: "var(--chart-subtitle)",
        fontWeight: 600,
        textTransform: "uppercase",
        letterSpacing: "0.08em",
        borderTop: "1px solid",
        borderColor: "divider",
        pt: 1,
        mt: 1.5,
      }}
    >
      {children}
    </Typography>
  );
}

const BODY_SX = { lineHeight: 1.75 } as const;

export function DescriptionView({ intro, series, notes }: DescriptionViewProps) {
  const paragraphs = splitParagraphs(intro);
  const describedSeries = (series ?? []).filter(
    (s): s is DescriptionSeriesEntry & { description: string } =>
      Boolean(s.description),
  );
  const noteLines = notes ?? [];
  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
      {paragraphs.map((p, i) => (
        <Typography key={i} variant="body2" sx={BODY_SX}>
          {p}
        </Typography>
      ))}
      {describedSeries.length > 0 && (
        <>
          <SectionRule>Series</SectionRule>
          {describedSeries.map((s, i) => (
            <Typography key={`${s.name}-${i}`} variant="body2" sx={BODY_SX}>
              <Box component="span" sx={{ fontWeight: 600 }}>
                {s.name}
                {s.unit && s.unit !== "%" ? ` (${s.unit})` : ""}
              </Box>
              {` — ${s.description}`}
            </Typography>
          ))}
        </>
      )}
      {noteLines.length > 0 && (
        <>
          <SectionRule>Notes</SectionRule>
          <Box
            component="ul"
            sx={{
              m: 0,
              p: 0,
              listStyle: "none",
              display: "flex",
              flexDirection: "column",
              gap: 0.75,
            }}
          >
            {noteLines.map((n, i) => (
              <Typography
                key={i}
                component="li"
                variant="body2"
                sx={{ display: "flex", gap: 1, lineHeight: 1.75 }}
              >
                <Box component="span" sx={{ color: "var(--chart-subtitle)" }}>
                  •
                </Box>
                <Box component="span">{n}</Box>
              </Typography>
            ))}
          </Box>
        </>
      )}
    </Box>
  );
}
