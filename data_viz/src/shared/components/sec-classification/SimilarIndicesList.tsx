/**
 * SimilarIndicesList — expandable panel showing the top-5 similar codes,
 * top-5 similar industry-classified peer codes, and top-5 dissimilar
 * industry-classified peer codes by mutual shared composition weight,
 * shown beneath the Index (L3) chips in SecClassificationNav.
 *
 * Fetches /api/sec-composition/similar-indices?code=<index>, which reads
 * stats.sec_similars (sec_type='index', latest snapshot <= today).
 *
 * All three categories store SEC CODES (index codes). "industry" means the
 * peer pool is filtered to is_industry_not_strategy=true.
 *
 * When expanded, renders THREE rows in a CSS grid (column-aligned labels):
 *   1. Similar Index            — top-5 similar indices from ALL peers (clickable)
 *   2. Similar Index (Industry) — top-5 similar indices from industry-classified peers (clickable)
 *   3. Distant Index            — top-5 dissimilar indices from industry-classified peers (clickable)
 *
 * Collapsed by default to keep the nav compact.
 */
import { useEffect, useState, type ReactNode } from "react";
import { Alert, Box, Chip, CircularProgress, Stack, Typography } from "@mui/material";
import { ExpandMore, Scale } from "@mui/icons-material";
import { fetchSimilarIndices } from "@/lib/api-client";
import type { SimilarIndicesResponse, SimilarIndexRow } from "../../../../shared/types";

interface Props {
  /** Bare index code (e.g. "000300") — the selected subject index. */
  code: string;
  /** Chip size — passed through from the parent nav for visual consistency. */
  chipSize: "small" | "medium";
  /** Chip font size (px string) — passed through from the parent nav. */
  chipFontSize: string;
  /** Called when a similar-index chip is clicked — selects that index code. */
  onSelectedCode: (code: string) => void;
}

export default function SimilarIndicesList({
  code,
  chipSize,
  chipFontSize,
  onSelectedCode,
}: Props) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [data, setData] = useState<SimilarIndicesResponse | null>(null);

  useEffect(() => {
    if (!open || !code) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchSimilarIndices(code)
      .then((d) => {
        if (cancelled) return;
        setData(d);
        setLoading(false);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setError(e.message);
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, code]);

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 0.25 }}>
      {/* Toggle chip + "as of" date on the same row */}
      <Box sx={{ display: "flex", alignItems: "center", gap: 0.75 }}>
        <Chip
          label="Similar"
          size={chipSize}
          variant="outlined"
          onClick={() => setOpen((o) => !o)}
          icon={<Scale sx={{ fontSize: "0.9rem" }} />}
          deleteIcon={
            <ExpandMore
              sx={{
                fontSize: "1rem",
                transform: open ? "rotate(180deg)" : "none",
                transition: "transform 0.2s",
              }}
            />
          }
          onDelete={() => setOpen((o) => !o)}
          sx={{ fontSize: chipFontSize, pl: 0.5 }}
        />
        {data?.snapshot_date && (
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ fontSize: chipFontSize, whiteSpace: "nowrap" }}
          >
            as of {data.snapshot_date}
          </Typography>
        )}
      </Box>

      {/* Expanded panel: 3 rows, each = label + 5 rank cells in ONE grid so
          each rank (1..5) forms an aligned vertical column across all rows. */}
      {open && (
        <Box
          sx={{
            pl: 1,
            display: "grid",
            gridTemplateColumns: "auto repeat(5, max-content)",
            rowGap: 0.25,
            columnGap: 0.5,
            alignItems: "center",
          }}
        >
          {loading && (
            <>
              <Typography variant="caption" color="text.secondary" sx={{ fontSize: chipFontSize }}>
                Similar Index
              </Typography>
              <Stack direction="row" spacing={0.5} alignItems="center" sx={{ gridColumn: "2 / -1" }}>
                <CircularProgress size={12} />
                <Typography variant="caption" color="text.secondary" sx={{ fontSize: chipFontSize }}>
                  loading…
                </Typography>
              </Stack>
            </>
          )}
          {error && (
            <>
              <Typography variant="caption" color="text.secondary" sx={{ fontSize: chipFontSize }}>
                Similar Index
              </Typography>
              <Alert
                severity="error"
                sx={{
                  py: 0,
                  px: 1,
                  gridColumn: "2 / -1",
                  "& .MuiAlert-message": { fontSize: chipFontSize },
                }}
              >
                {error}
              </Alert>
            </>
          )}
          {data && !loading && (
            <>
              {/* Row 1: Similar codes (all peers) */}
              <Label text="Similar Index" chipFontSize={chipFontSize} />
              {renderRankCells(data.similars, 5, chipSize, chipFontSize, onSelectedCode)}

              {/* Row 2: Similar industry-classified peers */}
              <Label text="Similar Index (Industry)" chipFontSize={chipFontSize} />
              {renderRankCells(data.similar_industries, 5, chipSize, chipFontSize, onSelectedCode)}

              {/* Row 3: Dissimilar industry-classified peers */}
              <Label text="Distant Index" chipFontSize={chipFontSize} />
              {renderRankCells(data.dissimilar_industries, 5, chipSize, chipFontSize, onSelectedCode)}
            </>
          )}
        </Box>
      )}
    </Box>
  );
}

/** Grid-column label — fixed-width text that auto-aligns across rows. */
function Label({ text, chipFontSize }: { text: string; chipFontSize: string }) {
  return (
    <Typography
      variant="caption"
      color="text.secondary"
      sx={{ fontSize: chipFontSize, whiteSpace: "nowrap" }}
    >
      {text}
    </Typography>
  );
}

/**
 * Emit exactly `maxRanks` grid cells for one category row: one Chip per
 * SimilarIndexRow, plus empty placeholder <Box/> cells for any missing ranks
 * (1..maxRanks) so every category contributes the same number of cells and
 * the rank columns stay vertically aligned across the 3 rows.
 */
function renderRankCells(
  rows: SimilarIndexRow[],
  maxRanks: number,
  chipSize: "small" | "medium",
  chipFontSize: string,
  onSelectedCode: (code: string) => void,
) {
  const cells: ReactNode[] = rows.map((s) => (
    <Chip
      key={s.rank}
      label={`${s.code} ${s.name} (${s.sharing_weight_pct != null ? s.sharing_weight_pct.toFixed(1) : "—"}%)`}
      size={chipSize}
      variant="outlined"
      onClick={() => onSelectedCode(s.code)}
      title={`Rank ${s.rank} · click to select ${s.code}`}
      sx={{
        fontSize: chipFontSize,
        width: "100%",
        justifyContent: "flex-start",
        cursor: "pointer",
        borderColor: "divider",
        "&:hover": {
          bgcolor: "action.hover",
        },
      }}
    />
  ));
  for (let i = rows.length; i < maxRanks; i++) {
    cells.push(<Box key={`empty-${i}`} sx={{ width: "100%" }} />);
  }
  return cells;
}
