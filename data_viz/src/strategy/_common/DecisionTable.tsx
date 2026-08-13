/**
 * DecisionTable — expandable trade-decisions table for any strategy backtest.
 *
 * Renders as an MUI Accordion (collapsed by default) with a summary showing
 * the decision count. When expanded, shows a scrollable table of BUY/SELL
 * decisions with columns:
 *   #, Side, Date, Conf, Qty, Norm, Mean Buy, Position, Cash After, P&L, Slip, Fee, Reason
 *
 * "Conf" is the confidence score (0-100). For BUY it equals qty (qty is
 * stored as confidence on BUYs). For SELL it is derived as
 * (qty / total_qty_before) * 100 (the stored qty on a SELL is the actual
 * quantity sold, not the confidence). Displayed as an integer with a colored
 * bar so weak vs strong signals are visually distinguishable.
 *
 * "Qty" is the actual quantity traded. For BUY: equals confidence (0-100).
 * For SELL: quantity sold = (confidence/100) * total_qty_before (can exceed
 * 100 when total_qty_before > 100). Displayed as a decimal.
 *
 * "Norm" is normalized_fill_price = fill_price / first_buy_fill_price * 100,
 * so the first BUY reads as 100 and later fills read as % change from the
 * entry (105.0 = +5%, 94.1 = -5.9%). Colored green (>100, above entry) /
 * red (<100, below entry) / neutral (==100, the anchor).
 *
 * "Mean Buy" is normalized_mean_buy_price = the weighted-avg BUY normalized
 * fill price across all historical BUYs still in the remaining position
 * (the cost basis realized_pnl is computed against). For BUY rows it carries
 * the new weighted average INCLUDING this BUY; for SELL rows it carries the
 * pre-SELL value used to compute realized_pnl. Colored like Norm: green
 * (>100, avg buy above the first-buy anchor), red (<100, avg below anchor),
 * neutral (=100). On a SELL, Norm > Mean Buy ⇒ winning trade; Norm < Mean
 * Buy ⇒ losing trade (the P&L column shows the magnitude).
 *
 * Reason column + mixed-mode tooltip
 * -----------------------------------
 * For BINARY runs (single algo), signal_reason is the algo's own reason text
 * (e.g. "MA5 crossed above MA60 by 2.1%").
 *
 * For MIXED runs (portfolio:bb*0.5+macd*0.5), signal_reason carries a JSON
 * prefix ``__MIX__<json>__<human-readable>``. The JSON encodes each algo's
 * weight (w), raw signal_confidence (c), and net contribution (n = w × c),
 * plus the blended total and whether netting occurred (algos disagreed on
 * direction). The cell shows the human-readable part; a structured tooltip
 * renders per-algo contribution bars so the user can audit how the composite
 * signal was netted.
 *
 * Works with any StrategyDecision[] that follows the shared type.
 */
import {
  Accordion, AccordionDetails, AccordionSummary,
  Box, Chip, MenuItem, Select, Tooltip, Typography,
} from "@mui/material";
import { Fragment, type ReactNode } from "react";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import InfoOutlinedIcon from "@mui/icons-material/InfoOutlined";
import type { StrategyDecision } from "../../../shared/types";

// ---------------------------------------------------------------------------
// Mixed-mode signal_reason parser
// ---------------------------------------------------------------------------
// signal_reason for mixed mode = "__MIX__<json>__<human-readable>"
// The JSON carries per-algo contributions so the UI can render a structured
// tooltip. Binary mode (no prefix) is returned as-is.

interface MixContribution {
  a: string;     // abbreviation (bb/macd/ma)
  algo: string;  // full algo name
  w: number;     // weight (0-1)
  c: number;     // raw signal_confidence (signed, -100..100)
  n: number;     // net contribution = w * c (signed)
}

interface MixData {
  algos: MixContribution[];
  blended: number;  // sum of contributions = blended signal
  netted: boolean;  // some algos BUY, some SELL on this bar
  side: string;     // BUY or SELL (the netted side)
}

const MIX_PREFIX = "__MIX__";

