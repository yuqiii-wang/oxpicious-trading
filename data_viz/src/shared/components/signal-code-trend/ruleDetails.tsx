/**
 * ruleDetails — the shared RULE DETAILS render of the analysis
 * signals scope (pure over a SignalRule row — module-scope so callers
 * can pass it identity-stable into ExpandedTable's expandedContent).
 * The 2026-10-21 text diet: ONE prose sentence (the family document's
 * sub-signal meaning) over ONE KeyValueTable — the pair/rule
 * construction rows (legs · response · trigger · gate · scale ·
 * episodes · entry) with the latest measured performance snapshot as
 * the captioned second section (verdict · evidence · confidence).
 *
 * Consumers: SignalRulesPanel (the per-code rules panel — Recent
 * Movements / Options) and PortfolioSignalRulesPanel (the portfolio
 * tier's coordinated member rows — Hedge Pairs) render the SAME
 * block, so a rule's story reads identically on every surface.
 */
import type { ReactNode } from "react";
import { Box } from "@mui/material";

import type { SignalRule } from "@/lib/api-client";

import KeyValueTable, {
  type KeyValueRow,
  MeaningLine,
} from "@/shared/components/key-value-table";

/** pct with a unicode minus, 2dp — null-safe for the digest lines. */
export const fmtPct = (v: number | null | undefined): string | null =>
  v == null ? null : `${v >= 0 ? "" : "−"}${Math.abs(v).toFixed(2)}%`;

/** rule_params.response → the response leg's words (the portfolio
 *  families' coordination vocabulary; anything else → no row). */
const RESPONSE_WORDS: Record<string, string> = {
  inverse_vol_pair_portfolio: "the inverse-vol pair portfolio (both legs)",
  strong_leg: "the strong leg — sign(ratio_z) picks it same day",
  laggard_leg: "the laggard leg — sign(ratio_z) picks it same day",
  own_price: "the cell's own price",
};

/** The rule's details — the one-sentence meaning, the construction
 *  table, and the measured performance section. */
export function ruleDetails(r: SignalRule): ReactNode {
  const knowledge = r.document.sub_signals?.[r.signal_sub_type] ?? null;
  const gate = r.rule_params.gate;
  const ungated = gate?.ungated === true;
  const conf = r.rule_params.confidence;
  const reentry = [
    r.rule_params.reentry_trend_split === true ? "trend-aware" : null,
    r.rule_params.reentry_cell_split === true ? "cell-split" : null,
  ].filter(Boolean).join(" · ");
  const response = r.rule_params.response != null
    ? RESPONSE_WORDS[String(r.rule_params.response)] ?? null
    : null;
  // (the variant-axes boost stamps — rule_params.pe_boosted /
  // ohlcv_boosted — left the rows with the 2026-11-01 axes unlink)
  // composite pair codes carry the legs in the code itself ('A~B')
  const legs = r.code.includes("~")
    ? r.code.split("~").join(" + ")
    : null;

  const construction: Array<KeyValueRow | null> = [
    legs != null ? { label: "legs", value: legs } : null,
    response != null ? { label: "response", value: response } : null,
    {
      label: "trigger",
      value: knowledge?.trigger != null
        ? String(knowledge.trigger)
        : "(see family doc)",
    },
    {
      label: "gate",
      value: ungated
        ? "observational (ungated)"
        : `dir_ave ≥ ${gate?.min_pct ?? "—"}% @ floor ${gate?.sample_floor ?? "—"}`,
    },
    conf?.depth_factor != null
      ? {
          label: "scale",
          value: `×${conf.depth_factor} of breach depth`
            + (conf.depth_cap != null ? ` · cap ×${conf.depth_cap}` : ""),
        }
      : null,
    {
      label: "episodes",
      value: `K=${String(r.rule_params.episode_merge_days ?? "—")}`
        + (reentry ? ` · re-entry ${reentry}` : ""),
    },
    {
      label: "entry",
      value: `${r.rule_entry} · emitted ${r.created_at}`,
    },
  ];

  // -- the measured half: the latest dated performance snapshot ----
  const m = r.rule_params.measure;
  const floor = gate?.sample_floor;
  const starved = !ungated && floor != null && m?.n_episodes != null
    && m.n_episodes < floor;
  const confScale = starved && floor
    ? Math.min((m?.n_episodes ?? 0) / floor, 1)
    : null;
  const evidence: string[] = [];
  if (m?.n_episodes != null) evidence.push(`n=${m.n_episodes}`);
  if (m?.dir_ave_pct != null) {
    evidence.push(`▲ ${fmtPct(m.dir_ave_pct)}`);
  }
  if (m?.hit_rate != null) evidence.push(`hit ${(m.hit_rate * 100).toFixed(0)}%`);
  if (m?.dir_std_pct != null) evidence.push(`σ ${fmtPct(m.dir_std_pct)}`);
  if (m?.t_stat != null) evidence.push(`t ${m.t_stat.toFixed(2)}`);

  const performance: Array<KeyValueRow | null> = [
    {
      label: "verdict",
      value: ungated
        ? "observational — never a trading row"
        : r.is_valid
          ? "gate PASSED"
            + (confScale != null && confScale < 1
              ? ` · conf ×${confScale.toFixed(2)}`
              : "")
          : "pending",
    },
    evidence.length > 0 ? { label: "evidence", value: evidence.join(" · ") } : null,
    conf?.base_pct != null
      ? {
          label: "confidence",
          value: `base ${fmtPct(conf.base_pct)}`
            + (r.is_in_portfolio ? " · sourced by portfolio" : ""),
        }
      : r.is_in_portfolio
        ? { label: "confidence", value: "sourced by portfolio" }
        : null,
  ];

  return (
    <Box sx={{ p: 1, px: 1.5 }}>
      {knowledge?.meaning
        ? <MeaningLine>{String(knowledge.meaning)}</MeaningLine>
        : <MeaningLine>(no sub-signal knowledge in the document)</MeaningLine>}
      <KeyValueTable rows={construction} />
      <KeyValueTable
        rows={performance}
        caption={`performance — latest snapshot${
          r.performance_as_of ? ` (through ${r.performance_as_of})` : ""}`}
      />
    </Box>
  );
}
