/**
 * PortfolioSignalHistoryPanel — the PORTFOLIO tier's HISTORY surface
 * (analysis_signals.portfolio_history_signals, the 2026-10-21 event
 * tier; the SignalHistoryPanel's portfolio sibling): ONE ROW PER EVENT
 * DAY, the day's coordinated PORTFOLIO events (the event classes —
 * entry · exit · tilt — with the strongest event's confidence). The
 * EVENT DATE is the day the coordination COMPLETED; the triggering
 * cell-level signals sit one click away: expanding a row renders the
 * day's event records AND the events' MEMBER cell signals — the
 * sourced component cells' own records, each WITH ITS OWN DATE
 * (different sub signals on different nearby days: the trio entry's
 * market capitulation up to ~5 traded days before the legs').
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
import ExpandedTable, {
  type ExpandedTableColumn,
} from "@/shared/components/ExpandedTable";
import { SignalRecordsTable } from "@/shared/components/signal-code-trend";
import { signalRecordName } from "@/shared/components/signal-code-trend/families";
import { tickDotItem } from "@/shared/components/signal-code-trend/tickDotItem";
import { regimeAccentColor } from "@/shared/charts/regimeBands";
import type {
  PortfolioEventMemberSignal,
  PortfolioHistorySignal,
  TradingSignal,
} from "@/lib/api-client";

import { subEventClasses } from "./common";

export interface PortfolioSignalHistoryPanelProps {
  /** The portfolio's coordinated EVENTS (the composite/pair-native
   *  rule cells' replayed triggers, each with its member cell-level
   *  signals), newest first — already sub-type-filtered by the host. */
  events: PortfolioHistorySignal[];
  /** The currently expanded event date (one at a time), or null. */
  expandedDate: string | null;
  /** Row click — set or clear the expansion. */
  onExpandedDateChange: (date: string | null) => void;
  /** Optional caller-supplied block rendered inside the panel above
   *  the caption. */
  headerExtra?: ReactNode;
}

/** One rendered row = one EVENT DAY: the day's coordinated portfolio
 *  events (classes + the strongest event's confidence); the group
 *  carries the day's event records AND their member cell-level
 *  signals (the expansion population, deduped across the day's
 *  events). NO action column: a portfolio day's actions are per-EVENT
 *  (the tilt buys the strong leg and sells the laggard the same day) —
 *  the event class is the row's vocabulary, the actions live in the
 *  expansion. */
interface EventDayRow {
  date: string;
  events: PortfolioHistorySignal[];
  /** The day's events' triggering cell signals, deduped across the
   *  events (an entry + a wash can share the market's witness). */
  members: PortfolioEventMemberSignal[];
  classes: string[];
  /** The day's highest-confidence event — the confidence column's
   *  number and the regime stamp. */
  rep: PortfolioHistorySignal;
}

/** The member records' fixed display order (the members fired on their
 *  own days — oldest witness first tells the coordination's story). */
const ACTION_ORDER = ["buy", "sell", "wait"];

/** Map one event/member record into the shared records-table row
 *  shape (the excess / excess-pct derivations are the history
 *  endpoint's). Both payloads carry the same event fields. */
function toRecord(x: Pick<PortfolioEventMemberSignal, "code"
  | "signal_type" | "signal_sub_type" | "action" | "date" | "signal"
  | "signal_reference" | "confidence"> & { time?: string }): TradingSignal {
  const excess = x.signal - x.signal_reference;
  return {
    code: x.code,
    code_name: null,
    sec_type: "index",
    signal_type: x.signal_type,
    signal_sub_type: x.signal_sub_type,
    date: x.date,
    time: x.time ?? "15:00",
    action: x.action,
    signal_excess: excess,
    signal_excess_pct: x.signal_reference !== 0
      ? (excess / Math.abs(x.signal_reference)) * 100
      : null,
    signal: x.signal,
    signal_threshold: x.signal_reference,
    confidence: x.confidence,
    confidence_pct: x.confidence / 100,
    is_day_close_trigger: true,
    regime_state: "all",
    is_ohlcv_boosted: false,
    is_typical: null,
    is_rejection: x.action === "wait",
    count_20d: 0,
  };
}

/** The expanded row's content — the day's event records + the events'
 *  member cell-level signals through the shared records table (the
 *  members carry their OWN dates — different sub signals on different
 *  nearby days). Module scope (pure over the group): stays
 *  identity-stable for the memoized shared table. */
