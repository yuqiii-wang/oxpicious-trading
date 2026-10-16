/**
 * SignalRulesPanel — the RULES surface of the signals era (2026-10-15):
 * ONE row per RULE CELL of the picked code (analysis_signals.signal_rules
 * — the (sub signal · trend · regime · action) key), showing the
 * SELF-ADAPTIVE prepared parameters the engine measured on the code's
 * OWN history for THAT cell: the breach bar, the measured evidence
 * (episodes · blended dir_ave · hit rate), the on-bar confidence base
 * and the ohlcv boost's trust-weighted base (ohlcv_boost_conf — null
 * where the side boosted nothing). Expanding a row surfaces the rule's
 * KNOWLEDGE — the family document's sub-signal meaning + trigger and
 * the cell's gate digest.
 *
 * Replaces the retired depth-response CURVE overlay (SignalOverlayPanel
 * + signalOverlayOption, retired with the signal_curves /
 * signal_thresholds tables 2026-10-14): where the overlay drew a fitted
 * μ(s) shape, the rules tier STATES the prepared bar and the measured
 * evidence per cell — the trend column IS the per-PK asymmetry axis
 * (the same sub signal measures different bars in different trends).
 *
 * Rendered on the SHARED ExpandedTable (2026-10-16 — the hand-rolled
 * MUI table retired): the expanded-table grammar + per-column HEADER
 * FILTERS (the trend / regime columns carry ticks menus — a colored
 * dot per label, matching the cells' own accents), the old header
 * tooltips became the shared InfoMark `info` descriptions, and the
 * knowledge expansion rides the shared table's caller-owned expansion
 * slot (expandedRowKey + expandedContent; an outside expansion scrolls
 * its row into view). The family pick is the HOST panel's (one pick
 * drives rules + markers + history).
 */
import { useCallback, useMemo, type ReactNode } from "react";
import { Box, Chip, Paper, Typography, useTheme } from "@mui/material";
import ExpandedTable, {
  type ExpandedTableColumn,
} from "@/shared/components/ExpandedTable";
import { SignalActionChip } from "@/shared/components/signal-action";
import { signalRecordName } from "./families";
import { ruleBarLabel, ruleCellKey } from "./ruleParams";
import { tickDotItem } from "./tickDotItem";
import { regimeAccentColor } from "@/shared/charts/regimeBands";
import type { SignalRule } from "@/lib/api-client";

export interface SignalRulesPanelProps {
  /** The code's rule cells (server-ordered: family, sub, trend, regime,
   *  action) — ALREADY family-filtered by the host's SIGNAL pick. */
  rules: SignalRule[];
  /** The currently expanded cell key ("sub|trend|regime|action"), one
   *  at a time; null = none. */
  expandedKey: string | null;
  onExpandedKeyChange: (key: string | null) => void;
}

/** The trend chip's palette slot per trend label (theme tokens — theme
 *  reactive; top/bottom are the overextended extremes, rise/fall the
 *  RSI re-entry momentum states, all the POOLED stamp the sparse
 *  options families stamp, unknown the unclassified bucket). */
const TREND_COLOR: Record<string, "error" | "success" | "info" | "warning"
  | "secondary"> = {
  top: "error",
  bottom: "success",
  rise: "info",
  fall: "warning",
  all: "secondary",
  unknown: "secondary",
};

/** The trend chip's SHADE GRADE by trend class — the chip backgrounds
 *  shade in step with the chart's trend bands (trendBands' graded
 *  depths): the overextended extremes (top/bottom — the trend-split
 *  cells' strongest populations) fill deepest, the directional states
 *  (rise/fall) one step lighter, all/unknown the faintest grey. */
const TREND_SHADE_ALPHA: Record<string, number> = {
  top: 0.20,
  bottom: 0.20,
  rise: 0.10,
  fall: 0.10,
  all: 0.06,
  unknown: 0.06,
};

/** "#RRGGBB" | "rgb(..)" → "rgba(.., a)" — the palette slots are plain
 *  CSS color strings at runtime. */
function withAlpha(color: string, alpha: number): string {
  const hex = /^#([0-9a-f]{6})$/i.exec(color.trim());
  if (hex) {
    const n = parseInt(hex[1], 16);
    return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
  }
  const rgb = /^rgb\((\d+),\s*(\d+),\s*(\d+)\)$/i.exec(color.trim());
  if (rgb) return `rgba(${rgb[1]}, ${rgb[2]}, ${rgb[3]}, ${alpha})`;
  return color;
}

/** The expanded row's KNOWLEDGE block — the family document's
 *  sub-signal meaning + trigger and the cell's gate digest. Module
 *  scope (pure over the rule row): the expandedContent prop stays
 *  identity-stable for the memoized shared table. */
