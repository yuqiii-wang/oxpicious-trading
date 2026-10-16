/**
 * SignalHistoryPanel — the HISTORY surface of the signals era
 * (2026-10-15): the signal events the RULES replayed (ONE row per
 * SIGNAL DATE, the day's highest-confidence record the representative,
 * a "+N" chip for its collapsed siblings — each row CLICKABLE to expand
 * the date's full records; a chart marker click drives `expandedDate`
 * from outside and scrolls the row into view). The rules-era reading
 * of the columns:
 * "Reference" is the crossed cell bar (signal_reference — the firing
 * rule's prepared bar, denormalized per event), Excess the derived
 * value − reference, Confidence the cell's measured base amplified
 * over the breach depth. The optional `headerExtra` slot renders
 * caller-supplied family context inside the panel, above the caption.
 *
 * Rendered on the SHARED ExpandedTable (2026-10-16 — the hand-rolled
 * MUI table retired): the expanded-table grammar + per-column HEADER
 * FILTERS (the Date column's from/to menu doubles as the table's
 * ordering key — newest first by default, the previous server order;
 * the regime column carries a ticks menu with its accent dot), the old
 * header tooltips became the shared InfoMark `info` descriptions, and
 * the per-date records expansion (the shared SignalRecordsTable) rides
 * the shared table's caller-owned expansion slot.
 */
import { useCallback, useMemo, type ReactNode } from "react";
import {
  Box,
  Chip,
  Paper,
  Tooltip,
  Typography,
  useTheme,
} from "@mui/material";
import ExpandedTable, {
  type ExpandedTableColumn,
} from "@/shared/components/ExpandedTable";
import { SignalActionChip } from "@/shared/components/signal-action";
import { signalRecordName } from "./families";
import { tickDotItem } from "./tickDotItem";
import { regimeAccentColor } from "@/shared/charts/regimeBands";
import { SignalRecordsTable } from "@/shared/components/signal-code-trend";
import type { TradingSignal } from "@/lib/api-client";

export interface SignalHistoryPanelProps {
  /** The code's signal history (replayed from the rules), newest first
   *  (server-ordered); already family/population-filtered by the host. */
  signals: TradingSignal[];
  /** The currently expanded signal date (one at a time), or null. */
  expandedDate: string | null;
  /** Row click / outside marker click — set or clear the expansion. */
  onExpandedDateChange: (date: string | null) => void;
  /** Optional caller-supplied block rendered inside the panel above
   *  the caption — family-specific context that belongs WITH the
   *  history. */
  headerExtra?: ReactNode;
}

/** One rendered row = one signal DATE: the representative (the day's
 *  highest-confidence record) + its collapsed siblings (the expansion
 *  population). Insertion-ordered newest first — the server order. */
interface DateGroupRow {
  date: string;
  rep: TradingSignal;
  group: TradingSignal[];
}

/** The expanded row's content — the date's EVERY record through the
 *  shared per-config records table. Module scope (pure over the group):
 *  the expandedContent prop stays identity-stable for the memoized
 *  shared table. */
function dateRecords(g: DateGroupRow): ReactNode {
  return (
    <Box sx={{ p: 1 }}>
      <SignalRecordsTable
        caption={`All signals on ${g.date} · ${g.group.length} record${g.group.length === 1 ? "" : "s"}`}
        rows={g.group}
        showTime
      />
    </Box>
  );
}