function dateCellRecords(g: EventDayRow): ReactNode {
  return (
    <Box sx={{ p: 1, display: "flex", flexDirection: "column", gap: 1 }}>
      <SignalRecordsTable
        caption={
          `Portfolio event records on ${g.date} · ` +
          `${g.events.length} event${g.events.length === 1 ? "" : "s"}`
        }
        rows={g.events.map(toRecord)}
        showTime
      />
      {g.members.length > 0 ? (
        <SignalRecordsTable
          caption={
            "Triggering cell-level signals — the member components' " +
            "own records, each on its OWN day (the witnesses pair " +
            "within the coordination window)"
          }
          rows={g.members.map(toRecord)}
          showDate
          showCode
        />
      ) : (
        <Typography variant="caption" color="text.secondary" sx={{ px: 0.5 }}>
          Pair-native event — triggered by the pair&apos;s own gauge
          (no member cell signals).
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
  expandedDate,
  onExpandedDateChange,
  headerExtra,
}: PortfolioSignalHistoryPanelProps) {
  // One event row per event date, insertion-ordered (newest first —
  // the server order); the classes dedupe the day's events, the
  // members dedupe across them (code + sub + action + own date).
  const rows = useMemo<EventDayRow[]>(() => {
    const groups = new Map<string, PortfolioHistorySignal[]>();
    for (const e of events) {
      const group = groups.get(e.date);
      if (group) group.push(e);
      else groups.set(e.date, [e]);
    }
    return [...groups.entries()].map(([date, group]) => {
      const seen = new Set<string>();
      const members: PortfolioEventMemberSignal[] = [];
      for (const e of group) {
        for (const m of e.members) {
          const key = `${m.code}|${m.signal_sub_type}|${m.action}|${m.date}`;
          if (!seen.has(key)) {
            seen.add(key);
            members.push(m);
          }
        }
      }
      members.sort((a, b) =>
        a.date.localeCompare(b.date)
        || ACTION_ORDER.indexOf(a.action) - ACTION_ORDER.indexOf(b.action));
      return {
        date,
        events: group,
        members,
        classes: subEventClasses(group.map((e) => e.signal_sub_type)),
        rep: group.reduce(
          (a, b) => (b.confidence > a.confidence ? b : a),
          group[0]!,
        ),
      };
    });
  }, [events]);

  const handleRowClick = useCallback(
    (g: EventDayRow) => {
      onExpandedDateChange(expandedDate === g.date ? null : g.date);
    },
    [expandedDate, onExpandedDateChange],
  );

  const columns = useMemo<ExpandedTableColumn<EventDayRow>[]>(() => {
    return [
      {
        key: "date",
        label: "Date",
        render: (g) => g.date,
        filter: { type: "date", value: (g) => g.date },
      },
      {
        key: "event",
        label: "Event",
        info:
          "The day's PORTFOLIO events — the coordinated event classes " +
          "(entry: buy the pair · exit: harvest/catch-up/stop · tilt: " +
          "momentum shift); expand for the triggering cell-level signals",
        render: (g) => (
          <Box component="span" sx={{ fontWeight: 600, whiteSpace: "nowrap" }}>
            {g.classes.length > 0 ? g.classes.join(" · ") : "—"}
          </Box>
        ),
      },
      {
        key: "members",
        label: "Cells",
        align: "right",
        info:
          "The CELL-LEVEL signals that triggered the day's events — " +
          "the sourced component cells' own records, each on its OWN " +
          "day (different sub signals on different nearby days, within " +
          "the coordination window) — expand the row for them",
        render: (g) => (
          <Tooltip
            title={
              <Box sx={{ display: "flex", flexDirection: "column", gap: 0.25 }}>
                {g.members.map((m) => (
                  <Typography key={`${m.code}-${m.signal_sub_type}-${m.date}`}
                    variant="caption"
                    sx={{ display: "block", whiteSpace: "nowrap" }}
                  >
                    {signalRecordName(m.signal_type, m.signal_sub_type)}
                    {" — "}{m.code}{" · "}{m.action.toUpperCase()}
                    {" @ "}{m.date}
                  </Typography>
                ))}
                {g.members.length === 0 && (
                  <Typography variant="caption" sx={{ display: "block" }}>
                    pair-native event — no member cell signals
                  </Typography>
                )}
              </Box>
            }
            arrow
          >
            <Chip
              size="small"
              variant="outlined"
              label={`${g.members.length} cell${g.members.length === 1 ? "" : "s"}`}
              sx={{ fontSize: "0.65rem", height: 18 }}
            />
          </Tooltip>
        ),
      },
      {
        key: "confidence",
        label: "Confidence",
        align: "right",
        info:
          "The day's strongest event's scaled expected-move confidence " +
          "(basis points / 100, shown as a %); per-event and per-member " +
          "values in the expansion",
        render: (g) => `${g.rep.confidence_pct.toFixed(2)}%`,
      },
      {
        key: "regime",
        label: "regime",
        align: "center",
        info:
          "The event day's market regime (stats.market_regimes: " +
          "calm / hot / panic / quiet; pool = the regime-free " +
          "families' stamp) — recorded context, never a gate",
        filter: {
          type: "ticks",
          value: (g) => g.rep.regime_state,
          renderItem: regimeDot,
        },
        render: (g) => (
          <Box
            component="span"
            sx={{
              color: regimeAccentColor(g.rep.regime_state) ?? "text.disabled",
              fontWeight: 600,
            }}
          >
            {g.rep.regime_state === "calm"
              ? "·"
              : g.rep.regime_state === "all" ? "pool" : g.rep.regime_state}
          </Box>
        ),
      },
    ];
  }, []);

  return (
    <Paper variant="outlined" sx={{ p: 1.25 }}>
      {headerExtra}
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ display: "block", mb: 0.75, px: 0.5 }}
      >
        Portfolio history signals · {rows.length} event
        {rows.length === 1 ? "" : " days"} · {events.length} coordinated
        {" "}event{events.length === 1 ? "" : "s"} — each row dates the
        coordination&apos;s completion; click it to expand the triggering
        cell-level signals (their own days)
      </Typography>
      <ExpandedTable
        columns={columns}
        rows={rows}
        rowKey={(g) => g.date}
        maxHeight={300}
        enableFilters
        filterScopeDeps={[events]}
        onRowClick={handleRowClick}
        selectedRowKey={expandedDate}
        expandedRowKey={expandedDate}
        expandedContent={dateCellRecords}
        emptyState={
          <Typography variant="body2" color="text.secondary" sx={{ px: 0.5, pb: 1 }}>
            No recorded portfolio signals for this pair.
          </Typography>
        }
      />
    </Paper>
  );
}