function ruleKnowledge(r: SignalRule): ReactNode {
  const knowledge = r.document.sub_signals?.[r.signal_sub_type] ?? null;
  const gate = r.rule_params.gate;
  const ungated = gate?.ungated === true;
  return (
    <Box sx={{ p: 1, px: 1.5 }}>
      {knowledge ? (
        <>
          <Typography variant="body2" sx={{ mb: 0.25 }}>
            {String(knowledge.meaning ?? "")}
          </Typography>
          {knowledge.trigger != null && (
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ display: "block" }}
            >
              trigger: {String(knowledge.trigger)}
            </Typography>
          )}
        </>
      ) : (
        <Typography variant="caption" color="text.secondary">
          (no sub-signal knowledge in the document)
        </Typography>
      )}
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ display: "block", mt: 0.5 }}
      >
        episode merge K=
        {String(r.rule_params.episode_merge_days ?? "—")}
        {" · gate "}
        {ungated
          ? "observational (ungated)"
          : `dir_ave ≥ ${gate?.min_pct ?? "—"}% @ floor ${
              gate?.sample_floor ?? "—"
            }`}
        {" · scale ×"}
        {String(r.rule_params.confidence?.depth_factor ?? 0)}
        {" · entry "}
        {r.rule_entry}
        {" · measured "}
        {r.created_at}
      </Typography>
    </Box>
  );
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

  const handleRowClick = useCallback(
    (r: SignalRule) => {
      const key = ruleCellKey(r);
      onExpandedKeyChange(expandedKey === key ? null : key);
    },
    [expandedKey, onExpandedKeyChange],
  );

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
            // the chip's ramp reads the 1..100 (bp) scale
            // — feed base_pct × 100, not the raw pct
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
        info:
          "The per-cell gate verdict (is_valid): true iff the cell's " +
          "measured future returns cleared its side's dir_ave bar at " +
          "the sample floor (buy ≥ 0.25% @ n ≥ 3 / sell ≥ 0% @ n ≥ 2). " +
          "False = observational (the wait watchlist / the ungated " +
          "twin) — negative future returns are discouraged: never " +
          "valid, never trades, never fires live",
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
        render: (r) => (
          <Chip
            size="small"
            label={r.trend}
            sx={{
              height: 18,
              fontSize: "0.65rem",
              color: theme.palette[TREND_COLOR[r.trend] ?? "secondary"].main,
              borderColor:
                theme.palette[TREND_COLOR[r.trend] ?? "secondary"].main,
              bgcolor: withAlpha(
                theme.palette[TREND_COLOR[r.trend] ?? "secondary"].main,
                TREND_SHADE_ALPHA[r.trend] ?? 0.06,
              ),
            }}
          />
        ),
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
        render: (r) => (
          <Box
            component="span"
            sx={{
              color: regimeAccentColor(r.regime_state) ?? "text.disabled",
              fontWeight: 600,
            }}
          >
            {r.regime_state === "calm"
              ? "·"
              : r.regime_state === "all" ? "pool" : r.regime_state}
          </Box>
        ),
      },
      {
        key: "bar",
        label: "Bar",
        info:
          "The cell's SELF-ADAPTIVE breach bar (rule_params.bar_def) — " +
          "the engine picked it per (code, sub, trend, regime) from the " +
          "candidate grid on the code's own history",
        render: (r) => ruleBarLabel(r.rule_params.bar_def),
      },
      {
        key: "evidence",
        label: "Evidence",
        info:
          "The cell's measured evidence on its trigger days: episodes, " +
          "the sign-aligned blended dir_ave (0/3/5/10/20d fixed mix) " +
          "and the hit rate",
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
        key: "base",
        label: "Base",
        align: "right",
        info:
          "The on-bar confidence base in percent (the measured " +
          "dir_ave); the event's confidence amplifies it over the " +
          "breach depth (depth_factor, capped)",
        render: (r) =>
          r.rule_params.confidence?.base_pct != null
            ? `${r.rule_params.confidence.base_pct.toFixed(2)}%`
            : "—",
      },
      {
        key: "boost",
        label: "Boost",
        align: "right",
        info:
          "The ohlcv boost's trust-weighted confidence base in " +
          "percent — the cell's measured dir_ave re-weighted by the " +
          "day-0 anatomy trust score (sum w·y / sum w over the cell's " +
          "triggers). Null = the side boosted nothing (its derivation " +
          "failed the ship gates / the family did not opt in) — an " +
          "honest absence, never a gate",
        render: (r) => (
          <Box
            component="span"
            sx={{
              color:
                r.ohlcv_boost_conf == null
                  ? "text.disabled"
                  : r.ohlcv_boost_conf >= 0
                    ? theme.palette.success.main
                    : theme.palette.error.main,
              fontWeight: 600,
            }}
          >
            {r.ohlcv_boost_conf != null
              ? `${r.ohlcv_boost_conf.toFixed(2)}%`
              : "—"}
          </Box>
        ),
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
  }, [theme]);

  return (
    <Paper variant="outlined" sx={{ p: 1.25 }}>
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ display: "block", mb: 0.75, px: 0.5 }}
      >
        Signal rules · {nSubs} sub signal{nSubs === 1 ? "" : "s"} · {nCells}{" "}
        cell{nCells === 1 ? "" : "s"} · {nValid} valid (gate-passed, future
        returns ≥ the side&apos;s bar) — one per (sub · trend · regime ·
        action); bars re-measured on the code&apos;s own history — click a row
        for the rule&apos;s knowledge
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
        expandedContent={ruleKnowledge}
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
