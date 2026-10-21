/**
 * SecClassificationNavExchange — inherited VARIANT of SecClassificationNav
 * (shares the ChipRow chrome via export) that shows EXCHANGES ONLY.
 *
 * Used by the Margin Trends page's "Exchange" attribution: instead of the
 * sector → industry tree, the nav collapses to ONE "Exchange" row with the
 * two exchanges that have market-wide margin summary data
 * (analysis.exchange_margin_summary):
 *   SS — 上交所 (SSE)   SZ — 深交所 (SZSE)
 *
 * Single-select, no deselect (the plotted entity IS the exchange's own
 * market-wide total curve; the page defaults to SS on first switch). The
 * values match the DB exchange column and the canonical source-CSV
 * exchange values — a subset of classify.ts PRIMARY_EXCHANGE_OPTIONS
 * (BSE has no margin summary data).
 */
import { Box, Chip, CircularProgress, Stack, Typography, type SxProps } from "@mui/material";
import { ChipRow } from "./ChipRow";

/** The exchanges with margin summary rows, in display order. */
const EXCHANGE_MARGIN_OPTIONS: Array<{ value: string; label: string }> = [
  { value: "SS", label: "SSE 上交所" },
  { value: "SZ", label: "SZSE 深交所" },
];

interface Props {
  /** Active exchange code ("SS" | "SZ") or null (nothing chosen yet). */
  exchange: string | null;
  onExchangeChange: (ex: string) => void;
  /** Inline spinner at the row end while the series data (re)fetches —
   *  mirrors SecClassificationNav's `loading` contract. */
  loading?: boolean;
  /** Chip size for the exchange chips. */
  chipSize?: "small" | "medium";
  /** MUI sx prop for the outer Stack container (same placement as the
   *  base nav's sx so the two variants are layout-identical). */
  sx?: SxProps;
}

export default function SecClassificationNavExchange({
  exchange,
  onExchangeChange,
  loading = false,
  chipSize = "small",
  sx,
}: Props) {
  const chipFontSize = chipSize === "small" ? "0.7rem" : "0.8rem";
  const rowSpacing = 0.75;
  return (
    <Stack
      spacing={rowSpacing}
      sx={{
        p: 1.5,
        mb: 2,
        bgcolor: "background.paper",
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1.5,
        ...sx,
      }}
    >
      <ChipRow label="Exchange" chipSize={chipSize}>
        {EXCHANGE_MARGIN_OPTIONS.map((opt) => (
          <Chip
            key={opt.value}
            label={opt.label}
            size={chipSize}
            color={exchange === opt.value ? "primary" : "default"}
            variant={exchange === opt.value ? "filled" : "outlined"}
            onClick={() => onExchangeChange(opt.value)}
            sx={{ fontSize: chipFontSize }}
          />
        ))}
        {loading && (
          <Box sx={{ display: "inline-flex", alignItems: "center", ml: "auto", pl: 1 }}>
            <CircularProgress size={14} sx={{ color: "text.secondary" }} />
          </Box>
        )}
        <Typography variant="caption" color="text.secondary" sx={{ fontSize: chipFontSize }}>
          market-wide official totals (analysis.exchange_margin_summary)
        </Typography>
      </ChipRow>
    </Stack>
  );
}
