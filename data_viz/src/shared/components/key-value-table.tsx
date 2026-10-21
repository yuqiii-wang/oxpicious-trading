/**
 * KeyValueTable — the compact label/value table the detail surfaces
 * render instead of prose lines: one row per construction fact (the
 * label column carries the fact name, the value column the fact).
 * Rows are `{label, value}` pairs; a null entry is skipped (cheap
 * conditional rows); an optional caption renders as a spanning gray
 * section row inside the table. Dense, borderless between label and
 * value — reads as a spec sheet, not a form.
 */
import type { ReactNode } from "react";
import {
  Table,
  TableBody,
  TableCell,
  TableRow,
  Typography,
} from "@mui/material";

export interface KeyValueRow {
  label: string;
  value: ReactNode;
}

const LABEL_CELL_SX = {
  component: "th" as const,
  scope: "row" as const,
  align: "left" as const,
  sx: {
    color: "text.secondary",
    fontSize: "0.7rem",
    whiteSpace: "nowrap",
    width: 96,
    pr: 1.5,
    py: 0.25,
    verticalAlign: "top",
    border: "none",
  },
};

const VALUE_CELL_SX = {
  align: "left" as const,
  sx: {
    fontSize: "0.75rem",
    py: 0.25,
    verticalAlign: "top",
    border: "none",
  },
};

export default function KeyValueTable({
  rows,
  caption,
}: {
  rows: ReadonlyArray<KeyValueRow | null>;
  /** A section caption rendered as a spanning gray row inside the
   *  table (the performance half of a details block). */
  caption?: string;
}) {
  const present = rows.filter((r): r is KeyValueRow => r != null);
  if (present.length === 0 && caption == null) return null;
  return (
    <Table size="small" sx={{ display: "block" }}>
      <TableBody>
        {caption != null && (
          <TableRow>
            <TableCell
              colSpan={2}
              sx={{
                color: "text.secondary",
                fontSize: "0.7rem",
                py: 0.25,
                border: "none",
                pb: 0,
              }}
            >
              {caption}
            </TableCell>
          </TableRow>
        )}
        {present.map((r) => (
          <TableRow key={r.label}>
            <TableCell {...LABEL_CELL_SX}>{r.label}</TableCell>
            <TableCell {...VALUE_CELL_SX}>{r.value}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

/** The one-sentence meaning line above a details table (body copy,
 *  the ONLY prose a detail block carries). */
export function MeaningLine({ children }: { children: ReactNode }) {
  return (
    <Typography variant="body2" sx={{ mb: 0.75 }}>
      {children}
    </Typography>
  );
}
