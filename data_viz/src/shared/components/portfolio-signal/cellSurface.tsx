/**
 * CellRulesSurface — the CELL tier's surface of one PORTFOLIO RULE. THE
 * CELL DEFINITION (user-directed 2026-10-25): a CELL is a SIGNAL
 * UNDER the portfolio — NEVER the portfolio itself. Since the same
 * day's portfolio_sub_type SPLIT each surface renders ONE portfolio
 * rule (trio wash / spread — its own ids array, coordination spec and
 * audit), so the row's OWN subs render here (no host-sub filtering).
 *
 * The WITNESS MIGRATION (2026-10-25, user-directed "migrate
 * rsi·witness mkt / hp_rsi_witness_mkt, etc, from DB signals to rule
 * params json ... on portfolio level there is no Portfolio signal no
 * portfolio Sub type, but in rule params json should have all"):
 * the witness definitions (the per-curve cell-signal conditions +
 * their MAPPED SIGNALS — the registered mov_rsi sub types each
 * witness maps at its own curve, action-side aligned) live ONLY in
 * the portfolio rule_params JSON (rule_params.subs.<sub>.cell_signals
 * — engine-written; the top-level rule_params also carries the
 * portfolio_sub_type word, so the JSON is self-describing). At the
 * PORTFOLIO LEVEL the surface renders NO "Portfolio signal" group and
 * NO "Sub type" column — the only population is the MAPPED RSI
 * SIGNALS table: the registered cells retrieved through the row's
 * signal_rule_ids (real signal_rules rows riding the is_in_portfolio
 * TRUE tier — a SEPARATE row from any standalone twin; the flag is a
 * signal_rules PK member) laid out directly under ONE expandable
 * WITNESS group per sub — each witness row expanding to
 * ITS mapped registered cells CLASSIFIED BY RSI WINDOW (2026-10-25
 * user-refined "rsi10 and rsi14 both are mixed, there should have been
 * classification between rsi14 vs rsi10": a window group row —
 * RSI(14) the classic first, then RSI(10), RSI(6), window-desc over
 * the cells' own rule_params.window — sits between the witness and
 * its cells; duplication across witnesses/rules is fine — every
 * witness carries its own mapped_signals list).
 */
import { Fragment, useState } from "react";
import {
  Box,
  Chip,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
} from "@mui/material";

import type {
  PortfolioSignalGroup,
  SignalRule,
} from "@/lib/api-client";

import {
  signalRecordName,
  signalSubWord,
  signalSubWordLoose,
  signalTailSide,
} from "@/shared/components/signal-code-trend/families";
import {
  ruleDetails,
} from "@/shared/components/signal-code-trend/ruleDetails";
import {
  RegimeWord,
  TrendChip,
} from "@/shared/components/signal-code-trend/cellContext";
import { SignalActionChip } from "@/shared/components/signal-action";

import {
  subSpecCells,
  type SubCellSignalSpec,
} from "./common";


/** The CELL surface of one PORTFOLIO RULE: the MAPPED RSI SIGNALS
 *  table only — the registered mov_rsi cells (via signal_rule_ids)
 *  laid out directly under their stored witness definitions
 *  (rule_params.subs.<sub>.cell_signals, verbatim from the DB —
 *  conditions + mapped_signals). The portfolio signal itself and its
 *  sub types render nowhere here: the rule's whole definition lives
 *  in the sibling PortfolioRuleParams (the rule params JSON) and the
 *  roster row. */
