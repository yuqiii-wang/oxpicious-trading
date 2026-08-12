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
 * Works with any StrategyDecision[] that follows the shared type.
 */
import {
  Accordion, AccordionDetails, AccordionSummary,
  Box, Chip, Tooltip, Typography,
} from "@mui/material";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import InfoOutlinedIcon from "@mui/icons-material/InfoOutlined";
import type { StrategyDecision } from "../../../shared/types";

interface DecisionTableProps {
  decisions: StrategyDecision[];
  /** Accordion expanded state. Defaults to false (collapsed). */
  defaultExpanded?: boolean;
  maxHeight?: number;
}

export default function DecisionTable({
  decisions,
  defaultExpanded = false,
  maxHeight = 400,
}: DecisionTableProps) {
  if (decisions.length === 0) return null;

  const nBuys = decisions.filter((d) => d.side === "BUY").length;
  const nSells = decisions.filter((d) => d.side === "SELL").length;

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
        <Box sx={{ display: "flex", alignItems: "center", gap: 1.5, mr: 2 }}>
          <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>
            Trade Decisions
          </Typography>
          <Chip label={`${decisions.length}`} size="small" sx={{ fontSize: "0.7rem" }} />
          <Chip label={`${nBuys} BUY`} size="small" color="success" variant="outlined" sx={{ fontSize: "0.7rem" }} />
          <Chip label={`${nSells} SELL`} size="small" color="error" variant="outlined" sx={{ fontSize: "0.7rem" }} />
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
              {decisions.map((d) => {
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
                return (
                  <tr key={d.decision_no}>
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
                      }}
                    >
                      {d.signal_reason}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </Box>
        </Box>
      </AccordionDetails>
    </Accordion>
  );
}
