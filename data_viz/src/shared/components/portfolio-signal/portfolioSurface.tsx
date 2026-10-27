/**
 * PortfolioSurface — the PORTFOLIO tier's own surface: reads ONLY the
 * portfolio-tier tables (portfolio_signal_rules construction +
 * history_portfolio_signal_performance audit). The PORTFOLIO ITSELF
 * lives here — never a cell, and it has NO action (only its cell
 * signals under it carry actions; the cell signals live on the
 * sibling CellRulesSurface). The audit (2026-10-25 correction: the
 * rules surface's per-cell performance matrix — which had rendered
 * the PAIR row as a cell — moved here as the portfolio's OWN
 * evidence): the ENTRY RULE is the trigger CONDITION (the
 * coordination spec under rule_params.subs — what makes the
 * portfolio's signals fire), the pooled COMBINED stats are the
 * MEASURED EVIDENCE (the orchestrating rows' measures
 * meta-combined — a result, not the rule), beside the gate verdict
 * with the member benefit boost and the max-drawdown benefit vs
 * the legs — the pair's whole point. The constant rule definition
 * (the structured rule params spec sheet) is its own TOP section of
 * the expansion (PortfolioRuleParams). The audit FOLDS: the header
 * row toggles, collapsed by default — the expansion runs rule params
 * → cell signals → this audit, the last section.
 */
import { useState } from "react";
import { Box, Typography } from "@mui/material";

import type { PortfolioSignalGroup } from "@/lib/api-client";

import KeyValueTable from "@/shared/components/key-value-table";
import { fmtPct, portfolioAudit, portfolioTriggerLines } from "./common";

/** The portfolio's own ENTRY-RULE audit block (the latest snapshot's
 *  pooled stats — the orchestrating rows' combined measures). */
interface PortfolioPooledAudit {
  n_episodes?: number | null;
  dir_ave_pct?: number | null;
  hit_rate?: number | null;
  t_stat?: number | null;
}

/** The signed pct word the pooled evidence line shares with the cell
 *  rows (+/−, 2dp). */
const signedPct = (v: number | null | undefined): string =>
  v == null ? "—" : `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(2)}%`;

/** The PORTFOLIO surface of one matched portfolio RULE (the
 *  2026-10-25 sub split: the row IS one rule — trio wash or spread —
 *  so its OWN subs' lines render, no host-sub narrowing): its OWN
 *  audit — the entry rule's trigger condition, the measured evidence,
 *  the gate + benefit boost, the headline max-drawdown benefit. Pure
 *  over its props; hosted inside the roster table's expansion as the
 *  LAST, FOLDED-BY-DEFAULT section (the header row toggles it). */
export default function PortfolioSurface({
  portfolio: p,
}: {
  portfolio: PortfolioSignalGroup;
}) {
  const [open, setOpen] = useState(false);
  const audit = portfolioAudit(p);
  const triggerLines = portfolioTriggerLines(p, null);
  const pooled = (p.performance?.pooled ?? null) as
    | PortfolioPooledAudit
    | null;
  const gate = (p.performance?.gate ?? null) as
    | { passed?: boolean; conf_scale?: number; metric?: string }
    | null;

  return (
    <Box sx={{ p: 1, px: 1.5, mb: 1, borderLeft: 3, borderColor: "primary.main" }}>
      <Box
        onClick={() => setOpen((o) => !o)}
        sx={{
          display: "flex",
          alignItems: "center",
          cursor: "pointer",
          borderRadius: 0.5,
          px: 0.25,
          "&:hover": { bgcolor: "action.hover" },
        }}
      >
        <Typography
          variant="caption"
          sx={{ fontWeight: 700, display: "block" }}
        >
          {`Portfolio Performance${
            p.performance?.as_of != null ? ` · ${p.performance.as_of}` : ""
          }`}
        </Typography>
        <Box
          component="span"
          sx={{ ml: "auto", fontSize: "0.7rem", color: "text.disabled" }}
        >
          {open ? "▾" : "▸"}
        </Box>
      </Box>
      {open && (
        <>
          <KeyValueTable
            rows={[
              {
                label: "entry rule",
                value: triggerLines.length > 0
                  ? (
                    <Box sx={{ display: "grid", gap: 0.25 }}>
                      {triggerLines.map((l) => (
                        <Typography
                          key={l.word}
                          variant="caption"
                          color="text.secondary"
                          sx={{ display: "block", whiteSpace: "normal" }}
                        >
                          <Box
                            component="span"
                            sx={{ fontWeight: 700, color: "text.primary" }}
                          >
                            {l.word}
                          </Box>
                          {` — ${l.text}`}
                        </Typography>
                      ))}
                    </Box>
                  )
                  : "—",
              },
              pooled != null && {
                label: "evidence",
                value: pooled.n_episodes != null
                  ? `${pooled.n_episodes} episodes · dir_ave ${
                      signedPct(pooled.dir_ave_pct)
                    } · hit ${
                      pooled.hit_rate != null
                        ? `${(pooled.hit_rate * 100).toFixed(0)}%`
                        : "—"
                    } · t ${
                      pooled.t_stat != null ? pooled.t_stat.toFixed(2) : "—"
                    }`
                  : "—",
              },
              pooled != null && {
                label: "gate",
                value: `${gate?.passed === true ? "passed" : "pending"}${
                  gate?.conf_scale != null
                    ? ` · member benefit boost ×${gate.conf_scale.toFixed(2)}`
                    : ""
                }`,
              },
            ]}
          />
          {audit != null && (
            <KeyValueTable
              rows={[
                {
                  label: "max DD",
                  value: (
                    <Box component="span">
                      {`${fmtPct(audit.maxDd.portfolio_pct)} vs legs ${fmtPct(
                        audit.maxDd.leg_a_pct,
                      )} / ${fmtPct(
                        audit.maxDd.leg_b_pct,
                      )} — `}
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
                },
              ]}
            />
          )}
        </>
      )}
    </Box>
  );
}