export default function CellRulesSurface({
  portfolio,
  byId,
  openMember,
  onToggleMember,
}: {
  portfolio: PortfolioSignalGroup;
  byId: Map<number, SignalRule>;
  openMember: string | null;
  onToggleMember: (key: string) => void;
}) {
  // the MAPPED REGISTERED cells: real mov_rsi rule rows riding the
  // is_in_portfolio TRUE tier (a SEPARATE signal_rules row from any
  // standalone twin — the flag is a PK member there), retrieved
  // through the portfolio rule's signal_rule_ids ONLY (user-directed
  // "the signals must register and retrieve under signal rules"; they
  // are never portfolio rows themselves)
  const members = portfolio.signal_rule_ids
    .map((id) => byId.get(id))
    .filter((r): r is SignalRule => r != null);
  const mappedCells = members.filter(
    (r) => r.rule_params.portfolio_role === "component",
  );
  // the WITNESS groups — the engine-written condition records of the
  // rule's OWN subs (rule_params.subs.<sub>.cell_signals), verbatim,
  // ONE group per sub: the trio's THREE witness curves (market + both
  // legs — the per-RSI-window groups were removed 2026-10-26,
  // user-directed "there are RSI(10) · 3 witnesses vs RSI(14) · 3
  // witnesses, remove them"), the spread subs' two role legs each
  const subs = Object.keys(
    (portfolio.rule_params?.subs ?? {}) as Record<string, unknown>,
  );
  const witnessGroups: Array<{ sub: string; rows: SubCellSignalSpec[] }> = [];
  for (const sub of subs) {
    const rows = subSpecCells(portfolio, sub) ?? [];
    if (rows.length > 0) witnessGroups.push({ sub, rows });
  }
  // a witness's MAPPED cells: the registered rows at the witness's OWN
  // code whose sub type the witness's stored mapped_signals list names
  // ("ensure mapping" — engine-written, action-side aligned; an empty
  // legacy list falls back to the code match) — resolved ACTION-SIDE
  // ALIGNED through the witness's OWN stored action (user-directed
  // 2026-10-26 "if the rules say buy, why the registered cell signals
  // still have sell action"): the sub-type label alone is side-ambiguous
  // (mov_rsi registers the same sub type on BOTH sides), so matching the
  // name only leaked the sell twins under a buy witness. Legacy records
  // without the action key keep the name-only match.
  const mappedFor = (w: SubCellSignalSpec): SignalRule[] =>
    mappedCells.filter((r) => r.code === w.code
      && (w.mapped_signals.length === 0
        || w.mapped_signals.includes(r.signal_sub_type))
      && (w.action == null || r.action === w.action));
  // the WINDOW CLASSIFICATION inside one witness's mapped cells
  // (user-directed "rsi10 and rsi14 both are mixed, there should have
  // been classification between rsi14 vs rsi10"): the witness's cells
  // grouped by their own RSI window (the registered rows'
  // rule_params.window), window-desc — rsi14 the classic first, then
  // rsi10, rsi6; a row without a window rides the 0 bucket last
  const mappedWindows = (cells: SignalRule[]): Array<
    [number, SignalRule[]]
  > => {
    const byWin = new Map<number, SignalRule[]>();
    for (const r of cells) {
      const win = typeof r.rule_params.window === "number"
        ? r.rule_params.window : 0;
      const list = byWin.get(win) ?? [];
      list.push(r);
      byWin.set(win, list);
    }
    return [...byWin.entries()].sort((a, b) => b[0] - a[0]);
  };
  // the rules table's dense cell styles
  const th = {
    fontSize: "0.65rem", fontWeight: 700, py: 0.25,
    whiteSpace: "nowrap", borderBottom: "1px solid",
  } as const;
  const td = { fontSize: "0.7rem", py: 0.25 } as const;
  // the registered cell's TAIL-SIDE marker on the name (user-directed
  // "for the name, rsi14_1pct add it is bottom or top to indicate rise
  // vs drop", then "all sourced from backend data as exact, not to add
  // UI-only text"): both action sides of one mov_rsi sub share the sub
  // literal, and the side word is READ from the row's own ENGINE-
  // WRITTEN rule_params.condition ("… own bottom … tail" = the drop
  // side, "… own top … tail" = the rise side) — signalTailSide returns
  // null when the condition carries no side word and the bare name
  // renders (never a UI-side action→side mapping)
  const sideWord = (r: SignalRule): string | null =>
    signalTailSide(r.rule_params.condition);
  // the expand states: one per witness (its mapped cells nested under
  // it), then the window classification inside the open witness (rsi14 /
  // rsi10 / rsi6 groups, one open at a time)
  const [openWitness, setOpenWitness] = useState<string | null>(null);
  const [openWitnessWin, setOpenWitnessWin] = useState<string | null>(null);
  const witnessKey = (sub: string, w: SubCellSignalSpec) =>
    `${sub}|${w.code}|${w.role}`;
  // one full-width group separator row inside the rules table
  const groupRow = (text: string) => (
    <TableRow>
      <TableCell
        colSpan={5}
        sx={{
          color: "text.secondary", fontWeight: 700, fontSize: "0.65rem",
          py: 0.35, borderBottom: "1px solid", borderColor: "divider",
          whiteSpace: "normal",
        }}
      >
        {text}
      </TableCell>
    </TableRow>
  );
  return (
    <Box sx={{ p: 1, px: 1.5, borderLeft: 3, borderColor: "divider" }}>
      <Box sx={{
        display: "flex", alignItems: "center", flexWrap: "wrap", gap: 1, mb: 0.5,
      }}>
        <Typography variant="caption" sx={{ fontWeight: 700 }}>
          {(typeof portfolio.rule_params?.name === "string"
            && portfolio.rule_params.name) || "Portfolio signal"}
        </Typography>
        <Typography variant="caption" color="text.secondary">
          cell signals — mapped rsi rules under the stored witnesses
        </Typography>
      </Box>
      {(witnessGroups.length > 0 || mappedCells.length > 0) && (
        <Box sx={{ mb: 1 }}>
          <Table size="small" sx={{ maxWidth: 1080 }}>
            <TableHead>
              <TableRow>
                <TableCell sx={th}>Code</TableCell>
                <TableCell sx={th}>Signal</TableCell>
                <TableCell sx={th}>Action</TableCell>
                <TableCell sx={{ ...th, minWidth: 420 }}>
                  Rule condition
                </TableCell>
                <TableCell sx={th}>Context</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {witnessGroups.map((g) => [
                groupRow(`${signalSubWordLoose(g.sub)} · ${g.rows.length}`
                  + ` witness${g.rows.length === 1 ? "" : "es"}`),
                ...g.rows.map((w) => {
                  const key = witnessKey(g.sub, w);
                  const open = openWitness === key;
                  const cells = mappedFor(w);
                  return (
                    <Fragment key={`witness|${key}`}>
                      <TableRow
                        hover
                        onClick={() => setOpenWitness(open ? null : key)}
                        sx={{ cursor: "pointer" }}
                      >
                        <TableCell sx={{ ...td, pl: 5 }}>
                          <Box
                            component="span"
                            sx={{
                              display: "inline-flex", alignItems: "center",
                              gap: 0.5, pl: 1.5, borderLeft: "2px solid",
                              borderColor: "divider",
                            }}
                          >
                            <Box
                              component="span"
                              sx={{
                                color: "text.disabled", fontSize: "0.7rem",
                              }}
                            >
                              {open ? "▾" : "└"}
                            </Box>
                            <Chip
                              size="small"
                              label={w.code}
                              variant="outlined"
                              sx={{ fontSize: "0.65rem", height: 20 }}
                            />
                          </Box>
                        </TableCell>
                        <TableCell sx={td}>
                          <Tooltip
                            title={`${w.role} — ${w.condition} (${w.bar})`}
                            arrow
                          >
                            <Chip
                              size="small"
                              label={w.role}
                              sx={{ fontSize: "0.65rem", height: 20 }}
                            />
                          </Tooltip>
                        </TableCell>
                        {/* the PORTFOLIO LEVEL carries NO action (the
                         *  2026-10-25 semantics — the portfolio has no
                         *  action, only its cell signals do; user
                         *  re-directed 2026-10-26 "the portfolio level
                         *  should not have action"): the witness rows
                         *  are rule-params records — the stored action
                         *  key rides the JSON for the side-aligned
                         *  mapping (mappedFor above), never a chip
                         *  here. The Action column chips only the
                         *  REGISTERED cell rows below. */}
                        <TableCell sx={td}>—</TableCell>
                        <TableCell sx={{ ...td, whiteSpace: "normal" }}>
                          {`${w.condition} · ${w.bar}`}
                        </TableCell>
                        <TableCell sx={td}>
                          {`${cells.length} mapped`}
                        </TableCell>
                      </TableRow>
                      {open && mappedWindows(cells).map(([win, rows]) => {
                        const winKey = `${key}|${win}`;
                        const winOpen = openWitnessWin === winKey;
                        return (
                          <Fragment key={`mapped-win|${winKey}`}>
                            <TableRow
                              hover
                              onClick={() =>
                                setOpenWitnessWin(winOpen ? null : winKey)}
                              sx={{ cursor: "pointer" }}
                            >
                              <TableCell sx={{ ...td, pl: 7 }}>
                                <Box
                                  component="span"
                                  sx={{
                                    display: "inline-flex",
                                    alignItems: "center", gap: 0.5,
                                    pl: 1.5, borderLeft: "2px solid",
                                    borderColor: "divider",
                                  }}
                                >
                                  <Box
                                    component="span"
                                    sx={{
                                      color: "text.disabled",
                                      fontSize: "0.7rem",
                                    }}
                                  >
                                    {winOpen ? "▾" : "└"}
                                  </Box>
                                  <Chip
                                    size="small"
                                    label={win > 0 ? `RSI(${win})` : "—"}
                                    variant="outlined"
                                    sx={{ fontSize: "0.65rem", height: 20 }}
                                  />
                                </Box>
                              </TableCell>
                              <TableCell sx={td}>
                                <Typography
                                  variant="caption"
                                  color="text.secondary"
                                >
                                  {`${rows.length} registered cell${rows.length === 1 ? "" : "s"}`}
                                </Typography>
                              </TableCell>
                              <TableCell sx={td}>—</TableCell>
                              <TableCell
                                sx={{ ...td, whiteSpace: "normal" }}
                              >
                                {rows[0]
                                  ? String(
                                      rows[0].rule_params.condition ?? "—")
                                  : "—"}
                              </TableCell>
                              <TableCell sx={td}>
                                {win > 0 ? `RSI(${win})` : "—"}
                              </TableCell>
                            </TableRow>
                            {winOpen && rows.map((r) => {
                              const memberOpen = openMember
                                === `${r.code}|${r.signal_sub_type}|${r.signal_rule_id}`;
                              return (
                                <Fragment
                                  key={`sourced|${r.signal_rule_id}`}
                                >
                                  <TableRow
                                    hover
                                    onClick={() => onToggleMember(
                                      `${r.code}|${r.signal_sub_type}|${r.signal_rule_id}`,
                                    )}
                                    sx={{ cursor: "pointer" }}
                                  >
                                    <TableCell sx={{ ...td, pl: 9 }}>
                                      <Box
                                        component="span"
                                        sx={{
                                          display: "inline-flex",
                                          alignItems: "center", gap: 0.5,
                                          pl: 1.5, borderLeft: "2px solid",
                                          borderColor: "divider",
                                        }}
                                      >
                                        <Box
                                          component="span"
                                          sx={{
                                            color: "text.disabled",
                                            fontSize: "0.7rem",
                                          }}
                                        >
                                          └
                                        </Box>
                                        <Chip
                                          size="small"
                                          label={r.code}
                                          variant="outlined"
                                          sx={{
                                            fontSize: "0.65rem", height: 20,
                                          }}
                                        />
                                        <Tooltip
                                          title={"is_in_portfolio — the in-portfolio "
                                            + "tier copy of the mov_rsi rule (a "
                                            + "SEPARATE signal_rules row from any "
                                            + "standalone twin: the flag is a PK "
                                            + "member); mapped into this portfolio "
                                            + "rule by signal_rule_ids"}
                                          arrow
                                        >
                                          <Chip
                                            size="small"
                                            label="in-portfolio"
                                            variant="outlined"
                                            sx={{
                                              fontSize: "0.6rem", height: 18,
                                              color: "text.secondary",
                                            }}
                                          />
                                        </Tooltip>
                                      </Box>
                                    </TableCell>
                                    <TableCell sx={td}>
                                      {(() => {
                                        const side = sideWord(r);
                                        return (
                                          <Tooltip
                                            title={side == null
                                              ? signalRecordName(
                                                r.signal_type,
                                                r.signal_sub_type,
                                              )
                                              : `${signalRecordName(
                                                r.signal_type,
                                                r.signal_sub_type,
                                              )} · ${side}`}
                                            arrow
                                          >
                                            <Chip
                                              size="small"
                                              label={side == null
                                                ? signalSubWord(
                                                  r.signal_type,
                                                  r.signal_sub_type,
                                                )
                                                : `${signalSubWord(
                                                  r.signal_type,
                                                  r.signal_sub_type,
                                                )} · ${side}`}
                                              sx={{
                                                fontSize: "0.65rem", height: 20,
                                              }}
                                            />
                                          </Tooltip>
                                        );
                                      })()}
                                    </TableCell>
                                    <TableCell sx={td}>
                                      <SignalActionChip action={r.action} />
                                    </TableCell>
                                    <TableCell
                                      sx={{ ...td, whiteSpace: "normal" }}
                                    >
                                      {String(r.rule_params.condition ?? "—")}
                                    </TableCell>
                                    <TableCell sx={td}>
                                      <Box sx={{
                                        display: "inline-flex", gap: 0.5,
                                        alignItems: "center",
                                      }}>
                                        <TrendChip trend={r.trend} />
                                        <RegimeWord regime={r.regime_state} />
                                      </Box>
                                    </TableCell>
                                  </TableRow>
                                  {memberOpen && (
                                    <TableRow>
                                      <TableCell
                                        colSpan={5}
                                        sx={{ py: 0.5,
                                              borderBottom: "1px solid",
                                              borderColor: "divider" }}
                                      >
                                        {ruleDetails(r)}
                                      </TableCell>
                                    </TableRow>
                                  )}
                                </Fragment>
                              );
                            })}
                          </Fragment>
                        );
                      })}
                    </Fragment>
                  );
                }),
              ])}
            </TableBody>
          </Table>
        </Box>
      )}
      {witnessGroups.length === 0 && mappedCells.length === 0 && (
        <Typography variant="caption" color="text.secondary">
          (no cell signals resolved — run the signals pipeline)
        </Typography>
      )}
    </Box>
  );
}
