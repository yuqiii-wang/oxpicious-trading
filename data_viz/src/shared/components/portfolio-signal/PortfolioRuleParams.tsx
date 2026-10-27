/**
 * PortfolioRuleParams — the portfolio's CONSTANT rule definition
 * (portfolio_signal_rules.rule_params) as a structured spec sheet:
 * every documented grammar key decoded into labeled rows, loaded from
 * the backend payload rather than hand-written prose. Sections:
 * the CONSTRUCTION block (rule_params.portfolio — legs, market, aim,
 * the opposite-score reading), the PER-SUB coordination specs
 * (rule_params.subs — kind, witnesses + their timing, the tail grid,
 * the response axis), and the shared CONSTANTS (universe,
 * pair_weighting, the episode K, the portfolio gate bars, the
 * member benefit-boost scale). Unknown top-level keys render as raw
 * JSON so the sheet stays honest when the engine grammar grows.
 * The FIRST section of the roster expansion (the rule definition
 * before the members and the audit) and it FOLDS: the header row
 * toggles, collapsed by default.
 */
import { useState } from "react";
import { Box, Typography } from "@mui/material";

import KeyValueTable, {
  type KeyValueRow,
} from "@/shared/components/key-value-table";

import { pairResponseRole, RESPONSE_ROLE_TEXT, subOrderRank } from "./common";

/** The portfolio gate bars (constants — the verdict digest rides the
 *  dated performance snapshots). */
interface PortfolioGateBars {
  metric?: unknown;
  min_pp?: unknown;
}

/** The member BENEFIT BOOST scale: the members' confidence increase
 *  is 1 + factor × min(benefit_pp / unit_pp, cap) on is_valid groups. */
interface PortfolioBenefitBoost {
  factor?: unknown;
  unit_pp?: unknown;
  cap?: unknown;
}

/** The CONSTRUCTION block. opposite_score / offset_rho are null on
 *  the pinned showcase pair when the offsets carry no reading. */
interface PortfolioConstruction {
  legs?: unknown;
  market?: unknown;
  aim?: unknown;
  opposite_score?: unknown;
  offset_rho?: unknown;
}

/** One sub's coordination spec (kind-first, the bar_def convention). */
interface CoordinationSpec {
  kind?: unknown;
  witnesses?: unknown;
  tail_pcts?: unknown;
  /** Legacy coordination kinds' catch-up window; the fresh trio is
   *  same-day and carries none. */
  witness_window?: unknown;
}

interface SubSpec {
  response?: unknown;
  coordination?: unknown;
  observational?: unknown;
}

/** pct list word: [1, 2, 3] → "1/2/3%". */
const pctList = (v: unknown): string | null =>
  Array.isArray(v) && v.length > 0 && v.every((x) => typeof x === "number")
    ? `${v.join("/")}%`
    : null;

/** Signed number with a unicode minus (the offset rho). */
const signedNum = (v: unknown): string | null =>
  typeof v === "number" && Number.isFinite(v)
    ? `${v < 0 ? "−" : ""}${Math.abs(v)}`
    : null;

/** The witness roles' words (the coordination spec's vocabulary). */
const WITNESS_WORDS: Record<string, string> = {
  leg_a: "leg A",
  leg_b: "leg B",
  market: "the broad market",
};

/** The witnesses list word: ["leg_a","leg_b","market"] →
 *  "leg A + leg B + the broad market"; null when absent/malformed. */
const witnessSubjects = (v: unknown): string | null =>
  Array.isArray(v) && v.length > 0
    ? v
        .map((w) =>
          typeof w === "string" ? (WITNESS_WORDS[w] ?? w) : String(w),
        )
        .join(" + ")
    : null;

/** The sub's response-axis word (the shared role vocabulary, the
 *  "response: " prefix dropped — the label carries it). */
const responseWord = (v: unknown): string => {
  const role = pairResponseRole(v);
  if (role != null) {
    return RESPONSE_ROLE_TEXT[role].replace(/^response:\s*/, "");
  }
  return typeof v === "string" ? v.replace(/_/g, " ") : "—";
};

