/**
 * PortfolioSignalHistoryPanel — the PORTFOLIO tier's HISTORY surface
 * (analysis_signals.portfolio_history_signals, the 2026-10-21 event
 * tier; the SignalHistoryPanel's portfolio sibling): ONE ROW PER
 * PORTFOLIO SIGNAL — the coordinated composite event at its own event
 * date (the day the coordination completed), the row carrying the
 * event's identity (the orchestrating family's word + the coordinated
 * sub rule). Expanding a row renders THAT event's
 * coordinated CELL SIGNALS under it (2026-10-23, user-directed: the
 * cell signals sit under their RESPECTIVE portfolio signal, never
 * merged across a day's events) — the member records at their OWN
 * days under the member-action invariant: at least TWO actions on at
 * least TWO codes per event, different dates allowed. A member is a
 * record the TRIGGERED PORTFOLIO SIGNAL sets itself (the simplified
 * 2026-10-24 design: no sourcing from the standalone families' tiers)
 * — the hedge family's per-curve WITNESS records (each curve's
 * deepest-alive RSI-tail breach run start, on its OWN day); a legacy
 * row may still resolve a sourced cell event through cell_signals
 * (the documented fallback kind).
 *
 * Rendered on the SHARED ExpandedTable with the per-column header
 * filters (the Date range + the regime ticks, the per-code panel's
 * set), newest first — the server order.
 */
import { useCallback, useMemo, type ReactNode } from "react";
import {
  Box,
  Chip,
  Paper,
  Tooltip,
  Typography,
} from "@mui/material";
import { useDocs } from "@/shared/docs";
import ExpandedTable, {
  type ExpandedTableColumn,
} from "@/shared/components/ExpandedTable";
import { SignalRecordsTable } from "@/shared/components/signal-code-trend";
import {
  SIGNAL_TYPE_LABELS,
  signalRecordName,
  signalSubWord,
} from "@/shared/components/signal-code-trend/families";
import { tickDotItem } from "@/shared/components/signal-code-trend/tickDotItem";
import { regimeAccentColor } from "@/shared/charts/regimeBands";
import type {
  PortfolioEventMemberSignal,
  PortfolioHistorySignal,
} from "@/lib/api-client";

import {
  portfolioEventRowKey,
  portfolioMemberToRecord as toRecord,
  portfolioTypeLabel,
  subEventClass,
} from "./common";

export interface PortfolioSignalHistoryPanelProps {
  /** The portfolio's coordinated SIGNAL events (the composite/
   *  pair-native rule cells' replayed triggers, each with its member
   *  cell-level signals), newest first — already sub-type-filtered by
   *  the host. */
  events: PortfolioHistorySignal[];
  /** The currently expanded event row key (one at a time), or null. */
  expandedRow: string | null;
  /** Row click — set or clear the expansion. */
  onExpandedRowChange: (key: string | null) => void;
  /** Optional caller-supplied block rendered inside the panel above
   *  the caption. */
  headerExtra?: ReactNode;
}

/** One rendered row = ONE PORTFOLIO SIGNAL (a coordinated composite
 *  event) with its OWN member cell signals — the coordinated cells
 *  sit under their respective portfolio signal. */
export interface PortfolioEventRow {
  key: string;
  event: PortfolioHistorySignal;
  members: PortfolioEventMemberSignal[];
}

/** The member records' fixed display order (the members fired on their
 *  own days — oldest witness first tells the coordination's story). */
const ACTION_ORDER = ["buy", "sell", "wait"];

/** The expanded row's content — THIS portfolio signal's coordinated
 *  CELL SIGNALS through the shared records table (the sourced
 *  component cells' own records and the legs' witnesses on their OWN
 *  days, plus the pair response legs on the event day). Module scope
 *  (pure over the row): stays identity-stable for the memoized shared
 *  table. */
function eventCellRecords(row: PortfolioEventRow): ReactNode {
  const e = row.event;
  return (
    <Box sx={{ p: 1 }}>
      {row.members.length > 0 ? (
        <SignalRecordsTable
          caption={
            `Coordinated cell signals under ${signalRecordName(
              e.signal_type, e.signal_sub_type)}`
          }
          rows={row.members.map(toRecord)}
          showDate
          showCode
          splitSignal
        />
      ) : (
        <Typography variant="caption" color="text.secondary" sx={{ px: 0.5 }}>
          No cell signals recorded for this portfolio signal.
        </Typography>
      )}
    </Box>
  );
}

/** The regime ticks menu's accent-dot renderer (module scope — pure
 *  over the label, no theme read). */
const regimeDot = tickDotItem((v) => regimeAccentColor(v));

