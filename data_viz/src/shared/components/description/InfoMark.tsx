/**
 * InfoMark — a small clickable info icon that opens a popover with a
 * titled description. The shared "what does this control mean" affordance:
 * embed it next to a button-group title / label (e.g. MA-Spread's
 * SectionLabel rows); clicking the mark opens the description with the
 * description kit's typography. Newlines in the description split into
 * paragraphs, same as DescriptionView's intro.
 */
import { useState } from "react";
import { Box, Popover, Typography } from "@mui/material";
import InfoOutlinedIcon from "@mui/icons-material/InfoOutlined";
import { splitParagraphs } from "./splitParagraphs";

export interface InfoMarkProps {
  /** Popover heading — usually the control/section's own title. */
  title: string;
  /** The description text — newlines split into paragraphs. */
  description: string;
}

export function InfoMark({ title, description }: InfoMarkProps) {
  // The anchor is the icon itself (an SVG element — not HTMLElement).
  const [anchorEl, setAnchorEl] = useState<SVGSVGElement | null>(null);
  return (
    <>
      <InfoOutlinedIcon
        aria-label={`About ${title}`}
        onClick={(e) => setAnchorEl(e.currentTarget)}
        sx={{
          fontSize: "0.85rem",
          ml: 0.25,
          verticalAlign: "middle",
          cursor: "pointer",
          color: anchorEl != null ? "primary.main" : "inherit",
          "&:hover": { color: "primary.main" },
        }}
      />
      <Popover
        open={anchorEl != null}
        anchorEl={anchorEl}
        onClose={() => setAnchorEl(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        transformOrigin={{ vertical: "top", horizontal: "left" }}
        PaperProps={{ sx: { maxWidth: 380, p: 1.25 } }}
      >
        <Typography variant="body2" sx={{ fontWeight: 600, mb: 0.5 }}>
          {title}
        </Typography>
        <Box sx={{ display: "flex", flexDirection: "column", gap: 0.75 }}>
          {splitParagraphs(description).map((p, i) => (
            <Typography
              key={i}
              variant="body2"
              color="text.secondary"
              sx={{ lineHeight: 1.75 }}
            >
              {p}
            </Typography>
          ))}
        </Box>
      </Popover>
    </>
  );
}
