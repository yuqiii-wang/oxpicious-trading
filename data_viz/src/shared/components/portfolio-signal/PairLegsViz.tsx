/**
 * PairLegsViz — the portfolio signal's LEGS diagram: what legs a
 * coordinated cell coordinates, as chips instead of prose. The two
 * leg chips are joined by the response bond (⇄ the inverse-vol pair
 * portfolio; L/S the long-short spread curve — leg A long, leg B
 * short) or carry the role word (▲ strong / ▼ laggard —
 * sign(ratio_z) picks the leg same day); the market-witness chip
 * appends when the bar watches the broad market. The leg codes ride
 * the tooltip (the row stays text-free).
 */
import { Box, Chip, Tooltip, Typography } from "@mui/material";
import { Link } from "@mui/icons-material";

import type { SignalRuleParams } from "@/lib/api-client";

import {
  RESPONSE_ROLE_TEXT,
  pairResponseRole,
} from "./common";

/** Whether the bar kind watches the broad market (the market-witness
 *  chip): the RSI trio (market is the third witness), the market-high
 *  exit (the market IS the witness), the wash trio (the _mkt twin),
 *  the ONE-leg wash + market witness (2026-10-23), and the offset-ρ
 *  axis subs (ρ_off is the legs' excess OVER the market — the market
 *  leg is the ρ basis). */
function barWatchesMarket(barDef: SignalRuleParams["bar_def"]): boolean {
  if (!barDef || typeof barDef !== "object") return false;
  const kind = String(barDef.kind ?? "");
  return kind === "hp_entry_legs_rsi"
    || kind === "hp_exit_mkt_high"
    || kind === "hp_stop_regime"
    || kind === "hp_entry_rho50"
    || kind === "hp_entry_wash_mkt1"
    || (kind === "hp_entry_wash" && barDef.mkt_tail_pct != null);
}

export default function PairLegsViz({
  legA,
  legB,
  market,
  response,
  barDef,
}: {
  legA: string;
  legB: string;
  market?: string;
  /** rule_params.response — the raw engine words. */
  response: unknown;
  /** rule_params.bar_def — decides the market-witness chip. */
  barDef: SignalRuleParams["bar_def"];
}) {
  const role = pairResponseRole(response);
  if (role == null) return null;
  const watches = barWatchesMarket(barDef);
  const tooltip = (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 0.25 }}>
      <Typography variant="caption" sx={{ display: "block", whiteSpace: "nowrap" }}>
        {`legs ${legA} + ${legB}`}
        {market != null && ` · market ${market}`}
      </Typography>
      <Typography variant="caption" sx={{ display: "block", whiteSpace: "nowrap" }}>
        {RESPONSE_ROLE_TEXT[role]}
      </Typography>
    </Box>
  );
  const legChipSx = {
    fontSize: "0.6rem",
    height: 17,
    color: "text.secondary",
    borderColor: "divider",
    maxWidth: 110,
  } as const;
  return (
    <Tooltip title={tooltip} arrow>
      <Box sx={{
        display: "inline-flex", alignItems: "center", gap: 0.4,
        minWidth: 0, whiteSpace: "nowrap",
      }}>
        {role === "pair" || role === "spread" ? (
          <>
            <Chip size="small" variant="outlined" label={legA} sx={legChipSx} />
            <Link sx={{ fontSize: "0.8rem", color: "primary.main" }} />
            <Chip size="small" variant="outlined" label={legB} sx={legChipSx} />
            {role === "spread" && (
              <Box
                component="span"
                sx={{
                  fontSize: "0.65rem", fontWeight: 700, whiteSpace: "nowrap",
                  color: "text.secondary",
                }}
              >
                L/S
              </Box>
            )}
          </>
        ) : (
          <>
            <Chip size="small" variant="outlined" label={legA} sx={legChipSx} />
            <Chip size="small" variant="outlined" label={legB} sx={legChipSx} />
            <Box
              component="span"
              sx={{
                fontSize: "0.65rem", fontWeight: 700, whiteSpace: "nowrap",
                color: role === "strong" ? "success.main" : "warning.main",
              }}
            >
              {role === "strong" ? "▲ strong" : "▼ lag"}
            </Box>
          </>
        )}
        {watches && market != null && (
          <Chip
            size="small"
            variant="outlined"
            label="mkt"
            sx={{
              fontSize: "0.6rem", height: 17,
              color: "text.secondary", borderColor: "divider",
            }}
          />
        )}
      </Box>
    </Tooltip>
  );
}
