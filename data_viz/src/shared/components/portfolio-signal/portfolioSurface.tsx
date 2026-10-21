/**
 * PortfolioSurface — the PORTFOLIO tier's own surface: reads ONLY the
 * portfolio-tier tables (portfolio_signal_rules construction +
 * history_portfolio_signal_performance audit). WHAT THE PORTFOLIO
 * TOOK, rendered as the pair CONSTRUCTION TABLE (2026-10-21 text
 * diet): one KeyValueTable — legs · market · weighting · gate /
 * cascade · max-drawdown benefit · the members' combined stats · the
 * trough lens. The member cells live on the sibling CellRulesSurface.
 */
import { Box, Typography } from "@mui/material";

import type { PortfolioSignalGroup } from "@/lib/api-client";

import KeyValueTable, {
  type KeyValueRow,
} from "@/shared/components/key-value-table";
import { fmtPct, pairLegs, portfolioAudit } from "./common";

/** The PORTFOLIO surface of one matched portfolio: construction ·
 * benefit audit · the members' combined stats · the portfolio gate.
 * Pure over its props; hosted inside the roster table's expansion. */
export default function PortfolioSurface({
  portfolio: p,
}: {
  portfolio: PortfolioSignalGroup;
}) {
  const audit = portfolioAudit(p);
  const [legA, legB] = pairLegs(p.code);
  const construction = p.rule_params?.portfolio as
    | { legs?: string[]; market?: string; aim?: string }
    | undefined;
  const meta = p.rule_params ?? {};
  const gate = p.performance?.gate as
    | { passed?: boolean; conf_scale?: number; metric?: string }
    | null
    | undefined;
  const lens = p.performance?.dd as
    | { min20_pct?: number; baseline_pct?: number; n?: number }
    | null
    | undefined;
  // the members' COMBINED STATS (the audit's pooled block)
  const pooled = p.performance?.pooled as
    | { n_cells?: number; n_episodes?: number; dir_ave_pct?: number;
        t_stat?: number | null; hit_rate?: number | null }
    | null
    | undefined;
  const nSubs = Object.keys(
    (meta as { subs?: Record<string, unknown> }).subs ?? {},
  ).length;
  const passed = gate?.passed === true;
  const scale = Number(gate?.conf_scale ?? 1);

  const members: string[] = [];
  if (pooled?.n_cells != null) members.push(`${pooled.n_cells} cells`);
  if (pooled?.n_episodes != null) members.push(`n=${pooled.n_episodes}`);
  if (pooled?.dir_ave_pct != null) {
    members.push(
      `${pooled.dir_ave_pct >= 0 ? "+" : ""}${pooled.dir_ave_pct.toFixed(2)}%`,
    );
  }
  if (pooled?.t_stat != null) members.push(`t ${pooled.t_stat.toFixed(2)}`);
  if (pooled?.hit_rate != null) {
    members.push(`hit ${(pooled.hit_rate * 100).toFixed(0)}%`);
  }

  const rows: Array<KeyValueRow | null> = [
    construction
      ? { label: "legs", value: (construction.legs ?? [legA, legB]).join(" + ") }
      : null,
    construction?.market != null
      ? { label: "market", value: construction.market }
      : null,
    meta.pair_weighting != null
      ? { label: "weighting", value: String(meta.pair_weighting) }
      : null,
    nSubs > 0 ? { label: "subs", value: String(nSubs) } : null,
    {
      label: "gate",
      value: (
        passed
          ? `${String(gate?.metric ?? "benefit")} passed`
            + (scale > 1 ? ` · cascade ×${scale.toFixed(2)} members` : "")
          : "benefit below bar"
      )
        + (p.rule_entry ? ` · ${p.rule_entry}` : ""),
    },
    audit
      ? {
          label: "max DD",
          value: (
            <Box component="span">
              {`${fmtPct(audit.maxDd.portfolio_pct)} vs legs ${fmtPct(
                audit.maxDd.leg_a_pct,
              )} / ${fmtPct(audit.maxDd.leg_b_pct)} — `}
              <Box
                component="span"
                sx={{
                  fontWeight: 600,
                  color: audit.maxDd.reduction_vs_worst_pct >= 0
                    ? "success.main"
                    : "error.main",
                }}
              >
                {`${audit.maxDd.reduction_vs_worst_pct >= 0
                  ? "shallower" : "deeper"} ${Math.abs(
                  audit.maxDd.reduction_vs_worst_pct).toFixed(1)}pp vs worst`}
              </Box>
            </Box>
          ),
        }
      : null,
    members.length > 0
      ? { label: "members", value: members.join(" · ") }
      : null,
    lens?.min20_pct != null
      ? {
          label: "trough",
          value: `${fmtPct(lens.min20_pct)} vs ${fmtPct(lens.baseline_pct)}`
            + (lens.n != null ? ` · n=${lens.n}` : ""),
        }
      : null,
  ];

  return (
    <Box sx={{ p: 1, px: 1.5, mb: 1, borderLeft: 3, borderColor: "primary.main" }}>
      <Typography
        variant="caption"
        sx={{ fontWeight: 700, display: "block" }}
      >
        {`Pair portfolio${
          audit?.as_of != null ? ` · audited ${audit.as_of}` : ""}`}
      </Typography>
      <KeyValueTable rows={rows} />
    </Box>
  );
}
