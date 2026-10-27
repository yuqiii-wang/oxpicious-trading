/**
 * SignalRulesPanel — the RULES surface of the signals era (2026-10-15,
 * merged 2026-10-19): ONE row per RULE CELL of the picked code
 * (analysis_signals.signal_rules — the (sub signal · trend · regime ·
 * action) key). The table's columns show the CONSTANT rule — the sub
 * signal, the action chip (ramped by the measured base), the trend /
 * regime split axes, the breach BAR definition and the engine entry;
 * EXPANDING a row reveals the cell's measured PERFORMANCE — the latest
 * dated snapshot of analysis_signals.history_signal_performance
 * (merged per cell by the API): the gate verdict line, the evidence
 * (episodes · blended dir_ave · hit · σ · t), the POOLED evidence
 * tier's full block (decay curve + expanding-year legs), the
 * confidence base, the boost base and the as-of date — alongside the
 * family KNOWLEDGE (the document's sub-signal meaning + trigger) and
 * the constant parameter digest (K, reentry flags, gate bars, scale).
 *
 * Replaces the retired depth-response CURVE overlay (SignalOverlayPanel
 * + signalOverlayOption, retired with the signal_curves /
 * signal_thresholds tables 2026-10-14): where the overlay drew a fitted
 * μ(s) shape, the rules tier STATES the constant bar and the measured
 * evidence per cell — the trend column IS the per-PK asymmetry axis
 * (the same sub signal measures different bars in different trends).
 *
 * Rendered on the SHARED ExpandedTable: the expanded-table grammar +
 * per-column HEADER FILTERS (the trend / regime columns carry ticks
 * menus — a colored dot per label, matching the cells' own accents),
 * header tooltips are the shared InfoMark `info` descriptions, and the
 * expansion rides the shared table's caller-owned expansion slot
 * (expandedRowKey + expandedContent; an outside expansion scrolls its
 * row into view — the host chart's marker click bolds the same cell).
 * The family pick is the HOST panel's (one pick drives rules + markers
 * + history). The expansion block is the SHARED ruleDetails renderer
 * (./ruleDetails — the same block the portfolio tier's member rows
 * expand), so a rule's story reads identically on every surface.
 */
import { useCallback, useMemo } from "react";
import { Box, Paper, Typography, useTheme } from "@mui/material";
import { useDocs } from "@/shared/docs";
import ExpandedTable, {
  type ExpandedTableColumn,
} from "@/shared/components/ExpandedTable";
import { SignalActionChip } from "@/shared/components/signal-action";
import { regimeAccentColor } from "@/shared/charts/regimeBands";
import type { SignalRule } from "@/lib/api-client";
import {
  TREND_COLOR,
  RegimeWord,
  TrendChip,
} from "./cellContext";
import { signalRecordName } from "./families";
import { ruleBarLabel, ruleCellKey } from "./ruleParams";
import { ruleDetails } from "./ruleDetails";
import { tickDotItem } from "./tickDotItem";

export interface SignalRulesPanelProps {
  /** The code's rule cells (server-ordered: family, sub, trend, regime,
   *  action) — ALREADY family-filtered by the host's SIGNAL pick. The
   *  measured performance rides each row (the API-merged latest
   *  history_signal_performance snapshot). */
  rules: SignalRule[];
  /** The currently expanded cell key ("sub|trend|regime|action"), one
   *  at a time; null = none. Drives the expansion AND the host chart's
   *  bolded cell. */
  expandedKey: string | null;
  onExpandedKeyChange: (key: string | null) => void;
}

