/**
 * CellRulesSurface — the CELL tier's surface of one portfolio: reads
 * ONLY the signal-tier tables (signal_rules merged with each cell's
 * own measured evidence: n / dir_ave / hit). The member cells render
 * grouped AS THE PAIR: the two legs side by side with the catch-up
 * badge between them — ONE leg's action commits the pair, the OTHER
 * leg's witness should fire within ≤5 traded days; the market
 * witness, the pair-native cells and the orchestrating composites
 * follow. Rows are VIZ-FIRST (the 2026-10-21 text diet): the short
 * sub word + action chip + the PairLegsViz legs diagram + the
 * evidence — the full record name and the decoded bar ride the
 * sub-word chip's tooltip, the details stay one click away. The
 * portfolio-level verdicts live on the sibling PortfolioSurface.
 */
import type { ReactNode } from "react";
import { Box, Chip, Tooltip, Typography } from "@mui/material";
import { SyncAlt } from "@mui/icons-material";

import type { PortfolioSignalGroup, SignalRule } from "@/lib/api-client";

import { SignalActionChip } from "@/shared/components/signal-action";
import {
  signalRecordName,
  signalSubWord,
} from "@/shared/components/signal-code-trend/families";
import { ruleBarLabel } from "@/shared/components/signal-code-trend/ruleParams";
import { ruleDetails } from "@/shared/components/signal-code-trend/ruleDetails";

import PairLegsViz from "./PairLegsViz";
import { pairLegs, pairWitnessWindow, subOrderRank } from "./common";

/** One contained signal cell's compact row — clicking expands the
 *  SHARED ruleDetails block (knowledge + the latest measured
 *  performance snapshot). The row is viz-first, text-light: the SHORT
 *  sub word chip (full record name + decoded bar ride its tooltip),
 *  the action chip, the LEGS DIAGRAM (PairLegsViz — what legs the
 *  portfolio signal coordinates), and the evidence; hideCode drops
 *  the code chip (leg columns already carry the code in their
 *  header). */
function memberRow(
  r: SignalRule,
  open: boolean,
  onToggle: () => void,
  cascadeScale: number,
  portfolioCode: string,
  legs: { legA: string; legB: string; market: string },
  hideCode = false,
): ReactNode {
  const m = r.rule_params.measure;
  const role = String(r.rule_params.portfolio_role ?? "");
  const key = `${r.code}|${r.signal_sub_type}|${r.signal_rule_id}`;
  const bar = ruleBarLabel(r.rule_params.bar_def);
  const full = signalRecordName(r.signal_type, r.signal_sub_type);
  return (
    <Box key={key} sx={{ borderBottom: "1px solid", borderColor: "divider",
      "&:last-child": { borderBottom: "none" } }}>
      <Box
        onClick={onToggle}
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 1,
          flexWrap: "wrap",
          py: 0.35,
          cursor: "pointer",
          borderRadius: 0.5,
          px: 0.25,
          "&:hover": { bgcolor: "action.hover" },
        }}
      >
        {!hideCode && (
          <Chip
            size="small"
            label={r.code}
            variant={role === "component" ? "outlined" : "filled"}
            sx={{ fontSize: "0.65rem", height: 20 }}
          />
        )}
        <Tooltip
          title={`${full}${bar !== "—" ? ` — bar: ${bar}` : ""}`}
          arrow
        >
          <Chip
            size="small"
            variant={role === "component" ? "outlined" : "filled"}
            label={signalSubWord(r.signal_type, r.signal_sub_type)}
            sx={{ fontSize: "0.65rem", height: 20, maxWidth: 170 }}
          />
        </Tooltip>
        <SignalActionChip
          action={r.action}
          confidence={(r.rule_params.confidence?.base_pct ?? 1) * 100}
          rejected={r.rule_params.gate?.ungated === true}
        />
        {cascadeScale > 1 && (
          <Tooltip title={`portfolio cascade ×${cascadeScale.toFixed(2)} from ${portfolioCode}`}>
            <Chip
              size="small"
              label={`pf ×${cascadeScale.toFixed(2)}`}
              sx={{
                fontSize: "0.6rem", height: 18,
                color: "success.main",
                borderColor: "success.main",
              }}
              variant="outlined"
            />
          </Tooltip>
        )}
        <PairLegsViz
          legA={legs.legA}
          legB={legs.legB}
          market={legs.market}
          response={r.rule_params.response}
          barDef={r.rule_params.bar_def}
        />
        {m?.n_episodes != null && (
          <Box component="span" sx={{ fontSize: "0.7rem", color: "text.secondary" }}>
            n={m.n_episodes}
            {m.dir_ave_pct != null
              && ` · ${m.dir_ave_pct >= 0 ? "+" : ""}${m.dir_ave_pct.toFixed(2)}%`}
            {m.hit_rate != null && ` · hit ${(m.hit_rate * 100).toFixed(0)}%`}
          </Box>
        )}
        <Box
          component="span"
          sx={{
            fontSize: "0.75rem",
            fontWeight: 700,
            color: r.is_valid ? "success.main" : "text.disabled",
          }}
        >
          {r.is_valid ? "✓" : "—"}
        </Box>
        <Box
          component="span"
          sx={{ ml: "auto", fontSize: "0.7rem", color: "text.disabled" }}
        >
          {open ? "▾" : "▸"}
        </Box>
      </Box>
      {open && (
        <Box sx={{ px: 1, pb: 0.75 }}>
          {ruleDetails(r)}
        </Box>
      )}
    </Box>
  );
}