export default function SignalHistoryPanel({
  signals,
  expandedDate,
  onExpandedDateChange,
  headerExtra,
}: SignalHistoryPanelProps) {
  const theme = useTheme();

  // One group per signal date, insertion-ordered (newest first — the
  // server order); the representative is the day's highest-confidence
  // record (the +N siblings surface in the expansion panel).
  const rows = useMemo<DateGroupRow[]>(() => {
    const groups = new Map<string, TradingSignal[]>();
    for (const s of signals) {
      const group = groups.get(s.date);
      if (group) group.push(s);
      else groups.set(s.date, [s]);
    }
    return [...groups.entries()].map(([date, group]) => ({
      date,
      group,
      rep: group.reduce(
        (a, b) => (b.confidence > a.confidence ? b : a),
        group[0]!,
      ),
    }));
  }, [signals]);

  const handleRowClick = useCallback(
    (g: DateGroupRow) => {
      onExpandedDateChange(expandedDate === g.date ? null : g.date);
    },
    [expandedDate, onExpandedDateChange],
  );

  const columns = useMemo<ExpandedTableColumn<DateGroupRow>[]>(() => {
    const regimeDot = tickDotItem((v) => regimeAccentColor(v));
    return [
      {
        key: "date",
        label: "Date",
        render: (g) => g.date,
        filter: { type: "date", value: (g) => g.date },
      },
      {
        key: "signal",
        label: "Signal",
        render: (g) => (
          <Box component="span" sx={{ whiteSpace: "nowrap" }}>
            {signalRecordName(g.rep.signal_type, g.rep.signal_sub_type)}
            {g.group.length > 1 && (
              <Tooltip
                title={
                  `+${g.group.length - 1} more config signalled ` +
                  "this date — expand for all: " +
                  g.group
                    .filter((x) => x !== g.rep)
                    .map((x) => signalRecordName(x.signal_type, x.signal_sub_type))
                    .join(", ")
                }
                arrow
              >
                <Chip
                  size="small"
                  label={`+${g.group.length - 1}`}
                  variant="outlined"
                  sx={{
                    ml: 0.5,
                    height: 18,
                    fontSize: "0.65rem",
                    verticalAlign: "middle",
                  }}
                />
              </Tooltip>
            )}
          </Box>
        ),
      },
      {
        key: "action",
        label: "Action",
        render: (g) => (
          <SignalActionChip
            action={g.rep.action}
            confidence={g.rep.confidence}
            rejected={g.rep.is_rejection === true}
          />
        ),
      },
      {
        key: "value",
        label: "Signal value",
        align: "right",
        render: (g) => g.rep.signal.toFixed(4),
      },
      {
        key: "reference",
        label: "Reference",
        align: "right",
        info:
          "The crossed cell bar — the firing rule's prepared " +
          "rule_params bar, denormalized per event " +
          "(history_signals.signal_reference)",
        render: (g) => g.rep.signal_threshold.toFixed(4),
      },
      {
        key: "excess",
        label: "Excess",
        align: "right",
        info:
          "value − reference: the signed breach depth past the " +
          "cell's own bar (buy breaches below → negative)",
        render: (g) => (
          <Box
            component="span"
            sx={{
              whiteSpace: "nowrap",
              color: g.rep.action === "buy"
                ? theme.palette.success.main
                : theme.palette.error.main,
              fontWeight: 600,
            }}
          >
            {g.rep.signal_excess >= 0 ? "▲ " : "▼ "}
            {g.rep.signal_excess.toFixed(4)}
            {g.rep.signal_excess_pct !== null &&
              g.rep.signal_excess_pct !== undefined && (
                <Box
                  component="span"
                  sx={{
                    color: theme.palette.text.secondary,
                    fontWeight: 400,
                    ml: 0.5,
                  }}
                >
                  ({g.rep.signal_excess_pct >= 0 ? "+" : ""}
                  {g.rep.signal_excess_pct.toFixed(2)}%)
                </Box>
              )}
          </Box>
        ),
      },
      {
        key: "confidence",
        label: "Confidence",
        align: "right",
        info:
          "The cell's measured base (the sign-aligned blended dir_ave " +
          "its rule shipped) amplified over the breach depth — basis " +
          "points / 100, shown as a %",
        render: (g) => `${g.rep.confidence_pct.toFixed(2)}%`,
      },
      {
        key: "regime",
        label: "regime",
        align: "center",
        info:
          "The breach day's market regime (stats.market_regimes: " +
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
  }, [theme]);

  return (
    <Paper variant="outlined" sx={{ p: 1.25 }}>
      {headerExtra}
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ display: "block", mb: 0.75, px: 0.5 }}
      >
        History signals · {rows.length} day{rows.length === 1 ? "" : "s"} ·{" "}
        {signals.length} record{signals.length === 1 ? "" : "s"} — replayed
        from the rules; click a row (or a chart marker) to expand the date&apos;s
        signals
      </Typography>
      <ExpandedTable
        columns={columns}
        rows={rows}
        rowKey={(g) => g.date}
        maxHeight={300}
        enableFilters
        filterScopeDeps={[signals]}
        onRowClick={handleRowClick}
        selectedRowKey={expandedDate}
        expandedRowKey={expandedDate}
        expandedContent={dateRecords}
        emptyState={
          <Typography variant="body2" color="text.secondary" sx={{ px: 0.5, pb: 1 }}>
            No recorded signals for this code.
          </Typography>
        }
      />
    </Paper>
  );
}
