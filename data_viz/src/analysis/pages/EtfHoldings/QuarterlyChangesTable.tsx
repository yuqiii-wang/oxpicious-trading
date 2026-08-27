/**
 * QuarterlyChangesTable — industry-level comparison across the TICKED
 * (selected) quarter bars of the ETF Holdings page's quarterly composition
 * chart.
 *
 * Rendered INSTEAD of the CompositionPieChart once MORE THAN ONE bar is
 * ticked: the single-season pie is replaced by a table listing every
 * industry's weight in each ticked season and how it changed:
 *   • one column per ticked quarter — the industry's weight NORMALIZED to %
 *     of that quarter's total composition (the same normalization the bars
 *     use, so the numbers agree with the chart);
 *   • one Δ column per CONSECUTIVE pair of ticked quarters
 *     (e.g. 2025Q4 → 2026Q1);
 *   • a final "Total Δ" column (first ticked → last ticked), tagged NEW /
 *     EXIT when the industry appears in / drops out of the composition
 *     between the first and last ticked season.
 *
 * Rows cover the UNION of industries across the ticked quarters — an
 * industry absent from a quarter renders "—" and counts as 0% in the deltas
 * — sorted by |Total Δ| desc so the biggest movers head the table. Each
 * industry keeps the bar chart's color dot (colorByIndustry) so rows can be
 * matched back to bar segments.
 *
 * Every row is CLICKABLE: clicking toggles an expansion row beneath it that
 * mounts the IndustryDrilldown — two additional plots (dual-axis industry
 * mean close vs % in this ETF, then the industry's member index curves).
 */
import { Fragment, useEffect, useMemo, useState } from "react";
import {
  Box,
  Chip,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
} from "@mui/material";
import {
  KeyboardArrowDown as KeyboardArrowDownIcon,
  KeyboardArrowUp as KeyboardArrowUpIcon,
} from "@mui/icons-material";
import {
  expandedTableBodyRowSx,
  expandedTableContainerSx,
  expandedTableHeadCellSx,
  expandedTableNumCellSx,
} from "@/shared/styles/expanded-table-styles";
import { UP_COLOR, DOWN_COLOR } from "@/theme/chart-palette";
import { fmtNum } from "@/lib/series";
import type { QuarterlyCompositionQuarter } from "@shared/types";
import IndustryDrilldown from "./IndustryDrilldown";

interface Props {
  /** ALL loaded quarters (chronological) — tickedIdxs index into this. */
  quarters: QuarterlyCompositionQuarter[];
  /** Indexes of the ticked bars, ascending (chronological order). */
  tickedIdxs: number[];
  /** Industry → color map shared with the bar chart (color dot per row). */
  colorByIndustry: Record<string, string>;
  /** Bare ETF code — passed through to the per-row IndustryDrilldown. */
  code: string;
  /** Notified whenever ANY row's drill-down expansion toggles — the parent
   *  uses this to grow this table panel into an overlay over the bar chart. */
  onExpandedChange?: (anyExpanded: boolean) => void;
}

/** Deltas with |v| below this many pp are treated as unchanged. */
const DELTA_EPS = 0.005;

/** Industry → normalized % of the quarter's TOTAL composition. */
function normalizedWeights(q: QuarterlyCompositionQuarter): Map<string, number> {
  const m = new Map<string, number>();
  for (const ind of q.industries) {
    m.set(
      ind.industry,
      q.total_weight_pct > 0 ? (ind.weight_pct / q.total_weight_pct) * 100 : 0,
    );
  }
  return m;
}

/** Signed percentage-point delta: "+1.2" / "-0.45" / "±0". */
function fmtDelta(v: number): string {
  if (Math.abs(v) < DELTA_EPS) return "±0";
  return (v > 0 ? "+" : "") + fmtNum(v, 2);
}

/** Semantic color for a delta (undefined = unchanged / muted). */
function deltaColor(v: number): string | undefined {
  if (Math.abs(v) < DELTA_EPS) return undefined;
  return v > 0 ? UP_COLOR : DOWN_COLOR;
}