/** One leg's bordered column of the pair block: the leg code chip over
 *  its own component cells (code chip hidden — the header carries it). */
function LegColumn({
  leg,
  cells,
  row,
}: {
  leg: string;
  cells: SignalRule[];
  row: (r: SignalRule) => ReactNode;
}) {
  return (
    <Box sx={{
      border: "1px dashed", borderColor: "divider", borderRadius: 1,
      p: 0.5, minWidth: 0,
    }}>
      <Chip
        size="small"
        label={leg}
        color="primary"
        variant="outlined"
        sx={{ fontSize: "0.65rem", height: 20, mb: 0.25 }}
      />
      {cells.length > 0
        ? cells.map(row)
        : (
          <Typography variant="caption" color="text.disabled" sx={{ px: 0.25 }}>
            — no cells
          </Typography>
        )}
    </Box>
  );
}

/** The CELL surface: the portfolio's coordinated signal cells grouped
 *  AS THE PAIR — legs side by side joined by the ≤{w}d catch-up badge
 *  (one leg's action → the other leg's witness within the window),
 *  then the market witness, the pair-native cells and the
 *  orchestrating composites. subType (the host page's dropdown)
 *  filters the cells to ONE coordinated sub rule and hides the source
 *  witnesses (they belong to no single sub); null = all. */