export default function PortfolioRuleParams({
  ruleParams,
}: {
  ruleParams: Record<string, unknown>;
}) {
  const [open, setOpen] = useState(false);
  const gate = ruleParams.gate as PortfolioGateBars | undefined;
  const benefitBoost = ruleParams.confidence as
    | PortfolioBenefitBoost
    | undefined;
  const construction = ruleParams.portfolio as
    | PortfolioConstruction
    | undefined;
  const subs = (ruleParams.subs ?? {}) as Record<string, SubSpec>;
  const subKeys = Object.keys(subs).sort(
    (a, b) => subOrderRank(a) - subOrderRank(b) || a.localeCompare(b),
  );
  // the documented grammar keys; anything else renders as raw JSON so
  // a grown grammar still shows up instead of silently vanishing
  const known = new Set([
    "axis",
    "name",
    "universe",
    "pair_weighting",
    "episode_merge_days",
    "gate",
    "confidence",
    "portfolio",
    "subs",
  ]);
  const other = Object.keys(ruleParams)
    .filter((k) => !known.has(k))
    .map(
      (k): KeyValueRow => ({
        label: k.replace(/_/g, " "),
        value: JSON.stringify(ruleParams[k]),
      }),
    );

  const constructionRows: (KeyValueRow | null)[] = [];
  if (construction != null) {
    const legs = Array.isArray(construction.legs)
      ? construction.legs.map(String)
      : null;
    constructionRows.push(
      // the portfolio rule's human name (rule_params.name, 2026-10-25)
      typeof ruleParams.name === "string"
        ? { label: "name", value: ruleParams.name }
        : null,
      legs != null && legs.length === 2
        ? { label: "legs", value: `${legs[0]} + ${legs[1]}` }
        : { label: "legs", value: JSON.stringify(construction.legs) },
      typeof construction.market === "string"
        ? { label: "market", value: construction.market }
        : null,
      typeof construction.aim === "string"
        ? {
            label: "aim",
            value: construction.aim.replace(/_/g, " "),
          }
        : null,
      typeof construction.opposite_score === "number"
        ? {
            label: "opposite leg",
            value: `score ${construction.opposite_score}`
              + (typeof construction.offset_rho === "number"
                ? ` · ρ ${signedNum(construction.offset_rho)}`
                : ""),
          }
        : null,
    );
  }

  const constantsRows: (KeyValueRow | null)[] = [
    typeof ruleParams.universe === "string"
      ? { label: "universe", value: ruleParams.universe }
      : null,
    typeof ruleParams.pair_weighting === "string"
      ? { label: "pair weighting", value: ruleParams.pair_weighting }
      : null,
    typeof ruleParams.episode_merge_days === "number"
      ? {
          label: "episode merge",
          value: `${ruleParams.episode_merge_days}d absorb`,
        }
      : null,
    gate != null && typeof gate.metric === "string"
      ? {
          label: "portfolio gate",
          value: `${gate.metric.replace(/_/g, " ")} ≥ ${
            typeof gate.min_pp === "number" ? gate.min_pp : "—"
          }pp`,
        }
      : null,
    benefitBoost != null && typeof benefitBoost.factor === "number"
      ? {
          label: "benefit boost",
          value: `1 + ${benefitBoost.factor} × min(benefit_pp / ${
            typeof benefitBoost.unit_pp === "number"
              ? benefitBoost.unit_pp
              : "—"
          }, ${
            typeof benefitBoost.cap === "number" ? benefitBoost.cap : "—"
          })`,
        }
      : null,
  ];

  // no constant definition on the row — nothing to spec
  if (Object.keys(ruleParams).length === 0) return null;

  return (
    <Box sx={{ p: 1, px: 1.5, mb: 1, borderLeft: 3, borderColor: "divider" }}>
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
          Rule params
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
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ display: "block", mb: 0.5 }}
          >
            the constant rule definition
            (portfolio_signal_rules.rule_params)
          </Typography>
          {constructionRows.length > 0 && (
            <KeyValueTable rows={constructionRows} caption="Construction" />
          )}
          {subKeys.map((sub) => {
            const spec = subs[sub];
            const c = (spec?.coordination ?? {}) as CoordinationSpec;
            const w = typeof c.witness_window === "number"
              ? c.witness_window
              : null;
            const subjects = witnessSubjects(c.witnesses);
            const tails = pctList(c.tail_pcts);
            return (
              <KeyValueTable
                key={sub}
                caption={`Coordination · ${sub}${
                  spec?.observational === true ? " (observational)" : ""
                }`}
                rows={[
                  typeof c.kind === "string"
                    ? { label: "kind", value: c.kind }
                    : null,
                  {
                    label: "response",
                    value: responseWord(spec?.response),
                  },
                  subjects != null
                    ? {
                        label: "witnesses",
                        value: `${subjects} — ${
                          w != null ? `within ≤${w}d` : "same day"
                        }`,
                      }
                    : null,
                  tails != null ? { label: "tails", value: tails } : null,
                ]}
              />
            );
          })}
          <KeyValueTable rows={constantsRows} caption="Constants" />
          {other.length > 0 && <KeyValueTable rows={other} caption="Other" />}
        </>
      )}
    </Box>
  );
}