/** One industry's row in the comparison table. */
interface ChangesRow {
  industry: string;
  /** Industry id from sec_classification ('' when 未分类) — drives the
   *  per-row IndustryDrilldown. */
  industryId: string;
  /** Per ticked quarter: normalized % — null when absent from that quarter. */
  values: Array<number | null>;
  /** Consecutive deltas (later − earlier; absent counts as 0). */
  deltas: number[];
  /** Last ticked − first ticked. */
  totalDelta: number;
}

export default function QuarterlyChangesTable({
  quarters,
  tickedIdxs,
  colorByIndustry,
  code,
  onExpandedChange,
}: Props) {
  // Industry labels whose drill-down expansion row is open.
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  // Notify the parent whenever the overlay state changes (ANY row open).
  useEffect(() => {
    onExpandedChange?.(expanded.size > 0);
  }, [expanded, onExpandedChange]);

  // The ticked quarters in chronological order.
  const ticked = useMemo(
    () => tickedIdxs.map((i) => quarters[i]).filter(Boolean),
    [quarters, tickedIdxs],
  );

  const rows = useMemo<ChangesRow[]>(() => {
    if (ticked.length < 2) return [];
    const maps = ticked.map(normalizedWeights);
    // Label → industry_id (first non-empty id across quarters wins).
    const ids = new Map<string, string>();
    for (const q of ticked) {
      for (const ind of q.industries) {
        if (ind.industry_id && !ids.has(ind.industry)) ids.set(ind.industry, ind.industry_id);
      }
    }
    const industries = new Set<string>();
    for (const m of maps) {
      for (const k of m.keys()) industries.add(k);
    }
    const out: ChangesRow[] = [];
    for (const industry of industries) {
      const values = maps.map((m) => {
        const v = m.get(industry);
        return v == null ? null : Number(v.toFixed(2));
      });
      const val = (v: number | null) => v ?? 0;
      const deltas: number[] = [];
      for (let i = 1; i < values.length; i++) {
        deltas.push(Number((val(values[i]) - val(values[i - 1])).toFixed(2)));
      }
      const totalDelta = Number(
        (val(values[values.length - 1]) - val(values[0])).toFixed(2),
      );
      out.push({ industry, industryId: ids.get(industry) ?? "", values, deltas, totalDelta });
    }
    // Biggest movers first (|Total Δ| desc); ties by latest weight desc.
    out.sort((a, b) => {
      const d = Math.abs(b.totalDelta) - Math.abs(a.totalDelta);
      if (d !== 0) return d;
      const la = a.values[a.values.length - 1] ?? 0;
      const lb = b.values[b.values.length - 1] ?? 0;
      return lb - la;
    });
    return out;
  }, [ticked]);

  if (ticked.length < 2) return null;

  const firstVals = rows.map((r) => r.values[0]);
  const lastVals = rows.map((r) => r.values[r.values.length - 1]);
  // Industry + quarter cols + consecutive Δ cols + Total Δ.
  const colCount = 1 + ticked.length + (ticked.length - 1) + 1;

  const toggleRow = (industry: string) => {
    setExpanded((cur) => {
      const next = new Set(cur);
      if (next.has(industry)) next.delete(industry);
      else next.add(industry);
      return next;
    });
  };

  return (
    <TableContainer sx={expandedTableContainerSx(420)}>
      <Table size="small" stickyHeader>
        <TableHead>
          <TableRow>
            <TableCell
              sx={{
                ...expandedTableHeadCellSx,
                position: "sticky",
                left: 0,
                zIndex: 3,
              }}
            >
              Industry
            </TableCell>
            {ticked.map((q) => (
              <TableCell key={q.quarter} align="right" sx={expandedTableHeadCellSx}>
                <Box>{q.quarter}</Box>
                <Box sx={{ fontSize: "0.58rem", fontWeight: 400, opacity: 0.8 }}>
                  {q.snapshot_date} · {q.n_holdings} hold.
                </Box>
              </TableCell>
            ))}
            {ticked.slice(1).map((later, i) => (
              <TableCell key={`delta-${i}`} align="right" sx={expandedTableHeadCellSx}>
                Δ {ticked[i].quarter}→{later.quarter}
              </TableCell>
            ))}
            <TableCell align="right" sx={expandedTableHeadCellSx}>
              Total Δ
            </TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.map((row, idx) => {
            const isNew = firstVals[idx] == null && lastVals[idx] != null;
            const isExit = firstVals[idx] != null && lastVals[idx] == null;
            const totalColor = deltaColor(row.totalDelta);
            const isOpen = expanded.has(row.industry);
            return (
              <Fragment key={row.industry}>
                <TableRow
                  hover
                  onClick={() => toggleRow(row.industry)}
                  sx={{
                    ...expandedTableBodyRowSx(idx),
                    cursor: "pointer",
                    "&:hover": { bgcolor: "action.hover" },
                    ...(isOpen ? { bgcolor: "action.selected" } : {}),
                  }}
                >
                  <TableCell
                    sx={{
                      ...expandedTableNumCellSx,
                      fontWeight: 600,
                      position: "sticky",
                      left: 0,
                      zIndex: 1,
                      bgcolor: idx % 2 === 0 ? "background.paper" : "action.hover",
                    }}
                  >
                    <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
                      {isOpen ? (
                        <KeyboardArrowUpIcon sx={{ fontSize: 14, opacity: 0.7 }} />
                      ) : (
                        <KeyboardArrowDownIcon sx={{ fontSize: 14, opacity: 0.7 }} />
                      )}
                      <Box
                        component="span"
                        sx={{
                          display: "inline-block",
                          width: 8,
                          height: 8,
                          borderRadius: "50%",
                          bgcolor: colorByIndustry[row.industry] ?? "transparent",
                          border: colorByIndustry[row.industry]
                            ? undefined
                            : "1px solid",
                          borderColor: "divider",
                          flexShrink: 0,
                        }}
                      />
                      {row.industry}
                    </Box>
                  </TableCell>
                  {row.values.map((v, i) => (
                    <TableCell key={i} align="right" sx={expandedTableNumCellSx}>
                      {v == null ? (
                        <Box component="span" sx={{ opacity: 0.4 }}>
                          —
                        </Box>
                      ) : (
                        fmtNum(v, 2)
                      )}
                    </TableCell>
                  ))}
                  {row.deltas.map((d, i) => (
                    <TableCell
                      key={`d-${i}`}
                      align="right"
                      sx={{
                        ...expandedTableNumCellSx,
                        ...(deltaColor(d) ? { color: deltaColor(d) } : {}),
                      }}
                    >
                      {fmtDelta(d)}
                    </TableCell>
                  ))}
                  <TableCell
                    align="right"
                    sx={{
                      ...expandedTableNumCellSx,
                      fontWeight: 700,
                      ...(totalColor ? { color: totalColor } : {}),
                    }}
                  >
                    {fmtDelta(row.totalDelta)}
                    {(isNew || isExit) && (
                      <Chip
                        label={isNew ? "NEW" : "EXIT"}
                        size="small"
                        color={isNew ? "success" : "default"}
                        variant="outlined"
                        sx={{
                          ml: 0.75,
                          height: 14,
                          fontSize: "0.55rem",
                          fontWeight: 700,
                          letterSpacing: "0.03em",
                        }}
                      />
                    )}
                  </TableCell>
                </TableRow>
                {isOpen && (
                  <TableRow>
                    <TableCell
                      colSpan={colCount}
                      sx={{
                        py: 0.5,
                        px: 1,
                        borderBottom: "1px solid",
                        borderColor: "divider",
                      }}
                    >
                      <IndustryDrilldown
                        etfCode={code}
                        industryId={row.industryId}
                        industryLabel={row.industry}
                        color={colorByIndustry[row.industry]}
                      />
                    </TableCell>
                  </TableRow>
                )}
              </Fragment>
            );
          })}
        </TableBody>
      </Table>
    </TableContainer>
  );
}