export default function CellRulesSurface({
  portfolio,
  byId,
  ownRows,
  openMember,
  subType,
  onToggleMember,
}: {
  portfolio: PortfolioSignalGroup;
  byId: Map<number, SignalRule>;
  ownRows: SignalRule[];
  openMember: string | null;
  /** The selected coordinated sub rule; null = all. */
  subType?: string | null;
  onToggleMember: (key: string) => void;
}) {
  const members = portfolio.signal_rule_ids
    .map((id) => byId.get(id))
    .filter((r): r is SignalRule => r != null);
  const [legA, legB] = pairLegs(portfolio.code);
  const market = String(
    (portfolio.rule_params?.portfolio as { market?: string } | undefined)
      ?.market ?? "000300",
  );
  const witness = pairWitnessWindow(portfolio);
  // the portfolio-level GATED cascade: the confidence increase every
  // member takes once the portfolio passed the gate + an entry rule
  const cascadeScale = portfolio.is_valid
    ? Number(
        (portfolio.performance?.gate as
          | { conf_scale?: number }
          | undefined)?.conf_scale ?? 1,
      )
    : 1;
  const bySub = (a: SignalRule, b: SignalRule) =>
    subOrderRank(a.signal_sub_type) - subOrderRank(b.signal_sub_type)
    || a.signal_sub_type.localeCompare(b.signal_sub_type);
  // the sourced components grouped as the pair: leg A, leg B, the
  // market witness, and any residual codes (defensive) — visible only
  // in the unfiltered view (no sub selected)
  const components = subType == null
    ? members.filter((r) => r.rule_params.portfolio_role === "component")
    : [];
  const legACells = components.filter((r) => r.code === legA).sort(bySub);
  const legBCells = components.filter((r) => r.code === legB).sort(bySub);
  const marketCells = components
    .filter((r) => r.code === market)
    .sort(bySub);
  const restCells = components
    .filter((r) => r.code !== legA && r.code !== legB && r.code !== market)
    .sort((a, b) => a.code.localeCompare(b.code) || bySub(a, b));
  // the pair-native self-members + the ORCHESTRATING composites,
  // narrowed to the selected sub rule when one is picked
  const inSub = (r: SignalRule) => subType == null || r.signal_sub_type === subType;
  const pairNative = members
    .filter((r) => r.rule_params.portfolio_role !== "component" && inSub(r))
    .sort(bySub);
  const own = ownRows
    .filter((r) => !r.is_in_portfolio && inSub(r))
    .sort(bySub);
  const row = (r: SignalRule, hideCode = false) => memberRow(
    r,
    openMember === `${r.code}|${r.signal_sub_type}|${r.signal_rule_id}`,
    () => onToggleMember(
      `${r.code}|${r.signal_sub_type}|${r.signal_rule_id}`,
    ),
    cascadeScale,
    portfolio.code,
    { legA, legB, market },
    hideCode,
  );
  const groupHeader = (text: string) => (
    <Typography variant="caption" sx={{ fontWeight: 700, display: "block" }}>
      {text}
    </Typography>
  );
  return (
    <Box sx={{ p: 1, px: 1.5, borderLeft: 3, borderColor: "divider" }}>
      <Typography variant="caption" sx={{ fontWeight: 700, display: "block", mb: 0.5 }}>
        Coordinated cells
      </Typography>
      {(legACells.length > 0 || legBCells.length > 0) && (
        <Box sx={{ mb: 1 }}>
          {groupHeader(`Pair legs · paired ≤${witness}d`)}
          <Box sx={{
            display: "grid",
            gridTemplateColumns: "minmax(0,1fr) auto minmax(0,1fr)",
            gap: 1,
            alignItems: "stretch",
          }}>
            <LegColumn leg={legA} cells={legACells} row={(r) => row(r, true)} />
            <Tooltip
              title={`One leg's action commits the pair — the other leg's witness should fire within ≤${witness} traded days`}
            >
              <Box sx={{
                display: "flex", flexDirection: "column",
                alignItems: "center", justifyContent: "center", px: 0.25,
              }}>
                <SyncAlt sx={{ fontSize: "1rem", color: "primary.main" }} />
                <Typography sx={{
                  fontSize: "0.65rem", fontWeight: 700,
                  color: "primary.main", whiteSpace: "nowrap",
                }}>
                  ≤{witness}d
                </Typography>
              </Box>
            </Tooltip>
            <LegColumn leg={legB} cells={legBCells} row={(r) => row(r, true)} />
          </Box>
        </Box>
      )}
      {marketCells.length > 0 && (
        <Box sx={{ mb: 1 }}>
          {groupHeader(`Market witness · ${market}`)}
          {marketCells.map((r) => row(r, true))}
        </Box>
      )}
      {restCells.length > 0 && (
        <Box sx={{ mb: 1 }}>
          {groupHeader("Other components")}
          {restCells.map((r) => row(r))}
        </Box>
      )}
      {pairNative.length > 0 && (
        <Box sx={{ mb: 1 }}>
          {groupHeader(`Pair-native cells · ${pairNative.length}`)}
          {pairNative.map((r) => row(r, true))}
        </Box>
      )}
      {own.length > 0 && (
        <Box>
          {groupHeader(`Composites · ${own.length}`)}
          {own.map((r) => row(r, true))}
        </Box>
      )}
      {components.length === 0 && pairNative.length === 0 && own.length === 0 && (
        <Typography variant="caption" color="text.secondary">
          {subType != null
            ? `(no cells for ${subType})`
            : "(no member cells resolved — run the signals pipeline)"}
        </Typography>
      )}
    </Box>
  );
}