export default function SignalRulesPanel({
  rules,
  expandedKey,
  onExpandedKeyChange,
}: SignalRulesPanelProps) {
  const theme = useTheme();

  const nCells = rules.length;
  const nSubs = useMemo(
    () => new Set(rules.map((r) => r.signal_sub_type)).size,
    [rules],
  );
  const nValid = useMemo(
    () => rules.filter((r) => r.is_valid).length,
    [rules],
  );
  const asOf = useMemo(
    () =>
      rules.reduce<string | null>(
        (acc, r) => (r.performance_as_of ?? acc), null,
      ),
    [rules],
  );

  const handleRowClick = useCallback(
    (r: SignalRule) => {
      const key = ruleCellKey(r);
      onExpandedKeyChange(expandedKey === key ? null : key);
    },
    [expandedKey, onExpandedKeyChange],
  );

  const docs = useDocs();
  const columns = useMemo<ExpandedTableColumn<SignalRule>[]>(() => {
    const trendDot = tickDotItem(
      (v) => theme.palette[TREND_COLOR[v] ?? "secondary"].main,
    );
    const regimeDot = tickDotItem((v) => regimeAccentColor(v));
    return [
      {
        key: "sub",
        label: "Sub signal",
        render: (r) => (
          <Box component="span" sx={{ whiteSpace: "nowrap" }}>
            {signalRecordName(r.signal_type, r.signal_sub_type)}
            {typeof r.rule_params.kind === "string" && (
              <Box
                component="span"
                sx={{
                  color: "text.secondary",
                  ml: 0.5,
                  fontSize: "0.7rem",
                }}
              >
                {" "}
                · {String(r.rule_params.kind)}
              </Box>
            )}
          </Box>
        ),
      },
      {
        key: "action",
        label: "Action",
        render: (r) => (
          <SignalActionChip
            action={r.action}
            // the chip's ramp reads the 1..100 (bp) scale — feed the
            // measured base × 100 (the performance snapshot's dir_ave)
            confidence={
              (r.rule_params.confidence?.base_pct ?? 1) * 100
            }
            rejected={r.rule_params.gate?.ungated === true}
          />
        ),
      },
      {
        key: "valid",
        label: "Valid",
        align: "center",
        info: docs.text("ui.signal-rules.col.valid"),
        render: (r) =>
          r.is_valid ? (
            <Box
              component="span"
              sx={{ color: theme.palette.success.main, fontWeight: 700 }}
            >
              ✓
            </Box>
          ) : (
            <Box component="span" sx={{ color: "text.disabled" }}>
              —
            </Box>
          ),
      },
      {
        key: "trend",
        label: "trend",
        align: "center",
        filter: {
          type: "ticks",
          value: (r) => r.trend,
          renderItem: trendDot,
        },
        render: (r) => <TrendChip trend={r.trend} />,
      },
      {
        key: "regime",
        label: "regime",
        align: "center",
        filter: {
          type: "ticks",
          value: (r) => r.regime_state,
          renderItem: regimeDot,
        },
        render: (r) => <RegimeWord regime={r.regime_state} />,
      },
      {
        key: "bar",
        label: "Bar",
        info: docs.text("ui.signal-rules.col.bar"),
        render: (r) => ruleBarLabel(r.rule_params.bar_def),
      },
      {
        key: "evidence",
        label: "Evidence",
        info: docs.text("ui.signal-rules.col.evidence"),
        render: (r) => {
          const m = r.rule_params.measure;
          const ungated = r.rule_params.gate?.ungated === true;
          const dirAve = m?.dir_ave_pct;
          return (
            <Box component="span" sx={{ whiteSpace: "nowrap" }}>
              {m?.n_episodes != null && (
                <Box component="span" sx={{ mr: 0.75 }}>
                  n={m.n_episodes}
                </Box>
              )}
              {dirAve != null && (
                <Box
                  component="span"
                  sx={{
                    fontWeight: 600,
                    color: ungated
                      ? "text.secondary"
                      : dirAve >= 0
                        ? theme.palette.success.main
                        : theme.palette.error.main,
                    mr: 0.75,
                  }}
                >
                  {dirAve >= 0 ? "▲ " : "▼ "}
                  {dirAve.toFixed(2)}%
                </Box>
              )}
              {m?.hit_rate != null && (
                <Box component="span" sx={{ color: "text.secondary" }}>
                  hit {(m.hit_rate * 100).toFixed(0)}%
                </Box>
              )}
            </Box>
          );
        },
      },
      {
        key: "entry",
        label: "Entry",
        render: (r) => (
          <Typography variant="caption" color="text.secondary">
            {r.rule_entry.replace(/Entry$/, "")}
          </Typography>
        ),
      },
    ];
  }, [docs, theme]);

  return (
    <Paper variant="outlined" sx={{ p: 1.25 }}>
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ display: "block", mb: 0.75, px: 0.5 }}
      >
        Signal rules · {nSubs} sub signal{nSubs === 1 ? "" : "s"} · {nCells}{" "}
        cell{nCells === 1 ? "" : "s"} · {nValid} valid (gate-passed, future
        returns ≥ the side&apos;s bar) · measured through {asOf ?? "—"} — one
        per (sub · trend · regime · action); click a row for the rule&apos;s
        knowledge and its measured performance
      </Typography>
      <ExpandedTable
        columns={columns}
        rows={rules}
        rowKey={ruleCellKey}
        maxHeight={320}
        enableFilters
        filterScopeDeps={[rules]}
        onRowClick={handleRowClick}
        selectedRowKey={expandedKey}
        expandedRowKey={expandedKey}
        expandedContent={ruleDetails}
        emptyState={
          <Typography variant="body2" color="text.secondary" sx={{ px: 0.5, pb: 1 }}>
            No rule cells shipped for this code — run the signals pipeline to
            measure cells.
          </Typography>
        }
      />
    </Paper>
  );
}