function parseMixReason(reason: string | null | undefined): {
  mix: MixData | null;
  human: string;
} {
  if (!reason || !reason.startsWith(MIX_PREFIX)) {
    return { mix: null, human: reason ?? "" };
  }
  // Strip "__MIX__" prefix, then find the closing "__" that ends the JSON.
  const rest = reason.slice(MIX_PREFIX.length);
  const endIdx = rest.indexOf("__");
  if (endIdx < 0) {
    return { mix: null, human: reason };
  }
  const jsonStr = rest.slice(0, endIdx);
  const human = rest.slice(endIdx + 2);
  try {
    const mix = JSON.parse(jsonStr) as MixData;
    return { mix, human };
  } catch {
    return { mix: null, human };
  }
}

/** Build a structured tooltip ReactNode for a mixed-mode decision. Shows
 *  each algo's weight, raw signal_confidence, and net contribution as
 *  colored bars. */
function renderMixTooltip(mix: MixData): ReactNode {
  const maxAbs = Math.max(100, ...mix.algos.map((a) => Math.abs(a.c)));
  return (
    <Box sx={{ p: 1, minWidth: 280, maxWidth: 360 }}>
      <Typography variant="caption" sx={{ fontWeight: 700, display: "block", mb: 1 }}>
        {mix.side} Composite Signal {mix.netted ? "(netted — algos disagreed)" : ""}
      </Typography>
      {mix.algos.map((a) => {
        // Bar width = |contribution| / maxAbs * 100%.
        const barWidth = Math.min(100, (Math.abs(a.n) / maxAbs) * 100);
        const isBuy = a.c > 0;
        const isSell = a.c < 0;
        const barColor = isBuy ? "#4caf50" : isSell ? "#f44336" : "#9e9e9e";
        return (
          <Box key={a.algo} sx={{ mb: 0.75 }}>
            <Box sx={{ display: "flex", justifyContent: "space-between", fontSize: "0.7rem", mb: 0.25 }}>
              <span style={{ fontWeight: 600 }}>{a.a}</span>
              <span style={{ color: "#9e9e9e" }}>
                w={Math.round(a.w * 100)}%
              </span>
            </Box>
            <Box sx={{ display: "flex", alignItems: "center", gap: 0.5, fontSize: "0.68rem" }}>
              <span style={{ width: 32, color: isBuy ? "#4caf50" : isSell ? "#f44336" : "#9e9e9e", fontWeight: 600 }}>
                {isBuy ? "BUY" : isSell ? "SELL" : "—"}
              </span>
              <Box sx={{ flex: 1, height: 8, bgcolor: "rgba(0,0,0,0.08)", borderRadius: 0.5, position: "relative" }}>
                <Box
                  sx={{
                    position: "absolute",
                    left: 0,
                    top: 0,
                    height: "100%",
                    width: `${barWidth}%`,
                    bgcolor: barColor,
                    borderRadius: 0.5,
                    opacity: 0.7,
                  }}
                />
              </Box>
              <span style={{ width: 48, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                {a.c >= 0 ? "+" : ""}{a.c.toFixed(1)}
              </span>
              <span style={{ width: 48, textAlign: "right", fontWeight: 600, fontVariantNumeric: "tabular-nums" }}>
                {a.n >= 0 ? "+" : ""}{a.n.toFixed(1)}
              </span>
            </Box>
          </Box>
        );
      })}
      <Box sx={{ mt: 1, pt: 0.5, borderTop: 1, borderColor: "divider", display: "flex", justifyContent: "space-between", fontSize: "0.7rem" }}>
        <span style={{ fontWeight: 700 }}>Blended Σ</span>
        <span style={{ fontWeight: 700, color: mix.blended >= 0 ? "#4caf50" : "#f44336" }}>
          {mix.blended >= 0 ? "+" : ""}{mix.blended.toFixed(1)}
        </span>
      </Box>
      <Typography variant="caption" sx={{ display: "block", mt: 0.5, color: "text.secondary", fontSize: "0.62rem" }}>
        c = raw signal_confidence (−100..+100); n = weight × c (net contribution)
      </Typography>
    </Box>
  );
}

interface DecisionTableProps {
  decisions: StrategyDecision[];
  /** Accordion expanded state. Defaults to false (collapsed). */
  defaultExpanded?: boolean;
  maxHeight?: number;
  /** Available forecast scenario names (e.g. ["mir_255d_std_scale", "flip_255d_std_scale", ...]).
   * When non-empty, a scenario dropdown is rendered. */
  forecastScenarios?: string[];
  /** Currently selected scenario (null = parent seq, no forecast). */
  selectedScenario?: string | null;
  /** Callback when the user selects a different scenario. */
  onScenarioChange?: (scenario: string | null) => void;
}

export default function DecisionTable({
  decisions,
  defaultExpanded = false,
  maxHeight = 400,
  forecastScenarios = [],
  selectedScenario = null,
  onScenarioChange,
}: DecisionTableProps) {
  if (decisions.length === 0) return null;

  const nBuys = decisions.filter((d) => d.side === "BUY").length;
  const nSells = decisions.filter((d) => d.side === "SELL").length;

  // Forecast decisions are SELL rows whose signal_reason starts with
  // "FORECAST SELL" (written by strategy._1m_forcast.decisions). When a
  // scenario is selected, the child seq's decisions include forecast sells
  // for that scenario.
  const FORECAST_PREFIX = "FORECAST SELL";
  const firstForecastIdx = decisions.findIndex(
    (d) => d.signal_reason?.startsWith(FORECAST_PREFIX),
  );
  const hasForecastDelimiter = firstForecastIdx >= 0;
  const nForecast = hasForecastDelimiter
    ? decisions.length - firstForecastIdx
    : 0;
  const nActualSells = nSells - nForecast;

  // Whether to show the scenario dropdown. Shown when scenarios are available
  // AND the callback is provided.
  const showScenarioDropdown = forecastScenarios.length > 0 && onScenarioChange;

  return (
    <Accordion
      defaultExpanded={defaultExpanded}
      sx={{
        bgcolor: "background.paper",
        border: 1,
        borderColor: "divider",
        borderRadius: "1.5px !important",
        "&:before": { display: "none" },
      }}
    >
      <AccordionSummary expandIcon={<ExpandMoreIcon />}>
        <Box sx={{ display: "flex", alignItems: "center", gap: 1.5, mr: 2, flexWrap: "wrap" }}>
          <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>
            Trade Decisions
          </Typography>
          <Chip label={`${decisions.length}`} size="small" sx={{ fontSize: "0.7rem" }} />
          <Chip label={`${nBuys} BUY`} size="small" color="success" variant="outlined" sx={{ fontSize: "0.7rem" }} />
          <Chip label={`${nActualSells} SELL`} size="small" color="error" variant="outlined" sx={{ fontSize: "0.7rem" }} />
          {hasForecastDelimiter && (
            <Chip
              label={`${nForecast} FORECAST SELL`}
              size="small"
              sx={{
                fontSize: "0.7rem",
                bgcolor: "rgba(149, 117, 205, 0.18)",
                color: "#9575CD",
                border: "1px solid rgba(149, 117, 205, 0.5)",
                fontWeight: 600,
              }}
            />
          )}
        </Box>
      </AccordionSummary>
      <AccordionDetails sx={{ p: 1 }}>
        <Box
          sx={{
            maxHeight,
            overflow: "auto",
          }}
        >
          <Box
            component="table"
            sx={{
              width: "100%",
              borderCollapse: "collapse",
              fontSize: "0.75rem",
              "& th, & td": {
                borderBottom: 1,
                borderColor: "divider",
                px: 1,
                py: 0.5,
                textAlign: "left",
              },
              "& th": {
                fontWeight: 600,
                color: "text.secondary",
                position: "sticky",
                top: 0,
                bgcolor: "background.paper",
                zIndex: 1,
              },
            }}
          >
            <thead>
              <tr>
                <th>#</th>
                <th>Side</th>
                <th>Date</th>
                <th>Conf</th>
                <th>Qty</th>
                <th>Norm</th>
                <th>Mean Buy</th>
                <th>Position</th>
                <th>Cash After</th>
                <th>P&L</th>
                <th>
                  <Box component="span" sx={{ display: "inline-flex", alignItems: "center", gap: 0.25 }}>
                    Slip
                    <Tooltip
                      title="Slippage = |fill_price − close| / 100  (worst-case OHLC fill deviation from close, per-100-shares scale)"
                      arrow
                      placement="top"
                    >
                      <InfoOutlinedIcon sx={{ fontSize: "0.85rem", color: "text.disabled", cursor: "help" }} />
                    </Tooltip>
                  </Box>
                </th>
                <th>
                  <Box component="span" sx={{ display: "inline-flex", alignItems: "center", gap: 0.25 }}>
                    Fee
                    <Tooltip
                      title="Fee = 0.2% × BUY notional (normalized money); BUY only, 0 for SELL. Deducted from cash_after."
                      arrow
                      placement="top"
                    >
                      <InfoOutlinedIcon sx={{ fontSize: "0.85rem", color: "text.disabled", cursor: "help" }} />
                    </Tooltip>
                  </Box>
                </th>
                <th>Reason</th>
              </tr>
            </thead>
            <tbody>
              {decisions.map((d, i) => {
                const normDelta = d.normalized_fill_price - 100;
                const normColor = normDelta > 0.01
                  ? "#2e7d32"
                  : normDelta < -0.01
                    ? "#c62828"
                    : "#9e9e9e";
                // Confidence (0-100): for BUY it equals qty (stored as
                // confidence); for SELL it is reverse-derived from the
                // actual qty sold and the pre-SELL total_qty.
                const confidence = d.side === "BUY"
                  ? d.qty
                  : d.total_qty_before > 0
                    ? (d.qty / d.total_qty_before) * 100
                    : 0;
                // Mean Buy = weighted-avg BUY normalized price (cost basis).
                // Same color scheme as Norm: green >100, red <100, gray =100.
                // On SELL rows, comparing Norm vs Mean Buy tells you if the
                // trade is winning (Norm > Mean Buy) or losing.
                const meanBuyDelta = d.normalized_mean_buy_price - 100;
                const meanBuyColor = meanBuyDelta > 0.01
                  ? "#2e7d32"
                  : meanBuyDelta < -0.01
                    ? "#c62828"
                    : "#9e9e9e";
                const meanBuyTooltip = d.side === "SELL"
                  ? `Cost basis used for realized_pnl: ${d.normalized_mean_buy_price.toFixed(2)} (${meanBuyDelta >= 0 ? "+" : ""}${meanBuyDelta.toFixed(2)} from entry). Sell Norm ${d.normalized_fill_price.toFixed(2)} ${d.normalized_fill_price >= d.normalized_mean_buy_price ? "≥" : "<"} Mean Buy ⇒ ${d.normalized_fill_price >= d.normalized_mean_buy_price ? "winning" : "losing"} trade`
                  : `Post-BUY weighted-avg cost basis: ${d.normalized_mean_buy_price.toFixed(2)} (${meanBuyDelta >= 0 ? "+" : ""}${meanBuyDelta.toFixed(2)} from entry)`;
                const isForecastRow = d.signal_reason?.startsWith(FORECAST_PREFIX);
                return (
                  <Fragment key={d.decision_no}>
                    {hasForecastDelimiter && i === firstForecastIdx && (
                      <tr>
                        <td
                          colSpan={13}
                          style={{
                            bgcolor: "rgba(149, 117, 205, 0.12)",
                            backgroundColor: "rgba(149, 117, 205, 0.12)",
                            borderTop: "2px solid rgba(149, 117, 205, 0.6)",
                            borderBottom: "2px solid rgba(149, 117, 205, 0.6)",
                            padding: "4px 8px",
                          }}
                        >
                          <Box sx={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 1 }}>
                            <Typography
                              component="span"
                              sx={{
                                fontWeight: 700,
                                color: "#9575CD",
                                fontSize: "0.7rem",
                                letterSpacing: "0.05em",
                              }}
                            >
                              ── FORECAST ·
                            </Typography>
                            {showScenarioDropdown ? (
                              <Select
                                size="small"
                                value={selectedScenario ?? ""}
                                onChange={(e) => {
                                  const v = e.target.value;
                                  onScenarioChange!(v === "" ? null : v);
                                }}
                                sx={{
                                  fontSize: "0.7rem",
                                  fontWeight: 600,
                                  color: "#9575CD",
                                  minWidth: 140,
                                  height: 24,
                                  "& .MuiSelect-select": { py: 0.25, px: 1 },
                                  "& .MuiOutlinedInput-notchedOutline": {
                                    borderColor: "rgba(149, 117, 205, 0.5)",
                                  },
                                  "&:hover .MuiOutlinedInput-notchedOutline": {
                                    borderColor: "rgba(149, 117, 205, 0.8)",
                                  },
                                }}
                              >
                                <MenuItem value="" sx={{ fontSize: "0.75rem" }}>
                                  (actual only)
                                </MenuItem>
                                {forecastScenarios.map((sc) => (
                                  <MenuItem key={sc} value={sc} sx={{ fontSize: "0.75rem" }}>
                                    {sc}
                                  </MenuItem>
                                ))}
                              </Select>
                            ) : (
                              <Typography
                                component="span"
                                sx={{
                                  fontWeight: 700,
                                  color: "#9575CD",
                                  fontSize: "0.7rem",
                                  letterSpacing: "0.05em",
                                }}
                              >
                                sell schedule ({nForecast} days)
                              </Typography>
                            )}
                            <Typography
                              component="span"
                              sx={{
                                fontWeight: 700,
                                color: "#9575CD",
                                fontSize: "0.7rem",
                                letterSpacing: "0.05em",
                              }}
                            >
                              ──
                            </Typography>
                          </Box>
                        </td>
                      </tr>
                    )}
                    <tr style={isForecastRow ? { backgroundColor: "rgba(149, 117, 205, 0.05)" } : undefined}>
                      <td>{d.decision_no}</td>
                      <td style={{ color: d.side === "BUY" ? "#4caf50" : "#f44336", fontWeight: 700 }}>
                        {d.side}
                      </td>
                      <td>{d.exec_date}</td>
                      <td>
                        <span
                          title={d.side === "BUY"
                            ? `Confidence: ${d.qty.toFixed(2)} / 100`
                            : `Confidence: ${confidence.toFixed(2)} / 100 (derived from qty / total_qty_before)`}
                          style={{
                            display: "inline-block",
                            minWidth: 36,
                            textAlign: "right",
                            fontWeight: 600,
                            color: confidence >= 66
                              ? "#2e7d32"
                              : confidence >= 33
                                ? "#ed6c02"
                                : "#9e9e9e",
                          }}
                        >
                          {Math.round(confidence)}
                        </span>
                      </td>
                      <td>
                        <span
                          title={d.side === "BUY"
                            ? `Quantity traded (= confidence): ${d.qty.toFixed(2)}`
                            : `Quantity sold: ${d.qty.toFixed(2)} (= (conf/100) * total_qty_before=${d.total_qty_before.toFixed(2)})`}
                          style={{
                            display: "inline-block",
                            minWidth: 48,
                            textAlign: "right",
                            color: "text.secondary",
                          }}
                        >
                          {d.qty.toFixed(2)}
                        </span>
                      </td>
                      <td>
                        <span
                          title={`Normalized to 100 at first BUY: ${d.normalized_fill_price.toFixed(2)} (${normDelta >= 0 ? "+" : ""}${normDelta.toFixed(2)} from entry)`}
                          style={{
                            display: "inline-block",
                            minWidth: 48,
                            textAlign: "right",
                            fontWeight: 600,
                            color: normColor,
                          }}
                        >
                          {d.normalized_fill_price.toFixed(1)}
                        </span>
                      </td>
                      <td>
                        <span
                          title={meanBuyTooltip}
                          style={{
                            display: "inline-block",
                            minWidth: 48,
                            textAlign: "right",
                            fontWeight: 600,
                            color: meanBuyColor,
                          }}
                        >
                          {d.normalized_mean_buy_price.toFixed(1)}
                        </span>
                      </td>
                      <td>{d.position_after.toFixed(4)}</td>
                      <td>{d.cash_after.toLocaleString(undefined, { minimumFractionDigits: 4, maximumFractionDigits: 4 })}</td>
                      <td style={{ color: d.realized_pnl >= 0 ? "#4caf50" : "#f44336", fontWeight: 600 }}>
                        {d.realized_pnl !== 0
                          ? `${d.realized_pnl >= 0 ? "+" : ""}${d.realized_pnl.toFixed(2)}`
                          : "—"}
                      </td>
                      <td>
                        <span
                          title={`Slippage = |fill - close| / 100: ${d.slippage ?? 0}`}
                          style={{
                            display: "inline-block",
                            minWidth: 40,
                            textAlign: "right",
                            color: "#9e9e9e",
                          }}
                        >
                          {d.slippage != null ? d.slippage.toFixed(2) : "—"}
                        </span>
                      </td>
                      <td>
                        <span
                          title={`Fee (0.2% BUY notional, normalized money): ${d.fee ?? 0}`}
                          style={{
                            display: "inline-block",
                            minWidth: 40,
                            textAlign: "right",
                            color: d.side === "BUY" ? "#ed6c02" : "#9e9e9e",
                          }}
                        >
                          {d.fee != null && d.fee !== 0
                            ? d.fee.toFixed(2)
                            : "—"}
                        </span>
                      </td>
                      <td
                        style={{
                          maxWidth: 300,
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                          color: isForecastRow ? "#9575CD" : undefined,
                        }}
                      >
                        {(() => {
                          const { mix, human } = parseMixReason(d.signal_reason);
                          if (mix) {
                            return (
                              <Tooltip
                                title={renderMixTooltip(mix)}
                                arrow
                                placement="left"
                                enterTouchDelay={0}
                              >
                                <span style={{ cursor: "help", borderBottom: mix.netted ? "1px dashed #ed6c02" : "1px dotted rgba(0,0,0,0.3)" }}>
                                  {human}
                                </span>
                              </Tooltip>
                            );
                          }
                          // Binary mode: show raw signal_reason (no tooltip —
                          // the algo's reason text is already self-explanatory).
                          return d.signal_reason;
                        })()}
                      </td>
                    </tr>
                  </Fragment>
                );
              })}
              {/* When no forecast decisions are shown (parent seq) but
                  scenarios are available, render a dropdown row at the end
                  so the user can select a scenario to view its forecast. */}
              {showScenarioDropdown && !hasForecastDelimiter && (
                <tr>
                  <td
                    colSpan={13}
                    style={{
                      bgcolor: "rgba(149, 117, 205, 0.08)",
                      backgroundColor: "rgba(149, 117, 205, 0.08)",
                      borderTop: "1px dashed rgba(149, 117, 205, 0.4)",
                      padding: "4px 8px",
                    }}
                  >
                    <Box sx={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 1 }}>
                      <Typography
                        component="span"
                        sx={{
                          fontWeight: 600,
                          color: "#9575CD",
                          fontSize: "0.7rem",
                          letterSpacing: "0.05em",
                        }}
                      >
                        View forecast scenario:
                      </Typography>
                      <Select
                        size="small"
                        value={selectedScenario ?? ""}
                        onChange={(e) => {
                          const v = e.target.value;
                          onScenarioChange!(v === "" ? null : v);
                        }}
                        displayEmpty
                        renderValue={(v) => v === "" ? "(select…)" : v as string}
                        sx={{
                          fontSize: "0.7rem",
                          fontWeight: 600,
                          color: "#9575CD",
                          minWidth: 140,
                          height: 24,
                          "& .MuiSelect-select": { py: 0.25, px: 1 },
                          "& .MuiOutlinedInput-notchedOutline": {
                            borderColor: "rgba(149, 117, 205, 0.5)",
                          },
                          "&:hover .MuiOutlinedInput-notchedOutline": {
                            borderColor: "rgba(149, 117, 205, 0.8)",
                          },
                        }}
                      >
                        <MenuItem value="" sx={{ fontSize: "0.75rem" }}>
                          (actual only)
                        </MenuItem>
                        {forecastScenarios.map((sc) => (
                          <MenuItem key={sc} value={sc} sx={{ fontSize: "0.75rem" }}>
                            {sc}
                          </MenuItem>
                        ))}
                      </Select>
                    </Box>
                  </td>
                </tr>
              )}
            </tbody>
          </Box>
        </Box>
      </AccordionDetails>
    </Accordion>
  );
}