export default function PortfolioSignalHistoryPanel({
  events,
  expandedRow,
  onExpandedRowChange,
  headerExtra,
}: PortfolioSignalHistoryPanelProps) {
  // One row per PORTFOLIO SIGNAL, the server order (newest first);
  // each row carries ITS OWN member cell signals — never merged across
  // the day's other events.
  const rows = useMemo<PortfolioEventRow[]>(() => {
    return events.map((e) => {
      const members = [...e.members].sort((a, b) =>
        a.date.localeCompare(b.date)
          || ACTION_ORDER.indexOf(a.action) - ACTION_ORDER.indexOf(b.action));
      return { key: portfolioEventRowKey(e), event: e, members };
    });
  }, [events]);

  const handleRowClick = useCallback(
    (row: PortfolioEventRow) => {
      onExpandedRowChange(expandedRow === row.key ? null : row.key);
    },
    [expandedRow, onExpandedRowChange],
  );

  const docs = useDocs();
  const columns = useMemo<ExpandedTableColumn<PortfolioEventRow>[]>(() => {
    return [
      {
        key: "date",
        label: "Date",
        render: (row) => row.event.date,
        filter: { type: "date", value: (row) => row.event.date },
      },
      {
        key: "portfolio_type",
        label: "Portfolio signal type",
        info: docs.text("ui.portfolio-history.col.type"),
        render: (row) =>
          SIGNAL_TYPE_LABELS[row.event.signal_type] ??
          row.event.signal_type,
      },
      {
        key: "portfolio_sub_type",
        label: "Portfolio signal sub type",
        align: "center",
        info: docs.text("ui.portfolio-history.col.sub-type"),
        render: (row) => {
          const e = row.event;
          return (
            <Tooltip
              title={
                `${e.signal_sub_type} — ` +
                `${portfolioTypeLabel(e.portfolio_type)} · ` +
                `${subEventClass(e.signal_sub_type) ?? "event"}`
              }
              arrow
            >
              <Chip
                size="small"
                variant="outlined"
                label={signalSubWord(e.signal_type, e.signal_sub_type)}
                sx={{ fontSize: "0.65rem", height: 20, maxWidth: 220 }}
              />
            </Tooltip>
          );
        },
      },
      {
        key: "members",
        label: "Cell signals",
        align: "right",
        info: docs.text("ui.portfolio-history.col.members"),
        render: (row) => (
          <Tooltip
            title={
              <Box sx={{ display: "flex", flexDirection: "column", gap: 0.25 }}>
                {row.members.map((m) => (
                  <Typography key={`${m.code}-${m.signal_sub_type}-${m.date}`}
                    variant="caption"
                    sx={{ display: "block", whiteSpace: "nowrap" }}
                  >
                    {signalRecordName(m.signal_type, m.signal_sub_type)}
                    {" — "}{m.code}{" · "}{m.action.toUpperCase()}
                    {" @ "}{m.date}
                  </Typography>
                ))}
                {row.members.length === 0 && (
                  <Typography variant="caption" sx={{ display: "block" }}>
                    no cell signals recorded
                  </Typography>
                )}
              </Box>
            }
            arrow
          >
            <Chip
              size="small"
              variant="outlined"
              label={`${row.members.length} cell signal${
                row.members.length === 1 ? "" : "s"
              }`}
              sx={{ fontSize: "0.65rem", height: 18 }}
            />
          </Tooltip>
        ),
      },
      {
        key: "confidence",
        label: "Confidence",
        align: "right",
        info: docs.text("ui.portfolio-history.col.confidence"),
        render: (row) => `${row.event.confidence_pct.toFixed(2)}%`,
      },
      {
        key: "regime",
        label: "regime",
        align: "center",
        info: docs.text("ui.portfolio-history.col.regime"),
        filter: {
          type: "ticks",
          value: (row) => row.event.regime_state,
          renderItem: regimeDot,
        },
        render: (row) => (
          <Box
            component="span"
            sx={{
              color:
                regimeAccentColor(row.event.regime_state) ?? "text.disabled",
              fontWeight: 600,
            }}
          >
            {row.event.regime_state === "calm"
              ? "·"
              : row.event.regime_state === "all"
                ? "pool"
                : row.event.regime_state}
          </Box>
        ),
      },
    ];
  }, [docs]);

  return (
    <Paper variant="outlined" sx={{ p: 1.25 }}>
      {headerExtra}
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ display: "block", mb: 0.75, px: 0.5 }}
      >
        Portfolio history signals · {rows.length} coordinated portfolio
        {" "}signal{rows.length === 1 ? "s" : ""} — click a row to expand
        its cell signals
      </Typography>
      <ExpandedTable
        columns={columns}
        rows={rows}
        rowKey={(row) => row.key}
        maxHeight={300}
        enableFilters
        filterScopeDeps={[events]}
        onRowClick={handleRowClick}
        selectedRowKey={expandedRow}
        expandedRowKey={expandedRow}
        expandedContent={eventCellRecords}
        emptyState={
          <Typography variant="body2" color="text.secondary" sx={{ px: 0.5, pb: 1 }}>
            No recorded portfolio signals for this pair.
          </Typography>
        }
      />
    </Paper>
  );
}
